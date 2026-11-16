#include "blendercad/document.hpp"
#include "blendercad/parameter_expression.hpp"

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

void normalize_sketch_parameters(SketchProblem& problem) {
    for (auto& constraint : problem.constraints) {
        if (constraint.parameter) constraint.value = 0;
    }
}

std::string sketch_dimension_context(const SketchDefinition& sketch, const SketchConstraint& constraint) {
    auto result = "sketch '" + sketch.name + "' (" + sketch.id.to_string()
        + "), constraint " + constraint.id.to_string();
    if (constraint.parameter) result += ", parameter '" + *constraint.parameter + "'";
    return result + ": ";
}

double resolve_sketch_parameter(const SketchDefinition& sketch, const SketchConstraint& constraint,
                                const std::vector<Parameter>& parameters) {
    const auto& name = *constraint.parameter;
    const auto found = std::ranges::find_if(parameters, [&](const auto& parameter) { return parameter.name == name; });
    if (found == parameters.end()) {
        throw std::invalid_argument(sketch_dimension_context(sketch, constraint) + "unknown dimension parameter");
    }
    if (found->unit != ParameterUnit::Millimetre) {
        throw std::invalid_argument(sketch_dimension_context(sketch, constraint) + "sketch dimensions require a length parameter in mm");
    }
    return found->value;
}

void validate_support_fields(const SketchDefinition& sketch) {
    if (!sketch.support) return;
    const auto& support = *sketch.support;
    validate_reference(support.face);
    if (support.face.kind != TopologyKind::Face) throw std::invalid_argument("sketch support requires a persistent FACE reference");
    const auto& x = support.x_direction;
    const auto length = std::hypot(x.x, x.y, x.z);
    if (!std::isfinite(x.x) || !std::isfinite(x.y) || !std::isfinite(x.z)
            || !std::isfinite(length) || length == 0) {
        throw std::invalid_argument("sketch support x_direction must be finite and nonzero");
    }
    if (!std::isfinite(support.offset_u_mm) || !std::isfinite(support.offset_v_mm)
            || !std::isfinite(support.normal_offset_mm)) {
        throw std::invalid_argument("sketch support offsets must be finite");
    }
    if (sketch.problem.plane != SketchPlane{}) {
        throw std::invalid_argument("face-attached sketches require a canonical identity placeholder plane");
    }
}

std::vector<DocumentId> design_feature_dependencies(const FeatureDefinition& feature,
                                                    const std::vector<SketchDefinition>& sketches) {
    auto dependencies = blendercad::feature_dependencies(feature);
    auto add = [&](const DocumentId& id) {
        if (std::ranges::find(dependencies, id) == dependencies.end()) dependencies.push_back(id);
    };
    if (const auto id = feature_sketch(feature)) {
        const auto found = std::ranges::find_if(sketches, [&](const auto& sketch) { return sketch.id == *id; });
        if (found != sketches.end() && found->support) {
            add(found->support->feature);
            add(found->support->face.anchor.feature);
        }
    }
    return dependencies;
}

std::unordered_set<std::string> validate_sketches(const DocumentId& document_id,
                                               const std::vector<SketchDefinition>& sketches,
                                               const std::vector<Parameter>& parameters) {
    std::unordered_set<std::string> identities{document_id.to_string()};
    auto insert = [&](const DocumentId& id) {
        if (!identities.insert(id.to_string()).second) throw std::invalid_argument("duplicate persistent identity in document: " + id.to_string());
    };
    for (const auto& sketch : sketches) {
        insert(sketch.id);
        validate_name(sketch.name);
        validate_support_fields(sketch);
        validate_sketch(sketch.problem, true);
        for (const auto& entity : sketch.problem.entities) insert(entity.id);
        for (const auto& constraint : sketch.problem.constraints) {
            insert(constraint.id);
            // Range/feasibility belongs to evaluation. Invalid currently
            // resolved dimensions remain repairable persistent definitions.
            if (constraint.parameter) (void)resolve_sketch_parameter(sketch, constraint, parameters);
        }
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
    if (found->unit != ParameterUnit::Millimetre) throw std::invalid_argument("feature dimension requires a length parameter in mm");
    return found->value;
}

void validate_design(const DocumentId& id, const std::vector<Parameter>& parameters,
                     const std::vector<SketchDefinition>& sketches, const std::vector<FeatureDefinition>& features,
                     const std::optional<DocumentId>& rollback_before, const std::vector<ReferenceDefinition>& references) {
    auto identities = validate_sketches(id, sketches, parameters);
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
        if (const auto sketch = feature_sketch(feature); sketch && !sketch_ids.contains(sketch->to_string())) {
            throw std::invalid_argument("feature references a missing sketch");
        }
        for (const auto& dependency : design_feature_dependencies(feature, sketches)) {
            if (!sketch_ids.contains(dependency.to_string()) && !available.contains(dependency.to_string())) {
                throw std::invalid_argument("feature dependency, including sketch support, must precede its consumer");
            }
        }
        (void)resolve_length(feature_length(feature), parameters);
        const auto body = feature.body.to_string();
        if (const auto input = feature_input(feature)) {
            const auto found = available.find(input->to_string());
            if (found == available.end()) throw std::invalid_argument("feature dependency must precede its consumer");
            if (found->second->body != feature.body) throw std::invalid_argument("dependent feature must retain its input body identity");
            if (body_tips.at(body) != *input) throw std::invalid_argument("feature input must be the current body history tip");
        } else {
            insert(feature.body);
        }
        if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) {
            if (fillet->edges.empty()) throw std::invalid_argument("fillet requires at least one persistent edge reference");
            std::unordered_set<std::string> ancestors;
            auto ancestor = std::optional{fillet->input};
            while (ancestor) {
                const auto& source = *available.at(ancestor->to_string());
                ancestors.insert(ancestor->to_string());
                ancestor = feature_input(source);
            }
            for (std::size_t index = 0; index < fillet->edges.size(); ++index) {
                const auto& edge = fillet->edges[index];
                validate_reference(edge);
                if (edge.kind != TopologyKind::Edge) throw std::invalid_argument("fillet targets must be persistent EDGE references");
                if (edge.body != feature.body) throw std::invalid_argument("fillet edge reference belongs to a different body");
                const auto creator = available.find(edge.anchor.feature.to_string());
                if (creator == available.end()) throw std::invalid_argument("fillet edge anchor creator must precede its consumer");
                if (creator->second->body != feature.body || !ancestors.contains(edge.anchor.feature.to_string())) {
                    throw std::invalid_argument("fillet edge anchor creator must belong to its input ancestor chain");
                }
                if (std::find(fillet->edges.begin(), fillet->edges.begin() + static_cast<std::ptrdiff_t>(index), edge)
                        != fillet->edges.begin() + static_cast<std::ptrdiff_t>(index)) {
                    throw std::invalid_argument("fillet contains a duplicate persistent edge reference");
                }
            }
        }
        body_tips.insert_or_assign(body, feature.id);
        available.emplace(feature.id.to_string(), &feature);
    }
    for (const auto& sketch : sketches) {
        if (!sketch.support) continue;
        const auto& support = *sketch.support;
        const auto context = available.find(support.feature.to_string());
        const auto creator = available.find(support.face.anchor.feature.to_string());
        if (context == available.end() || creator == available.end()) {
            throw std::invalid_argument("sketch support context or face creator references a missing feature");
        }
        if (context->second->body != support.face.body || creator->second->body != support.face.body) {
            throw std::invalid_argument("sketch support face body must match its context and creator");
        }
        auto ancestor = std::optional{support.feature};
        bool found = false;
        while (ancestor) {
            if (*ancestor == support.face.anchor.feature) { found = true; break; }
            ancestor = feature_input(*available.at(ancestor->to_string()));
        }
        if (!found) throw std::invalid_argument("sketch support face creator must belong to its context input ancestor chain");
    }
    if (rollback_before && !available.contains(rollback_before->to_string())) throw std::invalid_argument("rollback marker references a missing feature");
    for (const auto& reference : references) {
        insert(reference.id);
        validate_name(reference.name);
        validate_reference(reference.target);
        const auto anchor = available.find(reference.target.anchor.feature.to_string());
        if (anchor == available.end()) throw std::invalid_argument("reference anchors a missing feature");
        if (anchor->second->body != reference.target.body) throw std::invalid_argument("reference body does not match its anchor feature");
    }
}

struct DeletionClosure final {
    std::unordered_set<std::string> sketches;
    std::unordered_set<std::string> features;
};

DeletionClosure deletion_closure(const std::vector<SketchDefinition>& sketches,
                                 const std::vector<FeatureDefinition>& features, DeletionClosure removed) {
    bool changed = true;
    while (changed) {
        changed = false;
        for (const auto& sketch : sketches) {
            if (sketch.support && (removed.features.contains(sketch.support->feature.to_string())
                    || removed.features.contains(sketch.support->face.anchor.feature.to_string()))) {
                changed = removed.sketches.insert(sketch.id.to_string()).second || changed;
            }
        }
        for (const auto& feature : features) {
            if (std::ranges::any_of(design_feature_dependencies(feature, sketches), [&](const auto& dependency) {
                    return removed.sketches.contains(dependency.to_string()) || removed.features.contains(dependency.to_string());
                })) {
                changed = removed.features.insert(feature.id.to_string()).second || changed;
            }
        }
    }
    return removed;
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
                           std::optional<DocumentId> rollback_before, std::vector<ReferenceDefinition> references) {
    validate_value(tolerance_mm);
    if (tolerance_mm <= 0.0) throw std::invalid_argument("geometric tolerance must be positive");
    for (std::size_t index = 0; index < parameters.size(); ++index) {
        validate_name(parameters[index].name);
        // The saved expression, not any incoming evaluation cache, owns the
        // definition. Literal values still require a finite saved value.
        if (!parameters[index].expression) validate_value(parameters[index].value);
        validate_unit(parameters[index].unit);
        if (std::ranges::any_of(parameters.begin(), parameters.begin() + static_cast<std::ptrdiff_t>(index),
                [&](const Parameter& parameter) { return parameter.name == parameters[index].name; })) {
            throw std::invalid_argument("duplicate parameter name in document snapshot");
        }
    }
    resolve_parameter_expressions(parameters);
    for (auto& sketch : sketches) normalize_sketch_parameters(sketch.problem);
    validate_design(id, parameters, sketches, features, rollback_before, references);
    Document document(std::move(id));
    document.revision_ = revision;
    document.geometric_tolerance_mm_ = tolerance_mm;
    document.parameters_ = std::move(parameters);
    document.sketches_ = std::move(sketches);
    document.features_ = std::move(features);
    document.rollback_before_ = std::move(rollback_before);
    document.references_ = std::move(references);
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
    auto candidate = parameters_;
    candidate.push_back(Parameter{std::move(name), value, unit});
    publish_parameters(std::move(candidate));
}

void Document::add_parameter_expression(std::string name, std::string expression, ParameterUnit unit) {
    validate_name(name);
    validate_unit(unit);
    if (std::ranges::any_of(parameters_, [&](const Parameter& parameter) { return parameter.name == name; })) {
        throw std::invalid_argument("parameter name already exists: " + name);
    }
    auto candidate = parameters_;
    candidate.push_back(Parameter{std::move(name), 0, unit, std::move(expression)});
    publish_parameters(std::move(candidate));
}

void Document::set_parameter_value(std::string_view name, double value) {
    validate_value(value);
    const auto found = std::ranges::find_if(parameters_, [&](const Parameter& parameter) {
        return parameter.name == name;
    });
    if (found == parameters_.end()) throw std::invalid_argument("unknown parameter: " + std::string(name));
    if (found->value == value && !found->expression) return;
    auto candidate = parameters_;
    auto& parameter = candidate[static_cast<std::size_t>(found - parameters_.begin())];
    parameter.value = value;
    parameter.expression.reset();
    publish_parameters(std::move(candidate));
}

void Document::set_parameter_expression(std::string_view name, std::string expression) {
    const auto found = std::ranges::find_if(parameters_, [&](const Parameter& parameter) {
        return parameter.name == name;
    });
    if (found == parameters_.end()) throw std::invalid_argument("unknown parameter: " + std::string(name));
    auto candidate = parameters_;
    candidate[static_cast<std::size_t>(found - parameters_.begin())].expression = std::move(expression);
    publish_parameters(std::move(candidate));
}

void Document::publish_parameters(std::vector<Parameter> parameters) {
    // A changed upstream literal can invalidate a formerly valid downstream
    // expression. Resolve on detached definitions before revision/publication.
    resolve_parameter_expressions(parameters);
    if (parameters == parameters_) return;
    ensure_revision_available();
    parameters_ = std::move(parameters);
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

SketchProblem Document::evaluated_sketch(const DocumentId& id) const {
    if (sketch(id).support) {
        throw std::invalid_argument("face-attached sketch requires FeatureEvaluator.evaluated_sketch to resolve its support plane");
    }
    return parameterized_sketch(id);
}

SketchProblem Document::parameterized_sketch(const DocumentId& id) const {
    const auto& definition = sketch(id);
    auto resolved = definition.problem;
    for (auto& constraint : resolved.constraints) {
        if (!constraint.parameter) continue;
        constraint.value = resolve_sketch_parameter(definition, constraint, parameters_);
        try { validate_sketch_constraint_value(constraint.kind, constraint.value); }
        catch (const std::invalid_argument& error) {
            throw std::invalid_argument(sketch_dimension_context(definition, constraint) + error.what());
        }
        constraint.parameter.reset();
    }
    // Retain the solver-facing validation boundary even when no bindings are
    // present. Entity/constraint identities and initial geometry are unchanged.
    try { validate_sketch(resolved); }
    catch (const std::invalid_argument& error) {
        throw std::invalid_argument("sketch '" + definition.name + "' (" + id.to_string() + "): " + error.what());
    }
    return resolved;
}

void Document::publish_sketches(std::vector<SketchDefinition> sketches) {
    publish_design(std::move(sketches), features_, rollback_before_);
}

void Document::publish_design(std::vector<SketchDefinition> sketches, std::vector<FeatureDefinition> features,
                              std::optional<DocumentId> rollback_before) {
    publish_design(std::move(sketches), std::move(features), std::move(rollback_before), references_);
}

void Document::publish_design(std::vector<SketchDefinition> sketches, std::vector<FeatureDefinition> features,
                              std::optional<DocumentId> rollback_before, std::vector<ReferenceDefinition> references) {
    for (auto& sketch : sketches) normalize_sketch_parameters(sketch.problem);
    validate_design(id_, parameters_, sketches, features, rollback_before, references);
    if (sketches == sketches_ && features == features_ && rollback_before == rollback_before_ && references == references_) return;
    ensure_revision_available();
    sketches_ = std::move(sketches);
    features_ = std::move(features);
    rollback_before_ = std::move(rollback_before);
    references_ = std::move(references);
    ++revision_;
}

DocumentId Document::add_sketch(std::string name, SketchProblem problem, std::optional<SketchSupport> support) {
    const auto id = DocumentId::generate();
    auto candidate = sketches_;
    candidate.push_back({id, std::move(name), std::move(problem), std::move(support)});
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

void Document::set_sketch_support(const DocumentId& id, SketchSupport support) {
    (void)sketch(id);
    auto candidate = sketches_;
    for (auto& sketch : candidate) {
        if (sketch.id != id) continue;
        sketch.support = std::move(support);
        sketch.problem.plane = SketchPlane{};
    }
    publish_sketches(std::move(candidate));
}

void Document::detach_sketch(const DocumentId& id, SketchPlane plane) {
    if (!sketch(id).support) throw std::invalid_argument("sketch is not face-attached");
    auto candidate = sketches_;
    for (auto& sketch : candidate) {
        if (sketch.id != id) continue;
        sketch.support.reset();
        sketch.problem.plane = std::move(plane);
    }
    publish_sketches(std::move(candidate));
}

void Document::set_sketch_dimension(const DocumentId& sketch_id, const DocumentId& constraint_id, DepthInput value) {
    const auto& definition = sketch(sketch_id);
    const auto found = std::ranges::find_if(definition.problem.constraints, [&](const auto& constraint) {
        return constraint.id == constraint_id;
    });
    if (found == definition.problem.constraints.end()) {
        throw std::invalid_argument("unknown constraint " + constraint_id.to_string() + " in sketch " + sketch_id.to_string());
    }
    if (!is_sketch_dimension(found->kind)) {
        throw std::invalid_argument(sketch_dimension_context(definition, *found) + "target is not a dimensional constraint");
    }
    auto candidate = sketches_;
    for (auto& sketch : candidate) {
        if (sketch.id != sketch_id) continue;
        for (auto& constraint : sketch.problem.constraints) {
            if (constraint.id != constraint_id) continue;
            if (const auto* literal = std::get_if<double>(&value)) {
                constraint.value = *literal;
                constraint.parameter.reset();
            } else {
                constraint.parameter = std::get<std::string>(std::move(value));
                constraint.value = 0;
            }
        }
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

std::vector<DocumentId> Document::feature_dependencies(const DocumentId& id) const {
    return design_feature_dependencies(feature(id), sketches_);
}

const ReferenceDefinition& Document::reference(const DocumentId& id) const {
    const auto found = std::ranges::find_if(references_, [&](const auto& reference) { return reference.id == id; });
    if (found == references_.end()) throw std::invalid_argument("unknown reference identity: " + id.to_string());
    return *found;
}

DocumentId Document::add_reference(std::string name, PersistentReference target) {
    const auto id = DocumentId::generate();
    auto candidate = references_;
    candidate.push_back({id, std::move(name), std::move(target)});
    publish_design(sketches_, features_, rollback_before_, std::move(candidate));
    return id;
}

void Document::repair_reference(const DocumentId& id, PersistentReference target) {
    (void)reference(id);
    auto candidate = references_;
    for (auto& reference : candidate) if (reference.id == id) reference.target = std::move(target);
    publish_design(sketches_, features_, rollback_before_, std::move(candidate));
}

void Document::erase_reference(const DocumentId& id) {
    (void)reference(id);
    auto candidate = references_;
    std::erase_if(candidate, [&](const auto& reference) { return reference.id == id; });
    publish_design(sketches_, features_, rollback_before_, std::move(candidate));
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
    (void)feature_depth(feature(id));
    auto candidate = features_;
    for (auto& feature : candidate) {
        if (feature.id != id) continue;
        if (auto* extrusion = std::get_if<ExtrusionFeature>(&feature.parameters)) extrusion->depth = std::move(depth);
        else std::get<SubtractiveExtrusionFeature>(feature.parameters).depth = std::move(depth);
    }
    publish_design(sketches_, std::move(candidate), rollback_before_);
}

DocumentId Document::add_fillet(std::string name, const DocumentId& input,
                                std::vector<PersistentReference> edges, DepthInput radius) {
    const auto body = feature(input).body;
    const auto id = DocumentId::generate();
    auto candidate = features_;
    candidate.push_back({id, std::move(name), 1, body,
                         FilletFeature{input, std::move(radius), std::move(edges)}, false});
    publish_design(sketches_, std::move(candidate), rollback_before_);
    return id;
}

void Document::set_fillet_radius(const DocumentId& id, DepthInput radius) {
    (void)feature_radius(feature(id));
    auto candidate = features_;
    for (auto& feature : candidate) {
        if (feature.id == id) std::get<FilletFeature>(feature.parameters).radius = std::move(radius);
    }
    publish_design(sketches_, std::move(candidate), rollback_before_);
}

void Document::set_fillet_edges(const DocumentId& id, std::vector<PersistentReference> edges) {
    (void)feature_radius(feature(id));
    auto candidate = features_;
    for (auto& feature : candidate) {
        if (feature.id == id) std::get<FilletFeature>(feature.parameters).edges = std::move(edges);
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
    auto removed = deletion_closure(sketches_, features_, {{}, {id.to_string()}});
    if (!cascade) {
        for (const auto& sketch : sketches_) {
            if (removed.sketches.contains(sketch.id.to_string())) {
                throw std::invalid_argument("feature has dependent face-attached sketch: " + sketch.id.to_string());
            }
        }
        for (const auto& feature : features_) {
            if (feature.id != id && removed.features.contains(feature.id.to_string())) {
                throw std::invalid_argument("feature has dependent: " + feature.id.to_string());
            }
        }
    }
    auto sketches = sketches_;
    std::erase_if(sketches, [&](const auto& sketch) { return removed.sketches.contains(sketch.id.to_string()); });
    std::vector<FeatureDefinition> candidate;
    for (const auto& feature : features_) {
        if (!removed.features.contains(feature.id.to_string())) candidate.push_back(feature);
    }
    auto rollback = rollback_before_;
    if (rollback && removed.features.contains(rollback->to_string())) {
        if (!cascade) throw std::invalid_argument("feature is the rollback marker; clear rollback or request cascade deletion");
        rollback.reset();
    }
    auto references = references_;
    std::erase_if(references, [&](const auto& reference) {
        if (!removed.features.contains(reference.target.anchor.feature.to_string())) return false;
        if (!cascade) throw std::invalid_argument("feature has dependent reference: " + reference.id.to_string());
        return true;
    });
    publish_design(std::move(sketches), std::move(candidate), std::move(rollback), std::move(references));
}

void Document::erase_sketch(const DocumentId& id, bool cascade) {
    (void)sketch(id);
    auto removed = deletion_closure(sketches_, features_, {{id.to_string()}, {}});
    if (!cascade && !removed.features.empty()) {
        const auto found = std::ranges::find_if(features_, [&](const auto& feature) {
            return removed.features.contains(feature.id.to_string());
        });
        throw std::invalid_argument("sketch has dependent feature: " + found->id.to_string());
    }
    auto sketches = sketches_;
    std::erase_if(sketches, [&](const auto& sketch) { return removed.sketches.contains(sketch.id.to_string()); });
    std::vector<FeatureDefinition> features;
    for (const auto& feature : features_) {
        if (!removed.features.contains(feature.id.to_string())) features.push_back(feature);
    }
    auto rollback = rollback_before_;
    if (rollback && removed.features.contains(rollback->to_string())) rollback.reset();
    auto references = references_;
    std::erase_if(references, [&](const auto& reference) {
        if (!removed.features.contains(reference.target.anchor.feature.to_string())) return false;
        if (!cascade) throw std::invalid_argument("sketch has dependent reference: " + reference.id.to_string());
        return true;
    });
    publish_design(std::move(sketches), std::move(features), std::move(rollback), std::move(references));
}

} // namespace blendercad
