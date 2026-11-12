#include "blendercad/document_id.hpp"
#include "blendercad/document.hpp"
#include "blendercad/geometry.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <Standard_Failure.hxx>

#include <cstring>
#include <stdexcept>

#ifndef BLENDERCAD_VERSION
#define BLENDERCAD_VERSION "unknown"
#endif

namespace py = pybind11;

template <typename T>
py::array_t<T> bulk_array(const std::vector<T>& values) {
    py::array_t<T> array(values.size());
    if (!values.empty()) std::memcpy(array.mutable_data(), values.data(), values.size() * sizeof(T));
    return array;
}

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

    py::enum_<blendercad::ParameterUnit>(module, "ParameterUnit")
        .value("MILLIMETRE", blendercad::ParameterUnit::Millimetre)
        .value("DEGREE", blendercad::ParameterUnit::Degree)
        .value("UNITLESS", blendercad::ParameterUnit::Unitless);

    py::class_<blendercad::Parameter>(module, "Parameter")
        .def_readonly("name", &blendercad::Parameter::name)
        .def_readonly("value", &blendercad::Parameter::value)
        .def_property_readonly("unit", [](const blendercad::Parameter& parameter) {
            return std::string(blendercad::unit_name(parameter.unit));
        });

    py::class_<blendercad::Document>(module, "Document")
        .def(py::init<>())
        .def_property_readonly("id", [](const blendercad::Document& document) {
            return document.id().to_string();
        })
        .def_property_readonly("schema_version", &blendercad::Document::schema_version)
        .def_property_readonly("revision", &blendercad::Document::revision)
        .def_property_readonly("geometric_tolerance_mm", &blendercad::Document::geometric_tolerance_mm)
        .def_property_readonly("parameters", &blendercad::Document::parameters)
        .def("set_geometric_tolerance_mm", &blendercad::Document::set_geometric_tolerance_mm)
        .def("add_parameter", [](blendercad::Document& document, std::string name,
                                  double value, const std::string& unit) {
            document.add_parameter(std::move(name), value, blendercad::parse_unit(unit));
        }, py::arg("name"), py::arg("value"), py::arg("unit") = "unitless")
        .def("set_parameter_value", &blendercad::Document::set_parameter_value)
        .def("to_snapshot", [](const blendercad::Document& document) {
            py::dict snapshot;
            snapshot["schema_version"] = document.schema_version();
            snapshot["document_id"] = document.id().to_string();
            snapshot["revision"] = document.revision();
            snapshot["geometric_tolerance_mm"] = document.geometric_tolerance_mm();
            py::list parameters;
            for (const auto& parameter : document.parameters()) {
                py::dict item;
                item["name"] = parameter.name;
                item["value"] = parameter.value;
                item["unit"] = blendercad::unit_name(parameter.unit);
                parameters.append(std::move(item));
            }
            snapshot["parameters"] = std::move(parameters);
            return snapshot;
        })
        .def_static("from_snapshot", [](const py::dict& snapshot) {
            const auto schema = py::cast<std::uint32_t>(snapshot["schema_version"]);
            if (schema != blendercad::Document::current_schema_version) {
                throw std::invalid_argument("unsupported BlenderCAD document schema version");
            }
            std::vector<blendercad::Parameter> parameters;
            for (const auto item_object : py::cast<py::list>(snapshot["parameters"])) {
                const auto item = py::cast<py::dict>(item_object);
                parameters.push_back({
                    py::cast<std::string>(item["name"]),
                    py::cast<double>(item["value"]),
                    blendercad::parse_unit(py::cast<std::string>(item["unit"]))});
            }
            return blendercad::Document::restore(
                blendercad::DocumentId::parse(py::cast<std::string>(snapshot["document_id"])),
                py::cast<std::uint64_t>(snapshot["revision"]),
                py::cast<double>(snapshot["geometric_tolerance_mm"]), std::move(parameters));
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
            py::dict arrays;
            arrays["positions"] = bulk_array(display.positions);
            arrays["triangles"] = bulk_array(display.triangles);
            arrays["triangle_faces"] = bulk_array(display.triangle_faces);
            arrays["edge_positions"] = bulk_array(display.edge_positions);
            arrays["edge_offsets"] = bulk_array(display.edge_offsets);
            arrays["cad_vertices"] = bulk_array(display.cad_vertices);
            arrays["face_count"] = display.face_count;
            return arrays;
        })
        .def("volume_mm3", &blendercad::GeometryAdapter::volume_mm3)
        .def("is_valid", &blendercad::GeometryAdapter::is_valid)
        .def("max_tolerance_mm", &blendercad::GeometryAdapter::max_tolerance_mm)
        .def("release", &blendercad::GeometryAdapter::release);
    module.def("new_geometry_adapter", &blendercad::make_occt_geometry_adapter,
               py::arg("geometric_budget_mm") = 0.001);
}
