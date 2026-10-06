cmake_minimum_required(VERSION 3.8)
project(community_nav)

find_package(ament_cmake REQUIRED)

# 本包只包含数据文件（配置、启动脚本、地图），没有可执行代码，
# 因此不需要编译，只需要把文件安装到 share/ 目录下。

install(DIRECTORY config launch
  DESTINATION share/${PROJECT_NAME}
)

# 开发工具脚本：用 install(PROGRAMS ...) 装到 lib/<包名>/，
# 这样既带上可执行权限，又能用标准的 ros2 run 调用：
#     ros2 run community_nav save_map.py -f <路径前缀>
#     ros2 run community_nav make_keepout_mask.py ...
install(PROGRAMS
  scripts/save_map.py
  scripts/make_keepout_mask.py
  scripts/run_patrol_demo.sh
  DESTINATION lib/${PROJECT_NAME}
)

# map 目录在建图完成前不存在，用 OPTIONAL 避免 CMake 报错
install(DIRECTORY map
  DESTINATION share/${PROJECT_NAME}
  OPTIONAL
)

ament_package()
