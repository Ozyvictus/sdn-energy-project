"""Shared network configuration derived from the selected topology."""

from topology.fattree_spec import build_fat_tree_spec

OPENFLOW_PORT = 6653

# Canonical k=4 Fat-Tree is the default topology for this branch.
FAT_TREE_K = 4
HOST_LINK_BW_MBPS = 100
SWITCH_LINK_BW_MBPS = 1000
ENABLE_STP = True
STP_CONVERGENCE_SECONDS = 90

_DEFAULT_TOPOLOGY = build_fat_tree_spec(
    FAT_TREE_K,
    host_link_bw=HOST_LINK_BW_MBPS,
    switch_link_bw=SWITCH_LINK_BW_MBPS,
)

# Includes both fabric links and host-facing links. The component energy
# model filters for switch-to-switch links; the monitor uses this map to
# obtain the correct capacity for discovered inter-switch links.
LINK_CAPACITY_MBPS = dict(_DEFAULT_TOPOLOGY.link_capacities_mbps)

NETWORK_SWITCH_NAMES = _DEFAULT_TOPOLOGY.switches
NETWORK_HOST_NAMES = _DEFAULT_TOPOLOGY.hosts
NETWORK_HOST_IPS = dict(_DEFAULT_TOPOLOGY.host_ips)
INTER_SWITCH_LINKS = _DEFAULT_TOPOLOGY.inter_switch_links
HOST_FACING_LINKS = _DEFAULT_TOPOLOGY.host_links


def get_link_capacity(node1, node2):
    """Return configured link capacity in Mbps, independent of endpoint order."""
    direct = (str(node1).lower(), str(node2).lower())
    reverse = (direct[1], direct[0])
    if direct in LINK_CAPACITY_MBPS:
        return LINK_CAPACITY_MBPS[direct]
    if reverse in LINK_CAPACITY_MBPS:
        return LINK_CAPACITY_MBPS[reverse]
    return None
