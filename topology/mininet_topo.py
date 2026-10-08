r"""
topology/mininet_topo.py

Milestone-1 Mininet topology: a small two-tier data-center-style network.
6 switches (2 core, 4 edge, each edge dual-homed to both core switches),
8 hosts (2 per edge switch). Redundant paths are intentional -- needed
for later resilience/failure-recovery testing, and now also to give
monitor.py's link discovery something non-trivial to report.

Usage (from the project root, so `config` is importable):
    sudo python3 -m topology.mininet_topo
"""
import time
import subprocess

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.link import TCLink

from config import OPENFLOW_PORT


def build_topology():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch,
                   link=TCLink, autoSetMacs=True)

    c0 = net.addController('c0', controller=RemoteController,
                            ip='127.0.0.1', port=OPENFLOW_PORT)

    # NOTE: stp=True as a constructor kwarg was unreliable on this
    # Mininet/OVS version -- we enable STP explicitly via ovs-vsctl
    # below instead, right after net.build().
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    s4 = net.addSwitch('s4', protocols='OpenFlow13')
    s5 = net.addSwitch('s5', protocols='OpenFlow13')
    s6 = net.addSwitch('s6', protocols='OpenFlow13')

    net.addLink(s1, s2, bw=1000)
    for edge in (s3, s4, s5, s6):
        net.addLink(s1, edge, bw=1000)
        net.addLink(s2, edge, bw=1000)

    hosts = []
    for i, edge in enumerate((s3, s4, s5, s6), start=1):
        h_a = net.addHost('h%d' % (2 * i - 1))
        h_b = net.addHost('h%d' % (2 * i))
        net.addLink(h_a, edge, bw=100)
        net.addLink(h_b, edge, bw=100)
        hosts.extend([h_a, h_b])

    net.build()
    c0.start()
    for sw in (s1, s2, s3, s4, s5, s6):
        sw.start([c0])

    for sw in (s1, s2, s3, s4, s5, s6):
        subprocess.run(
            ['ovs-vsctl', 'set', 'bridge', sw.name, 'stp_enable=true'],
            check=True
        )
        print("STP enabled on %s" % sw.name)

    return net


if __name__ == '__main__':
    setLogLevel('info')
    net = build_topology()
    print("\nTopology up: 6 switches (s1-s2 core, s3-s6 edge), 8 hosts (h1-h8).")
    print("Waiting 90s for STP to converge and block redundant loop ports...")
    time.sleep(90)
    print("STP should be converged now. Try: pingall\n")
    CLI(net)
    net.stop()
