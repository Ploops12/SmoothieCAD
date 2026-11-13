#include "blendercad/document_id.hpp"
#include "blendercad/geometry.hpp"
#include "display.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <Standard_Failure.hxx>

#include <stdexcept>

#ifndef BLENDERCAD_VERSION
#define BLENDERCAD_VERSION "unknown"
#endif

namespace py = pybind11;
void bind_sketch(py::module_& module);
void bind_document(py::module_& module);
void bind_evaluation(py::module_& module);
void bind_topology(py::module_& module);
void bind_reference(py::module_& module);

PYBIND11_MODULE(_native, module) {
    module.doc() = "BlenderCAD native foundation";
    module.def("new_document_id", [] {
        return blendercad::DocumentId::generate().to_string();
    });
    module.def("is_document_id", [](const std::string& value) {
        try {
            (void)blendercad::DocumentId::parse(value);
            return true;
        } catch (const std::invalid_argument&) {
            return false;
        }
    });
    module.def("blendercad_version", [] { return BLENDERCAD_VERSION; });
    module.def("occt_version", &blendercad::occt_version);
    py::register_exception_translator([](std::exception_ptr exception) {
        try {
            if (exception) std::rethrow_exception(exception);
        } catch (const Standard_Failure& failure) {
            PyErr_SetString(PyExc_RuntimeError, failure.GetMessageString());
        }
    });

    py::class_<blendercad::Point2>(module, "Point2")
        .def(py::init<double, double>())
        .def_readwrite("x", &blendercad::Point2::x)
        .def_readwrite("y", &blendercad::Point2::y);
    py::class_<blendercad::Vector3>(module, "Vector3")
        .def(py::init<double, double, double>())
        .def_readwrite("x", &blendercad::Vector3::x)
        .def_readwrite("y", &blendercad::Vector3::y)
        .def_readwrite("z", &blendercad::Vector3::z);
    py::class_<blendercad::SketchPlane>(module, "SketchPlane")
        .def(py::init<>())
        .def_readwrite("origin", &blendercad::SketchPlane::origin)
        .def_readwrite("x_axis", &blendercad::SketchPlane::x_axis)
        .def_readwrite("y_axis", &blendercad::SketchPlane::y_axis);
    py::class_<blendercad::ShapeHandle>(module, "ShapeHandle")
        .def_readonly("value", &blendercad::ShapeHandle::value);
    py::class_<blendercad::GeometryAdapter, std::unique_ptr<blendercad::GeometryAdapter>>(module, "GeometryAdapter")
        .def("make_box", &blendercad::GeometryAdapter::make_box)
        .def("extrude_polygon", &blendercad::GeometryAdapter::extrude_polygon)
        .def("extrude_circle", &blendercad::GeometryAdapter::extrude_circle)
        .def("subtract", &blendercad::GeometryAdapter::subtract)
        .def("write_step", &blendercad::GeometryAdapter::write_step)
        .def("read_step", &blendercad::GeometryAdapter::read_step)
        .def("tessellate", [](const blendercad::GeometryAdapter& geometry, blendercad::ShapeHandle shape,
                              double linear_deflection_mm, double angular_deflection_rad) {
            blendercad::DisplayMesh display;
            {
                py::gil_scoped_release release;
                display = geometry.tessellate(shape, linear_deflection_mm, angular_deflection_rad);
            }
            return blendercad_bindings::display_arrays(display);
        })
        .def("volume_mm3", &blendercad::GeometryAdapter::volume_mm3)
        .def("is_valid", &blendercad::GeometryAdapter::is_valid)
        .def("max_tolerance_mm", &blendercad::GeometryAdapter::max_tolerance_mm)
        .def("release", &blendercad::GeometryAdapter::release);
    module.def("new_geometry_adapter", &blendercad::make_occt_geometry_adapter,
               py::arg("geometric_budget_mm") = 0.001);
    bind_topology(module);
    bind_reference(module);
    bind_sketch(module);
    bind_document(module);
    bind_evaluation(module);
}
