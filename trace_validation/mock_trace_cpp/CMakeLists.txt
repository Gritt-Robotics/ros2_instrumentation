cmake_minimum_required(VERSION 3.8)
project(mock_trace_cpp)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(THIS_PACKAGE_INCLUDE_DEPENDS
  rclcpp
  trace_msgs
  std_msgs
  builtin_interfaces
  tracetools
)

find_package(ament_cmake REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LTTNG_UST REQUIRED lttng-ust)
foreach(Dependency IN ITEMS ${THIS_PACKAGE_INCLUDE_DEPENDS})
  find_package(${Dependency} REQUIRED)
endforeach()

# message_type/transport parsing shared between the publisher and subscriber nodes.
add_library(message_dispatch STATIC
  src/message_dispatch.cpp)

target_include_directories(message_dispatch PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

# Node logic, split out from main() so gtest targets can link against it directly.
add_library(mock_publisher_node_lib STATIC
  src/publisher_node.cpp)

target_include_directories(mock_publisher_node_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

target_link_libraries(mock_publisher_node_lib PUBLIC message_dispatch)
ament_target_dependencies(mock_publisher_node_lib PUBLIC ${THIS_PACKAGE_INCLUDE_DEPENDS})

add_library(mock_subscriber_node_lib STATIC
  src/subscriber_node.cpp)

target_include_directories(mock_subscriber_node_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

target_link_libraries(mock_subscriber_node_lib PUBLIC message_dispatch)
ament_target_dependencies(mock_subscriber_node_lib PUBLIC ${THIS_PACKAGE_INCLUDE_DEPENDS})

add_library(mock_relay_node_lib STATIC
  src/relay_node.cpp)

target_include_directories(mock_relay_node_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

target_link_libraries(mock_relay_node_lib PUBLIC message_dispatch)
ament_target_dependencies(mock_relay_node_lib PUBLIC ${THIS_PACKAGE_INCLUDE_DEPENDS})

add_executable(mock_publisher_node src/publisher_main.cpp)
target_link_libraries(mock_publisher_node PUBLIC mock_publisher_node_lib)

add_executable(mock_subscriber_node src/subscriber_main.cpp)
target_link_libraries(mock_subscriber_node PUBLIC mock_subscriber_node_lib)

add_executable(mock_relay_node src/relay_main.cpp)
target_link_libraries(mock_relay_node PUBLIC mock_relay_node_lib)

add_library(mock_indirect_relay_node_lib STATIC
  src/indirect_relay_node.cpp)

target_include_directories(mock_indirect_relay_node_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

target_link_libraries(mock_indirect_relay_node_lib PUBLIC message_dispatch)
ament_target_dependencies(mock_indirect_relay_node_lib PUBLIC ${THIS_PACKAGE_INCLUDE_DEPENDS})

add_executable(mock_indirect_relay_node src/indirect_relay_main.cpp)
target_link_libraries(mock_indirect_relay_node PUBLIC mock_indirect_relay_node_lib)

install(
  TARGETS
    mock_publisher_node
    mock_subscriber_node
    mock_relay_node
    mock_indirect_relay_node
    message_dispatch
    mock_publisher_node_lib
    mock_subscriber_node_lib
    mock_relay_node_lib
    mock_indirect_relay_node_lib
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY include/
  DESTINATION include
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)

  # Each test binary gets its own DDS domain so parallel runs don't cross-talk.
  ament_add_gtest(test_message_dispatch test/test_message_dispatch.cpp
    ENV ROS_DOMAIN_ID=61
  )
  target_link_libraries(test_message_dispatch message_dispatch)

  ament_add_gtest(test_mock_publisher_node test/test_publisher_node.cpp
    ENV ROS_DOMAIN_ID=61
  )
  target_link_libraries(test_mock_publisher_node mock_publisher_node_lib)

  ament_add_gtest(test_mock_subscriber_node test/test_subscriber_node.cpp
    ENV ROS_DOMAIN_ID=61
  )
  target_link_libraries(test_mock_subscriber_node mock_subscriber_node_lib)

  ament_add_gtest(test_mock_relay_node test/test_relay_node.cpp
    ENV ROS_DOMAIN_ID=61
  )
  target_link_libraries(test_mock_relay_node mock_relay_node_lib)

  ament_add_gtest(test_mock_indirect_relay_node test/test_indirect_relay_node.cpp
    ENV ROS_DOMAIN_ID=61
  )
  target_link_libraries(test_mock_indirect_relay_node mock_indirect_relay_node_lib)
endif()

ament_package()
