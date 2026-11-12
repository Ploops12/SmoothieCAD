#include "blendercad/evaluation.hpp"
#include "display.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace blendercad;

void bind_evaluation(py::module_& module) {
    py::enum_<EvaluationState>(module, "EvaluationState")
        .value("UP_TO_DATE", EvaluationState::UpToDate)
        .value("FAILED", EvaluationState::Failed)
        .value("BLOCKED", EvaluationState::Blocked)
        .value("SUPPRESSED", EvaluationState::Suppressed)
        .value("ROLLED_BACK", EvaluationState::RolledBack);
    py::class_<FeatureEvaluation>(module, "FeatureEvaluation")
        .def_property_readonly("feature_id", [](const FeatureEvaluation& result) { return result.feature.to_string(); })
        .def_property_readonly("body_id", [](const FeatureEvaluation& result) { return result.body.to_string(); })
        .def_readonly("state", &FeatureEvaluation::state)
        .def_readonly("diagnostic", &FeatureEvaluation::diagnostic)
        .def_property_readonly("sketch_report", [](const FeatureEvaluation& result) { return result.sketch_report; })
        .def_readonly("reused", &FeatureEvaluation::reused)
        .def_readonly("has_last_valid", &FeatureEvaluation::has_last_valid);
    py::class_<BodyEvaluation>(module, "BodyEvaluation")
        .def_property_readonly("body_id", [](const BodyEvaluation& result) { return result.body.to_string(); })
        .def_property_readonly("feature_id", [](const BodyEvaluation& result) { return result.feature.to_string(); })
        .def_readonly("state", &BodyEvaluation::state)
        .def_readonly("has_display", &BodyEvaluation::has_display);
    py::class_<EvaluationReport>(module, "EvaluationReport")
        .def_property_readonly("document_id", [](const EvaluationReport& result) { return result.document.to_string(); })
        .def_readonly("revision", &EvaluationReport::revision)
        .def_property_readonly("features", [](const EvaluationReport& result) { return result.features; })
        .def_property_readonly("bodies", [](const EvaluationReport& result) { return result.bodies; })
        .def_readonly("computed_count", &EvaluationReport::computed_count)
        .def_readonly("reused_count", &EvaluationReport::reused_count);
    py::class_<FeatureEvaluator>(module, "FeatureEvaluator")
        .def(py::init<>())
        .def("fork", &FeatureEvaluator::fork,
             "Create an isolated evaluator state sharing immutable cached geometry ownership.")
        .def("evaluate", &FeatureEvaluator::evaluate)
        .def("matches_document", &FeatureEvaluator::matches_document)
        .def("volume_mm3", [](const FeatureEvaluator& evaluator, const Document& document, const std::string& body) {
            return evaluator.volume_mm3(document, DocumentId::parse(body));
        })
        .def("write_step", [](const FeatureEvaluator& evaluator, const Document& document,
                              const std::string& body, const std::string& path) {
            evaluator.write_step(document, DocumentId::parse(body), path);
        })
        .def("tessellate", [](const FeatureEvaluator& evaluator, const Document& document,
                              const std::string& body, double linear, double angular) {
            auto display = evaluator.tessellate(document, DocumentId::parse(body), linear, angular);
            auto result = blendercad_bindings::display_arrays(display.mesh);
            result["document_id"] = display.document.to_string();
            result["body_id"] = display.body.to_string();
            result["feature_id"] = display.feature.to_string();
            result["geometry_feature_id"] = display.geometry_feature.to_string();
            result["revision"] = display.revision;
            result["geometry_revision"] = display.geometry_revision;
            result["geometry_generation"] = display.geometry_generation;
            result["current"] = display.current;
            result["linear_deflection_mm"] = linear;
            result["angular_deflection_rad"] = angular;
            return result;
        }, py::arg("document"), py::arg("body"), py::arg("linear_deflection_mm") = 0.01,
           py::arg("angular_deflection_rad") = 0.2);
}
