"""
config.py

Single place for constants shared across the controller, topology script,
and (later) the optimization algorithm. Change values here rather than
hunting through multiple files.
"""

# OpenFlow port the os-ken controller listens on. 6653 is the OpenFlow 1.3
# standard port. If your controller log shows it bound to a different port
# on startup, change this to match -- topology/mininet_topo.py imports this
# constant so both sides always agree.
OPENFLOW_PORT = 6653

# --- Placeholders for later milestones (not used yet) ---
# Once we add link/switch sleeping, thresholds like these will live here
# so the algorithm module and any monitoring code read the same values.
LOW_UTILIZATION_THRESHOLD = 0.10   # e.g. consider a link "idle" below 10%
QOS_MAX_LATENCY_MS = 50            # placeholder QoS constraint
