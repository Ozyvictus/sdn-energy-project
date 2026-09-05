"""
algorithm/energy_model.py

STUB -- not yet wired into the controller. This module will hold the
software power model described in the project handoff:

    P_total = sum(active switch power) + sum(active link power)

For now it just defines the shape of that calculation with placeholder
per-unit power figures, so later milestones (link sleeping, switch
sleeping) have a real function to call instead of inventing one from
scratch. Nothing here is calibrated yet -- do not cite these numbers.
"""

# Placeholder power draw per active unit, in watts. Replace with figures
# from a real reference (e.g. published switch datasheets or the
# ~0.2 W/port at 10 Mbps figure cited in the Q-PoPS paper) before any
# real experiment.
SWITCH_ACTIVE_POWER_W = 150.0
LINK_ACTIVE_POWER_W = 5.0


def total_power(active_switch_count, active_link_count):
    """
    Compute total modeled power draw for the network's current state.

    Parameters
    ----------
    active_switch_count : int
        Number of switches currently powered on / not sleeping.
    active_link_count : int
        Number of links currently active / not sleeping.

    Returns
    -------
    float
        Modeled power in watts. This is a SOFTWARE MODEL, not a measured
        quantity -- do not present it as real hardware wattage.
    """
    return (active_switch_count * SWITCH_ACTIVE_POWER_W
            + active_link_count * LINK_ACTIVE_POWER_W)
