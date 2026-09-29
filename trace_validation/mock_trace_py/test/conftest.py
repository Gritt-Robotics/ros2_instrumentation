# Copyright 2026 Gritt Robotics Inc.

import os

# Isolate these tests on their own DDS domain.
os.environ["ROS_DOMAIN_ID"] = "62"
