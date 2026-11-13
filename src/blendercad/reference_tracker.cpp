#include "blendercad/reference_tracker.hpp"

#include <algorithm>
#include <map>
#include <set>
#include <stdexcept>

namespace blendercad {
namespace {

std::size_t domain(TopologyKind kind) {
    switch (kind) {
    case TopologyKind::Face: return 0;
    case TopologyKind::Edge: return 1;
    case TopologyKind::Vertex: return 2;
    }
    throw std::invalid_argument("invalid topology kind");
}

std::string signature(const ReferenceAnchor& anchor) {
    return anchor.feature.to_string() + "/" + std::to_string(anchor.key.size()) + ":" + anchor.key;
}

bool same_element(TopologyElement a, TopologyElement b) { return a == b; }

} // namespace

TopologyReferences TopologyReferences::seed(const GeometryTopology& topology,
                                           const std::vector<NamedTopologySeed>& seeds) {
    TopologyReferences result;
    for (std::size_t kind = 0; kind < 3; ++kind) result.elements_[kind].resize(topology.counts[kind]);
    for (const auto& seed : seeds) {
        auto& elements = result.elements_[domain(seed.target.kind)];
        if (seed.target.index >= elements.size()) throw std::invalid_argument("construction seed topology index out of range");
        auto& tags = elements[seed.target.index];
        if (std::ranges::none_of(tags, [&](const auto& tag) { return tag.anchor == seed.anchor; })) {
            tags.push_back({seed.anchor});
        }
    }
    for (auto& elements : result.elements_) {
        std::map<std::string, std::size_t> multiplicity;
        for (const auto& tags : elements) for (const auto& tag : tags) ++multiplicity[signature(tag.anchor)];
        for (auto& tags : elements) for (auto& tag : tags) tag.ambiguous = multiplicity[signature(tag.anchor)] > 1;
    }
    return result;
}

TopologyReferences TopologyReferences::operation(const GeometryTopology& topology,
                                                const TopologyReferences& argument,
                                                const TopologyReferences& tool,
                                                const DocumentId& feature) {
    TopologyReferences result;
    std::array<std::vector<std::set<std::string>>, 3> origins;
    std::array<std::vector<std::set<std::string>>, 3> same_kind_sources;
    std::array<std::vector<bool>, 3> incomplete_origins;
    bool complete_history = true;
    for (std::size_t kind = 0; kind < 3; ++kind) {
        result.elements_[kind].resize(topology.counts[kind]);
        origins[kind].resize(topology.counts[kind]);
        same_kind_sources[kind].resize(topology.counts[kind]);
        incomplete_origins[kind].resize(topology.counts[kind]);
    }
    auto check_target = [&](TopologyElement target) -> std::size_t {
        const auto kind = domain(target.kind);
        if (target.index >= result.elements_[kind].size()) throw std::invalid_argument("kernel history target index out of range");
        return kind;
    };
    auto propagate = [&](const TopologyReferences& source, const auto& history, char operand) {
        result.lost_.insert(result.lost_.end(), source.lost_.begin(), source.lost_.end());
        std::array<std::vector<bool>, 3> visited;
        for (std::size_t kind = 0; kind < 3; ++kind) visited[kind].resize(source.elements_[kind].size());
        for (const auto& entry : history) {
            const auto kind = domain(entry.source.kind);
            if (entry.source.index >= source.elements_[kind].size()) throw std::invalid_argument("kernel history source index out of range");
            if (visited[kind][entry.source.index]) throw std::invalid_argument("duplicate kernel history source entry");
            visited[kind][entry.source.index] = true;
            const auto& tags = source.elements_[kind][entry.source.index];
            std::vector<TopologyElement> continuations;
            for (const auto target : entry.modified) {
                check_target(target);
                if (target.kind != entry.source.kind) throw std::invalid_argument("modified history must preserve topology kind");
                if (std::ranges::none_of(continuations, [&](auto other) { return same_element(other, target); })) continuations.push_back(target);
            }
            std::set<std::string> origin_names;
            for (const auto& tag : tags) {
                const auto resolution = source.resolve(entry.source.kind, tag.anchor);
                if (resolution.state == ReferenceState::Resolved && resolution.candidates.size() == 1 && resolution.candidates.front() == entry.source) {
                    origin_names.insert(signature(tag.anchor));
                }
            }
            const bool incomplete_source = origin_names.empty();
            const auto source_id = std::string(1, operand) + std::to_string(kind) + "/" + std::to_string(entry.source.index);
            for (const auto target : continuations) {
                const auto output_kind = domain(target.kind);
                same_kind_sources[output_kind][target.index].insert(source_id);
                origins[output_kind][target.index].insert(origin_names.begin(), origin_names.end());
                incomplete_origins[output_kind][target.index] = incomplete_origins[output_kind][target.index] || incomplete_source;
                auto& output_tags = result.elements_[output_kind][target.index];
                for (auto tag : tags) {
                    tag.split = tag.split || continuations.size() > 1;
                    // Multiple aliases of one source topology element are not a
                    // merge. Distinct predecessor elements are counted below.
                    auto found = std::ranges::find_if(output_tags, [&](const auto& other) { return other.anchor == tag.anchor; });
                    if (found == output_tags.end()) output_tags.push_back(tag);
                    else { found->split |= tag.split; found->merged |= tag.merged; found->ambiguous |= tag.ambiguous; }
                }
            }
            for (const auto target : entry.generated) {
                const auto output_kind = check_target(target);
                origins[output_kind][target.index].insert(origin_names.begin(), origin_names.end());
                incomplete_origins[output_kind][target.index] = incomplete_origins[output_kind][target.index] || incomplete_source;
            }
            if (continuations.empty()) {
                for (const auto& tag : tags) result.lost_.push_back({entry.source.kind, tag.anchor, entry.deleted});
            }
        }
        // An incomplete kernel history never silently deletes a reference.
        for (std::size_t kind = 0; kind < 3; ++kind) {
            for (std::size_t index = 0; index < visited[kind].size(); ++index) {
                if (!visited[kind][index]) {
                    complete_history = false;
                    for (const auto& tag : source.elements_[kind][index]) result.lost_.push_back({static_cast<TopologyKind>(kind), tag.anchor, false});
                }
            }
        }
    };
    propagate(argument, topology.argument_history, 'a');
    propagate(tool, topology.tool_history, 't');
    // New topology may be named by the complete generating origin set, never a
    // traversal ordinal. Non-unique provenance stays explicitly ambiguous.
    for (std::size_t kind = 0; kind < 3; ++kind) {
        for (std::size_t index = 0; index < result.elements_[kind].size(); ++index) {
            auto& tags = result.elements_[kind][index];
            if (same_kind_sources[kind][index].size() > 1) {
                for (auto& tag : tags) tag.merged = true;
            }
            if (!origins[kind][index].empty()) {
                std::string key = "derived:" + std::to_string(kind) + ":";
                for (const auto& origin : origins[kind][index]) key += std::to_string(origin.size()) + ":" + origin;
                // A too-large construction expression cannot be truncated into
                // an unrelated identity. Keep only inherited names in that case.
                if (key.size() <= 65536) tags.push_back({{feature, std::move(key)}, false, false,
                                                        incomplete_origins[kind][index] || !complete_history});
            }
        }
        std::map<std::string, std::size_t> multiplicity;
        for (const auto& tags : result.elements_[kind]) {
            std::set<std::string> seen;
            for (const auto& tag : tags) seen.insert(signature(tag.anchor));
            for (const auto& name : seen) ++multiplicity[name];
        }
        for (auto& tags : result.elements_[kind]) {
            for (auto& tag : tags) {
                if (tag.anchor.feature == feature && tag.anchor.key.starts_with("derived:") && multiplicity[signature(tag.anchor)] > 1) tag.ambiguous = true;
            }
        }
    }
    return result;
}

ReferenceResolution TopologyReferences::resolve(TopologyKind kind, const ReferenceAnchor& anchor) const {
    ReferenceResolution result;
    bool split = false, merged = false, ambiguous = false;
    const auto& elements = elements_[domain(kind)];
    for (std::size_t index = 0; index < elements.size(); ++index) {
        for (const auto& tag : elements[index]) {
            if (tag.anchor == anchor) {
                result.candidates.push_back({kind, static_cast<std::uint32_t>(index)});
                split |= tag.split; merged |= tag.merged; ambiguous |= tag.ambiguous;
                break;
            }
        }
    }
    if (!result.candidates.empty()) {
        // Losing one fragment after a split cannot silently select the survivor.
        bool lost_fragment = false;
        for (const auto& lost : lost_) if (lost.kind == kind && lost.anchor == anchor) lost_fragment = true;
        if (ambiguous) result.state = ReferenceState::Ambiguous;
        else if (result.candidates.size() > 1 || split || lost_fragment) result.state = ReferenceState::Split;
        else if (merged) result.state = ReferenceState::Merged;
        else result.state = ReferenceState::Resolved;
    } else {
        bool known = false, deleted = true;
        for (const auto& lost : lost_) {
            if (lost.kind == kind && lost.anchor == anchor) { known = true; deleted &= lost.deleted; }
        }
        result.state = known && deleted ? ReferenceState::Deleted : ReferenceState::Ambiguous;
    }
    switch (result.state) {
    case ReferenceState::Resolved: result.diagnostic = "Reference resolved from construction provenance"; break;
    case ReferenceState::Split: result.diagnostic = "Referenced topology split; explicitly select a replacement region to repair"; break;
    case ReferenceState::Merged: result.diagnostic = "Referenced topology merged; explicitly confirm a replacement to repair"; break;
    case ReferenceState::Deleted: result.diagnostic = "Referenced topology was deleted; select a replacement to repair"; break;
    default: result.diagnostic = "Construction history cannot identify a unique target; select a replacement to repair"; break;
    }
    return result;
}

ReferenceAnchor TopologyReferences::capture(TopologyElement element) const {
    const auto& elements = elements_[domain(element.kind)];
    if (element.index >= elements.size()) throw std::invalid_argument("topology index out of range");
    auto candidates = elements[element.index];
    std::ranges::sort(candidates, [](const auto& a, const auto& b) {
        const bool a_derived = a.anchor.key.starts_with("derived:"), b_derived = b.anchor.key.starts_with("derived:");
        if (a_derived != b_derived) return !a_derived;
        return signature(a.anchor) < signature(b.anchor);
    });
    for (const auto& candidate : candidates) {
        const auto resolution = resolve(element.kind, candidate.anchor);
        if (resolution.state == ReferenceState::Resolved && resolution.candidates.size() == 1 && resolution.candidates.front() == element) return candidate.anchor;
    }
    throw std::invalid_argument("Selected topology has no unique persistent construction identity; choose another region to repair");
}

} // namespace blendercad
