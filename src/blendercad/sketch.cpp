#include "blendercad/sketch.hpp"

#include <cmath>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace blendercad {
namespace {

void require_finite(double value) {
    if (!std::isfinite(value)) throw std::invalid_argument("sketch numbers must be finite");
}

void validate_point(Point2 point) {
    require_finite(point.x);
    require_finite(point.y);
}

} // namespace

void validate_sketch(const SketchProblem& problem) {
    const auto& plane = problem.plane;
    for (const auto vector : {plane.origin, plane.x_axis, plane.y_axis}) {
        require_finite(vector.x); require_finite(vector.y); require_finite(vector.z);
    }
    const auto x = plane.x_axis, y = plane.y_axis;
    if (std::abs(x.x*x.x + x.y*x.y + x.z*x.z - 1) > 1e-10 ||
        std::abs(y.x*y.x + y.y*y.y + y.z*y.z - 1) > 1e-10 ||
        std::abs(x.x*y.x + x.y*y.y + x.z*y.z) > 1e-10) {
        throw std::invalid_argument("sketch plane axes must be orthonormal");
    }

    std::unordered_set<std::string> identities;
    std::unordered_map<std::string, const SketchEntity*> entities;
    for (const auto& entity : problem.entities) {
        const auto id = entity.id.to_string();
        if (!identities.insert(id).second) throw std::invalid_argument("duplicate sketch identity: " + id);
        entities.emplace(id, &entity);
        if (const auto* line = std::get_if<Line2>(&entity.geometry)) {
            validate_point(line->start); validate_point(line->end);
            const auto length = std::hypot(line->end.x - line->start.x, line->end.y - line->start.y);
            if (!std::isfinite(length) || length <= 0) throw std::invalid_argument("line must have finite positive length");
        } else {
            const auto& circle = std::get<Circle2>(entity.geometry);
            validate_point(circle.center); require_finite(circle.radius);
            if (circle.radius <= 0) throw std::invalid_argument("circle radius must be positive");
        }
    }

    auto entity_for = [&](const SketchReference& reference) -> const SketchEntity& {
        if (!reference.entity) throw std::invalid_argument("sketch target requires an entity identity");
        const auto found = entities.find(reference.entity->to_string());
        if (found == entities.end()) throw std::invalid_argument("sketch target refers to a missing entity");
        return *found->second;
    };
    auto whole_geometry = [&](const SketchReference& reference, bool circle) {
        const auto& entity = entity_for(reference);
        if (reference.element != SketchElement::Geometry ||
            std::holds_alternative<Circle2>(entity.geometry) != circle) {
            throw std::invalid_argument(circle ? "constraint requires a whole circle target" : "constraint requires a whole line target");
        }
    };
    auto point = [&](const SketchReference& reference) {
        if (reference.element == SketchElement::Origin) {
            if (reference.entity) throw std::invalid_argument("origin target cannot have an entity identity");
            return;
        }
        const auto& entity = entity_for(reference);
        const auto element = reference.element;
        if ((std::holds_alternative<Line2>(entity.geometry) &&
             (element == SketchElement::LineStart || element == SketchElement::LineEnd)) ||
            (std::holds_alternative<Circle2>(entity.geometry) && element == SketchElement::CircleCenter)) return;
        throw std::invalid_argument("constraint requires a compatible endpoint or center target");
    };

    using Kind = SketchConstraintKind;
    for (const auto& constraint : problem.constraints) {
        const auto id = constraint.id.to_string();
        if (!identities.insert(id).second) throw std::invalid_argument("duplicate sketch identity: " + id);
        require_finite(constraint.value);
        const auto kind = constraint.kind;
        const bool single = kind == Kind::Horizontal || kind == Kind::Vertical || kind == Kind::Length ||
                            kind == Kind::Radius || kind == Kind::Diameter;
        if (constraint.targets.size() != (single ? 1U : 2U)) throw std::invalid_argument("incorrect constraint target count");
        switch (kind) {
        case Kind::Horizontal: case Kind::Vertical: case Kind::Length:
            whole_geometry(constraint.targets[0], false); break;
        case Kind::Parallel: case Kind::Perpendicular:
            whole_geometry(constraint.targets[0], false);
            whole_geometry(constraint.targets[1], false); break;
        case Kind::Radius: case Kind::Diameter:
            whole_geometry(constraint.targets[0], true); break;
        case Kind::Coincident: case Kind::Distance: case Kind::DistanceX: case Kind::DistanceY:
            point(constraint.targets[0]); point(constraint.targets[1]);
            if (!constraint.targets[0].entity && !constraint.targets[1].entity) {
                throw std::invalid_argument("constraint must target at least one sketch entity");
            }
            break;
        default: throw std::invalid_argument("unsupported sketch constraint kind");
        }
        if ((kind == Kind::Length || kind == Kind::Radius || kind == Kind::Diameter) && constraint.value <= 0) {
            throw std::invalid_argument("length, radius and diameter must be positive");
        }
        if (kind == Kind::Distance && constraint.value < 0) throw std::invalid_argument("point distance cannot be negative");
        if ((kind == Kind::Coincident || kind == Kind::Horizontal || kind == Kind::Vertical ||
             kind == Kind::Parallel || kind == Kind::Perpendicular) && constraint.value != 0) {
            throw std::invalid_argument("geometric constraints do not accept a numeric dimension");
        }
    }
}

} // namespace blendercad
