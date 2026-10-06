from setuptools import find_packages, setup

package_name = 'community_patrol'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        # ament 索引标记文件（ament_python 包必须有）
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'PyYAML'],
    zip_safe=True,
    maintainer='智慧社区导航组',
    maintainer_email='nav-team@example.com',
    description='智慧社区多点巡检调度节点',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # 左边是 `ros2 run community_patrol <名字>`，右边是代码入口
            'patrol_node = community_patrol.patrol_node:main',
        ],
    },
)
