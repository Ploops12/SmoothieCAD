#include "blendercad/sketch_solver.hpp"

#include <GCS.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace blendercad {
namespace {

void finite(double value) {
    if (!std::isfinite(value)) throw std::invalid_argument("sketch numbers must be finite");
}

class PlaneGcsAdapter final : public SketchConstraintAdapter {
public:
    explicit PlaneGcsAdapter(double tolerance) : tolerance_(tolerance) {
        finite(tolerance);
        if (tolerance <= 0) throw std::invalid_argument("sketch tolerance must be positive");
    }

    SketchSolveReport solve(SketchProblem& problem) override {
        validate_plane(problem.plane);
        if (problem.constraints.size() >= static_cast<std::size_t>(std::numeric_limits<int>::max())) {
            throw std::invalid_argument("too many sketch constraints");
        }
        if (problem.entities.size() > static_cast<std::size_t>(std::numeric_limits<int>::max() / 4)) {
            throw std::invalid_argument("too many sketch entities");
        }
        // One contiguous parameter allocation gives stable lifetimes and a
        // deterministic relative pointer order for upstream pointer-keyed maps.
        std::vector<double> values(problem.entities.size() * 4);
        std::vector<double> dimensions(problem.constraints.size());
        std::vector<GCS::Line> lines(problem.entities.size());
        std::vector<GCS::Circle> circles(problem.entities.size());
        std::unordered_map<std::string, std::size_t> indices;
        std::unordered_set<std::string> identities;
        GCS::VEC_pD unknowns;
        for (std::size_t index = 0; index < problem.entities.size(); ++index) {
            const auto& entity = problem.entities[index];
            if (!identities.insert(entity.id.to_string()).second) throw std::invalid_argument("duplicate sketch identity");
            indices.emplace(entity.id.to_string(), index);
            auto* data = values.data() + index * 4;
            if (const auto* line = std::get_if<Line2>(&entity.geometry)) {
                data[0] = line->start.x; data[1] = line->start.y;
                data[2] = line->end.x; data[3] = line->end.y;
                if (std::hypot(data[2] - data[0], data[3] - data[1]) <= 0) throw std::invalid_argument("line must have positive length");
                lines[index].p1 = GCS::Point(data, data + 1);
                lines[index].p2 = GCS::Point(data + 2, data + 3);
                for (int parameter = 0; parameter < 4; ++parameter) unknowns.push_back(data + parameter);
            } else {
                const auto& circle = std::get<Circle2>(entity.geometry);
                data[0] = circle.center.x; data[1] = circle.center.y; data[2] = circle.radius;
                if (data[2] <= 0) throw std::invalid_argument("circle radius must be positive");
                circles[index].center = GCS::Point(data, data + 1);
                circles[index].rad = data + 2;
                for (int parameter = 0; parameter < 3; ++parameter) unknowns.push_back(data + parameter);
            }
            for (int parameter = 0; parameter < 4; ++parameter) finite(data[parameter]);
        }
        double origin_values[2]{};
        GCS::Point origin(origin_values, origin_values + 1);
        auto index_of = [&](const SketchReference& reference) {
            if (!reference.entity) throw std::invalid_argument("sketch target requires an entity identity");
            const auto found = indices.find(reference.entity->to_string());
            if (found == indices.end()) throw std::invalid_argument("sketch target refers to a missing entity");
            return found->second;
        };
        auto point = [&](const SketchReference& reference) -> GCS::Point {
            if (reference.element == SketchElement::Origin) {
                if (reference.entity) throw std::invalid_argument("origin target cannot have an entity identity");
                return origin;
            }
            const auto index = index_of(reference);
            if (std::holds_alternative<Line2>(problem.entities[index].geometry)) {
                if (reference.element == SketchElement::LineStart) return lines[index].p1;
                if (reference.element == SketchElement::LineEnd) return lines[index].p2;
            } else if (reference.element == SketchElement::CircleCenter) return circles[index].center;
            throw std::invalid_argument("constraint requires a compatible endpoint or center target");
        };
        auto line = [&](const SketchReference& reference) -> GCS::Line& {
            const auto index = index_of(reference);
            if (reference.element != SketchElement::Geometry || !std::holds_alternative<Line2>(problem.entities[index].geometry)) {
                throw std::invalid_argument("constraint requires a whole line target");
            }
            return lines[index];
        };
        auto circle = [&](const SketchReference& reference) -> GCS::Circle& {
            const auto index = index_of(reference);
            if (reference.element != SketchElement::Geometry || !std::holds_alternative<Circle2>(problem.entities[index].geometry)) {
                throw std::invalid_argument("constraint requires a whole circle target");
            }
            return circles[index];
        };
        GCS::System system; // Destroy before the parameter storage above.
        system.debugMode = GCS::NoDebug;
        for (std::size_t index = 0; index < problem.constraints.size(); ++index) {
            const auto& constraint = problem.constraints[index];
            if (!identities.insert(constraint.id.to_string()).second) throw std::invalid_argument("duplicate sketch identity");
            finite(constraint.value);
            dimensions[index] = constraint.value;
            auto* value = &dimensions[index];
            const int tag = static_cast<int>(index + 1);
            using Kind = SketchConstraintKind;
            const auto kind = constraint.kind;
            const bool single = kind == Kind::Horizontal || kind == Kind::Vertical || kind == Kind::Length ||
                                kind == Kind::Radius || kind == Kind::Diameter;
            if (constraint.targets.size() != (single ? 1U : 2U)) throw std::invalid_argument("incorrect constraint target count");
            const auto& a = constraint.targets[0];
            switch (kind) {
            case Kind::Horizontal: system.addConstraintHorizontal(line(a), tag); break;
            case Kind::Vertical: system.addConstraintVertical(line(a), tag); break;
            case Kind::Parallel: system.addConstraintParallel(line(a), line(constraint.targets[1]), tag); break;
            case Kind::Perpendicular: system.addConstraintPerpendicular(line(a), line(constraint.targets[1]), tag); break;
            case Kind::Length: {
                if (*value <= 0) throw std::invalid_argument("line length must be positive");
                auto& geometry = line(a);
                system.addConstraintP2PDistance(geometry.p1, geometry.p2, value, tag);
                break;
            }
            case Kind::Radius: case Kind::Diameter:
                if (*value <= 0) throw std::invalid_argument("radius/diameter must be positive");
                if (kind == Kind::Radius) system.addConstraintCircleRadius(circle(a), value, tag);
                else system.addConstraintCircleDiameter(circle(a), value, tag);
                break;
            case Kind::Coincident: case Kind::Distance: case Kind::DistanceX: case Kind::DistanceY: {
                auto p1 = point(a), p2 = point(constraint.targets[1]);
                if (kind == Kind::Distance && *value < 0) throw std::invalid_argument("point distance cannot be negative");
                if (kind == Kind::Coincident || (kind == Kind::Distance && *value == 0)) system.addConstraintP2PCoincident(p1, p2, tag);
                else if (kind == Kind::Distance) system.addConstraintP2PDistance(p1, p2, value, tag);
                else if (kind == Kind::DistanceX) system.addConstraintDifference(p1.x, p2.x, value, tag);
                else system.addConstraintDifference(p1.y, p2.y, value, tag);
                break;
            }
            default: throw std::invalid_argument("unsupported sketch constraint kind");
            }
        }
        SketchSolveReport report;
        if (problem.constraints.empty()) {
            report.remaining_degrees_of_freedom = static_cast<int>(unknowns.size());
            report.state = unknowns.empty() ? SketchSolveState::FullyConstrained : SketchSolveState::Underconstrained;
            return report;
        }
        system.declareUnknowns(unknowns);
        system.initSolution();
        report.remaining_degrees_of_freedom = system.dofsNumber();
        GCS::VEC_I conflicts, redundant, partial;
        system.getConflicting(conflicts);
        system.getRedundant(redundant);
        system.getPartiallyRedundant(partial);
        redundant.insert(redundant.end(), partial.begin(), partial.end());
        auto translate = [&](GCS::VEC_I tags, std::vector<DocumentId>& destination) {
            std::sort(tags.begin(), tags.end());
            tags.erase(std::unique(tags.begin(), tags.end()), tags.end());
            for (const auto tag : tags) {
                if (tag > 0 && static_cast<std::size_t>(tag) <= problem.constraints.size()) destination.push_back(problem.constraints[tag - 1].id);
            }
        };
        translate(conflicts, report.conflicting_constraints);
        translate(redundant, report.redundant_constraints);
        if (!conflicts.empty()) {
            report.state = SketchSolveState::Conflicting;
            report.diagnostics.push_back("PlaneGCS found conflicting constraints; geometry was not changed");
            return report;
        }
        if (report.remaining_degrees_of_freedom < 0) {
            report.diagnostics.push_back("PlaneGCS could not diagnose the system; geometry was not changed");
            return report;
        }
        const auto status = system.solve();
        if (status != GCS::Success && status != GCS::Converged) {
            report.diagnostics.push_back("PlaneGCS did not find a solution; geometry was not changed");
            return report;
        }
        // Redundant constraints can produce Converged before reduced parameters
        // are applied. Neither status is sufficient: validate every published
        // constraint against our explicit tolerances below.
        system.applySolution();
        for (const auto* parameter : unknowns) {
            if (!std::isfinite(*parameter)) {
                report.diagnostics.push_back("PlaneGCS returned non-finite geometry");
                return report;
            }
        }
        // Validate residuals in physical distance (or normalized angular error)
        // rather than interpreting the solver's convergence number as mm.
        for (const auto& constraint : problem.constraints) {
            const auto& a = constraint.targets[0];
            const auto kind = constraint.kind;
            using Kind = SketchConstraintKind;
            double error = 0;
            if (kind == Kind::Radius || kind == Kind::Diameter) {
                error = *circle(a).rad * (kind == Kind::Diameter ? 2 : 1) - constraint.value;
            } else if (kind == Kind::Horizontal || kind == Kind::Vertical || kind == Kind::Length) {
                const auto& geometry = line(a);
                const double dx = *geometry.p2.x - *geometry.p1.x, dy = *geometry.p2.y - *geometry.p1.y;
                error = kind == Kind::Horizontal ? dy : kind == Kind::Vertical ? dx : std::hypot(dx, dy) - constraint.value;
            } else if (kind == Kind::Parallel || kind == Kind::Perpendicular) {
                const auto& first = line(a); const auto& second = line(constraint.targets[1]);
                const double ax = *first.p2.x - *first.p1.x, ay = *first.p2.y - *first.p1.y;
                const double bx = *second.p2.x - *second.p1.x, by = *second.p2.y - *second.p1.y;
                const double divisor = std::hypot(ax, ay) * std::hypot(bx, by);
                error = divisor <= 0 ? std::numeric_limits<double>::infinity() :
                    (kind == Kind::Parallel ? ax * by - ay * bx : ax * bx + ay * by) / divisor;
                if (std::abs(error) <= 1e-8) continue;
                report.diagnostics.push_back("Solved angular constraint exceeds validation tolerance");
                return report;
            } else {
                const auto p1 = point(a), p2 = point(constraint.targets[1]);
                const double dx = *p2.x - *p1.x, dy = *p2.y - *p1.y;
                error = kind == Kind::Coincident ? std::hypot(dx, dy) : kind == Kind::Distance ? std::hypot(dx, dy) - constraint.value :
                    kind == Kind::DistanceX ? dx - constraint.value : dy - constraint.value;
            }
            if (!std::isfinite(error) || std::abs(error) > tolerance_) {
                report.diagnostics.push_back("Solved dimensional constraint exceeds the geometric budget");
                return report;
            }
        }
        auto result = problem.entities;
        for (std::size_t index = 0; index < result.size(); ++index) {
            const auto* data = values.data() + index * 4;
            if (std::holds_alternative<Line2>(result[index].geometry)) {
                if (std::hypot(data[2] - data[0], data[3] - data[1]) <= 0) {
                    report.diagnostics.push_back("Solved line is degenerate"); return report;
                }
                result[index].geometry = Line2{{data[0], data[1]}, {data[2], data[3]}};
            } else {
                if (data[2] <= 0) { report.diagnostics.push_back("Solved radius is not positive"); return report; }
                result[index].geometry = Circle2{{data[0], data[1]}, data[2]};
            }
        }
        problem.entities = std::move(result);
        report.state = !report.redundant_constraints.empty() ? SketchSolveState::Redundant :
            report.remaining_degrees_of_freedom == 0 ? SketchSolveState::FullyConstrained : SketchSolveState::Underconstrained;
        return report;
    }

private:
    static void validate_plane(const SketchPlane& plane) {
        for (const auto v : {plane.origin, plane.x_axis, plane.y_axis}) { finite(v.x); finite(v.y); finite(v.z); }
        const auto x = plane.x_axis, y = plane.y_axis;
        if (std::abs(x.x*x.x + x.y*x.y + x.z*x.z - 1) > 1e-10 ||
            std::abs(y.x*y.x + y.y*y.y + y.z*y.z - 1) > 1e-10 ||
            std::abs(x.x*y.x + x.y*y.y + x.z*y.z) > 1e-10) throw std::invalid_argument("sketch plane axes must be orthonormal");
    }
    const double tolerance_;
};

} // namespace

std::unique_ptr<SketchConstraintAdapter> make_planegcs_adapter(double tolerance_mm) {
    return std::make_unique<PlaneGcsAdapter>(tolerance_mm);
}

} // namespace blendercad
