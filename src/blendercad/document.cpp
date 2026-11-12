#include "blendercad/document.hpp"

#include <algorithm>
#include <cmath>
#include <ranges>
#include <stdexcept>
#include <utility>

namespace blendercad {
namespace {

void validate_value(double value) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument("parameter values must be finite");
    }
}

void validate_name(std::string_view name) {
    if (name.empty()) throw std::invalid_argument("parameter name cannot be empty");
    if (name.find_first_of("\t\r\n") != std::string_view::npos) {
        throw std::invalid_argument("parameter name cannot contain control whitespace");
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
                           double tolerance_mm, std::vector<Parameter> parameters) {
    validate_value(tolerance_mm);
    if (tolerance_mm <= 0.0) throw std::invalid_argument("geometric tolerance must be positive");
    for (std::size_t index = 0; index < parameters.size(); ++index) {
        validate_name(parameters[index].name);
        validate_value(parameters[index].value);
        if (unit_name(parameters[index].unit) == "unitless" &&
            parameters[index].unit != ParameterUnit::Unitless) {
            throw std::invalid_argument("invalid parameter unit in document snapshot");
        }
        if (std::ranges::any_of(parameters.begin(), parameters.begin() + static_cast<std::ptrdiff_t>(index),
                [&](const Parameter& parameter) { return parameter.name == parameters[index].name; })) {
            throw std::invalid_argument("duplicate parameter name in document snapshot");
        }
    }
    Document document(std::move(id));
    document.revision_ = revision;
    document.geometric_tolerance_mm_ = tolerance_mm;
    document.parameters_ = std::move(parameters);
    return document;
}

void Document::set_geometric_tolerance_mm(double tolerance) {
    validate_value(tolerance);
    if (tolerance <= 0.0) throw std::invalid_argument("geometric tolerance must be positive");
    if (geometric_tolerance_mm_ == tolerance) return;
    geometric_tolerance_mm_ = tolerance;
    ++revision_;
}

void Document::add_parameter(std::string name, double value, ParameterUnit unit) {
    validate_name(name);
    validate_value(value);
    if (std::ranges::any_of(parameters_, [&](const Parameter& parameter) {
            return parameter.name == name;
        })) {
        throw std::invalid_argument("parameter name already exists: " + name);
    }
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
    found->value = value;
    ++revision_;
}

} // namespace blendercad
