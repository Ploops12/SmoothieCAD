#include "blendercad/feature.hpp"

#include <algorithm>
#include <stdexcept>

namespace blendercad {

std::optional<DocumentId> feature_sketch(const FeatureDefinition& feature) {
    if (const auto* extrusion = std::get_if<ExtrusionFeature>(&feature.parameters)) return extrusion->sketch;
    if (const auto* cut = std::get_if<SubtractiveExtrusionFeature>(&feature.parameters)) return cut->sketch;
    return {};
}

const DepthInput& feature_depth(const FeatureDefinition& feature) {
    if (const auto* extrusion = std::get_if<ExtrusionFeature>(&feature.parameters)) return extrusion->depth;
    if (const auto* cut = std::get_if<SubtractiveExtrusionFeature>(&feature.parameters)) return cut->depth;
    throw std::invalid_argument("fillet features have a radius, not an extrusion depth");
}

const DepthInput& feature_length(const FeatureDefinition& feature) {
    if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) return fillet->radius;
    return feature_depth(feature);
}

const DepthInput& feature_radius(const FeatureDefinition& feature) {
    if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) return fillet->radius;
    throw std::invalid_argument("only fillet features have a fillet radius");
}

std::optional<DocumentId> feature_input(const FeatureDefinition& feature) {
    if (const auto* cut = std::get_if<SubtractiveExtrusionFeature>(&feature.parameters)) return cut->input;
    if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) return fillet->input;
    return {};
}

std::string feature_type(const FeatureDefinition& feature) {
    if (std::holds_alternative<ExtrusionFeature>(feature.parameters)) return "extrusion";
    if (std::holds_alternative<SubtractiveExtrusionFeature>(feature.parameters)) return "subtractive_extrusion";
    return "fillet";
}

std::vector<DocumentId> feature_dependencies(const FeatureDefinition& feature) {
    std::vector<DocumentId> dependencies;
    auto add = [&](const DocumentId& id) {
        if (std::find(dependencies.begin(), dependencies.end(), id) == dependencies.end()) dependencies.push_back(id);
    };
    if (const auto sketch = feature_sketch(feature)) add(*sketch);
    if (const auto input = feature_input(feature)) add(*input);
    if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) {
        for (const auto& edge : fillet->edges) add(edge.anchor.feature);
    }
    return dependencies;
}

} // namespace blendercad
