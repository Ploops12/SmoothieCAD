#include "blendercad/reference_tracker.hpp"

#include <algorithm>
#include <stdexcept>

using namespace blendercad;

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

int main() {
    const auto feature = DocumentId::generate();
    const auto cut = DocumentId::generate();
    const auto later = DocumentId::generate();
    const ReferenceAnchor a{feature, "side:a"}, alias{feature, "side:alias"}, b{feature, "side:b"};
    GeometryTopology one, two, empty;
    one.counts = {1, 0, 0}; two.counts = {2, 0, 0};
    auto original = TopologyReferences::seed(one, {{{TopologyKind::Face, 0}, a}, {{TopologyKind::Face, 0}, alias}});
    auto none = TopologyReferences::seed(empty, {});
    require(original.resolve(TopologyKind::Face, a).state == ReferenceState::Resolved, "seed did not resolve");
    GeometryTopology split = two;
    split.argument_history.push_back({{TopologyKind::Face, 0}, {{TopologyKind::Face, 0}, {TopologyKind::Face, 1}}, {}, false});
    auto divided = TopologyReferences::operation(split, original, none, cut);
    auto resolution = divided.resolve(TopologyKind::Face, a);
    require(resolution.state == ReferenceState::Split && resolution.candidates.size() == 2, "split picked one child");
    bool refused = false;
    try { (void)divided.capture({TopologyKind::Face, 0}); } catch (const std::invalid_argument&) { refused = true; }
    require(refused, "ambiguous generated children acquired arbitrary ordinal names");
    GeometryTopology survivor = one;
    survivor.argument_history = {{{TopologyKind::Face, 0}, {}, {}, true},
                                 {{TopologyKind::Face, 1}, {{TopologyKind::Face, 0}}, {}, false}};
    auto reduced = TopologyReferences::operation(survivor, divided, none, later);
    resolution = reduced.resolve(TopologyKind::Face, a);
    require(resolution.state == ReferenceState::Split && resolution.candidates.size() == 1, "split survivor silently substituted for original");
    refused = false;
    try { (void)reduced.capture({TopologyKind::Face, 0}); } catch (const std::invalid_argument&) { refused = true; }
    require(refused, "unidentified split survivor acquired a different-looking but equally ambiguous name");
    auto pair = TopologyReferences::seed(two, {{{TopologyKind::Face, 0}, a}, {{TopologyKind::Face, 1}, b}});
    GeometryTopology merge = one;
    merge.argument_history = {{{TopologyKind::Face, 0}, {{TopologyKind::Face, 0}}, {}, false},
                             {{TopologyKind::Face, 1}, {{TopologyKind::Face, 0}}, {}, false}};
    auto merged = TopologyReferences::operation(merge, pair, none, cut);
    require(merged.resolve(TopologyKind::Face, a).state == ReferenceState::Merged, "merged reference silently resolved");
    require(merged.resolve(TopologyKind::Face, b).state == ReferenceState::Merged, "second merged reference silently resolved");
    require(merged.resolve(TopologyKind::Face, merged.capture({TopologyKind::Face, 0})).state == ReferenceState::Resolved, "merged-result explicit repair failed");
    GeometryTopology unchanged = one;
    unchanged.argument_history = {{{TopologyKind::Face, 0}, {{TopologyKind::Face, 0}}, {}, false}};
    auto aliases = TopologyReferences::operation(unchanged, original, none, cut);
    require(aliases.resolve(TopologyKind::Face, a).state == ReferenceState::Resolved, "aliases of one input falsely counted as merge");
    GeometryTopology removed = empty;
    removed.argument_history = {{{TopologyKind::Face, 0}, {}, {}, true}};
    auto deleted = TopologyReferences::operation(removed, original, none, cut);
    require(deleted.resolve(TopologyKind::Face, a).state == ReferenceState::Deleted, "explicit kernel deletion lost");
    auto missing = TopologyReferences::operation(empty, original, none, cut);
    require(missing.resolve(TopologyKind::Face, a).state == ReferenceState::Ambiguous, "missing history falsely treated as deletion");
    auto colliding = TopologyReferences::seed(two, {{{TopologyKind::Face, 0}, a}, {{TopologyKind::Face, 1}, a}});
    require(colliding.resolve(TopologyKind::Face, a).state == ReferenceState::Ambiguous, "nonunique construction key not ambiguous");
    GeometryTopology invalid = one;
    invalid.argument_history = {{{TopologyKind::Face, 0}, {{TopologyKind::Face, 1}}, {}, false}};
    refused = false;
    try { (void)TopologyReferences::operation(invalid, original, none, cut); } catch (const std::invalid_argument&) { refused = true; }
    require(refused, "out-of-range kernel history accepted");
    // A named origin cannot conceal a second, unnameable generator.
    GeometryTopology edge_result;
    edge_result.counts = {0, 1, 0};
    edge_result.argument_history = {{{TopologyKind::Face, 0}, {}, {{TopologyKind::Edge, 0}}, true}};
    edge_result.tool_history = {{{TopologyKind::Face, 0}, {}, {{TopologyKind::Edge, 0}}, true}};
    auto unnameable = TopologyReferences::seed(one, {});
    auto partial = TopologyReferences::operation(edge_result, original, unnameable, cut);
    refused = false;
    try { (void)partial.capture({TopologyKind::Edge, 0}); } catch (const std::invalid_argument&) { refused = true; }
    require(refused, "partial origin set fabricated a persistent generated edge");
    // Nonempty but indistinguishable predecessor names are not complete origins.
    GeometryTopology ambiguous_generator;
    ambiguous_generator.counts = {0, 1, 0};
    ambiguous_generator.argument_history = {{{TopologyKind::Face, 0}, {}, {{TopologyKind::Edge, 0}}, true},
                                             {{TopologyKind::Face, 1}, {}, {}, true}};
    auto generated_from_ambiguous = TopologyReferences::operation(ambiguous_generator, colliding, none, later);
    refused = false;
    try { (void)generated_from_ambiguous.capture({TopologyKind::Edge, 0}); } catch (const std::invalid_argument&) { refused = true; }
    require(refused, "one output laundered ambiguous source names into a false unique identity");

    // A fillet's round face may be reported directly while its cap-boundary
    // edges are only identifiable by exact incidence with that face and the
    // evolved start/end caps. This is not a continuation of the old edge.
    const ReferenceAnchor round_source{feature, "edge:round-source"};
    const ReferenceAnchor old_vertex{feature, "vertex:removed"};
    GeometryTopology boundary_input;
    boundary_input.counts = {2, 1, 1};
    auto boundary_source = TopologyReferences::seed(boundary_input,
        {{{TopologyKind::Face, 0}, a}, {{TopologyKind::Face, 1}, b},
         {{TopologyKind::Edge, 0}, round_source}, {{TopologyKind::Vertex, 0}, old_vertex}});
    GeometryTopology boundary_output;
    boundary_output.counts = {3, 2, 1};
    boundary_output.argument_history = {
        {{TopologyKind::Face, 0}, {{TopologyKind::Face, 0}}, {}, false,
            {{TopologyKind::Edge, 0}, {TopologyKind::Vertex, 0}}},
        {{TopologyKind::Face, 1}, {{TopologyKind::Face, 1}}, {}, false,
            {{TopologyKind::Edge, 1}}},
        {{TopologyKind::Edge, 0}, {}, {{TopologyKind::Face, 2}}, true,
            {{TopologyKind::Edge, 0}, {TopologyKind::Edge, 1}, {TopologyKind::Vertex, 0}}},
        {{TopologyKind::Vertex, 0}, {}, {}, true}};
    auto bounded = TopologyReferences::operation(boundary_output, boundary_source, cut);
    const auto start_boundary = bounded.capture({TopologyKind::Edge, 0});
    const auto end_boundary = bounded.capture({TopologyKind::Edge, 1});
    const auto boundary_vertex = bounded.capture({TopologyKind::Vertex, 0});
    require(start_boundary.feature == cut && start_boundary.key.starts_with("boundary:1:"),
            "boundary edge did not receive its distinct incidence role");
    require(end_boundary != start_boundary, "different named cap incidences lost their distinction");
    resolution = bounded.resolve(TopologyKind::Edge, start_boundary);
    require(resolution.state == ReferenceState::Resolved && resolution.candidates.size() == 1 &&
            resolution.candidates.front() == TopologyElement{TopologyKind::Edge, 0},
            "unique exact boundary incidence did not resolve");
    require(boundary_vertex.key.starts_with("boundary:2:") &&
            bounded.resolve(TopologyKind::Vertex, boundary_vertex).state == ReferenceState::Resolved,
            "new boundary vertex did not receive a resolvable incidence name");
    require(bounded.resolve(TopologyKind::Edge, round_source).state == ReferenceState::Deleted,
            "new same-kind boundary edge silently inherited its deleted source edge");
    require(bounded.resolve(TopologyKind::Vertex, old_vertex).state == ReferenceState::Deleted,
            "new boundary vertex silently inherited its deleted source vertex");
    require(bounded.capture({TopologyKind::Face, 0}) == a,
            "boundary naming replaced an existing construction continuation");
    require(bounded.capture({TopologyKind::Face, 2}).key.starts_with("derived:0:"),
            "direct generated face was relabelled as boundary incidence");

    // Names must follow complete origin sets, not history traversal or result
    // ordinals. Reverse entry traversal and exchange the two edge ordinals.
    auto reordered_boundary = boundary_output;
    std::reverse(reordered_boundary.argument_history.begin(), reordered_boundary.argument_history.end());
    for (auto& entry : reordered_boundary.argument_history) {
        for (auto& target : entry.boundary_generated) {
            if (target.kind == TopologyKind::Edge) target.index = 1 - target.index;
        }
    }
    auto reordered_result = TopologyReferences::operation(reordered_boundary, boundary_source, cut);
    require(reordered_result.capture({TopologyKind::Edge, 1}) == start_boundary &&
            reordered_result.capture({TopologyKind::Edge, 0}) == end_boundary,
            "boundary identity depended on history order or output edge ordinal");

    auto require_uncapturable = [](const TopologyReferences& names, TopologyElement target, const char* message) {
        bool rejected = false;
        try { (void)names.capture(target); } catch (const std::invalid_argument&) { rejected = true; }
        require(rejected, message);
    };
    auto indistinguishable = boundary_output;
    indistinguishable.argument_history[0].boundary_generated.push_back({TopologyKind::Edge, 1});
    indistinguishable.argument_history[1].boundary_generated.clear();
    auto boundary_collision = TopologyReferences::operation(indistinguishable, boundary_source, cut);
    resolution = boundary_collision.resolve(TopologyKind::Edge, start_boundary);
    require(resolution.state == ReferenceState::Ambiguous && resolution.candidates.size() == 2,
            "indistinguishable boundary edges acquired an ordinal tie-breaker");
    require_uncapturable(boundary_collision, {TopologyKind::Edge, 0}, "ambiguous first boundary was capturable");
    require_uncapturable(boundary_collision, {TopologyKind::Edge, 1}, "ambiguous second boundary was capturable");

    auto unnamed_boundary_source = TopologyReferences::seed(boundary_input,
        {{{TopologyKind::Face, 0}, a}, {{TopologyKind::Face, 1}, b}, {{TopologyKind::Vertex, 0}, old_vertex}});
    auto incomplete_boundary = TopologyReferences::operation(boundary_output, unnamed_boundary_source, cut);
    require_uncapturable(incomplete_boundary, {TopologyKind::Edge, 0},
                        "a named cap concealed an unnamed boundary contributor");
    auto missing_boundary_history = boundary_output;
    missing_boundary_history.argument_history.pop_back();
    auto missing_boundary = TopologyReferences::operation(missing_boundary_history, boundary_source, cut);
    require(missing_boundary.resolve(TopologyKind::Edge, start_boundary).state == ReferenceState::Ambiguous,
            "incomplete source history produced a resolved boundary name");
    require_uncapturable(missing_boundary, {TopologyKind::Edge, 0},
                        "missing history was laundered through exact boundary incidence");
    require(missing_boundary.resolve(TopologyKind::Vertex, old_vertex).state == ReferenceState::Ambiguous,
            "unreported old vertex was declared deleted by new boundary incidence");

    auto invalid_boundary = boundary_output;
    invalid_boundary.argument_history[0].boundary_generated.push_back({TopologyKind::Face, 0});
    refused = false;
    try { (void)TopologyReferences::operation(invalid_boundary, boundary_source, cut); }
    catch (const std::invalid_argument&) { refused = true; }
    require(refused, "face target was accepted as face-boundary incidence");
}
