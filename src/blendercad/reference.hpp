#pragma once

#include "blendercad/document_id.hpp"
#include "blendercad/topology.hpp"

#include <string>
#include <vector>

namespace blendercad {

// A construction/provenance key, not a saved evaluated face/edge ordinal.
struct ReferenceAnchor final {
    DocumentId feature;
    std::string key;
    friend bool operator==(const ReferenceAnchor&, const ReferenceAnchor&) = default;
};

struct PersistentReference final {
    DocumentId body;
    TopologyKind kind;
    ReferenceAnchor anchor;
    friend bool operator==(const PersistentReference&, const PersistentReference&) = default;
};

struct ReferenceDefinition final {
    DocumentId id;
    std::string name;
    PersistentReference target;
    friend bool operator==(const ReferenceDefinition&, const ReferenceDefinition&) = default;
};

enum class ReferenceState { Resolved, Split, Merged, Deleted, Ambiguous, Unavailable };

struct ReferenceResolution final {
    ReferenceState state{ReferenceState::Unavailable};
    std::vector<TopologyElement> candidates;
    std::string diagnostic;
};

// Strict canonical key validation is independent of evaluated kernel objects.
// Unknown well-formed construction keys remain repairable saved intent.
void validate_reference(const PersistentReference& reference);

} // namespace blendercad
