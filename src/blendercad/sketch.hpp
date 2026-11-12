#pragma once

#include "blendercad/document_id.hpp"
#include "blendercad/geometry_types.hpp"

#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace blendercad {

struct Line2 final {
    Point2 start;
    Point2 end;
    friend bool operator==(const Line2&, const Line2&) = default;
};

struct Circle2 final {
    Point2 center;
    double radius{};
    friend bool operator==(const Circle2&, const Circle2&) = default;
};

using SketchGeometry = std::variant<Line2, Circle2>;

struct SketchEntity final {
    DocumentId id;
    SketchGeometry geometry;
    friend bool operator==(const SketchEntity&, const SketchEntity&) = default;
};

enum class SketchConstraintKind {
    Coincident, Horizontal, Vertical, Parallel, Perpendicular, Distance,
    Length, Radius, Diameter, DistanceX, DistanceY,
};

enum class SketchElement { Geometry, LineStart, LineEnd, CircleCenter, Origin };

struct SketchReference final {
    std::optional<DocumentId> entity;
    SketchElement element{SketchElement::Origin};
    friend bool operator==(const SketchReference&, const SketchReference&) = default;
};

struct SketchConstraint final {
    DocumentId id;
    SketchConstraintKind kind{SketchConstraintKind::Coincident};
    std::vector<SketchReference> targets;
    double value{};
    friend bool operator==(const SketchConstraint&, const SketchConstraint&) = default;
};

struct SketchProblem final {
    SketchPlane plane;
    std::vector<SketchEntity> entities;
    std::vector<SketchConstraint> constraints;
    friend bool operator==(const SketchProblem&, const SketchProblem&) = default;
};

struct SketchDefinition final {
    DocumentId id;
    std::string name;
    SketchProblem problem;
    friend bool operator==(const SketchDefinition&, const SketchDefinition&) = default;
};

// Structural validity is independent of solver feasibility. Conflicting but
// well-formed constraints remain valid persistent definitions for later repair.
void validate_sketch(const SketchProblem& problem);

} // namespace blendercad
