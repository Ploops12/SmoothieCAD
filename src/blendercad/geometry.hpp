#pragma once

#include <cstdint>
#include <memory>

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
    [[nodiscard]] virtual double volume_mm3(ShapeHandle shape) const = 0;
    [[nodiscard]] virtual bool is_valid(ShapeHandle shape) const = 0;
    virtual void release(ShapeHandle shape) = 0;
};

[[nodiscard]] std::unique_ptr<GeometryAdapter> make_occt_geometry_adapter();
[[nodiscard]] const char* occt_version() noexcept;

} // namespace blendercad
