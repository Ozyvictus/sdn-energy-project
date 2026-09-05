"""
launch.py

Entry point for the controller side. Run this from the project root
directory (so the `controller` and `algorithm` packages are importable).

os-ken 4.2.2 (pip package) ships no CLI -- no `osken-manager` entry point
exists in the installed package. This script uses AppManager.run_apps()
directly instead, which is the real mechanism underneath the missing CLI.

This also sets OSKEN_HUB_TYPE=eventlet BEFORE importing os_ken. Without
this, os-ken defaults to a native-threading hub whose shutdown path calls
t.kill() on each service thread -- but its native-mode HubThread class
never implements .kill(), so ANY interruption (including a plain Ctrl+C)
crashes with AttributeError: 'HubThread' object has no attribute 'kill'.
This is a real bug in os-ken's native hub fallback, not anything in our
code; forcing eventlet mode uses actual greenthreads that do implement
kill() and avoids the crash entirely.

This also configures logging explicitly: AppManager.run_apps() does NOT
set up log handlers the way the (missing) osken-manager CLI would --
without this, self.logger.info(...) calls inside controller apps print
nothing, even though the controller is running correctly.

Usage:
    python3 launch.py
"""
import os
os.environ.setdefault('OSKEN_HUB_TYPE', 'eventlet')

import logging
from os_ken.base.app_manager import AppManager

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )
    print("Starting os-ken controller... (this will block -- that's normal)")
    print("Waiting for OpenFlow switches to connect...")
    AppManager.run_apps(['controller.l2_forwarding'])