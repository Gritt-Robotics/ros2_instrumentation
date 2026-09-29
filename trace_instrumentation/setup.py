# Copyright 2026 Gritt Robotics Inc.

from setuptools import find_packages, setup

package_name = "trace_instrumentation"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="aramachandra",
    maintainer_email="aramachandra@gritt.ai",
    description=(
        "Minimal LTTng tracing launch action and trace analysis tooling for "
        "wrapping ROS 2 launch files"
    ),
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "analyze_trace = trace_instrumentation.trace_analysis.cli:main",
        ],
    },
)
