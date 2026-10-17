from mininet.link import TCLink
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel

class FileSharingTopo(Topo):

   def build(self):
             #create switches
             s1=self.addSwitch('s1')
             s2=self.addSwitch('s2')

             #create three clients
             h1=self.addHost('h1', ip='10.0.0.1/24')
             h2=self.addHost('h2', ip='10.0.0.2/24')
             h3=self.addHost('h3', ip='10.0.0.3/24')

            #create the file server
             h4=self.addHost('h4', ip='10.0.0.4/24')

            #connect clients to switch s1
             self.addLink(h1,s1)
             self.addLink(h2,s1)
             self.addLink(h3,s1)

           #connect the two switches
             self.addLink(s1,s2,bw=10,delay="5ms")

           #connect the server to switch s2
             self.addLink(s2,h4)


def run():
        topo = FileSharingTopo()

        net=Mininet(
             topo=topo,
             controller=None,
             switch=OVSSwitch,
             link=TCLink,
             autoSetMacs=True,
             build=False
           )

        net.addController(
             'c0',
             controller=RemoteController,
             ip='127.0.0.1',
             port=6653
            )
        net.build()
        for switch in net.switches:
                 switch.cmd('ovs-vsctl set bridge %s protocols=OpenFlow13' % switch.name)
        net.start()

        print("\nCongestion experiment network started")
        print("h1, h2, h3 are clients.")
        print("h4(10.0.0.4) is the file server")
        print("Bottleneck: s1 <-> s2 at 10Mbps")
        print("Type pingall to test connectivity\n")

        try:
           CLI(net)
        finally:
           net.stop()

if __name__=='__main__':
         setLogLevel('info')
         run()
