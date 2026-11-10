"""Legacy active-port energy calculations retained for compatibility.

This estimate is not a physical power measurement. Current graph output is
handled by the session measurement and live monitoring graph producers.
"""


# -------------------------------------------------------------------
# Model Parameters
# -------------------------------------------------------------------

PORT_POWER_W = 0.2

DEFAULT_INTERVAL_SECONDS = 10

# -------------------------------------------------------------------
# History
# -------------------------------------------------------------------

power_history = []
energy_history = []
active_port_history = []
time_history = []

cumulative_energy_wh = 0.0
elapsed_time_seconds = 0.0


# -------------------------------------------------------------------
# Power Calculation
# -------------------------------------------------------------------

def total_power(active_port_count):
    """
    Compute modeled network power.

    Parameters
    ----------
    active_port_count : int
        Number of currently active switch ports.

    Returns
    -------
    float
        Modeled power in watts.
    """

    return active_port_count * PORT_POWER_W


# -------------------------------------------------------------------
# Energy Calculation
# -------------------------------------------------------------------

def energy_from_power(power_w, duration_seconds):
    """
    Calculate energy consumed during a time interval.

    Returns
    -------
    float
        Energy in watt-hours (Wh).
    """

    return power_w * duration_seconds / 3600.0


def energy_from_active_ports(active_port_count, duration_seconds):
    """
    Calculate modeled energy directly from active port count.
    """

    power_w = total_power(active_port_count)

    return energy_from_power(
        power_w,
        duration_seconds
    )


# -------------------------------------------------------------------
# History Update
# -------------------------------------------------------------------

def update_energy(
    total_energy_wh,
    active_port_count,
    duration_seconds
):
    """
    Add the current interval's energy to cumulative energy.

    This function also records the current state for graphing.
    """

    global cumulative_energy_wh
    global elapsed_time_seconds

    power_w = total_power(active_port_count)

    interval_energy_wh = energy_from_power(
        power_w,
        duration_seconds
    )

    # Update cumulative values
    cumulative_energy_wh = (
        total_energy_wh + interval_energy_wh
    )

    elapsed_time_seconds += duration_seconds

    # Store history
    time_history.append(elapsed_time_seconds)
    active_port_history.append(active_port_count)
    power_history.append(power_w)
    energy_history.append(cumulative_energy_wh)

    return cumulative_energy_wh


# -------------------------------------------------------------------
# Simple Update Function
# -------------------------------------------------------------------

def record_state(active_port_count, duration_seconds):
    """
    Record one monitoring interval.

    This is the recommended function for the monitoring module.

    Returns
    -------
    dict
        Current energy state.
    """

    global cumulative_energy_wh
    global elapsed_time_seconds

    power_w = total_power(active_port_count)

    interval_energy_wh = energy_from_power(
        power_w,
        duration_seconds
    )

    cumulative_energy_wh += interval_energy_wh
    elapsed_time_seconds += duration_seconds

    # Store data
    time_history.append(elapsed_time_seconds)
    active_port_history.append(active_port_count)
    power_history.append(power_w)
    energy_history.append(cumulative_energy_wh)

    return {
        "active_ports": active_port_count,
        "power_w": power_w,
        "interval_energy_wh": interval_energy_wh,
        "cumulative_energy_wh": cumulative_energy_wh,
        "elapsed_time_seconds": elapsed_time_seconds
    }


# -------------------------------------------------------------------
# Reset
# -------------------------------------------------------------------

def reset_history():
    """
    Clear all recorded energy history.

    Useful when starting a new experiment.
    """

    global cumulative_energy_wh
    global elapsed_time_seconds

    active_port_history.clear()
    power_history.clear()
    energy_history.clear()
    time_history.clear()

    cumulative_energy_wh = 0.0
    elapsed_time_seconds = 0.0


# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------

def model_summary(
    active_port_count,
    duration_seconds=DEFAULT_INTERVAL_SECONDS
):
    """
    Return a summary of the current modeled energy state.
    """

    power_w = total_power(active_port_count)

    energy_wh = energy_from_power(
        power_w,
        duration_seconds
    )

    return {
        "active_ports": active_port_count,
        "power_w": power_w,
        "interval_seconds": duration_seconds,
        "energy_wh": energy_wh
    }