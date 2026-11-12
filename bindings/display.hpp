#pragma once

#include "blendercad/geometry_types.hpp"
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <cstring>

namespace blendercad_bindings {
namespace py = pybind11;

template <typename T>
py::array_t<T> bulk_array(const std::vector<T>& values) {
    py::array_t<T> array(values.size());
    if (!values.empty()) std::memcpy(array.mutable_data(), values.data(), values.size() * sizeof(T));
    return array;
}

inline py::dict display_arrays(const blendercad::DisplayMesh& display) {
    py::dict arrays;
    arrays["positions"] = bulk_array(display.positions);
    arrays["triangles"] = bulk_array(display.triangles);
    arrays["triangle_faces"] = bulk_array(display.triangle_faces);
    arrays["edge_positions"] = bulk_array(display.edge_positions);
    arrays["edge_offsets"] = bulk_array(display.edge_offsets);
    arrays["cad_vertices"] = bulk_array(display.cad_vertices);
    arrays["face_count"] = display.face_count;
    return arrays;
}

} // namespace blendercad_bindings
