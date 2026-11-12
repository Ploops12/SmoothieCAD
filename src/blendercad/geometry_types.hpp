#pragma once

#include <cstdint>
#include <vector>

namespace blendercad {

struct Point2 final {
    double x{}; double y{};
    friend bool operator==(const Point2&, const Point2&) = default;
};
struct Vector3 final {
    double x{}; double y{}; double z{};
    friend bool operator==(const Vector3&, const Vector3&) = default;
};

// Right-handed local sketch coordinates in millimetres. Axes must be unit
// length and orthogonal; geometry adapters reject malformed frames.
struct SketchPlane final {
    Vector3 origin;
    Vector3 x_axis{1.0, 0.0, 0.0};
    Vector3 y_axis{0.0, 1.0, 0.0};
    friend bool operator==(const SketchPlane&, const SketchPlane&) = default;
};

// Disposable display arrays. Topology indices are local to this evaluated
// shape, not persistent references. The publisher must attach its revision.
struct DisplayMesh final {
    std::vector<double> positions;
    std::vector<std::uint32_t> triangles;
    std::vector<std::uint32_t> triangle_faces;
    std::vector<double> edge_positions;
    std::vector<std::uint64_t> edge_offsets{0};
    std::vector<double> cad_vertices;
    std::uint32_t face_count{};
};

} // namespace blendercad
