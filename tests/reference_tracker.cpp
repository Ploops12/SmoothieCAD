#include "blendercad/reference_tracker.hpp"

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
}
