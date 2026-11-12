#pragma once

#include "blendercad/document_id.hpp"

#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace blendercad {

// A literal length in mm or a reference to a named length parameter. This is
// deliberately not an expression parser or a solver-owned dimension.
using DepthInput = std::variant<double, std::string>;

struct ExtrusionFeature final {
    DocumentId sketch;
    DepthInput depth{1.0};
    friend bool operator==(const ExtrusionFeature&, const ExtrusionFeature&) = default;
};

struct SubtractiveExtrusionFeature final {
    DocumentId input;
    DocumentId sketch;
    DepthInput depth{1.0};
    friend bool operator==(const SubtractiveExtrusionFeature&, const SubtractiveExtrusionFeature&) = default;
};

using FeatureParameters = std::variant<ExtrusionFeature, SubtractiveExtrusionFeature>;

struct FeatureDefinition final {
    DocumentId id;
    std::string name;
    std::uint32_t version{1};
    DocumentId body;
    FeatureParameters parameters;
    bool suppressed{};
    friend bool operator==(const FeatureDefinition&, const FeatureDefinition&) = default;
};

[[nodiscard]] const DocumentId& feature_sketch(const FeatureDefinition& feature);
[[nodiscard]] const DepthInput& feature_depth(const FeatureDefinition& feature);
[[nodiscard]] std::optional<DocumentId> feature_input(const FeatureDefinition& feature);
[[nodiscard]] std::string feature_type(const FeatureDefinition& feature);
[[nodiscard]] std::vector<DocumentId> feature_dependencies(const FeatureDefinition& feature);

} // namespace blendercad
