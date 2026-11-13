#include "blendercad/reference.hpp"

#include <stdexcept>

namespace blendercad {

void validate_reference(const PersistentReference& reference) {
    switch (reference.kind) {
    case TopologyKind::Face: case TopologyKind::Edge: case TopologyKind::Vertex: break;
    default: throw std::invalid_argument("invalid reference topology kind");
    }
    const auto& key = reference.anchor.key;
    if (key.empty() || key.size() > 65536) throw std::invalid_argument("reference key must contain 1..65536 bytes");
    for (const unsigned char c : key) {
        if (c < 0x21 || c > 0x7e) throw std::invalid_argument("reference keys must use printable non-whitespace ASCII");
    }
}

} // namespace blendercad
