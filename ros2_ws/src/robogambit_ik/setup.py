from setuptools import setup

package_name = "robogambit_ik"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages",
            ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="RoboGambit Team",
    maintainer_email="team@robogambit.local",
    description="IK translator: chess moves -> servo angles",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "ik_node = robogambit_ik.ik_node:main",
            "calibrate_arm = robogambit_ik.calibrate_arm:main",
            'serial = robogambit_ik.serial_node:main',
        ],
    },
)
