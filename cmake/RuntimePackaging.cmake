include_guard(GLOBAL)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "The native addon bundle currently targets Linux; other platform packaging adapters are not implemented")
endif()
include(CheckLinkerFlag)
check_linker_flag(CXX "-Wl,--disable-new-dtags" BLENDERCAD_HAS_TRANSITIVE_RPATH)
if(NOT BLENDERCAD_HAS_TRANSITIVE_RPATH)
    message(FATAL_ERROR "The Linux bundle requires a linker supporting --disable-new-dtags")
endif()
# DT_RPATH, unlike DT_RUNPATH, also searches the extension's directory for
# transitive dependencies of installed OCCT libraries lacking their own rpath.
target_link_options(_native PRIVATE "LINKER:--disable-new-dtags")
set_target_properties(_native PROPERTIES
    BUILD_WITH_INSTALL_RPATH TRUE INSTALL_RPATH "$ORIGIN/.libs"
)

blendercad_distribution_sources()
set(BLENDERCAD_RUNTIME_LICENSE_ROOT "/usr/share/licenses" CACHE PATH "Installed transitive dependency notices")
set(BLENDERCAD_NOTICES_OVERRIDE_DIR "" CACHE PATH "Per-SONAME notice directories for dependencies not covered by the installed package mapping")
set(_package_root "${CMAKE_BINARY_DIR}/package/blendercad")

add_custom_target(stage_runtime
    COMMAND "${CMAKE_COMMAND}" "-DMODULE=$<TARGET_FILE:_native>"
        "-DOUTPUT_DIR=${_package_root}" "-DRESOURCE_DIR=${OpenCASCADE_RESOURCE_DIR}"
        "-DLIBRARY_DIR=$<TARGET_FILE_DIR:TKernel>"
        -P "${CMAKE_SOURCE_DIR}/cmake/StageRuntime.cmake"
    COMMAND "${CMAKE_COMMAND}" -E create_symlink "../../package/blendercad/.libs"
        "$<TARGET_FILE_DIR:_native>/.libs"
    DEPENDS _native
    COMMENT "Staging private native runtime libraries and exchange resources"
    VERBATIM
)
add_custom_target(package_notices
    COMMAND "${CMAKE_COMMAND}" "-DSOURCE_DIR=${CMAKE_SOURCE_DIR}" "-DBINARY_DIR=${CMAKE_BINARY_DIR}"
        "-DOUTPUT_DIR=${_package_root}/notices"
        "-DOCCT_SOURCE_DIR=${BLENDERCAD_DISTRIBUTION_occt_SOURCE}"
        "-DOCCT_RESOURCE_DIR=${OpenCASCADE_RESOURCE_DIR}" "-DOCCT_PACKAGE_DIR=${OpenCASCADE_DIR}"
        "-DEIGEN_SOURCE_DIR=${BLENDERCAD_DISTRIBUTION_eigen_SOURCE}"
        "-DBOOST_SOURCE_DIR=${BLENDERCAD_DISTRIBUTION_boost_SOURCE}"
        "-DPYBIND11_SOURCE_DIR=${BLENDERCAD_DISTRIBUTION_pybind11_SOURCE}"
        "-DRUNTIME_MANIFEST=${_package_root}/runtime-manifest.json"
        "-DLICENSE_ROOT=${BLENDERCAD_RUNTIME_LICENSE_ROOT}"
        "-DNOTICES_OVERRIDE_DIR=${BLENDERCAD_NOTICES_OVERRIDE_DIR}"
        -P "${CMAKE_SOURCE_DIR}/cmake/PackageNotices.cmake"
    DEPENDS stage_runtime
    COMMENT "Collecting native dependency notices and rebuild provenance"
    VERBATIM
)
add_custom_target(package_sources
    COMMAND "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/package_sources.py"
        --source-dir "${CMAKE_SOURCE_DIR}" --binary-dir "${CMAKE_BINARY_DIR}"
        --output "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}-sources.tar.gz"
        --version "${PROJECT_VERSION}"
        --eigen-source-dir "${BLENDERCAD_DISTRIBUTION_eigen_SOURCE}"
        --boost-source-dir "${BLENDERCAD_DISTRIBUTION_boost_SOURCE}"
        --pybind11-source-dir "${BLENDERCAD_DISTRIBUTION_pybind11_SOURCE}"
        --occt-source-dir "${BLENDERCAD_DISTRIBUTION_occt_SOURCE}"
        --occt-resource-dir "${OpenCASCADE_RESOURCE_DIR}"
        --runtime-manifest "${_package_root}/runtime-manifest.json"
        --include-project-path cmake/RuntimePackaging.cmake
        --include-project-path cmake/StageRuntime.cmake
        --include-project-path cmake/PackageNotices.cmake
        --include-project-path tools/package_sources.py
        --include-project-path addon/blendercad/runtime.py
        --include-project-path tests/runtime_relocation.py
        --include-project-path tests/blender_relocation.py
        --include-project-path tests/source_rebuild.py
        --include-project-path tests/runtime_staging.py
        --include-project-path docs/runtime-packaging.md
        --include-project-path tests/blender_bracket.py
        --include-project-path tests/blender_bracket_ui.py
        --include-project-path tests/blender_step_export.py
        --include-project-path tests/blender_installation.py
        --include-project-path tools/check_blender_install.py
        --include-project-path docs/bracket-acceptance.md
        --include-project-path docs/step-exchange.md
    DEPENDS package_notices
    COMMENT "Packaging companion sources and offline rebuild materials"
    VERBATIM
)
add_custom_target(package_addon
    COMMAND "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/package_addon.py"
        --addon-dir "${CMAKE_SOURCE_DIR}/addon/blendercad" --native-module "$<TARGET_FILE:_native>"
        --runtime-dir "${_package_root}" --notices-dir "${_package_root}/notices"
        --output "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
    DEPENDS package_sources
    COMMENT "Packaging BlenderCAD ${PROJECT_VERSION} addon with its private runtime"
    VERBATIM
)

set_property(DIRECTORY APPEND PROPERTY ADDITIONAL_CLEAN_FILES
    "${CMAKE_BINARY_DIR}/package" "${CMAKE_BINARY_DIR}/relocation-tests" "${CMAKE_BINARY_DIR}/source-rebuild-tests"
    "${CMAKE_BINARY_DIR}/runtime-staging-tests"
)
