#include "blendercad/evaluation.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace blendercad {
namespace {

struct OwnedShape final {
    OwnedShape(std::shared_ptr<GeometryAdapter> owner, ShapeHandle shape, DocumentId feature_id,
               std::uint64_t source_revision, std::uint64_t serial)
        : kernel(std::move(owner)), handle(shape), feature(std::move(feature_id)),
          revision(source_revision), generation(serial) {}
    OwnedShape(const OwnedShape&) = delete;
    OwnedShape& operator=(const OwnedShape&) = delete;
    std::shared_ptr<GeometryAdapter> kernel;
    ShapeHandle handle;
    DocumentId feature;
    std::uint64_t revision;
    std::uint64_t generation;
    ~OwnedShape() { try { kernel->release(handle); } catch (...) {} }
};

struct InputKey final {
    FeatureParameters parameters;
    DocumentId body;
    std::uint32_t version;
    SketchProblem sketch;
    double depth;
    double tolerance;
    std::shared_ptr<OwnedShape> input;
    friend bool operator==(const InputKey&, const InputKey&) = default;
};

struct CachedFeature final {
    std::optional<InputKey> key;
    std::shared_ptr<OwnedShape> shape;
    std::optional<SketchSolveReport> sketch_report;
};

ShapeHandle extrude(GeometryAdapter& kernel, const SketchProblem& profile, double depth, double tolerance) {
    if (!std::isfinite(depth) || depth <= 0) throw std::runtime_error("Extrusion depth must be finite and positive");
    if (profile.entities.size() == 1) {
        if (const auto* circle = std::get_if<Circle2>(&profile.entities.front().geometry)) {
            return kernel.extrude_circle(circle->center, circle->radius, profile.plane, depth);
        }
    }
    if (profile.entities.size() < 3) throw std::runtime_error("Profile requires one circle or a closed line loop");
    std::vector<const Line2*> lines;
    for (const auto& entity : profile.entities) {
        const auto* line = std::get_if<Line2>(&entity.geometry);
        if (!line) throw std::runtime_error("Mixed/multiple profile loops are not supported by this feature version");
        lines.push_back(line);
    }
    auto near = [&](Point2 a, Point2 b) { return std::hypot(a.x - b.x, a.y - b.y) <= tolerance; };
    std::vector<bool> used(lines.size(), false);
    used[0] = true;
    std::vector<Point2> points{lines[0]->start, lines[0]->end};
    auto endpoint = points.back();
    for (std::size_t connected = 1; connected < lines.size(); ++connected) {
        std::optional<std::size_t> next;
        bool reverse = false;
        for (std::size_t index = 0; index < lines.size(); ++index) {
            if (used[index]) continue;
            const bool at_start = near(endpoint, lines[index]->start), at_end = near(endpoint, lines[index]->end);
            if (at_start || at_end) {
                if (next || (at_start && at_end)) throw std::runtime_error("Profile junction is ambiguous within the geometric budget");
                next = index; reverse = at_end;
            }
        }
        if (!next) throw std::runtime_error("Profile is open or contains disconnected loops");
        used[*next] = true;
        endpoint = reverse ? lines[*next]->start : lines[*next]->end;
        points.push_back(endpoint);
    }
    if (!near(endpoint, points.front())) throw std::runtime_error("Profile is open at its closing junction");
    points.pop_back(); // Closing endpoint shares the first vertex within budget.
    return kernel.extrude_polygon(points, profile.plane, depth);
}

} // namespace

struct FeatureEvaluator::Impl final {
    std::optional<Document> document;
    std::shared_ptr<GeometryAdapter> kernel;
    double tolerance{};
    std::uint64_t generation{};
    std::unordered_map<std::string, CachedFeature> cache;
    std::unordered_map<std::string, std::shared_ptr<OwnedShape>> displays;
    std::unordered_map<std::string, std::shared_ptr<OwnedShape>> last_body_shapes;
    std::optional<EvaluationReport> report;

    std::shared_ptr<OwnedShape> own(std::shared_ptr<GeometryAdapter> owner, ShapeHandle handle,
                                   const DocumentId& feature, std::uint64_t revision) {
        if (generation == std::numeric_limits<std::uint64_t>::max()) {
            owner->release(handle);
            throw std::overflow_error("evaluation generation space exhausted");
        }
        return std::make_shared<OwnedShape>(std::move(owner), handle, feature, revision, ++generation);
    }

    const BodyEvaluation& body(const Document& source, const DocumentId& id, bool require_current) const {
        if (!document || *document != source || !report) throw std::runtime_error("Document changed; recompute before querying evaluated geometry");
        for (const auto& body : report->bodies) {
            if (body.body == id) {
                if (require_current && body.state != EvaluationState::UpToDate) throw std::runtime_error("Requested body has no current valid geometry; stale geometry cannot be measured or exported");
                return body;
            }
        }
        throw std::invalid_argument("body is not available at the current history position");
    }
};

FeatureEvaluator::FeatureEvaluator() : impl_(std::make_unique<Impl>()) {}
FeatureEvaluator::~FeatureEvaluator() = default;

std::unique_ptr<FeatureEvaluator> FeatureEvaluator::fork() const {
    auto result = std::make_unique<FeatureEvaluator>();
    result->impl_ = std::make_unique<Impl>(*impl_);
    return result;
}

bool FeatureEvaluator::matches_document(const Document& document) const {
    return impl_->document && *impl_->document == document;
}

EvaluationReport FeatureEvaluator::evaluate(const Document& document) {
    auto& state = *impl_;
    if (state.document && state.document->id() != document.id()) {
        state.cache.clear(); state.last_body_shapes.clear(); state.displays.clear();
    }
    if (!state.kernel || state.tolerance != document.geometric_tolerance_mm()) {
        state.tolerance = document.geometric_tolerance_mm();
        state.kernel = make_occt_geometry_adapter(state.tolerance);
    }
    auto solver = make_planegcs_adapter(state.tolerance);
    EvaluationReport report{document.id(), document.revision(), {}, {}, 0, 0};
    std::unordered_map<std::string, std::shared_ptr<OwnedShape>> current;
    std::unordered_map<std::string, std::size_t> tips;
    std::unordered_set<std::string> existing_features, existing_bodies;
    bool rolled_back = false;
    for (const auto& feature : document.features()) {
        const auto id = feature.id.to_string(), body = feature.body.to_string();
        existing_features.insert(id); existing_bodies.insert(body);
        auto& cached = state.cache[id];
        if (cached.key && cached.key->body != feature.body) cached = CachedFeature{};
        FeatureEvaluation evaluation{feature.id, feature.body, EvaluationState::Failed, {}, {}, false, bool(cached.shape)};
        if (document.rollback_before() == feature.id) rolled_back = true;
        if (rolled_back) {
            evaluation.state = EvaluationState::RolledBack;
        } else {
            tips.insert_or_assign(body, report.features.size());
            if (feature.suppressed) {
                evaluation.state = EvaluationState::Suppressed;
                evaluation.diagnostic = "Feature is suppressed; its output is unavailable";
            } else if (const auto input = feature_input(feature); input && !current.contains(input->to_string())) {
                evaluation.state = EvaluationState::Blocked;
                evaluation.diagnostic = "Blocked by unavailable input feature " + input->to_string();
            } else {
                try {
                    const auto input = feature_input(feature);
                    const auto input_shape = input ? current.at(input->to_string()) : nullptr;
                    const auto depth = document.length_value(feature_depth(feature));
                    const auto& sketch = document.sketch(feature_sketch(feature));
                    InputKey key{feature.parameters, feature.body, feature.version, sketch.problem, depth, state.tolerance, input_shape};
                    if (cached.key && *cached.key == key && cached.shape) {
                        current.emplace(id, cached.shape);
                        evaluation.reused = true;
                        evaluation.sketch_report = cached.sketch_report;
                        ++report.reused_count;
                    } else {
                        auto profile = sketch.problem;
                        evaluation.sketch_report = solver->solve(profile);
                        const auto solve_state = evaluation.sketch_report->state;
                        if (solve_state == SketchSolveState::Conflicting || solve_state == SketchSolveState::Failed) {
                            throw std::runtime_error("Profile sketch did not solve; repair its reported constraints");
                        }
                        // A cached ancestor may belong to an earlier adapter
                        // with the same restored tolerance. Keep each Boolean's
                        // operands inside that adapter's handle namespace.
                        auto operation_kernel = input_shape ? input_shape->kernel : state.kernel;
                        auto tool = state.own(operation_kernel,
                            extrude(*operation_kernel, profile, depth, state.tolerance), feature.id, document.revision());
                        auto result = tool;
                        if (input_shape) {
                            result = state.own(operation_kernel, operation_kernel->subtract(input_shape->handle, tool->handle), feature.id, document.revision());
                            if (operation_kernel->volume_mm3(result->handle) >= operation_kernel->volume_mm3(input_shape->handle)) {
                                throw std::runtime_error("Subtraction did not remove material");
                            }
                        }
                        cached.key = std::move(key);
                        cached.shape = std::move(result);
                        cached.sketch_report = evaluation.sketch_report;
                        current.emplace(id, cached.shape);
                        ++report.computed_count;
                    }
                    evaluation.state = EvaluationState::UpToDate;
                    evaluation.has_last_valid = true;
                } catch (const std::exception& error) {
                    evaluation.diagnostic = error.what();
                } catch (...) {
                    evaluation.diagnostic = "Native geometry operation failed";
                }
            }
        }
        report.features.push_back(std::move(evaluation));
    }
    state.displays.clear();
    // Use history order for deterministic body publication, not hash-map order.
    for (std::size_t index = 0; index < report.features.size(); ++index) {
        const auto& feature = report.features[index];
        const auto body = feature.body.to_string();
        if (!tips.contains(body) || tips.at(body) != index) continue;
        std::shared_ptr<OwnedShape> display;
        if (feature.state == EvaluationState::UpToDate) {
            display = current.at(feature.feature.to_string());
            state.last_body_shapes.insert_or_assign(body, display);
        } else {
            display = state.cache.at(feature.feature.to_string()).shape;
            if (!display && state.last_body_shapes.contains(body)) display = state.last_body_shapes.at(body);
            auto ancestor = feature_input(document.feature(feature.feature));
            while (!display && ancestor) {
                if (current.contains(ancestor->to_string())) display = current.at(ancestor->to_string());
                ancestor = feature_input(document.feature(*ancestor));
            }
        }
        if (display) state.displays.emplace(body, std::move(display));
        report.bodies.push_back({feature.body, feature.feature, feature.state, state.displays.contains(body)});
    }
    std::erase_if(state.cache, [&](const auto& item) { return !existing_features.contains(item.first); });
    std::erase_if(state.last_body_shapes, [&](const auto& item) { return !existing_bodies.contains(item.first); });
    state.document = document;
    state.report = report;
    return report;
}

double FeatureEvaluator::volume_mm3(const Document& document, const DocumentId& id) const {
    (void)impl_->body(document, id, true);
    const auto shape = impl_->displays.at(id.to_string());
    return shape->kernel->volume_mm3(shape->handle);
}

EvaluatedDisplay FeatureEvaluator::tessellate(const Document& document, const DocumentId& id,
                                             double linear_deflection_mm, double angular_deflection_rad) const {
    const auto& body = impl_->body(document, id, false);
    if (!body.has_display) throw std::runtime_error("Body has no last-valid display geometry");
    const auto shape = impl_->displays.at(id.to_string());
    return {document.id(), id, body.feature, shape->feature, document.revision(), shape->revision, shape->generation,
            body.state == EvaluationState::UpToDate,
            shape->kernel->tessellate(shape->handle, linear_deflection_mm, angular_deflection_rad)};
}

void FeatureEvaluator::write_step(const Document& document, const DocumentId& id, const std::string& path) const {
    (void)impl_->body(document, id, true);
    const auto shape = impl_->displays.at(id.to_string());
    shape->kernel->write_step(shape->handle, path);
}

} // namespace blendercad
