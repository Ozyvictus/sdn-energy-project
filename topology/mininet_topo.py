r"""
topology/mininet_topo.py

Milestone-1 Mininet topology: a small two-tier data-center-style network.

Layout:
                s1 --- s2        <- "core" tier (2 switches)
               /  \   /  \
             s3    s4    s5 s6   <- "edge" tier (4 switches, each with 2 hosts)
            /  \   /  \  ...
          h1  h2 h3  h4 ...      <- 8 hosts total (2 per edge switch)

This gives 6 switches / 8 hosts: enough structural redundancy (multiple
paths core->edge) to later test link/switch sleeping and failure recovery,
while still being small enough to reason about by hand.

Usage (from the project root, so `config` is importable):
    sudo python3 -m topology.mininet_topo

Requires: Mininet + Open vSwitch installed on the host.
Run this in one terminal; run `python3 launch.py` (the os-ken controller)
in a separate terminal BEFORE running this script, so switches have a
controller to connect to as soon as they start.
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

    # NOTE: we no longer pass stp=True here. On this Mininet/OVS version
    # combination, the stp=True constructor kwarg silently failed to
    # actually enable STP at the OVS level (confirmed via
    # `ovs-appctl stp/show <switch>` returning "no such stp object").
    # Instead, we explicitly run `ovs-vsctl set bridge <name> stp_enable=true`
    # for every switch after net.build() below -- this is guaranteed to
    # take effect since it's the same command used to verify/fix it
    # manually. STP is essential here because this topology has
    # intentional redundant paths (each edge switch dual-homed to both
    # core switches, plus a core-to-core link) for later resilience
    # testing, and l2_forwarding.py has no loop prevention of its own.
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    # Edge tier
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    s4 = net.addSwitch('s4', protocols='OpenFlow13')
    s5 = net.addSwitch('s5', protocols='OpenFlow13')
    s6 = net.addSwitch('s6', protocols='OpenFlow13')

    # Core <-> core link (redundancy path)
    net.addLink(s1, s2, bw=1000)

    # Core <-> edge links (each edge switch dual-homed to both core
    # switches -- this redundancy is what later lets you test link
    # sleeping without losing connectivity)
    for edge in (s3, s4, s5, s6):
        net.addLink(s1, edge, bw=1000)
        net.addLink(s2, edge, bw=1000)

    # Hosts: 2 per edge switch, 8 total
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

    # Explicitly enable STP at the OVS level for every switch. Doing this
    # via ovs-vsctl directly (rather than the stp=True kwarg above) is
    # what actually worked when we tested it manually.
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
    print("(This is a fresh build with STP enabled from the start -- if you")
    print(" previously ran without STP or live-patched it onto a running")
    print(" session, restart both the controller and this script cleanly")
    print(" so no stale learned MAC entries or storm traffic linger.)")
    time.sleep(90)
    print("STP should be converged now. Try: pingall\n")
    CLI(net)
    net.stop()