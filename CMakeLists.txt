cmake_minimum_required(VERSION 3.25)

project(BlenderCAD VERSION 0.1.0 LANGUAGES CXX)

include(GNUInstallDirs)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BLENDERCAD_BUILD_PYTHON "Build the CPython extension used by the Blender addon" ON)

find_package(OpenCASCADE 7.9.3 EXACT CONFIG REQUIRED)

add_library(blendercad_core STATIC
    src/blendercad/document_id.cpp
    src/blendercad/document.cpp
)
add_library(BlenderCAD::Core ALIAS blendercad_core)
set_target_properties(blendercad_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(blendercad_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
)
target_compile_features(blendercad_core PUBLIC cxx_std_20)

add_library(blendercad_geometry_occt STATIC
    src/blendercad/geometry_occt.cpp
)
add_library(BlenderCAD::GeometryOCCT ALIAS blendercad_geometry_occt)
set_target_properties(blendercad_geometry_occt PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(blendercad_geometry_occt
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
    PRIVATE ${OpenCASCADE_INCLUDE_DIR}
)
target_compile_features(blendercad_geometry_occt PUBLIC cxx_std_20)
target_link_libraries(blendercad_geometry_occt
    PUBLIC BlenderCAD::Core
    PRIVATE TKernel TKMath TKG2d TKG3d TKGeomBase TKBRep TKTopAlgo TKPrim
)

if(BLENDERCAD_BUILD_PYTHON)
    find_package(Python 3.14 EXACT COMPONENTS Interpreter Development.Module REQUIRED)
    find_package(pybind11 3.1.0 EXACT CONFIG REQUIRED)

    pybind11_add_module(_native MODULE bindings/module.cpp)
    target_link_libraries(_native PRIVATE BlenderCAD::Core)
    target_include_directories(_native PRIVATE ${OpenCASCADE_INCLUDE_DIR})
    target_compile_definitions(_native PRIVATE BLENDERCAD_VERSION="${PROJECT_VERSION}")
    target_link_libraries(_native PRIVATE BlenderCAD::GeometryOCCT)
    set_target_properties(_native PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/addon/blendercad"
    )

    add_custom_target(package_addon
        COMMAND "${Python_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/package_addon.py"
            --addon-dir "${CMAKE_CURRENT_SOURCE_DIR}/addon/blendercad"
            --native-module "$<TARGET_FILE:_native>"
            --output "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
        DEPENDS _native
        COMMENT "Packaging BlenderCAD ${PROJECT_VERSION} addon"
        VERBATIM
    )
endif()

install(TARGETS blendercad_core
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
)
install(TARGETS blendercad_geometry_occt
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
)
install(DIRECTORY src/blendercad/
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/blendercad"
    FILES_MATCHING PATTERN "*.hpp"
)
