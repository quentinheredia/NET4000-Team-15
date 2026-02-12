#!/bin/bash
export PATH="/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/frr:$PATH"

# Cleanup in case there are any leftover configs from testing 
for ns in r1 r2 r3 r4 r5 r6 h1 h2; do ip netns del $ns 2>/dev/null; done
for link in $(ip link show | grep 'v-' | awk -F': ' '{print $2}' | cut -d'@' -f1); do ip link del $link 2>/dev/null; done
pkill -f "ospfd.*-N" 2>/dev/null
pkill -f "zebra.*-N" 2>/dev/null
rm -rf /var/run/frr-* 2>/dev/null
rm -rf /tmp/frr-* 2>/dev/null

# Create namespaces
for r in r1 r2 r3 r4 r5 r6; do
    ip netns add $r
    ip netns exec $r ip link set lo up
    ip netns exec $r sysctl -w net.ipv4.ip_forward=1 > /dev/null
done

for host in h1 h2; do
    ip netns add $host
    ip netns exec $host ip link set lo up
done

connect() {
    local ns1=$1
    local ns2=$2
    local ip1=$3
    local ip2=$4
    local dev1="v-${ns1}-${ns2}"
    local dev2="v-${ns2}-${ns1}"

    ip link add $dev1 type veth peer name $dev2
    ip link set $dev1 netns $ns1
    ip link set $dev2 netns $ns2
    ip netns exec $ns1 ip addr add $ip1 dev $dev1
    ip netns exec $ns2 ip addr add $ip2 dev $dev2
    ip netns exec $ns1 ip link set $dev1 up
    ip netns exec $ns2 ip link set $dev2 up
}

# Host connections (10.0.x.0/24)
connect h1 r4 10.0.4.1/24 10.0.4.2/24
connect h2 r6 10.0.6.1/24 10.0.6.2/24

# Vertical spine
connect r4 r2 10.4.2.1/24 10.4.2.2/24
connect r5 r2 10.5.2.1/24 10.5.2.2/24
connect r5 r3 10.5.3.1/24 10.5.3.2/24
connect r6 r3 10.6.3.1/24 10.6.3.2/24
connect r2 r1 10.2.1.1/24 10.2.1.2/24
connect r3 r1 10.3.1.1/24 10.3.1.2/24

# Horizontal mesh
connect r4 r5 10.4.5.1/24 10.4.5.2/24
connect r5 r6 10.5.6.1/24 10.5.6.2/24
connect r2 r3 10.2.3.1/24 10.2.3.2/24

# Wait for interfaces
sleep 2

# Host default routes only
ip netns exec h1 ip route add default via 10.0.4.2
ip netns exec h2 ip route add default via 10.0.6.2

# --- FRR CONFIGURATION ---
declare -A ROUTER_IDS=(
    ["r1"]="10.2.1.2"
    ["r2"]="10.2.1.1"
    ["r3"]="10.3.1.1"
    ["r4"]="10.4.2.1"
    ["r5"]="10.5.2.1"
    ["r6"]="10.6.3.1"
)

# Create FRR directories with proper permissions
for r in r1 r2 r3 r4 r5 r6; do
    # Use /var/run instead of /tmp (better for daemon pid files)
    mkdir -p /var/run/frr-$r
    chmod 777 /var/run/frr-$r

    cat > /var/run/frr-$r/frr.conf << EOF
hostname $r
log stdout debugging
service integrated-vtysh-config

!
zebra:
  router-id ${ROUTER_IDS[$r]}
!
router ospf
  router-id ${ROUTER_IDS[$r]}
  log-adjacency-changes detail
  network 0.0.0.0/0 area 0
  passive-interface default
  no passive-interface v-$r-r1
  no passive-interface v-$r-r2
  no passive-interface v-$r-r3
  no passive-interface v-$r-r4
  no passive-interface v-$r-r5
  no passive-interface v-$r-r6
!
line vty
!
EOF
done

# Set host-facing interfaces to passive
cat >> /var/run/frr-r4/frr.conf << EOF
  passive-interface v-r4-h1
EOF

cat >> /var/run/frr-r6/frr.conf << EOF
  passive-interface v-r6-h2
EOF

for r in r1 r2 r3 r4 r5 r6; do
    sudo mkdir -p /etc/frr/$r
    sudo ln -sf /var/run/frr-$r/frr.conf /etc/frr/$r/frr.conf
done

# Start FRR - WITH FULL PATHS AND ENV
echo "Starting FRR daemons..."
for r in r1 r2 r3 r4 r5 r6; do
    echo "Starting $r..."

    # Use env to set PATH inside the namespace
    ip netns exec $r env PATH="/usr/lib/frr:/usr/sbin:/usr/bin:/sbin:/bin" \
        zebra -N $r -d -f /var/run/frr-$r/frr.conf

    sleep 1

    ip netns exec $r env PATH="/usr/lib/frr:/usr/sbin:/usr/bin:/sbin:/bin" \
        ospfd -N $r -d -f /var/run/frr-$r/frr.conf
done

echo "Waiting for OSPF convergence (30 seconds)..."
sleep 30
