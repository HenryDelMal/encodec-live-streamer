cmake_minimum_required(VERSION 3.16)
project(encodec_live_native LANGUAGES CXX)

option(ENCODEC_ENABLE_OPENMP "Enable Eigen/OpenMP encoder threading" ON)

add_executable(
    encodec-live-native
    worker.cpp
    encodec/encodec.cpp
)
target_compile_features(encodec-live-native PRIVATE cxx_std_20)
target_compile_definitions(encodec-live-native PRIVATE ENCODEC_RUNTIME_MODELS_ONLY=1)
target_include_directories(
    encodec-live-native
    PRIVATE
    encodec
    third_party/eigen
)
target_compile_options(
    encodec-live-native
    PRIVATE
    -O3 -ffunction-sections -fdata-sections
)
if(NOT APPLE)
    target_link_options(encodec-live-native PRIVATE -Wl,--gc-sections)
endif()

if(ENCODEC_ENABLE_OPENMP)
    find_package(OpenMP QUIET)
    if(OpenMP_CXX_FOUND)
        target_compile_definitions(encodec-live-native PRIVATE ENCODEC_HAS_OPENMP=1)
        target_link_libraries(encodec-live-native PRIVATE OpenMP::OpenMP_CXX)
    else()
        message(STATUS "OpenMP not found; building the native encoder single-threaded")
        target_compile_definitions(encodec-live-native PRIVATE EIGEN_DONT_PARALLELIZE=1)
    endif()
else()
    target_compile_definitions(encodec-live-native PRIVATE EIGEN_DONT_PARALLELIZE=1)
endif()

install(TARGETS encodec-live-native RUNTIME DESTINATION bin)
