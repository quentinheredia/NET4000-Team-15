#!/bin/bash
export PATH="/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/frr:$PATH"

# --- USER INPUT ---
read -p "Enter number of satellites: " NUM_SATS
read -p "Enter number of hosts (ground stations): " NUM_HOSTS

# Validate inputs
if ! [[ "$NUM_SATS" =~ ^[0-9]+$ ]] || [ "$NUM_SATS" -lt 1 ] || [ "$NUM_SATS" -gt 200 ]; then
    echo "Error: Number of satellites must be between 1 and 200."
    exit 1
fi
if ! [[ "$NUM_HOSTS" =~ ^[0-9]+$ ]] || [ "$NUM_HOSTS" -lt 1 ] || [ "$NUM_HOSTS" -gt 255 ]; then
    echo "Error: Number of hosts must be between 1 and 255."
    exit 1
fi
# In a full mesh, satellite pair (i, j) uses subnet 10.i.j.0/24.
# Both i and j must fit in a single octet (<=255), so NUM_SATS <= 200 covers this.

echo ""
echo "Creating full mesh topology: $NUM_SATS satellites, $NUM_HOSTS hosts..."
echo ""

# --- CLEANUP ---
echo "Cleaning up any previous topology..."
for ns in $(ip netns list 2>/dev/null | awk '{print $1}' | grep -E '^[rh][0-9]+$'); do
    ip netns del "$ns" 2>/dev/null
done
for link in $(ip link show 2>/dev/null | grep 'v-' | awk -F': ' '{print $2}' | cut -d'@' -f1); do
    ip link del "$link" 2>/dev/null
done
pkill -f "ospfd.*-N" 2>/dev/null
pkill -f "zebra.*-N" 2>/dev/null
rm -rf /var/run/frr-* 2>/dev/null
rm -rf /tmp/frr-* 2>/dev/null

# --- CREATE NAMESPACES ---
echo "Creating satellite namespaces (r1-r${NUM_SATS})..."
for i in $(seq 1 "$NUM_SATS"); do
    ip netns add r$i
    ip netns exec r$i ip link set lo up
    ip netns exec r$i sysctl -w net.ipv4.ip_forward=1 > /dev/null
done

echo "Creating host namespaces (h1-h${NUM_HOSTS})..."
for i in $(seq 1 "$NUM_HOSTS"); do
    ip netns add h$i
    ip netns exec h$i ip link set lo up
done

# --- CONNECT FUNCTION ---
# Creates a veth pair between two namespaces and assigns IPs.
connect() {
    local ns1=$1
    local ns2=$2
    local ip1=$3
    local ip2=$4
    local dev1="v-${ns1}-${ns2}"
    local dev2="v-${ns2}-${ns1}"

    ip link add "$dev1" type veth peer name "$dev2"
    ip link set "$dev1" netns "$ns1"
    ip link set "$dev2" netns "$ns2"
    ip netns exec "$ns1" ip addr add "$ip1" dev "$dev1"
    ip netns exec "$ns2" ip addr add "$ip2" dev "$dev2"
    ip netns exec "$ns1" ip link set "$dev1" up
    ip netns exec "$ns2" ip link set "$dev2" up
}

# --- FULL MESH: SATELLITE-TO-SATELLITE LINKS ---
# Every pair (ri, rj) with i < j gets subnet 10.i.j.0/24.
# ri gets 10.i.j.1/24, rj gets 10.i.j.2/24.
# Interface on ri: v-ri-rj  |  Interface on rj: v-rj-ri
echo "Wiring full mesh between satellites..."
for i in $(seq 1 "$NUM_SATS"); do
    for j in $(seq $((i + 1)) "$NUM_SATS"); do
        connect r$i r$j "10.${i}.${j}.1/24" "10.${i}.${j}.2/24"
    done
done

# --- HOST-TO-SATELLITE LINKS ---
# Host hX connects to satellite r((X-1) % NUM_SATS + 1), distributing hosts
# evenly across satellites. Subnet: 10.0.X.0/24, hX gets .1, satellite gets .2.
echo "Connecting hosts to satellites..."
for i in $(seq 1 "$NUM_HOSTS"); do
    sat_idx=$(( (i - 1) % NUM_SATS + 1 ))
    connect h$i r${sat_idx} "10.0.${i}.1/24" "10.0.${i}.2/24"
done

sleep 2

# --- HOST DEFAULT ROUTES ---
for i in $(seq 1 "$NUM_HOSTS"); do
    sat_idx=$(( (i - 1) % NUM_SATS + 1 ))
    ip netns exec h$i ip route add default via "10.0.${i}.2"
done

# --- FRR/OSPF CONFIGURATION ---
echo "Writing FRR/OSPF configuration..."
for i in $(seq 1 "$NUM_SATS"); do
    router_id="192.168.0.${i}"
    mkdir -p /var/run/frr-r$i
    chmod 777 /var/run/frr-r$i

    {
        echo "hostname r$i"
        echo "log stdout debugging"
        echo "service integrated-vtysh-config"
        echo "!"
        echo "router ospf"
        echo "  router-id ${router_id}"
        echo "  log-adjacency-changes detail"
        echo "  network 0.0.0.0/0 area 0"
        echo "  passive-interface default"
        # Activate OSPF on all satellite-to-satellite interfaces.
        # For router rI, its interface to rJ is always named v-rI-rJ.
        for j in $(seq 1 "$NUM_SATS"); do
            if [ "$j" -ne "$i" ]; then
                echo "  no passive-interface v-r${i}-r${j}"
            fi
        done
        # Host-facing interfaces remain passive (advertised but no adjacency).
        # They are already passive by default; nothing extra needed here.
        echo "!"
        echo "line vty"
        echo "!"
    } > /var/run/frr-r$i/frr.conf

    mkdir -p /etc/frr/r$i
    ln -sf /var/run/frr-r$i/frr.conf /etc/frr/r$i/frr.conf
done

# --- START FRR DAEMONS ---
echo "Starting FRR daemons..."
for i in $(seq 1 "$NUM_SATS"); do
    echo "  Starting r$i..."
    ip netns exec r$i env PATH="/usr/lib/frr:/usr/sbin:/usr/bin:/sbin:/bin" \
        zebra -N r$i -d -f /var/run/frr-r$i/frr.conf
    sleep 1
    ip netns exec r$i env PATH="/usr/lib/frr:/usr/sbin:/usr/bin:/sbin:/bin" \
        ospfd -N r$i -d -f /var/run/frr-r$i/frr.conf
done

echo ""
echo "Waiting for OSPF convergence (30 seconds)..."
sleep 30

# --- SUMMARY ---
echo ""
echo "============================================"
echo " Topology ready"
echo "============================================"
echo ""
echo "Satellites : $(seq -s ' ' 1 "$NUM_SATS" | sed 's/\([0-9]*\)/r\1/g')"
echo "Hosts      : $(seq -s ' ' 1 "$NUM_HOSTS" | sed 's/\([0-9]*\)/h\1/g')"
echo ""
echo "Satellite full-mesh links:"
for i in $(seq 1 "$NUM_SATS"); do
    for j in $(seq $((i + 1)) "$NUM_SATS"); do
        echo "  r${i} <-> r${j}   10.${i}.${j}.0/24  (r${i}=.1, r${j}=.2)"
    done
done
echo ""
echo "Host connections:"
for i in $(seq 1 "$NUM_HOSTS"); do
    sat_idx=$(( (i - 1) % NUM_SATS + 1 ))
    echo "  h${i} <-> r${sat_idx}   10.0.${i}.0/24  (h${i}=.1, r${sat_idx}=.2)"
done
echo ""
echo "Run with: sudo bash full-mesh-namespace.sh"
