#include "blendercad/geometry.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace blendercad;

void bind_queries(py::module_& module) {
    py::class_<TopologyMeasure>(module, "TopologyMeasure")
        .def_property_readonly("element", [](const TopologyMeasure& result) { return result.element; })
        .def_property_readonly("center_mm", [](const TopologyMeasure& result) { return result.center_mm; })
        .def_readonly("length_mm", &TopologyMeasure::length_mm)
        .def_readonly("area_mm2", &TopologyMeasure::area_mm2)
        .def_readonly("radius_mm", &TopologyMeasure::radius_mm);
    py::class_<TopologyPoint>(module, "TopologyPoint")
        .def_property_readonly("element", [](const TopologyPoint& result) { return result.element; })
        .def_property_readonly("point_mm", [](const TopologyPoint& result) { return result.point_mm; })
        .def_readonly("distance_mm", &TopologyPoint::distance_mm);
    py::class_<FaceHit>(module, "FaceHit")
        .def_readonly("face_index", &FaceHit::face_index)
        .def_property_readonly("point_mm", [](const FaceHit& result) { return result.point_mm; })
        .def_property_readonly("normal", [](const FaceHit& result) { return result.normal; })
        .def_readonly("distance_mm", &FaceHit::distance_mm);
    module.def("geometry_measure", &GeometryAdapter::measure,
               py::arg("adapter"), py::arg("shape"), py::arg("element"));
    module.def("geometry_closest_point", &GeometryAdapter::closest_point,
               py::arg("adapter"), py::arg("shape"), py::arg("element"), py::arg("point_mm"));
    module.def("geometry_ray_cast", &GeometryAdapter::ray_cast,
               py::arg("adapter"), py::arg("shape"), py::arg("origin_mm"), py::arg("direction"), py::arg("max_distance_mm"));
}
