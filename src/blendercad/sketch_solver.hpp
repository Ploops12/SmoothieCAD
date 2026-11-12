#pragma once

#include "blendercad/sketch.hpp"

#include <cstddef>
#include <string>
#include <memory>
#include <optional>
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

struct SketchSolveReport final {
    SketchSolveState state{SketchSolveState::Failed};
    int remaining_degrees_of_freedom{-1};
    std::vector<std::string> diagnostics;
    std::vector<DocumentId> conflicting_constraints;
    std::vector<DocumentId> redundant_constraints;
};

// Solver-specific entities and handles remain adapter details. The document
// layer will pass persistent sketch definitions through this boundary.
class SketchConstraintAdapter {
public:
    virtual ~SketchConstraintAdapter() = default;
    [[nodiscard]] virtual SketchSolveReport solve(SketchProblem& problem) = 0;
};

[[nodiscard]] std::unique_ptr<SketchConstraintAdapter> make_planegcs_adapter(double tolerance_mm = 0.001);

} // namespace blendercad
