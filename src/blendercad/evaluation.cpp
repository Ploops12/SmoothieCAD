#include "blendercad/evaluation.hpp"
#include "blendercad/reference_tracker.hpp"

#include <algorithm>
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
    std::shared_ptr<const TopologyReferences> references;
    ~OwnedShape() { try { kernel->release(handle); } catch (...) {} }
};

struct InputKey final {
    FeatureParameters parameters;
    DocumentId body;
    std::uint32_t version;
    std::optional<SketchProblem> sketch;
    double length;
    double tolerance;
    std::shared_ptr<OwnedShape> input;
    std::vector<std::uint32_t> edges;
    friend bool operator==(const InputKey&, const InputKey&) = default;
};

struct CachedFeature final {
    std::optional<InputKey> key;
    std::shared_ptr<OwnedShape> shape;
    std::optional<SketchSolveReport> sketch_report;
};

struct ProfileOutput final {
    ShapeHandle handle;
    std::vector<std::string> edges;
    std::vector<std::vector<std::string>> vertices;
};

ProfileOutput extrude(GeometryAdapter& kernel, const SketchProblem& profile, double depth, double tolerance) {
    if (!std::isfinite(depth) || depth <= 0) throw std::runtime_error("Extrusion depth must be finite and positive");
    if (profile.entities.size() == 1) {
        if (const auto* circle = std::get_if<Circle2>(&profile.entities.front().geometry)) {
            const auto id = profile.entities.front().id.to_string();
            std::vector<std::string> edges{id};
            std::vector<std::vector<std::string>> vertices{{id + ":seam"}};
            return {kernel.extrude_circle(circle->center, circle->radius, profile.plane, depth), std::move(edges), std::move(vertices)};
        }
    }
    if (profile.entities.size() < 3) throw std::runtime_error("Profile requires one circle or a closed line loop");
    std::vector<const Line2*> lines;
    std::vector<std::string> identities;
    for (const auto& entity : profile.entities) {
        const auto* line = std::get_if<Line2>(&entity.geometry);
        if (!line) throw std::runtime_error("Mixed/multiple profile loops are not supported by this feature version");
        lines.push_back(line);
        identities.push_back(entity.id.to_string());
    }
    auto near = [&](Point2 a, Point2 b) { return std::hypot(a.x - b.x, a.y - b.y) <= tolerance; };
    bool open_junction = false;
    for (std::size_t index = 0; index < lines.size(); ++index) {
        for (const auto endpoint : {lines[index]->start, lines[index]->end}) {
            std::size_t connections = 0;
            for (std::size_t other = 0; other < lines.size(); ++other) {
                if (index == other) continue;
                connections += near(endpoint, lines[other]->start);
                connections += near(endpoint, lines[other]->end);
            }
            if (connections > 1) throw std::runtime_error("Profile junction is ambiguous within the geometric budget");
            open_junction = open_junction || connections == 0;
        }
    }
    if (open_junction) throw std::runtime_error("Profile is open or contains disconnected geometry");
    std::vector<bool> used(lines.size(), false);
    const auto first = static_cast<std::size_t>(std::min_element(identities.begin(), identities.end()) - identities.begin());
    used[first] = true;
    std::vector<Point2> points{lines[first]->start, lines[first]->end};
    std::vector<std::string> edge_ids{identities[first]};
    std::vector<std::vector<std::string>> vertex_ids{{identities[first] + ":start"}, {identities[first] + ":end"}};
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
        vertex_ids.back().push_back(identities[*next] + (reverse ? ":end" : ":start"));
        endpoint = reverse ? lines[*next]->start : lines[*next]->end;
        points.push_back(endpoint);
        edge_ids.push_back(identities[*next]);
        vertex_ids.push_back({identities[*next] + (reverse ? ":start" : ":end")});
    }
    if (!near(endpoint, points.front())) throw std::runtime_error("Profile is open at its closing junction");
    points.pop_back(); // Closing endpoint shares the first vertex within budget.
    vertex_ids.front().insert(vertex_ids.front().end(), vertex_ids.back().begin(), vertex_ids.back().end());
    vertex_ids.pop_back();
    return {kernel.extrude_polygon(points, profile.plane, depth), std::move(edge_ids), std::move(vertex_ids)};
}

TopologyReferences name_profile(const GeometryTopology& topology, const ProfileOutput& profile, const DocumentId& feature) {
    std::vector<NamedTopologySeed> names;
    for (const auto& seed : topology.seeds) {
        auto add = [&](std::string key) { names.push_back({seed.target, {feature, std::move(key)}}); };
        switch (seed.role) {
        case TopologySeedRole::StartCap: add("cap:start"); break;
        case TopologySeedRole::EndCap: add("cap:end"); break;
        case TopologySeedRole::Side: add("side:" + profile.edges.at(seed.profile_index)); break;
        case TopologySeedRole::StartEdge: add("edge:start:" + profile.edges.at(seed.profile_index)); break;
        case TopologySeedRole::EndEdge: add("edge:end:" + profile.edges.at(seed.profile_index)); break;
        case TopologySeedRole::SweepEdge: case TopologySeedRole::StartVertex: case TopologySeedRole::EndVertex:
            for (const auto& vertex : profile.vertices.at(seed.profile_index)) {
                const std::string prefix = seed.role == TopologySeedRole::SweepEdge ? "edge:sweep:" :
                                           seed.role == TopologySeedRole::StartVertex ? "vertex:start:" : "vertex:end:";
                add(prefix + vertex);
            }
            break;
        }
    }
    return TopologyReferences::seed(topology, names);
}

std::optional<DocumentId> source_entity(const PersistentReference& reference) {
    const std::pair<std::string_view, TopologyKind> prefixes[] = {
        {"side:", TopologyKind::Face}, {"edge:start:", TopologyKind::Edge}, {"edge:end:", TopologyKind::Edge},
        {"edge:sweep:", TopologyKind::Edge}, {"vertex:start:", TopologyKind::Vertex}, {"vertex:end:", TopologyKind::Vertex}};
    const std::string_view key = reference.anchor.key;
    for (const auto& [prefix, kind] : prefixes) {
        if (reference.kind != kind || !key.starts_with(prefix)) continue;
        const auto source = key.substr(prefix.size());
        if (source.size() < 36) return {};
        const auto suffix = source.substr(36);
        const bool vertex_role = prefix == "edge:sweep:" || kind == TopologyKind::Vertex;
        if ((!vertex_role && !suffix.empty()) ||
            (vertex_role && suffix != ":start" && suffix != ":end" && suffix != ":seam")) return {};
        try { return DocumentId::parse(source.substr(0, 36)); }
        catch (const std::invalid_argument&) { return {}; }
    }
    return {};
}

std::optional<ReferenceResolution> source_reference_issue(const Document& document,
                                                         const FeatureDefinition& creator,
                                                         const PersistentReference& reference) {
    if (const auto source = source_entity(reference)) {
        const auto sketch = feature_sketch(creator);
        if (!sketch) {
            return ReferenceResolution{ReferenceState::Ambiguous, {},
                "Sketch-source construction key is not defined by its creator feature; select a replacement to repair"};
        }
        const auto& entities = document.sketch(*sketch).problem.entities;
        if (std::ranges::none_of(entities, [&](const auto& entity) { return entity.id == *source; })) {
            return ReferenceResolution{ReferenceState::Deleted, {},
                "Referenced source sketch entity was deleted; select a replacement to repair"};
        }
    }
    return {};
}

using CurrentShapes = std::unordered_map<std::string, std::shared_ptr<OwnedShape>>;

class SupportUnavailable final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

Vector3 cross(Vector3 a, Vector3 b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}

double dot(Vector3 a, Vector3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }

Vector3 scaled(Vector3 a, double factor) { return {a.x * factor, a.y * factor, a.z * factor}; }

Vector3 unit(Vector3 value, const std::string& context) {
    const auto magnitude = std::hypot(value.x, value.y, value.z);
    if (!std::isfinite(magnitude) || magnitude <= 0) throw std::runtime_error(context);
    return {value.x / magnitude, value.y / magnitude, value.z / magnitude};
}

SketchPlane resolve_support_plane(const Document& document, const SketchSupport& support,
                                  const CurrentShapes& current) {
    const auto context = "Sketch support on feature " + support.feature.to_string() + " face '"
        + support.face.anchor.key + "' from feature " + support.face.anchor.feature.to_string() + ": ";
    validate_reference(support.face);
    if (support.face.kind != TopologyKind::Face) throw std::invalid_argument(context + "support requires a FACE reference");
    const auto& source = document.feature(support.feature);
    if (source.body != support.face.body) throw std::invalid_argument(context + "support context belongs to a different body");
    auto ancestor = std::optional<DocumentId>{support.feature};
    while (ancestor && *ancestor != support.face.anchor.feature) ancestor = feature_input(document.feature(*ancestor));
    if (!ancestor) throw std::invalid_argument(context + "reference creator is not an ancestor of the support context");
    const auto shape = current.find(support.feature.to_string());
    if (shape == current.end()) throw SupportUnavailable(context + "support context has no current valid result");
    if (!current.contains(support.face.anchor.feature.to_string())) {
        throw SupportUnavailable(context + "reference creator has no current valid result");
    }
    const auto& creator = document.feature(support.face.anchor.feature);
    if (const auto issue = source_reference_issue(document, creator, support.face)) {
        throw std::runtime_error(context + issue->diagnostic);
    }
    const auto resolution = shape->second->references->resolve(TopologyKind::Face, support.face.anchor);
    if (resolution.state != ReferenceState::Resolved || resolution.candidates.size() != 1
            || resolution.candidates.front().kind != TopologyKind::Face) {
        throw std::runtime_error(context + "requires exactly one resolved FACE; " + resolution.diagnostic);
    }
    const auto plane = shape->second->kernel->planar_face(shape->second->handle, resolution.candidates.front().index);
    if (!plane) throw std::runtime_error(context + "referenced face is not an exact analytic plane; repair the support");
    auto normal = unit(cross(plane->x_axis, plane->y_axis), context + "face has a degenerate plane normal");
    if (support.reversed) normal = scaled(normal, -1);
    // Saved tangent direction and CAD-origin projection define the frame.
    // Kernel UV axes/origins and trimmed-face centroids are not stable design
    // identities and must not move a sketch after an unrelated face edit.
    const auto preferred = unit(support.x_direction, context + "saved tangent direction must be finite and nonzero");
    const auto along_normal = dot(preferred, normal);
    auto x = Vector3{preferred.x - along_normal * normal.x, preferred.y - along_normal * normal.y,
                     preferred.z - along_normal * normal.z};
    if (std::hypot(x.x, x.y, x.z) <= 1e-10) {
        throw std::runtime_error(context + "saved tangent direction is parallel to the face normal; repair the support orientation");
    }
    x = unit(x, context + "could not normalize the saved support tangent");
    const auto y = unit(cross(normal, x), context + "could not construct a support frame");
    const auto distance = dot(plane->origin, normal) + support.normal_offset_mm;
    const auto origin = Vector3{normal.x * distance + x.x * support.offset_u_mm + y.x * support.offset_v_mm,
                               normal.y * distance + x.y * support.offset_u_mm + y.y * support.offset_v_mm,
                               normal.z * distance + x.z * support.offset_u_mm + y.z * support.offset_v_mm};
    if (!std::isfinite(origin.x) || !std::isfinite(origin.y) || !std::isfinite(origin.z)) {
        throw std::runtime_error(context + "support offsets produced a nonfinite plane origin");
    }
    return {origin, x, y};
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
    // Only same-pass current feature results, including upstream contexts whose
    // final body tip may be failed. Never populate this from last-valid caches.
    CurrentShapes current_features;
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

SketchPlane FeatureEvaluator::support_plane(const Document& document, const SketchSupport& support) const {
    if (!matches_document(document) || !impl_->report) {
        throw std::runtime_error("Document changed; recompute before resolving sketch support");
    }
    return resolve_support_plane(document, support, impl_->current_features);
}

SketchProblem FeatureEvaluator::evaluated_sketch(const Document& document, const DocumentId& id) const {
    if (!matches_document(document) || !impl_->report) {
        throw std::runtime_error("Document changed; recompute before evaluating sketch support");
    }
    auto profile = document.parameterized_sketch(id);
    if (const auto& support = document.sketch(id).support) profile.plane = support_plane(document, *support);
    return profile;
}

EvaluationReport FeatureEvaluator::evaluate(const Document& document) {
    auto& state = *impl_;
    if (state.document && state.document->id() != document.id()) {
        state.cache.clear(); state.last_body_shapes.clear(); state.displays.clear(); state.current_features.clear();
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
                    const auto length = document.length_value(feature_length(feature));
                    // Resolve/validate dimensions before testing the cache:
                    // canonical stored zeros are not evaluated solver inputs.
                    // Invalid parameter ranges must fail, not reuse old solids.
                    std::optional<SketchProblem> profile;
                    if (const auto sketch = feature_sketch(feature)) {
                        profile = document.parameterized_sketch(*sketch);
                        if (const auto& support = document.sketch(*sketch).support) {
                            profile->plane = resolve_support_plane(document, *support, current);
                        }
                    }
                    const auto* fillet = std::get_if<FilletFeature>(&feature.parameters);
                    std::vector<std::uint32_t> edge_indices;
                    if (fillet) {
                        if (!std::isfinite(length) || length <= 0) throw std::runtime_error("Fillet radius must be finite and positive");
                        std::unordered_set<std::uint32_t> selected_edges;
                        for (const auto& edge : fillet->edges) {
                            const auto context = "Fillet " + id + " edge '" + edge.anchor.key
                                + "' from feature " + edge.anchor.feature.to_string() + ": ";
                            if (!current.contains(edge.anchor.feature.to_string())) {
                                throw std::runtime_error(context + "reference creator has no current valid result");
                            }
                            const auto& creator = document.feature(edge.anchor.feature);
                            if (const auto issue = source_reference_issue(document, creator, edge)) {
                                throw std::runtime_error(context + issue->diagnostic);
                            }
                            // The requested region belongs to this operation's
                            // input, not the final body/previous evaluation report.
                            const auto resolution = input_shape->references->resolve(TopologyKind::Edge, edge.anchor);
                            if (resolution.state != ReferenceState::Resolved || resolution.candidates.size() != 1
                                    || resolution.candidates.front().kind != TopologyKind::Edge) {
                                throw std::runtime_error(context + "requires exactly one resolved EDGE; " + resolution.diagnostic);
                            }
                            const auto index = resolution.candidates.front().index;
                            if (!selected_edges.insert(index).second) {
                                throw std::runtime_error(context + "different references resolve to the same input edge; repair the selection");
                            }
                            edge_indices.push_back(index);
                        }
                    }
                    // Even an otherwise identical key may not bypass current
                    // source/reference validation above after an upstream edit.
                    InputKey key{feature.parameters, feature.body, feature.version, profile, length,
                                 state.tolerance, input_shape, edge_indices};
                    if (cached.key && *cached.key == key && cached.shape) {
                        current.emplace(id, cached.shape);
                        evaluation.reused = true;
                        evaluation.sketch_report = cached.sketch_report;
                        ++report.reused_count;
                    } else {
                        // A cached ancestor may belong to an earlier adapter
                        // with the same restored tolerance. Keep each operation
                        // inside its input adapter's handle namespace.
                        auto operation_kernel = input_shape ? input_shape->kernel : state.kernel;
                        std::shared_ptr<OwnedShape> result;
                        if (fillet) {
                            result = state.own(operation_kernel,
                                operation_kernel->fillet(input_shape->handle, edge_indices, length), feature.id, document.revision());
                            result->references = std::make_shared<TopologyReferences>(TopologyReferences::operation(
                                operation_kernel->topology(result->handle), *input_shape->references, feature.id));
                        } else {
                            evaluation.sketch_report = solver->solve(*profile);
                            const auto solve_state = evaluation.sketch_report->state;
                            if (solve_state == SketchSolveState::Conflicting || solve_state == SketchSolveState::Failed) {
                                throw std::runtime_error("Profile sketch did not solve; repair its reported constraints");
                            }
                            const auto construction = extrude(*operation_kernel, *profile, length, state.tolerance);
                            auto tool = state.own(operation_kernel, construction.handle, feature.id, document.revision());
                            tool->references = std::make_shared<TopologyReferences>(name_profile(operation_kernel->topology(tool->handle), construction, feature.id));
                            result = tool;
                            if (input_shape) {
                                result = state.own(operation_kernel, operation_kernel->subtract(input_shape->handle, tool->handle), feature.id, document.revision());
                                if (operation_kernel->volume_mm3(result->handle) >= operation_kernel->volume_mm3(input_shape->handle)) {
                                    throw std::runtime_error("Subtraction did not remove material");
                                }
                                result->references = std::make_shared<TopologyReferences>(TopologyReferences::operation(
                                    operation_kernel->topology(result->handle), *input_shape->references, *tool->references, feature.id));
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
                } catch (const SupportUnavailable& error) {
                    evaluation.state = EvaluationState::Blocked;
                    evaluation.diagnostic = error.what();
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
    state.current_features = std::move(current);
    state.document = document;
    state.report = report;
    for (const auto& reference : document.references()) {
        report.references.push_back({reference.id, resolve_reference(document, reference.target)});
    }
    state.report = report;
    return report;
}

PersistentReference FeatureEvaluator::capture_reference(const Document& document, const DocumentId& body,
                                                       TopologyKind kind, std::uint32_t index) const {
    (void)impl_->body(document, body, true);
    const auto shape = impl_->displays.at(body.to_string());
    PersistentReference reference{body, kind, shape->references->capture({kind, index})};
    validate_reference(reference);
    return reference;
}

ReferenceResolution FeatureEvaluator::resolve_reference(const Document& document, const PersistentReference& reference) const {
    if (!matches_document(document) || !impl_->report) throw std::runtime_error("Document changed; recompute before resolving references");
    validate_reference(reference);
    const auto creator = std::ranges::find_if(document.features(), [&](const auto& feature) { return feature.id == reference.anchor.feature; });
    if (creator == document.features().end()) return {ReferenceState::Deleted, {}, "Reference creator feature was deleted"};
    if (creator->body != reference.body) throw std::invalid_argument("reference creator belongs to a different body");
    const auto creation = std::ranges::find_if(impl_->report->features, [&](const auto& feature) { return feature.feature == reference.anchor.feature; });
    if (creation == impl_->report->features.end() || creation->state != EvaluationState::UpToDate) {
        return {ReferenceState::Unavailable, {}, "Reference creator is failed, blocked, suppressed or rolled back"};
    }
    const auto body = std::ranges::find_if(impl_->report->bodies, [&](const auto& body) { return body.body == reference.body; });
    if (body == impl_->report->bodies.end() || body->state != EvaluationState::UpToDate) {
        return {ReferenceState::Unavailable, {}, "Requested body has no current result at this history position"};
    }
    if (const auto issue = source_reference_issue(document, *creator, reference)) return *issue;
    return impl_->displays.at(reference.body.to_string())->references->resolve(reference.kind, reference.anchor);
}

double FeatureEvaluator::volume_mm3(const Document& document, const DocumentId& id) const {
    (void)impl_->body(document, id, true);
    const auto shape = impl_->displays.at(id.to_string());
    return shape->kernel->volume_mm3(shape->handle);
}

TopologyMeasure FeatureEvaluator::measure_element(const Document& document, const DocumentId& body,
                                                  TopologyElement element) const {
    (void)impl_->body(document, body, true);
    const auto shape = impl_->displays.at(body.to_string());
    return shape->kernel->measure(shape->handle, element);
}

std::optional<SketchPlane> FeatureEvaluator::planar_face(const Document& document, const DocumentId& body,
                                                       std::uint32_t face_index) const {
    (void)impl_->body(document, body, true);
    const auto shape = impl_->displays.at(body.to_string());
    return shape->kernel->planar_face(shape->handle, face_index);
}

TopologyMeasure FeatureEvaluator::measure_reference(const Document& document, const PersistentReference& reference) const {
    const auto resolution = resolve_reference(document, reference);
    if (resolution.state != ReferenceState::Resolved || resolution.candidates.size() != 1) {
        throw std::runtime_error("Cannot measure an unresolved reference: " + resolution.diagnostic);
    }
    return measure_element(document, reference.body, resolution.candidates.front());
}

TopologyPoint FeatureEvaluator::closest_point_element(const Document& document, const DocumentId& body,
                                                      TopologyElement element, Vector3 point_mm) const {
    (void)impl_->body(document, body, true);
    const auto shape = impl_->displays.at(body.to_string());
    return shape->kernel->closest_point(shape->handle, element, point_mm);
}

TopologyPoint FeatureEvaluator::closest_point_reference(const Document& document, const PersistentReference& reference,
                                                        Vector3 point_mm) const {
    const auto resolution = resolve_reference(document, reference);
    if (resolution.state != ReferenceState::Resolved || resolution.candidates.size() != 1) {
        throw std::runtime_error("Cannot snap to an unresolved reference: " + resolution.diagnostic);
    }
    return closest_point_element(document, reference.body, resolution.candidates.front(), point_mm);
}

std::optional<FaceHit> FeatureEvaluator::ray_cast(const Document& document, const DocumentId& body,
                                                Vector3 origin_mm, Vector3 direction, double max_distance_mm) const {
    (void)impl_->body(document, body, true);
    const auto shape = impl_->displays.at(body.to_string());
    return shape->kernel->ray_cast(shape->handle, origin_mm, direction, max_distance_mm);
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
