#pragma once

#include "blendercad/document_id.hpp"
#include "blendercad/geometry_types.hpp"

#include <cstddef>
#include <string>
#include <variant>
#include <vector>

namespace blendercad {

enum class SketchSolveState {
    Underconstrained,
    FullyConstrained,
    Redundant,
    Conflicting,
    Failed,
};

struct Line2 final {
    Point2 start;
    Point2 end;
};

struct Circle2 final {
    Point2 center;
    double radius{};
};

using SketchGeometry = std::variant<Line2, Circle2>;

struct SketchEntity final {
    DocumentId id;
    SketchGeometry geometry;
};

enum class SketchConstraintKind {
    Coincident,
    Horizontal,
    Vertical,
    Parallel,
    Perpendicular,
    Distance,
    Length,
    Radius,
};

struct SketchConstraint final {
    DocumentId id;
    SketchConstraintKind kind{SketchConstraintKind::Coincident};
    std::vector<DocumentId> entities;
    double value{};
};

struct SketchProblem final {
    SketchPlane plane;
    std::vector<SketchEntity> entities;
    std::vector<SketchConstraint> constraints;
};

struct SketchSolveReport final {
    SketchSolveState state{SketchSolveState::Failed};
    std::size_t remaining_degrees_of_freedom{};
    std::vector<std::string> diagnostics;
};

// Solver-specific entities and handles remain adapter details. The document
// layer will pass persistent sketch definitions through this boundary.
class SketchConstraintAdapter {
public:
    virtual ~SketchConstraintAdapter() = default;
    [[nodiscard]] virtual SketchSolveReport solve(SketchProblem& problem) = 0;
};

} // namespace blendercad
