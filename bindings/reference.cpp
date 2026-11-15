#include "blendercad/reference.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace blendercad;

void bind_reference(py::module_& module) {
    module.def("remap_reference", [](const PersistentReference& reference, const py::dict& mappings) {
        std::vector<std::pair<DocumentId, DocumentId>> identities;
        for (const auto& item : mappings) {
            identities.emplace_back(DocumentId::parse(py::cast<std::string>(item.first)),
                                    DocumentId::parse(py::cast<std::string>(item.second)));
        }
        return remap_reference(reference, identities);
    }, py::arg("reference"), py::arg("identities"));
    py::class_<ReferenceAnchor>(module, "ReferenceAnchor")
        .def(py::init([](const std::string& feature, std::string key) { return ReferenceAnchor{DocumentId::parse(feature), std::move(key)}; }))
        .def_property_readonly("feature_id", [](const ReferenceAnchor& anchor) { return anchor.feature.to_string(); })
        .def_readonly("key", &ReferenceAnchor::key);
    py::class_<PersistentReference>(module, "PersistentReference")
        .def(py::init([](const std::string& body, TopologyKind kind, const ReferenceAnchor& anchor) {
            PersistentReference result{DocumentId::parse(body), kind, anchor};
            validate_reference(result);
            return result;
        }))
        .def_property_readonly("body_id", [](const PersistentReference& reference) { return reference.body.to_string(); })
        .def_readonly("kind", &PersistentReference::kind)
        .def_property_readonly("anchor", [](const PersistentReference& reference) { return reference.anchor; });
    py::class_<ReferenceDefinition>(module, "ReferenceDefinition")
        .def_property_readonly("id", [](const ReferenceDefinition& reference) { return reference.id.to_string(); })
        .def_readonly("name", &ReferenceDefinition::name)
        .def_property_readonly("target", [](const ReferenceDefinition& reference) { return reference.target; });
    py::enum_<ReferenceState>(module, "ReferenceState")
        .value("RESOLVED", ReferenceState::Resolved).value("SPLIT", ReferenceState::Split)
        .value("MERGED", ReferenceState::Merged).value("DELETED", ReferenceState::Deleted)
        .value("AMBIGUOUS", ReferenceState::Ambiguous).value("UNAVAILABLE", ReferenceState::Unavailable);
    py::class_<ReferenceResolution>(module, "ReferenceResolution")
        .def_readonly("state", &ReferenceResolution::state)
        .def_property_readonly("candidates", [](const ReferenceResolution& result) { return result.candidates; })
        .def_readonly("diagnostic", &ReferenceResolution::diagnostic);
}
