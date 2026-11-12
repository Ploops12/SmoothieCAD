include_guard(GLOBAL)

set(_planegcs_root "${CMAKE_CURRENT_LIST_DIR}/../third_party/planegcs")
set(_planegcs_source "${_planegcs_root}/upstream/src/Mod/Sketcher/App/planegcs")
file(READ "${_planegcs_root}/manifest.json" _planegcs_manifest)
string(JSON _planegcs_file_count LENGTH "${_planegcs_manifest}" files)
math(EXPR _planegcs_last_file "${_planegcs_file_count} - 1")
foreach(_index RANGE 0 ${_planegcs_last_file})
    string(JSON _path MEMBER "${_planegcs_manifest}" files ${_index})
    string(JSON _expected GET "${_planegcs_manifest}" files "${_path}")
    file(SHA256 "${_planegcs_root}/upstream/${_path}" _actual)
    if(NOT _actual STREQUAL _expected)
        message(FATAL_ERROR "Vendored PlaneGCS source differs from its recorded pin: ${_path}")
    endif()
endforeach()
add_library(blendercad_planegcs STATIC
    "${_planegcs_source}/GCS.cpp"
    "${_planegcs_source}/Geo.cpp"
    "${_planegcs_source}/Constraints.cpp"
    "${_planegcs_source}/SubSystem.cpp"
    "${_planegcs_source}/qp_eq.cpp"
)
set_target_properties(blendercad_planegcs PROPERTIES
    POSITION_INDEPENDENT_CODE ON CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON
)
target_compile_features(blendercad_planegcs PUBLIC cxx_std_20)
target_include_directories(blendercad_planegcs SYSTEM PUBLIC
    "${_planegcs_source}" "${_planegcs_root}/compat/include"
)
target_link_libraries(blendercad_planegcs PUBLIC blendercad_solver_headers)
