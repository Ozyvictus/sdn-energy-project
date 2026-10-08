"""
config.py

Single place for constants shared across the controller, topology script,
and (later) the optimization algorithm. Change values here rather than
hunting through multiple files.
"""

OPENFLOW_PORT = 6653

# Link capacities
LINK_CAPACITY_MBPS = {

    # Core-to-core link
    ("s1", "s2"): 1000,

    # Core-to-edge links
    ("s1", "s3"): 1000,
    ("s2", "s3"): 1000,

    ("s1", "s4"): 1000,
    ("s2", "s4"): 1000,

    ("s1", "s5"): 1000,
    ("s2", "s5"): 1000,

    ("s1", "s6"): 1000,
    ("s2", "s6"): 1000,

    # Edge-to-host links
    ("s3", "h1"): 100,
    ("s3", "h2"): 100,

    ("s4", "h3"): 100,
    ("s4", "h4"): 100,

    ("s5", "h5"): 100,
    ("s5", "h6"): 100,

    ("s6", "h7"): 100,
    ("s6", "h8"): 100,
}

def get_link_capacity(node1, node2):
    """
    Return link capacity in Mbps regardless of link direction.
    """

    if (node1, node2) in LINK_CAPACITY_MBPS:
        return LINK_CAPACITY_MBPS[(node1, node2)]

    if (node2, node1) in LINK_CAPACITY_MBPS:
        return LINK_CAPACITY_MBPS[(node2, node1)]

    return None
