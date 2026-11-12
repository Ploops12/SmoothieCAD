#include "blendercad/sketch_solver.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace blendercad;

namespace {
DocumentId identity(const std::string& value) {
    return value.empty() ? DocumentId::generate() : DocumentId::parse(value);
}

std::vector<std::string> strings(const std::vector<DocumentId>& ids) {
    std::vector<std::string> result;
    for (const auto& id : ids) result.push_back(id.to_string());
    return result;
}
} // namespace

void bind_sketch(py::module_& module) {
    py::class_<Line2>(module, "Line2")
        .def(py::init<Point2, Point2>())
        .def_readwrite("start", &Line2::start)
        .def_readwrite("end", &Line2::end);
    py::class_<Circle2>(module, "Circle2")
        .def(py::init<Point2, double>())
        .def_readwrite("center", &Circle2::center)
        .def_readwrite("radius", &Circle2::radius);
    py::class_<SketchEntity>(module, "SketchEntity")
        .def(py::init([](SketchGeometry geometry, const std::string& id) {
            return SketchEntity{identity(id), std::move(geometry)};
        }), py::arg("geometry"), py::arg("id") = "")
        .def_property_readonly("id", [](const SketchEntity& entity) { return entity.id.to_string(); })
        .def_readwrite("geometry", &SketchEntity::geometry);

    py::enum_<SketchElement>(module, "SketchElement")
        .value("GEOMETRY", SketchElement::Geometry)
        .value("LINE_START", SketchElement::LineStart)
        .value("LINE_END", SketchElement::LineEnd)
        .value("CIRCLE_CENTER", SketchElement::CircleCenter)
        .value("ORIGIN", SketchElement::Origin);
    py::class_<SketchReference>(module, "SketchReference")
        .def(py::init([](const std::string& entity, SketchElement element) {
            return SketchReference{entity.empty() ? std::nullopt : std::optional{DocumentId::parse(entity)}, element};
        }), py::arg("entity") = "", py::arg("element") = SketchElement::Origin)
        .def_property_readonly("entity", [](const SketchReference& reference) {
            return reference.entity ? reference.entity->to_string() : std::string{};
        })
        .def_readwrite("element", &SketchReference::element);
    py::enum_<SketchConstraintKind>(module, "SketchConstraintKind")
        .value("COINCIDENT", SketchConstraintKind::Coincident)
        .value("HORIZONTAL", SketchConstraintKind::Horizontal)
        .value("VERTICAL", SketchConstraintKind::Vertical)
        .value("PARALLEL", SketchConstraintKind::Parallel)
        .value("PERPENDICULAR", SketchConstraintKind::Perpendicular)
        .value("DISTANCE", SketchConstraintKind::Distance)
        .value("LENGTH", SketchConstraintKind::Length)
        .value("RADIUS", SketchConstraintKind::Radius)
        .value("DIAMETER", SketchConstraintKind::Diameter)
        .value("DISTANCE_X", SketchConstraintKind::DistanceX)
        .value("DISTANCE_Y", SketchConstraintKind::DistanceY);
    py::class_<SketchConstraint>(module, "SketchConstraint")
        .def(py::init([](SketchConstraintKind kind, std::vector<SketchReference> targets,
                        double value, const std::string& id) {
            return SketchConstraint{identity(id), kind, std::move(targets), value};
        }), py::arg("kind"), py::arg("targets"), py::arg("value") = 0, py::arg("id") = "")
        .def_property_readonly("id", [](const SketchConstraint& constraint) { return constraint.id.to_string(); })
        .def_readwrite("kind", &SketchConstraint::kind)
        .def_readwrite("targets", &SketchConstraint::targets)
        .def_readwrite("value", &SketchConstraint::value);
    py::class_<SketchProblem>(module, "SketchProblem")
        .def(py::init<>())
        .def_readwrite("plane", &SketchProblem::plane)
        .def_readwrite("entities", &SketchProblem::entities)
        .def_readwrite("constraints", &SketchProblem::constraints);
    py::enum_<SketchSolveState>(module, "SketchSolveState")
        .value("UNDERCONSTRAINED", SketchSolveState::Underconstrained)
        .value("FULLY_CONSTRAINED", SketchSolveState::FullyConstrained)
        .value("REDUNDANT", SketchSolveState::Redundant)
        .value("CONFLICTING", SketchSolveState::Conflicting)
        .value("FAILED", SketchSolveState::Failed);
    py::class_<SketchSolveReport>(module, "SketchSolveReport")
        .def_readonly("state", &SketchSolveReport::state)
        .def_readonly("remaining_degrees_of_freedom", &SketchSolveReport::remaining_degrees_of_freedom)
        .def_readonly("diagnostics", &SketchSolveReport::diagnostics)
        .def_property_readonly("conflicting_constraints", [](const SketchSolveReport& report) {
            return strings(report.conflicting_constraints);
        })
        .def_property_readonly("redundant_constraints", [](const SketchSolveReport& report) {
            return strings(report.redundant_constraints);
        });
    py::class_<SketchConstraintAdapter>(module, "SketchConstraintAdapter")
        .def("solve", &SketchConstraintAdapter::solve);
    module.def("new_sketch_solver", &make_planegcs_adapter, py::arg("tolerance_mm") = 0.001);
}
