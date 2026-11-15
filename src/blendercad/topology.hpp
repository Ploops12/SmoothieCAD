#pragma once

#include <array>
#include <cstdint>
#include <vector>

namespace blendercad {

enum class TopologyKind { Face, Edge, Vertex };

struct TopologyElement final {
    TopologyKind kind;
    std::uint32_t index{};
    friend bool operator==(const TopologyElement&, const TopologyElement&) = default;
};

enum class TopologySeedRole { StartCap, EndCap, Side, StartEdge, EndEdge, SweepEdge, StartVertex, EndVertex };

// Kernel-local construction provenance. profile_index addresses the ordered
// input polygon edge/vertex, or zero for a circle. The feature evaluator maps
// those inputs to persistent sketch identities; these ordinals are never saved.
struct TopologySeed final {
    TopologyElement target;
    TopologySeedRole role;
    std::uint32_t profile_index{};
};

struct TopologyHistoryEntry final {
    TopologyElement source;
    std::vector<TopologyElement> modified; // Includes unchanged identical output.
    std::vector<TopologyElement> generated;
    bool deleted{}; // Explicit OCCT IsDeleted; missing history is not deletion.
    // Separate from OCCT Generated: new result edges/vertices on the exact
    // boundary of an output face explicitly reported Modified/Generated from
    // this source. The adapter establishes incidence by IsSame membership,
    // never geometric proximity. This is not a same-kind continuation.
    std::vector<TopologyElement> boundary_generated{};
};

struct GeometryTopology final {
    // Indexed by TopologyKind, in exactly the display array numbering.
    std::array<std::uint32_t, 3> counts{};
    std::vector<TopologySeed> seeds;
    std::vector<TopologyHistoryEntry> argument_history;
    std::vector<TopologyHistoryEntry> tool_history;
};

} // namespace blendercad
