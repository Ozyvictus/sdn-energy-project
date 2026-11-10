"""
algorithm/traffic_utilisation.py

Traffic utilisation monitoring and graphing.

This module does NOT communicate with the network.

monitor.py provides:
    - logical link name
    - traffic rate
    - link capacity

This module calculates/stores:
    Utilisation (%) = Traffic Rate / Link Capacity * 100

It generates one unified graph containing all monitored
inter-switch links.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = PROJECT_ROOT / "graphs" / "monitoring"


# ================================================================
# HISTORY
# ================================================================

# Example:
#
# {
#     "s1-s3": {
#         "time": [10, 20, 30],
#         "traffic_mbps": [50, 80, 20],
#         "capacity_mbps": [1000, 1000, 1000],
#         "utilisation": [5, 8, 2]
#     }
# }

link_history = {}

elapsed_time_seconds = 0.0


# ================================================================
# UTILISATION CALCULATION
# ================================================================

def calculate_utilisation(traffic_mbps, capacity_mbps):
    """
    Calculate link utilisation percentage.
    """

    if capacity_mbps is None or capacity_mbps <= 0:
        return 0.0

    utilisation = (
        traffic_mbps / capacity_mbps
    ) * 100.0

    return utilisation


# ================================================================
# RECORD LINK DATA
# ================================================================

def record_link_utilisation(
    link_name,
    traffic_mbps,
    capacity_mbps
):
    """
    Record one link's utilisation for the current
    monitoring cycle.
    """

    utilisation = calculate_utilisation(
        traffic_mbps,
        capacity_mbps
    )

    if link_name not in link_history:

        link_history[link_name] = {
            "time": [],
            "traffic_mbps": [],
            "capacity_mbps": [],
            "utilisation": []
        }

    link_history[link_name]["time"].append(
        elapsed_time_seconds
    )

    link_history[link_name]["traffic_mbps"].append(
        traffic_mbps
    )

    link_history[link_name]["capacity_mbps"].append(
        capacity_mbps
    )

    link_history[link_name]["utilisation"].append(
        utilisation
    )


# ================================================================
# ADVANCE MONITORING TIME
# ================================================================

def advance_time(duration_seconds):
    """
    Advance the common monitoring timeline.

    This is called exactly once after a complete
    monitoring cycle has been processed.
    """

    global elapsed_time_seconds

    elapsed_time_seconds += duration_seconds


# ================================================================
# GRAPH DIRECTORY
# ================================================================

def create_graph_directory():
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)


# ================================================================
# UNIFIED UTILISATION GRAPH
# ================================================================

def generate_utilisation_graph():
    """
    Generate one unified graph containing the utilisation
    of every monitored inter-switch link.
    """

    if not link_history:
        return

    create_graph_directory()

    plt.figure(figsize=(13, 7))

    for link_name, data in sorted(
        link_history.items()
    ):

        if not data["time"]:
            continue

        plt.plot(
            data["time"],
            data["utilisation"],
            marker="o",
            label=link_name.upper()
        )

    plt.xlabel("Time (seconds)")
    plt.ylabel("Link Utilisation (%)")

    plt.title(
        "Traffic Utilisation of Inter-Switch Links"
    )

    plt.grid(
        True,
        alpha=0.3
    )

    plt.legend(
        bbox_to_anchor=(1.02, 1),
        loc="upper left"
    )

    plt.tight_layout()

    plt.savefig(GRAPH_DIR / "traffic_utilisation.png", dpi=150)

    plt.close()


# ================================================================
# RESET
# ================================================================

def reset_history():
    """
    Clear all utilisation history.
    """

    global elapsed_time_seconds

    link_history.clear()

    elapsed_time_seconds = 0.0