# Copyright 2026 Gritt Robotics Inc.

from setuptools import find_packages, setup

package_name = "trace_report"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test"]),
    # The static frontend is read from the installed package via importlib.resources,
    # so it ships as package data rather than into share/.
    package_data={package_name: ["frontend/*"]},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="aramachandra",
    maintainer_email="aramachandra@gritt.ai",
    description=("Turns one trace's analysis JSON into a self-contained HTML report"),
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "generate_trace_report = trace_report.generate_report:main",
        ],
    },
)
