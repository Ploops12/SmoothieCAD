#pragma once

#include "blendercad/document_id.hpp"
#include "blendercad/geometry_types.hpp"
#include "blendercad/reference.hpp"

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
    // A bound definition stores zero here; only a resolved copy contains the
    // parameter's current numerical dimension before it reaches the solver.
    std::optional<std::string> parameter{};
    friend bool operator==(const SketchConstraint&, const SketchConstraint&) = default;
};

struct SketchProblem final {
    SketchPlane plane;
    std::vector<SketchEntity> entities;
    std::vector<SketchConstraint> constraints;
    friend bool operator==(const SketchProblem&, const SketchProblem&) = default;
};

// Defines a frame on a persistent face at an explicit historical result.
// The numeric plane is evaluated geometry, never a second saved placement.
struct SketchSupport final {
    DocumentId feature;
    PersistentReference face;
    Vector3 x_direction{1.0, 0.0, 0.0};
    double offset_u_mm{};
    double offset_v_mm{};
    double normal_offset_mm{};
    bool reversed{};
    friend bool operator==(const SketchSupport&, const SketchSupport&) = default;
};

struct SketchDefinition final {
    DocumentId id;
    std::string name;
    SketchProblem problem;
    std::optional<SketchSupport> support{};
    friend bool operator==(const SketchDefinition&, const SketchDefinition&) = default;
};

// Structural validity is independent of solver feasibility. Conflicting but
// well-formed constraints remain valid persistent definitions for later repair.
// Solver-facing validation rejects unresolved parameter bindings. Definition
// validation permits canonical bindings without imposing evaluated domains.
void validate_sketch(const SketchProblem& problem, bool allow_parameters = false);
[[nodiscard]] bool is_sketch_dimension(SketchConstraintKind kind) noexcept;
void validate_sketch_constraint_value(SketchConstraintKind kind, double value);

} // namespace blendercad
