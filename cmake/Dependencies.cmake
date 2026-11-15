include_guard(GLOBAL)
include(FetchContent)

option(BLENDERCAD_USE_SYSTEM_DEPENDENCIES "Prefer matching installed OCCT and pybind11 packages" ON)
option(BLENDERCAD_OFFLINE "Use only installed or already-populated dependencies" OFF)
if(BLENDERCAD_OFFLINE)
    set(FETCHCONTENT_FULLY_DISCONNECTED ON)
endif()

# Header-only prerequisites of the embedded PlaneGCS solver. Populate without
# configuring their examples, tests, installation rules or compiled libraries.
FetchContent_Declare(blendercad_eigen
    GIT_REPOSITORY https://gitlab.com/libeigen/eigen.git
    GIT_TAG 3147391d946bb4b6c68edd901f2add6ac1f31f8c
    GIT_SUBMODULES ""
    GIT_PROGRESS TRUE
    GIT_CONFIG http.lowSpeedLimit=1024 http.lowSpeedTime=30
    UPDATE_DISCONNECTED TRUE
    SOURCE_SUBDIR blendercad_headers_only
)
FetchContent_Declare(blendercad_boost
    URL https://archives.boost.io/release/1.88.0/source/boost_1_88_0.tar.bz2
    URL_HASH SHA256=46d9d2c06637b219270877c9e16155cbd015b6dc84349af064c088e9b5b12f7b
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE
    TIMEOUT 300
    SOURCE_SUBDIR blendercad_headers_only
)
FetchContent_MakeAvailable(blendercad_eigen blendercad_boost)
if(NOT EXISTS "${blendercad_eigen_SOURCE_DIR}/Eigen/Core" OR
   NOT EXISTS "${blendercad_boost_SOURCE_DIR}/boost/graph/adjacency_list.hpp")
    message(FATAL_ERROR "Pinned solver headers are absent. Populate dependencies before using offline mode.")
endif()

add_library(blendercad_solver_headers INTERFACE)
target_include_directories(blendercad_solver_headers SYSTEM INTERFACE
    "${blendercad_eigen_SOURCE_DIR}" "${blendercad_boost_SOURCE_DIR}"
)

set(OpenCASCADE_FOUND FALSE)
if(BLENDERCAD_USE_SYSTEM_DEPENDENCIES)
    find_package(OpenCASCADE 7.9.3 EXACT CONFIG QUIET)
endif()
if(NOT OpenCASCADE_FOUND)
    # Release V7_9_3. Git verifies the full pinned object identity.
    FetchContent_Declare(blendercad_occt
        GIT_REPOSITORY https://github.com/Open-Cascade-SAS/OCCT.git
        GIT_TAG a016080bf6738d6aeae020badee4e888ad1540a5
        GIT_SUBMODULES ""
        GIT_PROGRESS TRUE
        UPDATE_DISCONNECTED TRUE
        SOURCE_SUBDIR blendercad_external_build
    )
    FetchContent_MakeAvailable(blendercad_occt)
    # OCCT 7.9 uses CMAKE_SOURCE_DIR internally and is not an add_subdirectory
    # project. Give it an isolated CMake configure/build/install graph instead.
    include(ExternalProject)
    set(_occt_prefix "${FETCHCONTENT_BASE_DIR}/occt-install")
    set(_occt_options)
    foreach(_module IN ITEMS FoundationClasses ModelingData ModelingAlgorithms Visualization ApplicationFramework DataExchange DETools Draw)
        list(APPEND _occt_options "-DBUILD_MODULE_${_module}:BOOL=OFF")
    endforeach()
    foreach(_optional_dependency IN ITEMS TCL TK FREETYPE FREEIMAGE FFMPEG VTK TBB GLES2 XLIB RAPIDJSON DRACO)
        list(APPEND _occt_options "-DUSE_${_optional_dependency}:BOOL=OFF")
    endforeach()
    if(CMAKE_TOOLCHAIN_FILE)
        list(APPEND _occt_options "-DCMAKE_TOOLCHAIN_FILE:FILEPATH=${CMAKE_TOOLCHAIN_FILE}")
    endif()
    set(_occt_libraries TKernel TKMath TKG2d TKG3d TKGeomBase TKBRep TKGeomAlgo TKTopAlgo TKPrim TKBO TKBool TKFillet TKMesh TKDESTEP TKXSBase)
    set(_occt_byproducts)
    foreach(_library IN LISTS _occt_libraries)
        list(APPEND _occt_byproducts "${_occt_prefix}/lib/${CMAKE_SHARED_LIBRARY_PREFIX}${_library}${CMAKE_SHARED_LIBRARY_SUFFIX}")
    endforeach()
    ExternalProject_Add(blendercad_occt_build
        SOURCE_DIR "${blendercad_occt_SOURCE_DIR}"
        BINARY_DIR "${blendercad_occt_BINARY_DIR}"
        PREFIX "${FETCHCONTENT_BASE_DIR}/occt-project"
        DOWNLOAD_COMMAND "" UPDATE_COMMAND ""
        LIST_SEPARATOR "|"
        CMAKE_ARGS
            "-DCMAKE_INSTALL_PREFIX:PATH=${_occt_prefix}"
            "-DCMAKE_BUILD_TYPE:STRING=${CMAKE_BUILD_TYPE}"
            "-DCMAKE_CXX_COMPILER:FILEPATH=${CMAKE_CXX_COMPILER}"
            "-DCMAKE_INSTALL_RPATH:STRING=$ORIGIN"
            -DCMAKE_EXPORT_NO_PACKAGE_REGISTRY:BOOL=ON
            -DCMAKE_EXPORT_PACKAGE_REGISTRY:BOOL=OFF
            "-DINSTALL_DIR_LIB:STRING=lib"
            "-DINSTALL_DIR_INCLUDE:STRING=include/opencascade"
            "-DINSTALL_DIR_RESOURCE:PATH=share/opencascade/resources"
            "-DBUILD_ADDITIONAL_TOOLKITS:STRING=TKPrim|TKBool|TKFillet|TKMesh|TKDESTEP"
            -DBUILD_LIBRARY_TYPE:STRING=Shared
            -DBUILD_DOC_Overview:BOOL=OFF -DBUILD_SAMPLES_QT:BOOL=OFF -DBUILD_Inspector:BOOL=OFF
            ${_occt_options}
        BUILD_BYPRODUCTS ${_occt_byproducts}
    )
    set(OpenCASCADE_INCLUDE_DIR "${_occt_prefix}/include/opencascade")
    set(OpenCASCADE_RESOURCE_DIR "${_occt_prefix}/share/opencascade/resources")
    set(OpenCASCADE_LIBRARY_DIR "${_occt_prefix}/lib")
    file(MAKE_DIRECTORY "${OpenCASCADE_INCLUDE_DIR}")
    foreach(_library IN LISTS _occt_libraries)
        add_library(${_library} SHARED IMPORTED GLOBAL)
        set_target_properties(${_library} PROPERTIES
            IMPORTED_LOCATION "${_occt_prefix}/lib/${CMAKE_SHARED_LIBRARY_PREFIX}${_library}${CMAKE_SHARED_LIBRARY_SUFFIX}"
            INTERFACE_INCLUDE_DIRECTORIES "${OpenCASCADE_INCLUDE_DIR}"
        )
        add_dependencies(${_library} blendercad_occt_build)
    endforeach()
endif()

# Also clean previous source-build outputs after switching back to system
# packages, but never erase binaries in a user-supplied external/shared cache.
cmake_path(IS_PREFIX CMAKE_BINARY_DIR "${FETCHCONTENT_BASE_DIR}" NORMALIZE _local_dependency_cache)
if(_local_dependency_cache)
    set_property(DIRECTORY APPEND PROPERTY ADDITIONAL_CLEAN_FILES
        "${FETCHCONTENT_BASE_DIR}/blendercad_occt-build"
        "${FETCHCONTENT_BASE_DIR}/occt-install"
        "${FETCHCONTENT_BASE_DIR}/occt-project"
    )
endif()

function(blendercad_resolve_pybind11)
    # pybind11 writes a cached FOUND result. It is not evidence that this fresh
    # configure has defined its targets/functions, especially after source-mode
    # rebuilds or switching between installed and pinned dependencies.
    set(pybind11_FOUND FALSE)
    if(BLENDERCAD_USE_SYSTEM_DEPENDENCIES)
        find_package(pybind11 3.1.0 EXACT CONFIG QUIET)
    endif()
    if(NOT pybind11_FOUND)
        FetchContent_Declare(blendercad_pybind11
            GIT_REPOSITORY https://github.com/pybind/pybind11.git
            GIT_TAG 97bf890db679505a14dfe547a5e77bb2bd05dc90
            GIT_SUBMODULES ""
            UPDATE_DISCONNECTED TRUE
        )
        set(PYBIND11_TEST OFF CACHE BOOL "" FORCE)
        FetchContent_MakeAvailable(blendercad_pybind11)
    endif()
endfunction()

# Corresponding rebuild materials are build artifacts too. Even an installed
# binary package needs pinned upstream sources/notices for its companion archive.
# Do not configure a second copy of either dependency in the main build graph.
function(blendercad_distribution_sources)
    FetchContent_Declare(blendercad_occt
        GIT_REPOSITORY https://github.com/Open-Cascade-SAS/OCCT.git
        GIT_TAG a016080bf6738d6aeae020badee4e888ad1540a5
        GIT_SUBMODULES "" UPDATE_DISCONNECTED TRUE
        SOURCE_SUBDIR blendercad_distribution_only
    )
    FetchContent_Declare(blendercad_pybind11
        GIT_REPOSITORY https://github.com/pybind/pybind11.git
        GIT_TAG 97bf890db679505a14dfe547a5e77bb2bd05dc90
        GIT_SUBMODULES "" UPDATE_DISCONNECTED TRUE
        SOURCE_SUBDIR blendercad_distribution_only
    )
    FetchContent_MakeAvailable(blendercad_occt blendercad_pybind11)
    foreach(_dependency IN ITEMS occt pybind11 eigen boost)
        FetchContent_GetProperties(blendercad_${_dependency} SOURCE_DIR _source)
        if(NOT IS_DIRECTORY "${_source}")
            message(FATAL_ERROR "Distribution source is absent: ${_dependency}; populate before offline packaging")
        endif()
        set(BLENDERCAD_DISTRIBUTION_${_dependency}_SOURCE "${_source}" PARENT_SCOPE)
    endforeach()
endfunction()
