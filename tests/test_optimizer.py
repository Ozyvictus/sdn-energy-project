import math

from algorithm.optimizer import check_constraints, find_low_utilization_link


def build_graph():
    return {
        "s1": {"s2": 1.0, "s3": 1.0, "s4": 1.0},
        "s2": {"s1": 1.0, "s3": 1.0, "s4": 1.0},
        "s3": {"s1": 1.0, "s2": 1.0, "s5": 1.0},
        "s4": {"s1": 1.0, "s2": 1.0, "s6": 1.0},
        "s5": {"s3": 1.0},
        "s6": {"s4": 1.0},
    }


def test_find_low_utilization_link_returns_safe_candidate():
    topology_graph = build_graph()
    utilization_by_link = {
        "s1-s2": 0.2,
        "s1-s3": 3.0,
        "s1-s4": 8.0,
        "s2-s3": 7.5,
        "s2-s4": 9.0,
        "s3-s5": 1.0,
        "s4-s6": 4.0,
    }

    candidate = find_low_utilization_link(topology_graph, utilization_by_link, threshold=5.0)

    assert candidate == "s1-s2"


def test_check_constraints_rejects_link_that_disconnects_graph():
    topology_graph = build_graph()
    candidate = "s3-s5"

    result = check_constraints(topology_graph, candidate, utilization_by_link={candidate: 1.0}, qos_limits={"avg_rtt_ms": 20.0})

    assert result["safe"] is False
    assert result["reason"]


def test_check_constraints_accepts_redundant_low_utilization_link():
    topology_graph = build_graph()
    candidate = "s1-s2"

    result = check_constraints(topology_graph, candidate, utilization_by_link={candidate: 0.1}, qos_limits={"avg_rtt_ms": 30.0})

    assert result["safe"] is True
    assert result["candidate"] == "s1-s2"
    assert math.isclose(result["utilization_percent"], 0.1, rel_tol=0.0, abs_tol=1e-9)
