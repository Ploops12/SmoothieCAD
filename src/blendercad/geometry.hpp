#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "blendercad/geometry_types.hpp"
#include "blendercad/geometry_queries.hpp"
#include "blendercad/topology.hpp"

namespace blendercad {

// Opaque, adapter-local handle. It is not persistent and cannot be used to
// access a kernel object outside the adapter that created it.
struct ShapeHandle final {
    std::uint64_t adapter{};
    std::uint64_t value{};
    friend bool operator==(const ShapeHandle&, const ShapeHandle&) = default;
};

class GeometryAdapter {
public:
    virtual ~GeometryAdapter() = default;
    [[nodiscard]] virtual ShapeHandle make_box(double x_mm, double y_mm, double z_mm) = 0;
    [[nodiscard]] virtual ShapeHandle extrude_polygon(const std::vector<Point2>& points,
                                                     const SketchPlane& plane, double depth_mm) = 0;
    [[nodiscard]] virtual ShapeHandle extrude_circle(Point2 center, double radius_mm,
                                                    const SketchPlane& plane, double depth_mm) = 0;
    [[nodiscard]] virtual ShapeHandle subtract(ShapeHandle body, ShapeHandle tool) = 0;
    [[nodiscard]] virtual DisplayMesh tessellate(ShapeHandle shape, double linear_deflection_mm,
                                               double angular_deflection_rad) const = 0;
    virtual void write_step(ShapeHandle shape, const std::string& path) const = 0;
    [[nodiscard]] virtual ShapeHandle read_step(const std::string& path) = 0;
    [[nodiscard]] virtual double volume_mm3(ShapeHandle shape) const = 0;
    [[nodiscard]] virtual bool is_valid(ShapeHandle shape) const = 0;
    [[nodiscard]] virtual double max_tolerance_mm(ShapeHandle shape) const = 0;
    [[nodiscard]] virtual GeometryTopology topology(ShapeHandle shape) const = 0;
    [[nodiscard]] virtual TopologyMeasure measure(ShapeHandle shape, TopologyElement element) const = 0;
    [[nodiscard]] virtual TopologyPoint closest_point(ShapeHandle shape, TopologyElement element,
                                                    Vector3 point_mm) const = 0;
    [[nodiscard]] virtual std::optional<FaceHit> ray_cast(ShapeHandle shape, Vector3 origin_mm,
                                                       Vector3 direction, double max_distance_mm) const = 0;
    virtual void release(ShapeHandle shape) = 0;
};

[[nodiscard]] std::unique_ptr<GeometryAdapter> make_occt_geometry_adapter(double geometric_budget_mm = 0.001);
[[nodiscard]] const char* occt_version() noexcept;

} // namespace blendercad
