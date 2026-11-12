#include "blendercad/feature.hpp"

namespace blendercad {

const DocumentId& feature_sketch(const FeatureDefinition& feature) {
    return std::visit([](const auto& parameters) -> const DocumentId& { return parameters.sketch; }, feature.parameters);
}

const DepthInput& feature_depth(const FeatureDefinition& feature) {
    return std::visit([](const auto& parameters) -> const DepthInput& { return parameters.depth; }, feature.parameters);
}

std::optional<DocumentId> feature_input(const FeatureDefinition& feature) {
    if (const auto* cut = std::get_if<SubtractiveExtrusionFeature>(&feature.parameters)) return cut->input;
    return {};
}

std::string feature_type(const FeatureDefinition& feature) {
    return std::holds_alternative<ExtrusionFeature>(feature.parameters) ? "extrusion" : "subtractive_extrusion";
}

std::vector<DocumentId> feature_dependencies(const FeatureDefinition& feature) {
    std::vector<DocumentId> dependencies{feature_sketch(feature)};
    if (const auto input = feature_input(feature)) dependencies.push_back(*input);
    return dependencies;
}

} // namespace blendercad
