cmake_minimum_required(VERSION 3.25)

project(BlenderCAD VERSION 0.1.0 LANGUAGES CXX)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
    message(FATAL_ERROR "Use an out-of-source build: cmake -S . -B build")
endif()
file(WRITE "${CMAKE_BINARY_DIR}/.blendercad-build" "BlenderCAD generated build tree\n")

include(GNUInstallDirs)
include(CTest)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BLENDERCAD_BUILD_PYTHON "Build the CPython extension used by the Blender addon" ON)

include(cmake/Dependencies.cmake)
include(cmake/PlaneGCS.cmake)

add_custom_target(purge_dependencies
    COMMAND "${CMAKE_COMMAND}" "-DBLENDERCAD_BUILD_DIR=${CMAKE_BINARY_DIR}"
        -DMODE=dependencies -P "${CMAKE_SOURCE_DIR}/cmake/Clean.cmake"
    COMMENT "Removing build-local dependency caches; reconfigure before rebuilding"
    VERBATIM
)
set_property(DIRECTORY APPEND PROPERTY ADDITIONAL_CLEAN_FILES
    "${CMAKE_BINARY_DIR}/dist" "${CMAKE_BINARY_DIR}/addon" "${CMAKE_BINARY_DIR}/ui-tests"
    "${CMAKE_BINARY_DIR}/sketch-ui-tests"
    "${CMAKE_BINARY_DIR}/fillet-ui-tests"
    "${CMAKE_BINARY_DIR}/bracket-tests" "${CMAKE_BINARY_DIR}/bracket-ui-tests"
    "${CMAKE_BINARY_DIR}/installation-tests"
    "${CMAKE_BINARY_DIR}/feature-preview-ui-tests"
    "${CMAKE_BINARY_DIR}/sketch-support-ui-tests"
)

add_library(blendercad_core STATIC
    src/blendercad/document_id.cpp
    src/blendercad/document.cpp
    src/blendercad/parameter_expression.cpp
    src/blendercad/sketch.cpp
    src/blendercad/feature.cpp
    src/blendercad/reference.cpp
    src/blendercad/reference_tracker.cpp
)
add_library(BlenderCAD::Core ALIAS blendercad_core)
set_target_properties(blendercad_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(blendercad_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
)
target_compile_features(blendercad_core PUBLIC cxx_std_20)

if(BUILD_TESTING)
    add_executable(reference_tracker_check tests/reference_tracker.cpp)
    target_link_libraries(reference_tracker_check PRIVATE BlenderCAD::Core)
    add_test(NAME reference_tracker COMMAND reference_tracker_check)
endif()

add_library(blendercad_sketch STATIC src/blendercad/sketch_planegcs.cpp)
set_target_properties(blendercad_sketch PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(blendercad_sketch PUBLIC BlenderCAD::Core PRIVATE blendercad_planegcs)

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
    PRIVATE TKernel TKMath TKG2d TKG3d TKGeomBase TKBRep TKGeomAlgo TKTopAlgo TKPrim
            TKBO TKBool TKFillet TKMesh TKDESTEP TKXSBase
)

add_library(blendercad_evaluation STATIC src/blendercad/evaluation.cpp)
set_target_properties(blendercad_evaluation PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(blendercad_evaluation PUBLIC BlenderCAD::Core BlenderCAD::GeometryOCCT blendercad_sketch)

if(BLENDERCAD_BUILD_PYTHON)
    find_package(Python 3.14 EXACT COMPONENTS Interpreter Development.Module REQUIRED)
    blendercad_resolve_pybind11()

    pybind11_add_module(_native MODULE bindings/module.cpp bindings/sketch.cpp bindings/document.cpp bindings/evaluation.cpp bindings/reference.cpp bindings/topology.cpp bindings/queries.cpp)
    target_link_libraries(_native PRIVATE BlenderCAD::Core)
    target_include_directories(_native PRIVATE ${OpenCASCADE_INCLUDE_DIR})
    target_compile_definitions(_native PRIVATE BLENDERCAD_VERSION="${PROJECT_VERSION}")
    target_link_libraries(_native PRIVATE BlenderCAD::GeometryOCCT)
    target_link_libraries(_native PRIVATE blendercad_sketch)
    target_link_libraries(_native PRIVATE blendercad_evaluation)
    set_target_properties(_native PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/addon/blendercad"
    )
    add_custom_command(TARGET _native PRE_LINK
        COMMAND "${CMAKE_COMMAND}" -E make_directory "$<TARGET_FILE_DIR:_native>"
        VERBATIM
    )
    include(cmake/RuntimePackaging.cmake)
    if(BUILD_TESTING)
        find_program(BLENDERCAD_BLENDER_EXECUTABLE NAMES blender REQUIRED)
        add_test(NAME native_geometry
            COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                --python-exit-code 1 --python "${CMAKE_CURRENT_SOURCE_DIR}/tests/native_geometry.py"
                -- --module-dir "$<TARGET_FILE_DIR:_native>"
        )
        add_test(NAME native_sketch
            COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                --python-exit-code 1 --python "${CMAKE_CURRENT_SOURCE_DIR}/tests/native_sketch.py"
                -- --module-dir "$<TARGET_FILE_DIR:_native>"
        )
        add_test(NAME document_snapshot
            COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                --python-exit-code 1 --python "${CMAKE_CURRENT_SOURCE_DIR}/tests/document_snapshot.py"
                -- --module-dir "$<TARGET_FILE_DIR:_native>"
        )
        add_test(NAME native_features
            COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                --python-exit-code 1 --python "${CMAKE_CURRENT_SOURCE_DIR}/tests/native_features.py"
                -- --module-dir "$<TARGET_FILE_DIR:_native>"
        )
        foreach(_reference_check IN ITEMS native_topology native_references native_queries native_query_guards native_parameters native_sketch_parameters native_fillets native_sketch_support)
            add_test(NAME ${_reference_check}
                COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                    --python-exit-code 1 --python "${CMAKE_CURRENT_SOURCE_DIR}/tests/${_reference_check}.py"
                    -- --module-dir "$<TARGET_FILE_DIR:_native>"
            )
        endforeach()
        add_test(NAME cmake_cleanup
            COMMAND "${Python_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/cmake_cleanup.py"
                --cmake "${CMAKE_COMMAND}" --script "${CMAKE_CURRENT_SOURCE_DIR}/cmake/Clean.cmake"
        )
        add_test(NAME blender_foundation
            COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                --python-exit-code 1 --python "${CMAKE_CURRENT_SOURCE_DIR}/tests/blender_foundation.py"
                -- --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
        )
        foreach(_blendercad_check IN ITEMS blender_solids blender_capabilities blender_references blender_selection blender_lifecycle blender_parameters blender_sketch_parameters blender_sketch_editor blender_fillets blender_step_export blender_feature_preview blender_sketch_support highlight_arrays)
            add_test(NAME ${_blendercad_check}
                COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                    --python-exit-code 1 --python "${CMAKE_CURRENT_SOURCE_DIR}/tests/${_blendercad_check}.py"
                    -- --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
            )
        endforeach()
        add_test(NAME blender_bracket
            COMMAND "${BLENDERCAD_BLENDER_EXECUTABLE}" --background --factory-startup
                --python-exit-code 1 --python "${CMAKE_SOURCE_DIR}/tests/blender_bracket.py"
                -- --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                --output-dir "${CMAKE_BINARY_DIR}/bracket-tests")
        set_tests_properties(blender_bracket PROPERTIES ENVIRONMENT "ALSOFT_DRIVERS=null" FIXTURES_SETUP bracket_model)
        add_test(NAME blender_installation
            COMMAND "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/check_blender_install.py"
                --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                --output-dir "${CMAKE_BINARY_DIR}/installation-tests"
                --script "${CMAKE_SOURCE_DIR}/tests/blender_installation.py"
                --fixture-blend "${CMAKE_BINARY_DIR}/bracket-tests/mechanical/bracket-mechanical.blend")
        set_tests_properties(blender_installation PROPERTIES FIXTURES_REQUIRED bracket_model)
        add_custom_target(check_installation
            COMMAND "${CMAKE_CTEST_COMMAND}" --output-on-failure -R "^blender_installation$"
            WORKING_DIRECTORY "${CMAKE_BINARY_DIR}" DEPENDS package_addon USES_TERMINAL VERBATIM)
        set_tests_properties(native_geometry native_sketch document_snapshot native_features native_topology native_references native_queries native_query_guards native_parameters native_sketch_parameters native_fillets native_sketch_support blender_foundation blender_solids blender_capabilities blender_references blender_selection blender_lifecycle blender_parameters blender_sketch_parameters blender_sketch_editor blender_fillets blender_step_export blender_feature_preview blender_sketch_support highlight_arrays PROPERTIES
            ENVIRONMENT "ALSOFT_DRIVERS=null"
        )
        find_program(BLENDERCAD_BWRAP_EXECUTABLE NAMES bwrap)
        add_test(NAME runtime_staging
            COMMAND "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tests/runtime_staging.py"
                --cmake "${CMAKE_COMMAND}" --script "${CMAKE_SOURCE_DIR}/cmake/StageRuntime.cmake"
                --module "$<TARGET_FILE:_native>" --resource-dir "${OpenCASCADE_RESOURCE_DIR}"
                --library-dir "$<TARGET_FILE_DIR:TKernel>" --work-dir "${CMAKE_BINARY_DIR}/runtime-staging-tests")
        if(BLENDERCAD_BWRAP_EXECUTABLE)
            set(_relocation_command "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tests/runtime_relocation.py"
                --blender "${BLENDERCAD_BLENDER_EXECUTABLE}" --bwrap "${BLENDERCAD_BWRAP_EXECUTABLE}"
                --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                --output-dir "${CMAKE_BINARY_DIR}/relocation-tests"
                --script "${CMAKE_SOURCE_DIR}/tests/blender_relocation.py"
                --occt-library-dir "${OpenCASCADE_LIBRARY_DIR}")
            add_test(NAME runtime_relocation COMMAND ${_relocation_command})
            set_tests_properties(runtime_relocation PROPERTIES ENVIRONMENT "ALSOFT_DRIVERS=null")
            add_custom_target(check_relocation COMMAND ${_relocation_command}
                DEPENDS package_addon USES_TERMINAL VERBATIM)
        endif()
        add_custom_target(check_source_rebuild
            COMMAND "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tests/source_rebuild.py"
                --cmake "${CMAKE_COMMAND}" --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                --archive "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}-sources.tar.gz"
                --output-dir "${CMAKE_BINARY_DIR}/source-rebuild-tests"
            DEPENDS package_addon USES_TERMINAL VERBATIM)
        add_custom_target(check
            COMMAND "${CMAKE_CTEST_COMMAND}" --output-on-failure -C "$<CONFIG>"
            WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
            DEPENDS package_addon reference_tracker_check
            USES_TERMINAL
        )
        find_program(BLENDERCAD_XVFB_RUN_EXECUTABLE NAMES xvfb-run)
        if(BLENDERCAD_XVFB_RUN_EXECUTABLE)
            add_custom_target(check_sketch_support_ui
                COMMAND "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/check_blender_ui.py"
                    --xvfb "${BLENDERCAD_XVFB_RUN_EXECUTABLE}" --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                    --script "${CMAKE_SOURCE_DIR}/tests/blender_sketch_support_ui.py"
                    --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                    --output-dir "${CMAKE_BINARY_DIR}/sketch-support-ui-tests" --timeout 180
                DEPENDS package_addon USES_TERMINAL VERBATIM)
            add_custom_target(check_feature_preview_ui
                COMMAND "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/check_blender_ui.py"
                    --xvfb "${BLENDERCAD_XVFB_RUN_EXECUTABLE}" --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                    --script "${CMAKE_SOURCE_DIR}/tests/blender_feature_preview_ui.py"
                    --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                    --output-dir "${CMAKE_BINARY_DIR}/feature-preview-ui-tests" --timeout 180
                DEPENDS package_addon USES_TERMINAL VERBATIM)
            set(_bracket_ui_commands)
            foreach(_case IN ITEMS small ordinary furniture)
                if(_case STREQUAL "small")
                    set(_scale 0.01)
                elseif(_case STREQUAL "ordinary")
                    set(_scale 1)
                else()
                    set(_scale 20)
                endif()
                set(_bracket_command "${Python_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/check_blender_ui.py"
                    --xvfb "${BLENDERCAD_XVFB_RUN_EXECUTABLE}" --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                    --script "${CMAKE_SOURCE_DIR}/tests/blender_bracket_ui.py"
                    --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                    --output-dir "${CMAKE_BINARY_DIR}/bracket-ui-tests/${_case}" --timeout 180
                    --script-arg=--scale "--script-arg=${_scale}")
                list(APPEND _bracket_ui_commands COMMAND ${_bracket_command})
                add_custom_target(check_bracket_ui_${_case} COMMAND ${_bracket_command}
                    DEPENDS package_addon USES_TERMINAL VERBATIM)
            endforeach()
            add_custom_target(check_bracket_ui ${_bracket_ui_commands}
                DEPENDS package_addon USES_TERMINAL VERBATIM)
            add_custom_target(check_fillet_ui
                COMMAND "${Python_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/check_blender_ui.py"
                    --xvfb "${BLENDERCAD_XVFB_RUN_EXECUTABLE}"
                    --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                    --script "${CMAKE_CURRENT_SOURCE_DIR}/tests/blender_fillet_ui.py"
                    --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                    --output-dir "${CMAKE_BINARY_DIR}/fillet-ui-tests"
                DEPENDS package_addon
                USES_TERMINAL
                VERBATIM
            )
            add_custom_target(check_sketch_ui
                COMMAND "${Python_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/check_blender_ui.py"
                    --xvfb "${BLENDERCAD_XVFB_RUN_EXECUTABLE}"
                    --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                    --script "${CMAKE_CURRENT_SOURCE_DIR}/tests/blender_sketch_ui.py"
                    --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                    --output-dir "${CMAKE_BINARY_DIR}/sketch-ui-tests"
                DEPENDS package_addon
                USES_TERMINAL
                VERBATIM
            )
            add_custom_target(check_solid_ui
                COMMAND "${Python_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/check_blender_ui.py"
                    --xvfb "${BLENDERCAD_XVFB_RUN_EXECUTABLE}"
                    --blender "${BLENDERCAD_BLENDER_EXECUTABLE}"
                    --script "${CMAKE_CURRENT_SOURCE_DIR}/tests/blender_solid_ui.py"
                    --addon-zip "${CMAKE_BINARY_DIR}/dist/blendercad-${PROJECT_VERSION}.zip"
                    --output-dir "${CMAKE_BINARY_DIR}/ui-tests"
                DEPENDS package_addon
                USES_TERMINAL
                VERBATIM
            )
        endif()
    endif()

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
