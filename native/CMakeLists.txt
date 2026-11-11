# AirBrief native kernel: built by scikit-build-core (pip install ./native).
# Pattern from wjakob/nanobind_example.
cmake_minimum_required(VERSION 3.18...3.30)

project(${SKBUILD_PROJECT_NAME} VERSION ${SKBUILD_PROJECT_VERSION} LANGUAGES CXX)

if(NOT SKBUILD)
  message(WARNING "This project is meant to be built with scikit-build-core: pip install ./native")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

find_package(Python 3.11 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_airbrief_native NB_STATIC src/airbrief_native.cpp)

# Results must match the numpy fallback bit for bit: no fused multiply-add contraction.
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(_airbrief_native PRIVATE -ffp-contract=off)
elseif(MSVC)
  target_compile_options(_airbrief_native PRIVATE /fp:precise)
endif()

install(TARGETS _airbrief_native LIBRARY DESTINATION airbrief_native)
