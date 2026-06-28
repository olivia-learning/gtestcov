cmake_minimum_required(VERSION 3.16)
project(energy_mini_repo CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(energy_core
  src/can_codec.cpp
  src/energy_service.cpp
  src/global_config.cpp
  src/power_limit.cpp
  external/crc_lib/src/crc8.cpp
  external/emap/src/emap_memory.cpp
)
target_include_directories(energy_core PUBLIC
  src
  external/crc_lib/include
  external/emap/include
  external/hal/include
  external/osal/include
)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(energy_core PUBLIC --coverage -O0 -g)
  target_link_options(energy_core PUBLIC --coverage)
endif()

enable_testing()
find_package(GTest QUIET)
if(GTest_FOUND)
  add_executable(energy_core_tests
    tests/can_codec_test.cpp
    tests/energy_service_component_test.cpp
    tests/regression_crash_test.cpp
    tests/support/deps/osal_host_shim.cpp
    tests/support/fakes/fake_hal.cpp
    tests/support/fakes/fake_nvm.cpp
  )
  target_include_directories(energy_core_tests PRIVATE
    tests/support/deps
    tests/support/fakes
    tests/support/guards
    tests/support/harness
  )
  target_link_libraries(energy_core_tests PRIVATE energy_core GTest::gtest_main)
  add_test(NAME energy_core_tests COMMAND energy_core_tests)
endif()
