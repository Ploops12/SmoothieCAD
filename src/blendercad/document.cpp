#include "blendercad/document.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <ranges>
#include <stdexcept>
#include <utility>
#include <unordered_set>

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

void validate_sketches(const DocumentId& document_id, const std::vector<SketchDefinition>& sketches) {
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
                           std::vector<SketchDefinition> sketches) {
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
    validate_sketches(id, sketches);
    Document document(std::move(id));
    document.revision_ = revision;
    document.geometric_tolerance_mm_ = tolerance_mm;
    document.parameters_ = std::move(parameters);
    document.sketches_ = std::move(sketches);
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
    validate_sketches(id_, sketches);
    if (sketches == sketches_) return;
    ensure_revision_available();
    sketches_ = std::move(sketches);
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

} // namespace blendercad
