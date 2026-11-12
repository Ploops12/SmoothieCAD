# Also callable without a successful configure:
# cmake -DBLENDERCAD_BUILD_DIR=/absolute/build -DMODE=all -P cmake/Clean.cmake
if(NOT DEFINED BLENDERCAD_BUILD_DIR)
    message(FATAL_ERROR "Pass -DBLENDERCAD_BUILD_DIR=<build directory>")
endif()
file(REAL_PATH "${CMAKE_CURRENT_LIST_DIR}/.." _source)
file(REAL_PATH "${BLENDERCAD_BUILD_DIR}" _build)
if(_build STREQUAL _source OR _build STREQUAL "/" OR
   NOT EXISTS "${_build}/.blendercad-build")
    message(FATAL_ERROR "Refusing cleanup: not a marked BlenderCAD out-of-source build: ${_build}")
endif()
cmake_path(IS_PREFIX _build "${_source}" NORMALIZE _contains_source)
if(_contains_source)
    message(FATAL_ERROR "Refusing to remove a build directory containing the source tree")
endif()
if(MODE STREQUAL "all")
    file(REMOVE_RECURSE "${_build}")
elseif(MODE STREQUAL "dependencies")
    # Deliberately do not follow a user override to an external/shared cache.
    file(REMOVE_RECURSE "${_build}/_deps" "${_build}/dependencies")
else()
    message(FATAL_ERROR "MODE must be all or dependencies")
endif()
