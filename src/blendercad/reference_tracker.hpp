#pragma once

#include "blendercad/reference.hpp"

#include <array>

namespace blendercad {

struct NamedTopologySeed final {
    TopologyElement target;
    ReferenceAnchor anchor;
};

// Evaluated provenance, rebuilt from design definitions and exact kernel
// history. No kernel objects, geometric proximity guesses or persisted indices.
class TopologyReferences final {
public:
    static TopologyReferences seed(const GeometryTopology& topology,
                                   const std::vector<NamedTopologySeed>& seeds);
    static TopologyReferences operation(const GeometryTopology& topology,
                                        const TopologyReferences& argument,
                                        const TopologyReferences& tool,
                                        const DocumentId& feature);
    [[nodiscard]] ReferenceResolution resolve(TopologyKind kind, const ReferenceAnchor& anchor) const;
    [[nodiscard]] ReferenceAnchor capture(TopologyElement element) const;

private:
    struct Tag final {
        ReferenceAnchor anchor;
        bool split{};
        bool merged{};
        bool ambiguous{};
    };
    struct Lost final {
        TopologyKind kind;
        ReferenceAnchor anchor;
        bool deleted{};
    };
    using Elements = std::vector<std::vector<Tag>>;
    std::array<Elements, 3> elements_;
    std::vector<Lost> lost_;
};

} // namespace blendercad
