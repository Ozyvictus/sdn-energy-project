"""
algorithm/optimizer.py

STUB -- not yet wired into the controller. This is where the hierarchical
link-first, then switch-second deactivation logic will live, once
controller/monitor.py's utilization data (Milestone 2) is available to
feed it. Not part of tonight's partial results.
"""


def find_low_utilization_link(topology_graph, utilization_by_link, threshold):
    """
    Return the lowest-utilization link below `threshold`, or None.
    Depends on utilization data that controller/monitor.py now collects
    but does not yet expose in a structured form -- that wiring is the
    next step after tonight.
    """
    raise NotImplementedError("Utilization data structuring lands next.")


def check_constraints(topology_graph, candidate_link):
    """
    Check whether removing `candidate_link` keeps the network within
    connectivity / QoS / security / resilience constraints.
    """
    raise NotImplementedError("Constraint checks land after monitoring.")
