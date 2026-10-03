find_package(gz-sim8 REQUIRED)

add_library(TrafficLightSystem SHARED TrafficLightSystem.cc)
target_link_libraries(TrafficLightSystem PRIVATE gz-sim8::gz-sim8)
target_compile_features(TrafficLightSystem PRIVATE cxx_std_17)

# 安装到 <prefix>/lib，launch 里把 GZ_SIM_SYSTEM_PLUGIN_PATH 指向该目录，
# SDF 里用 filename="libTrafficLightSystem.so" 即可被 gz-sim 加载。
install(TARGETS TrafficLightSystem
  LIBRARY DESTINATION lib
  ARCHIVE DESTINATION lib
  RUNTIME DESTINATION bin
)
