import os
from glob import glob

from setuptools import find_packages, setup

package_name = "smart_community_perception"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name, "launch"), glob("launch/*.py")),
        (os.path.join("share", package_name, "models"), glob("models/*")),
        (os.path.join("share", package_name, "docs"), glob("docs/*.md")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="chen",
    maintainer_email="chen@users.noreply.github.com",
    description="YOLO 感知与红绿灯规则控制（智慧社区仿真）",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "autolabel_capture = smart_community_perception.autolabel_capture_node:main",
            "yolo_detector = smart_community_perception.yolo_detector_node:main",
            "traffic_controller = smart_community_perception.traffic_controller_node:main",
        ],
    },
)
