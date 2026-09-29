cmake_minimum_required(VERSION 3.8)
project(trace_msgs)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(std_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/TraceMessage.msg"
  "msg/TraceMessageFixed.msg"
  "msg/TraceChainMessage.msg"
  DEPENDENCIES std_msgs builtin_interfaces
)

ament_package()
