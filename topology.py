from mininet.topo import Topo
class NetTopo(Topo):
    def __init__(self):
        Topo.__init__(self)
        h1 = self.addHost('h1') # Ground Station 1
        h2 = self.addHost('h2') # Ground Station 2
        s1 = self.addSwitch('s1') # Lowest Layer 
        s2 = self.addSwitch('s2') # Lowest Layer
        s3 = self.addSwitch('s3') # Lowest Layer 
        s4 = self.addSwitch('s4') # Middle layer
        s5 = self.addSwitch('s5') # Middle Layer 
        s6 = self.addSwitch('s6')  #Highest layeri

        # Ground level hosts connected to respective lowest layer switch 
        self.addLink(h1, s1)  
        self.addLink(h2, s3)
    
        # Interconnection between lowest layer switches
        self.addLink(s1, s2)
        self.addLink(s2, s3)

        # Middle layer interconnection 
        self.addLink(s4, s5)

        # Middle layer mesh with lowest layer (s4) 
        self.addLink(s4, s1)
        self.addLink(s4, s2)
    
        # Middle layer mesh with lowest layer (s5) 
        self.addLink(s5, s2)
        self.addLink(s5, s3) 

        # Highest layer interconnection with middle layer 
        self.addLink(s6, s4)
        self.addLink(s6, s5)

topos = {'NetTopo': (lambda: NetTopo())}

