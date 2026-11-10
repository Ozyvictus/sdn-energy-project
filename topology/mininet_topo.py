"""Mininet launcher for the canonical k-ary Fat-Tree topology.

Run from the project root after starting launch.py:
    sudo -E python3 -m topology.mininet_topo

The topology inventory itself lives in topology/fattree_spec.py so that its
structure can be unit-tested without installing Mininet.
"""

from __future__ import annotations

import subprocess
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

from config import (
    ENABLE_STP,
    FAT_TREE_K,
    HOST_LINK_BW_MBPS,
    OPENFLOW_PORT,
    STP_CONVERGENCE_SECONDS,
    SWITCH_LINK_BW_MBPS,
)
from topology.fattree_spec import build_fat_tree_spec


def build_topology(k: int = FAT_TREE_K, *, enable_stp: bool = ENABLE_STP):
    """Build and start a Mininet Fat-Tree controlled by the local OS-Ken app.

    Switch IDs are deterministic: core first, then aggregation, then edge.
    Hosts receive deterministic 10.0.0.x/8 addresses. STP remains enabled by
    default to preserve the current project's loop-protection behavior while
    the forwarding strategy is still the existing L2 learning switch.
    """
    spec = build_fat_tree_spec(
        k,
        host_link_bw=HOST_LINK_BW_MBPS,
        switch_link_bw=SWITCH_LINK_BW_MBPS,
    )

    net = Mininet(
        controller=RemoteController,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
    )
    controller = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=OPENFLOW_PORT,
    )

    switch_objects = {
        name: net.addSwitch(name, protocols="OpenFlow13")
        for name in spec.switches
    }
    host_objects = {
        name: net.addHost(name, ip=spec.host_ips[name])
        for name in spec.hosts
    }

    for switch_a, switch_b in spec.inter_switch_links:
        net.addLink(
            switch_objects[switch_a],
            switch_objects[switch_b],
            bw=SWITCH_LINK_BW_MBPS,
        )

    for edge_switch, host in spec.host_links:
        net.addLink(
            host_objects[host],
            switch_objects[edge_switch],
            bw=HOST_LINK_BW_MBPS,
        )

    net.build()
    controller.start()

    for switch in net.switches:
        switch.start([controller])

    if enable_stp:
        for switch in net.switches:
            subprocess.run(
                [
                    "ovs-vsctl",
                    "set",
                    "bridge",
                    switch.name,
                    "stp_enable=true",
                ],
                check=True,
            )
        print(f"STP enabled on all {len(net.switches)} switches.")
    else:
        print(
            "WARNING: STP is disabled. Do not use this mode with the current "
            "flooding-based L2 controller until loop-free forwarding is installed."
        )

    counts = spec.counts
    print(
        "Fat-Tree k={k}: {switches} switches "
        "(core={core_switches}, aggregation={aggregation_switches}, "
        "edge={edge_switches}), {hosts} hosts, "
        "{inter_switch_links} inter-switch links, "
        "{host_links} host links.".format(k=k, **counts)
    )
    print(
        f"Link capacities: host-facing={HOST_LINK_BW_MBPS} Mbps, "
        f"inter-switch={SWITCH_LINK_BW_MBPS} Mbps."
    )
    return net


if __name__ == "__main__":
    setLogLevel("info")
    network = None
    try:
        network = build_topology()
        if ENABLE_STP:
            print(
                f"Waiting {STP_CONVERGENCE_SECONDS}s for STP convergence "
                "before opening the Mininet CLI..."
            )
            time.sleep(STP_CONVERGENCE_SECONDS)
        print("Topology started. Try: pingall\n")
        CLI(network)
    finally:
        if network is not None:
            network.stop()
