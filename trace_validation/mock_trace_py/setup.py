# Copyright 2026 Gritt Robotics Inc.

import os
from glob import glob

from setuptools import find_packages, setup

package_name = "mock_trace_py"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="aramachandra",
    maintainer_email="aramachandra@gritt.ai",
    description="Python publisher and subscriber nodes for trace-analyzer validation",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "mock_publisher_node = mock_trace_py.publisher_node:main",
            "mock_subscriber_node = mock_trace_py.subscriber_node:main",
            "mock_relay_node = mock_trace_py.relay_node:main",
            "mock_indirect_relay_node = mock_trace_py.indirect_relay_node:main",
            "analyze_chain_latency = mock_trace_py.analyze_chain_latency:main",
        ],
    },
)
