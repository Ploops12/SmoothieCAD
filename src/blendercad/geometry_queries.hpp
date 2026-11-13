#pragma once

#include "blendercad/geometry_types.hpp"
#include "blendercad/topology.hpp"

#include <optional>

namespace blendercad {

struct TopologyMeasure final {
    TopologyElement element;
    Vector3 center_mm;
    std::optional<double> length_mm;
    std::optional<double> area_mm2;
    std::optional<double> radius_mm;
};

struct TopologyPoint final {
    TopologyElement element;
    Vector3 point_mm;
    double distance_mm{};
};

struct FaceHit final {
    std::uint32_t face_index{};
    Vector3 point_mm;
    std::optional<Vector3> normal;
    double distance_mm{};
};

} // namespace blendercad
