# ============================================
# CUSTOM MININET TOPOLOGY FOR SDN HOST DISCOVERY
# ============================================
# This script creates a virtual network using Mininet.
# It connects to a remote Ryu controller.

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink
import time


def create_topology():
    """Create and start the Mininet topology"""

    # Initialize Mininet
    net = Mininet(
        controller=RemoteController,   # Use external controller (Ryu)
        switch=OVSKernelSwitch,        # Use Open vSwitch kernel switches
        link=TCLink,                   # Use traffic-controlled links
        autoSetMacs=True,              # Automatically assign MAC addresses
        autoStaticArp=False            # Disable static ARP (needed for discovery)
    )

    info("*** Adding Remote Controller (Ryu on 127.0.0.1:6653)\n")

    # Add controller
    net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',   # Controller IP (localhost)
        port=6653         # Default OpenFlow port
    )

    info("*** Adding Switches\n")

    # Add switches
    s1 = net.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')
    s2 = net.addSwitch('s2', cls=OVSKernelSwitch, protocols='OpenFlow13')
    s3 = net.addSwitch('s3', cls=OVSKernelSwitch, protocols='OpenFlow13')

    info("*** Adding Hosts\n")

    # Add hosts with IP addresses
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')
    h5 = net.addHost('h5', ip='10.0.0.5/24')
    h6 = net.addHost('h6', ip='10.0.0.6/24')

    info("*** Adding Links\n")

    # Connect switches (core topology)
    net.addLink(s1, s2)   # s1 <-> s2
    net.addLink(s1, s3)   # s1 <-> s3

    # Connect hosts to switches
    net.addLink(s2, h1)
    net.addLink(s2, h2)
    net.addLink(s2, h3)

    net.addLink(s3, h4)
    net.addLink(s3, h5)
    net.addLink(s3, h6)

    info("*** Starting Network\n")

    # Start the network
    net.start()

    info("*** Setting OpenFlow 1.3 on all switches\n")

    # Ensure all switches use OpenFlow 1.3
    for sw in [s1, s2, s3]:
        sw.cmd(f'ovs-vsctl set bridge {sw.name} protocols=OpenFlow13')

    info("*** Waiting 3 seconds for controller handshake...\n")
    time.sleep(3)

    info("\n*** Topology ready!\n")

    # Show topology structure
    info("    s1 (core) -- s2 [h1, h2, h3]\n")
    info("                 -- s3 [h4, h5, h6]\n")
    info("    Run 'pingall' to trigger host discovery\n\n")

    # Start Mininet CLI
    CLI(net)

    # Stop network after exiting CLI
    net.stop()


# Entry point
if __name__ == '__main__':
    setLogLevel('info')  # Set logging level
    create_topology()    # Run topology
