"""
launch.py

Entry point for the OS-Ken controller.
"""

import os

# Must be set before importing OS-Ken.
os.environ.setdefault("OSKEN_HUB_TYPE", "eventlet")

import logging

from os_ken import cfg
from os_ken.topology import switches

# IMPORTANT:
# oslo.config set_override() uses the destination name:
# observe_links, not observe-links.
CONF = cfg.CONF
CONF.set_override("observe_links", True)

from os_ken.base.app_manager import AppManager


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    print("Starting os-ken controller... (this will block -- that's normal)")
    print("Waiting for OpenFlow switches to connect...")
    print("LLDP link discovery: ENABLED")

    AppManager.run_apps([
        "controller.l2_forwarding",
        "controller.monitor",
        "os_ken.topology.switches",
    ])