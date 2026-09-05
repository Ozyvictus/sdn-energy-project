"""
algorithm/optimizer.py

STUB -- not yet wired into the controller. This is where the hierarchical
link-first, then switch-second deactivation logic from the project
handoff will live:

    Level 1: find low-utilization link -> simulate removal -> recompute
             routes -> check constraints (connectivity, QoS, security,
             resilience) -> sleep if safe, else keep active.
    Level 2: same, at switch granularity, run after Level 1 converges.

Right now this just defines the function signatures so the controller
(controller/l2_forwarding.py, or its successor once we add real routing)
has a clear place to call into, without needing a redesign later.
"""


def find_low_utilization_link(topology_graph, utilization_by_link, threshold):
    """
    Return the lowest-utilization link below `threshold`, or None if no
    candidate exists.

    Parameters
    ----------
    topology_graph : networkx.Graph
        Not yet produced anywhere in the codebase -- topology discovery
        (reading link state from os-ken events) is a later milestone.
    utilization_by_link : dict
        Not yet populated -- port-stats polling is a later milestone.
    threshold : float
        e.g. config.LOW_UTILIZATION_THRESHOLD

    Returns
    -------
    tuple or None
        (node_a, node_b) of the candidate link, or None.
    """
    raise NotImplementedError("Topology discovery and monitoring land first.")


def check_constraints(topology_graph, candidate_link):
    """
    Check whether removing `candidate_link` keeps the network within
    connectivity / QoS / security / resilience constraints.

    Returns
    -------
    bool
        True if safe to sleep, False otherwise.
    """
    raise NotImplementedError("Constraint checks land after monitoring.")
