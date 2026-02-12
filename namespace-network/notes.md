# IP address Scheme
H1 - 10.0.4.2 
H2 - 10.0.6.2

For routers, refer to hop by hop pings for addresses

# Topology view
    
        R1

    R2      R3

R4      R5      R6

H1              H2


# Alternative way to access: frr-rx || frr-hx

# Ping hop by hop from h1 to h2
ip netns exec h1 ping -c 2 10.0.4.2     # h1 → r4
ip netns exec r4 ping -c 2 10.4.2.2     # r4 → r2
ip netns exec r2 ping -c 2 10.2.1.2     # r2 → r1
ip netns exec r1 ping -c 2 10.3.1.1     # r1 → r3
ip netns exec r3 ping -c 2 10.6.3.1     # r3 → r6
ip netns exec r6 ping -c 2 10.0.6.1     # r6 → h2

# Or trace the whole path
ip netns exec h1 traceroute -n 10.0.6.1


# Linux kernel routes
ip netns exec r1 ip route
ip netns exec r1 ip route show 10.0.6.0/24  # Specific route

# FRR OSPF database
ip netns exec r1 vtysh -N r1 -c "show ip ospf neighbor"     # Adjacencies
ip netns exec r1 vtysh -N r1 -c "show ip ospf database"     # Link state DB
ip netns exec r1 vtysh -N r1 -c "show ip ospf route"        # OSPF routes
ip netns exec r1 vtysh -N r1 -c "show ip route"            # FRR RIB

# FRR vtysh aliases for each router --> will be in ~/.bashrc so they are here just for reference
alias frr-r1='sudo ip netns exec r1 /usr/lib/frr/vtysh -N r1'
alias frr-r2='sudo ip netns exec r2 /usr/lib/frr/vtysh -N r2'
alias frr-r3='sudo ip netns exec r3 /usr/lib/frr/vtysh -N r3'
alias frr-r4='sudo ip netns exec r4 /usr/lib/frr/vtysh -N r4'
alias frr-r5='sudo ip netns exec r5 /usr/lib/frr/vtysh -N r5'
alias frr-r6='sudo ip netns exec r6 /usr/lib/frr/vtysh -N r6'


# Changing costs with new aliases
frr-rX -c "configure terminal" -c "interface v-rX-rY" -c "ip ospf cost Z" -c "end" -c "write memory"

where X - target router to change 
      Y - router connection
      Z - OSPF cost
