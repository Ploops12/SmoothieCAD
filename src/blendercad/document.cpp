#include "blendercad/document.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <ranges>
#include <stdexcept>
#include <utility>
#include <unordered_set>
#include <unordered_map>

namespace blendercad {
namespace {

void validate_value(double value) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument("parameter values must be finite");
    }
}

void validate_name(std::string_view name) {
    if (name.empty()) throw std::invalid_argument("definition name cannot be empty");
    for (const unsigned char character : name) {
        if (character < 0x20 || character == 0x7f) throw std::invalid_argument("definition name cannot contain control characters");
    }
}

void validate_unit(ParameterUnit unit) {
    switch (unit) {
    case ParameterUnit::Millimetre: case ParameterUnit::Degree: case ParameterUnit::Unitless: return;
    }
    throw std::invalid_argument("invalid parameter unit");
}

std::unordered_set<std::string> validate_sketches(const DocumentId& document_id, const std::vector<SketchDefinition>& sketches) {
    std::unordered_set<std::string> identities{document_id.to_string()};
    auto insert = [&](const DocumentId& id) {
        if (!identities.insert(id.to_string()).second) throw std::invalid_argument("duplicate persistent identity in document: " + id.to_string());
    };
    for (const auto& sketch : sketches) {
        insert(sketch.id);
        validate_name(sketch.name);
        validate_sketch(sketch.problem);
        for (const auto& entity : sketch.problem.entities) insert(entity.id);
        for (const auto& constraint : sketch.problem.constraints) insert(constraint.id);
    }
    return identities;
}

double resolve_length(const DepthInput& input, const std::vector<Parameter>& parameters) {
    if (const auto* value = std::get_if<double>(&input)) {
        validate_value(*value);
        return *value;
    }
    const auto& name = std::get<std::string>(input);
    const auto found = std::ranges::find_if(parameters, [&](const auto& parameter) { return parameter.name == name; });
    if (found == parameters.end()) throw std::invalid_argument("unknown length parameter: " + name);
    if (found->unit != ParameterUnit::Millimetre) throw std::invalid_argument("feature depth requires a length parameter in mm");
    return found->value;
}

void validate_design(const DocumentId& id, const std::vector<Parameter>& parameters,
                     const std::vector<SketchDefinition>& sketches, const std::vector<FeatureDefinition>& features,
                     const std::optional<DocumentId>& rollback_before) {
    auto identities = validate_sketches(id, sketches);
    std::unordered_set<std::string> sketch_ids;
    for (const auto& sketch : sketches) sketch_ids.insert(sketch.id.to_string());
    std::unordered_map<std::string, const FeatureDefinition*> available;
    std::unordered_map<std::string, DocumentId> body_tips;
    auto insert = [&](const DocumentId& identifier) {
        if (!identities.insert(identifier.to_string()).second) throw std::invalid_argument("duplicate persistent identity: " + identifier.to_string());
    };
    for (const auto& feature : features) {
        insert(feature.id);
        validate_name(feature.name);
        if (feature.version != 1) throw std::invalid_argument("unsupported feature definition version");
        if (!sketch_ids.contains(feature_sketch(feature).to_string())) throw std::invalid_argument("feature references a missing sketch");
        (void)resolve_length(feature_depth(feature), parameters);
        const auto body = feature.body.to_string();
        if (const auto input = feature_input(feature)) {
            const auto found = available.find(input->to_string());
            if (found == available.end()) throw std::invalid_argument("feature dependency must precede its consumer");
            if (found->second->body != feature.body) throw std::invalid_argument("subtractive feature must retain its input body identity");
            if (body_tips.at(body) != *input) throw std::invalid_argument("feature input must be the current body history tip");
        } else {
            insert(feature.body);
        }
        body_tips.insert_or_assign(body, feature.id);
        available.emplace(feature.id.to_string(), &feature);
    }
    if (rollback_before && !available.contains(rollback_before->to_string())) throw std::invalid_argument("rollback marker references a missing feature");
}

} // namespace

std::string_view unit_name(ParameterUnit unit) noexcept {
    switch (unit) {
    case ParameterUnit::Millimetre: return "mm";
    case ParameterUnit::Degree: return "deg";
    case ParameterUnit::Unitless: return "unitless";
    }
    return "unitless";
}

ParameterUnit parse_unit(std::string_view unit) {
    if (unit == "mm") return ParameterUnit::Millimetre;
    if (unit == "deg") return ParameterUnit::Degree;
    if (unit == "unitless") return ParameterUnit::Unitless;
    throw std::invalid_argument("unsupported parameter unit: " + std::string(unit));
}

Document::Document() : id_(DocumentId::generate()) {}

Document::Document(DocumentId id) : id_(std::move(id)) {}

Document Document::restore(DocumentId id, std::uint64_t revision,
                           double tolerance_mm, std::vector<Parameter> parameters,
                           std::vector<SketchDefinition> sketches, std::vector<FeatureDefinition> features,
                           std::optional<DocumentId> rollback_before) {
    validate_value(tolerance_mm);
    if (tolerance_mm <= 0.0) throw std::invalid_argument("geometric tolerance must be positive");
    for (std::size_t index = 0; index < parameters.size(); ++index) {
        validate_name(parameters[index].name);
        validate_value(parameters[index].value);
        validate_unit(parameters[index].unit);
        if (std::ranges::any_of(parameters.begin(), parameters.begin() + static_cast<std::ptrdiff_t>(index),
                [&](const Parameter& parameter) { return parameter.name == parameters[index].name; })) {
            throw std::invalid_argument("duplicate parameter name in document snapshot");
        }
    }
    validate_design(id, parameters, sketches, features, rollback_before);
    Document document(std::move(id));
    document.revision_ = revision;
    document.geometric_tolerance_mm_ = tolerance_mm;
    document.parameters_ = std::move(parameters);
    document.sketches_ = std::move(sketches);
    document.features_ = std::move(features);
    document.rollback_before_ = std::move(rollback_before);
    return document;
}

void Document::set_geometric_tolerance_mm(double tolerance) {
    validate_value(tolerance);
    if (tolerance <= 0.0) throw std::invalid_argument("geometric tolerance must be positive");
    if (geometric_tolerance_mm_ == tolerance) return;
    ensure_revision_available();
    geometric_tolerance_mm_ = tolerance;
    ++revision_;
}

void Document::add_parameter(std::string name, double value, ParameterUnit unit) {
    validate_name(name);
    validate_value(value);
    validate_unit(unit);
    if (std::ranges::any_of(parameters_, [&](const Parameter& parameter) {
            return parameter.name == name;
        })) {
        throw std::invalid_argument("parameter name already exists: " + name);
    }
    ensure_revision_available();
    parameters_.push_back(Parameter{std::move(name), value, unit});
    ++revision_;
}

void Document::set_parameter_value(std::string_view name, double value) {
    validate_value(value);
    const auto found = std::ranges::find_if(parameters_, [&](const Parameter& parameter) {
        return parameter.name == name;
    });
    if (found == parameters_.end()) throw std::invalid_argument("unknown parameter: " + std::string(name));
    if (found->value == value) return;
    ensure_revision_available();
    found->value = value;
    ++revision_;
}

void Document::ensure_revision_available() const {
    if (revision_ == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("document revision exhausted; edit was not committed");
    }
}

const SketchDefinition& Document::sketch(const DocumentId& id) const {
    const auto found = std::ranges::find_if(sketches_, [&](const auto& sketch) { return sketch.id == id; });
    if (found == sketches_.end()) throw std::invalid_argument("unknown sketch identity: " + id.to_string());
    return *found;
}

void Document::publish_sketches(std::vector<SketchDefinition> sketches) {
    publish_design(std::move(sketches), features_, rollback_before_);
}

void Document::publish_design(std::vector<SketchDefinition> sketches, std::vector<FeatureDefinition> features,
                              std::optional<DocumentId> rollback_before) {
    validate_design(id_, parameters_, sketches, features, rollback_before);
    if (sketches == sketches_ && features == features_ && rollback_before == rollback_before_) return;
    ensure_revision_available();
    sketches_ = std::move(sketches);
    features_ = std::move(features);
    rollback_before_ = std::move(rollback_before);
    ++revision_;
}

DocumentId Document::add_sketch(std::string name, SketchProblem problem) {
    const auto id = DocumentId::generate();
    auto candidate = sketches_;
    candidate.push_back({id, std::move(name), std::move(problem)});
    publish_sketches(std::move(candidate));
    return id;
}

void Document::replace_sketch(const DocumentId& id, SketchProblem problem) {
    (void)sketch(id); // Fail before touching candidate data if identity is missing.
    auto candidate = sketches_;
    for (auto& sketch : candidate) {
        if (sketch.id == id) sketch.problem = std::move(problem);
    }
    publish_sketches(std::move(candidate));
}

void Document::rename_sketch(const DocumentId& id, std::string name) {
    (void)sketch(id);
    auto candidate = sketches_;
    for (auto& sketch : candidate) {
        if (sketch.id == id) sketch.name = std::move(name);
    }
    publish_sketches(std::move(candidate));
}

const FeatureDefinition& Document::feature(const DocumentId& id) const {
    const auto found = std::ranges::find_if(features_, [&](const auto& feature) { return feature.id == id; });
    if (found == features_.end()) throw std::invalid_argument("unknown feature identity: " + id.to_string());
    return *found;
}

double Document::length_value(const DepthInput& input) const { return resolve_length(input, parameters_); }

DocumentId Document::add_extrusion(std::string name, const DocumentId& sketch_id, DepthInput depth) {
    const auto id = DocumentId::generate();
    auto candidate = features_;
    candidate.push_back({id, std::move(name), 1, DocumentId::generate(), ExtrusionFeature{sketch_id, std::move(depth)}, false});
    publish_design(sketches_, std::move(candidate), rollback_before_);
    return id;
}

DocumentId Document::add_subtractive_extrusion(std::string name, const DocumentId& input,
                                               const DocumentId& sketch_id, DepthInput depth) {
    const auto body = feature(input).body;
    const auto id = DocumentId::generate();
    auto candidate = features_;
    candidate.push_back({id, std::move(name), 1, body, SubtractiveExtrusionFeature{input, sketch_id, std::move(depth)}, false});
    publish_design(sketches_, std::move(candidate), rollback_before_);
    return id;
}

void Document::set_feature_depth(const DocumentId& id, DepthInput depth) {
    (void)feature(id);
    auto candidate = features_;
    for (auto& feature : candidate) {
        if (feature.id == id) std::visit([&](auto& parameters) { parameters.depth = std::move(depth); }, feature.parameters);
    }
    publish_design(sketches_, std::move(candidate), rollback_before_);
}

void Document::set_feature_suppressed(const DocumentId& id, bool suppressed) {
    (void)feature(id);
    auto candidate = features_;
    for (auto& feature : candidate) if (feature.id == id) feature.suppressed = suppressed;
    publish_design(sketches_, std::move(candidate), rollback_before_);
}

void Document::set_rollback_before(std::optional<DocumentId> id) {
    publish_design(sketches_, features_, std::move(id));
}

void Document::reorder_features(const std::vector<DocumentId>& order) {
    if (order.size() != features_.size()) throw std::invalid_argument("feature order must be a complete permutation");
    std::vector<FeatureDefinition> candidate;
    for (const auto& id : order) candidate.push_back(feature(id));
    publish_design(sketches_, std::move(candidate), rollback_before_);
}

void Document::erase_feature(const DocumentId& id, bool cascade) {
    (void)feature(id);
    std::unordered_set<std::string> removed{id.to_string()};
    std::vector<FeatureDefinition> candidate;
    for (const auto& feature : features_) {
        if (const auto input = feature_input(feature); input && removed.contains(input->to_string())) {
            if (!cascade) throw std::invalid_argument("feature has dependent: " + feature.id.to_string());
            removed.insert(feature.id.to_string());
        }
        if (!removed.contains(feature.id.to_string())) candidate.push_back(feature);
    }
    auto rollback = rollback_before_;
    if (rollback && removed.contains(rollback->to_string())) {
        if (!cascade) throw std::invalid_argument("feature is the rollback marker; clear rollback or request cascade deletion");
        rollback.reset();
    }
    publish_design(sketches_, std::move(candidate), std::move(rollback));
}

void Document::erase_sketch(const DocumentId& id, bool cascade) {
    (void)sketch(id);
    auto sketches = sketches_;
    std::erase_if(sketches, [&](const auto& sketch) { return sketch.id == id; });
    std::unordered_set<std::string> removed;
    std::vector<FeatureDefinition> features;
    for (const auto& feature : features_) {
        const auto input = feature_input(feature);
        if (feature_sketch(feature) == id || (input && removed.contains(input->to_string()))) {
            if (!cascade) throw std::invalid_argument("sketch has dependent feature: " + feature.id.to_string());
            removed.insert(feature.id.to_string());
        } else features.push_back(feature);
    }
    auto rollback = rollback_before_;
    if (rollback && removed.contains(rollback->to_string())) rollback.reset();
    publish_design(std::move(sketches), std::move(features), std::move(rollback));
}

} // namespace blendercad
