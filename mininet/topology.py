from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import time
import subprocess


class NetTopo(Topo):
    def __init__(self):
        Topo.__init__(self)
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='9a:75:1e:52:59:34') # Assigning these values to h1 and h2 as they were what was assigned when scripts did initial check so everything can be consistent
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='d6:13:51:b3:e3:33')

        s1 = self.addSwitch('s1', protocols='OpenFlow13')
        s2 = self.addSwitch('s2', protocols='OpenFlow13')
        s3 = self.addSwitch('s3', protocols='OpenFlow13')
        s4 = self.addSwitch('s4', protocols='OpenFlow13')
        s5 = self.addSwitch('s5', protocols='OpenFlow13')
        s6 = self.addSwitch('s6', protocols='OpenFlow13')

        # Your topology
        self.addLink(h1, s1)
        self.addLink(h2, s3)
        self.addLink(s1, s2)
        self.addLink(s2, s3)
        self.addLink(s4, s5)
        self.addLink(s4, s1)
        self.addLink(s4, s2)
        self.addLink(s5, s2)
        self.addLink(s5, s3)
        self.addLink(s6, s4)
        self.addLink(s6, s5)

def setup_network_with_lldp():
    """Setup network with LLDP enabled"""

    # Clean up
    subprocess.run(['sudo', 'mn', '-c'])
    time.sleep(2)

    # Create network
    net = Mininet(topo=NetTopo(), controller=lambda name: RemoteController(name, ip='172.17.0.3', port=6653), switch=OVSSwitch, autoSetMacs=False)

    info('*** Starting network\n')
    net.start()
    time.sleep(5)

    # Configure OVS for LLDP and topology discovery
    info('*** Configuring OVS for LLDP discovery...\n')

    for i in range(1, 7):
        switch = net.get(f's{i}')

        # Set OpenFlow 1.3
        switch.cmd('ovs-vsctl set bridge s%d protocols=OpenFlow13' % i)

        # Enable LLDP on the bridge
        switch.cmd('ovs-vsctl set bridge s%d other_config:enable-lldp=true' % i)
        

        # Set controller
        switch.cmd('ovs-vsctl set-controller s%d tcp:172.17.0.3:6653' % i)

        # Set fail mode to secure (forces controller consultation)
        switch.cmd('ovs-vsctl set-fail-mode s%d secure' % i)

        # Enable MAC learning (helps with host discovery)
        switch.cmd('ovs-vsctl set bridge s%d flood_vlans=0' % i)

        # Show OVS status
        info(f'  s{i}: ')
        switch.cmd('ovs-vsctl show | grep -A2 "Bridge s%d"' % i)

    # Wait for LLDP to work
    info('\n*** Waiting 15 seconds for LLDP discovery...\n')
    time.sleep(15)

    # Generate traffic to help discovery
    h1, h2 = net.get('h1', 'h2')

    info('*** Generating traffic...\n')
    h1.cmd('ping -c 10 -i 0.5 10.0.0.2 > /dev/null 2>&1 &')
    time.sleep(8)

    # Check connectivity
    result = h1.cmd('ping -c 3 -W 1 10.0.0.2')
    if '64 bytes' in result:
        info('✓ Hosts communicating\n')
    else:
        info('⚠ Connectivity issues\n')

    # Final wait
    info('\n*** Waiting 10 more seconds for ODL topology updates...\n')
    time.sleep(10)

    # Show ODL topology via REST
    info('\n*** Checking ODL topology via REST...\n')
    check_cmd = '''curl -s -u admin:admin http://172.17.0.3:8181/restconf/operational/network-topology:network-topology/ | \
        python3 -c "import sys,json; data=json.load(sys.stdin);
        print('Topology ID:', data['topology'][0]['topology-id']);
        print('Nodes:', len(data['topology'][0].get('node', [])));
        print('Links:', len(data['topology'][0].get('link', [])));" 2>/dev/null || echo "Could not query topology"'''

    subprocess.run(['bash', '-c', check_cmd])


    return net

if __name__ == '__main__':
    setLogLevel('info')
    net = setup_network_with_lldp()
    CLI(net)
    net.stop()

