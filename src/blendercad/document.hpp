#pragma once

#include "blendercad/document_id.hpp"
#include "blendercad/sketch.hpp"
#include "blendercad/feature.hpp"
#include "blendercad/reference.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace blendercad {

enum class ParameterUnit {
    Millimetre,
    Degree,
    Unitless,
};

[[nodiscard]] std::string_view unit_name(ParameterUnit unit) noexcept;
[[nodiscard]] ParameterUnit parse_unit(std::string_view unit);

struct Parameter final {
    std::string name;
    double value{};
    ParameterUnit unit{ParameterUnit::Unitless};
    // Expression values are disposable evaluations; the expression itself is
    // the definition. A literal has no expression and retains its saved value.
    std::optional<std::string> expression{};
    friend bool operator==(const Parameter&, const Parameter&) = default;
};

// Owns persistent design definitions. Evaluated kernel objects are deliberately
// held by GeometryAdapter and are not part of this snapshot.
class Document final {
public:
    static constexpr std::uint32_t current_schema_version = 5;
    static constexpr double default_geometric_tolerance_mm = 0.001;

    Document();
    explicit Document(DocumentId id);
    static Document restore(DocumentId id, std::uint64_t revision,
                            double tolerance_mm, std::vector<Parameter> parameters,
                            std::vector<SketchDefinition> sketches = {},
                            std::vector<FeatureDefinition> features = {},
                            std::optional<DocumentId> rollback_before = {},
                            std::vector<ReferenceDefinition> references = {});

    [[nodiscard]] const DocumentId& id() const noexcept { return id_; }
    [[nodiscard]] std::uint32_t schema_version() const noexcept { return current_schema_version; }
    [[nodiscard]] std::uint64_t revision() const noexcept { return revision_; }
    [[nodiscard]] double geometric_tolerance_mm() const noexcept { return geometric_tolerance_mm_; }
    [[nodiscard]] const std::vector<Parameter>& parameters() const noexcept { return parameters_; }
    [[nodiscard]] const std::vector<SketchDefinition>& sketches() const noexcept { return sketches_; }
    [[nodiscard]] const SketchDefinition& sketch(const DocumentId& id) const;
    [[nodiscard]] const std::vector<FeatureDefinition>& features() const noexcept { return features_; }
    [[nodiscard]] const FeatureDefinition& feature(const DocumentId& id) const;
    [[nodiscard]] const std::optional<DocumentId>& rollback_before() const noexcept { return rollback_before_; }
    [[nodiscard]] const std::vector<ReferenceDefinition>& references() const noexcept { return references_; }
    [[nodiscard]] const ReferenceDefinition& reference(const DocumentId& id) const;
    [[nodiscard]] double length_value(const DepthInput& input) const;

    void set_geometric_tolerance_mm(double tolerance);
    void add_parameter(std::string name, double value, ParameterUnit unit);
    void add_parameter_expression(std::string name, std::string expression, ParameterUnit unit);
    void set_parameter_value(std::string_view name, double value);
    void set_parameter_expression(std::string_view name, std::string expression);
    DocumentId add_sketch(std::string name, SketchProblem problem);
    void replace_sketch(const DocumentId& id, SketchProblem problem);
    void rename_sketch(const DocumentId& id, std::string name);
    DocumentId add_extrusion(std::string name, const DocumentId& sketch, DepthInput depth);
    DocumentId add_subtractive_extrusion(std::string name, const DocumentId& input,
                                         const DocumentId& sketch, DepthInput depth);
    void set_feature_depth(const DocumentId& id, DepthInput depth);
    void set_feature_suppressed(const DocumentId& id, bool suppressed);
    void reorder_features(const std::vector<DocumentId>& order);
    void set_rollback_before(std::optional<DocumentId> id);
    void erase_feature(const DocumentId& id, bool cascade = false);
    void erase_sketch(const DocumentId& id, bool cascade = false);
    DocumentId add_reference(std::string name, PersistentReference target);
    void repair_reference(const DocumentId& id, PersistentReference target);
    void erase_reference(const DocumentId& id);
    friend bool operator==(const Document&, const Document&) = default;

private:
    DocumentId id_;
    std::uint64_t revision_{0};
    double geometric_tolerance_mm_{default_geometric_tolerance_mm};
    std::vector<Parameter> parameters_;
    std::vector<SketchDefinition> sketches_;
    std::vector<FeatureDefinition> features_;
    std::optional<DocumentId> rollback_before_;
    std::vector<ReferenceDefinition> references_;
    void ensure_revision_available() const;
    void publish_parameters(std::vector<Parameter> parameters);
    void publish_sketches(std::vector<SketchDefinition> sketches);
    void publish_design(std::vector<SketchDefinition> sketches, std::vector<FeatureDefinition> features,
                        std::optional<DocumentId> rollback_before);
    void publish_design(std::vector<SketchDefinition> sketches, std::vector<FeatureDefinition> features,
                        std::optional<DocumentId> rollback_before, std::vector<ReferenceDefinition> references);
};

} // namespace blendercad
