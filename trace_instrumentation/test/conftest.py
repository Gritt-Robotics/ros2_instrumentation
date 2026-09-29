# Copyright 2026 Gritt Robotics Inc.

import atexit
import os
import shutil
import tempfile

# Isolate these tests on their own DDS domain.
os.environ["ROS_DOMAIN_ID"] = "63"

# Concurrent test processes sharing $ROS_HOME/log race on the `latest` symlink that
# launch.logging renews, and the loser warns. Set before any test module imports.
_ROS_LOG_DIR = tempfile.mkdtemp(prefix="trace_instrumentation_log_")
os.environ["ROS_LOG_DIR"] = _ROS_LOG_DIR
atexit.register(shutil.rmtree, _ROS_LOG_DIR, ignore_errors=True)
