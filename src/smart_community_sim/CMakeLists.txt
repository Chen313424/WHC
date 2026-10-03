cmake_minimum_required(VERSION 3.8)
project(smart_community_sim)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)

# 红绿灯时序控制 gz-sim 系统插件（编译成 libTrafficLightSystem.so）
add_subdirectory(plugins/traffic_light)

install(DIRECTORY
  launch
  worlds
  robot
  config
  textures
  scripts
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
