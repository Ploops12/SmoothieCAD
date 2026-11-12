#include "blendercad/geometry.hpp"

#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <GProp_GProps.hxx>
#include <Standard_Version.hxx>
#include <TopoDS_Shape.hxx>

#include <cmath>
#include <atomic>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <unordered_map>

namespace blendercad {
namespace {

class OcctGeometryAdapter final : public GeometryAdapter {
public:
    OcctGeometryAdapter() : adapter_id_(next_adapter_id_.fetch_add(1)) {
        if (adapter_id_ == 0) throw std::overflow_error("geometry adapter identity space exhausted");
    }

    ShapeHandle make_box(double x_mm, double y_mm, double z_mm) override {
        validate_dimension(x_mm, "x");
        validate_dimension(y_mm, "y");
        validate_dimension(z_mm, "z");
        TopoDS_Shape box = BRepPrimAPI_MakeBox(x_mm, y_mm, z_mm).Shape();

        std::lock_guard lock(mutex_);
        if (next_handle_ == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("geometry handle space exhausted");
        }
        const ShapeHandle handle{adapter_id_, next_handle_++};
        shapes_.emplace(handle.value, std::move(box));
        return handle;
    }

    double volume_mm3(ShapeHandle handle) const override {
        const TopoDS_Shape shape = lookup(handle);
        GProp_GProps properties;
        BRepGProp::VolumeProperties(shape, properties);
        return properties.Mass();
    }

    bool is_valid(ShapeHandle handle) const override {
        return BRepCheck_Analyzer(lookup(handle)).IsValid();
    }

    void release(ShapeHandle handle) override {
        validate_owner(handle);
        std::lock_guard lock(mutex_);
        if (shapes_.erase(handle.value) == 0) {
            throw std::invalid_argument("unknown or released geometry handle");
        }
    }

private:
    static void validate_dimension(double value, const char* axis) {
        if (!std::isfinite(value) || value <= 0.0) {
            throw std::invalid_argument(std::string("box ") + axis + " dimension must be finite and positive");
        }
    }

    TopoDS_Shape lookup(ShapeHandle handle) const {
        validate_owner(handle);
        std::lock_guard lock(mutex_);
        const auto found = shapes_.find(handle.value);
        if (found == shapes_.end()) throw std::invalid_argument("unknown or released geometry handle");
        return found->second;
    }

    void validate_owner(ShapeHandle handle) const {
        if (handle.adapter != adapter_id_) {
            throw std::invalid_argument("geometry handle belongs to a different adapter");
        }
    }

    inline static std::atomic<std::uint64_t> next_adapter_id_{1};
    const std::uint64_t adapter_id_;
    mutable std::mutex mutex_;
    std::unordered_map<std::uint64_t, TopoDS_Shape> shapes_;
    std::uint64_t next_handle_{1};
};

} // namespace

std::unique_ptr<GeometryAdapter> make_occt_geometry_adapter() {
    return std::make_unique<OcctGeometryAdapter>();
}

const char* occt_version() noexcept {
    return OCC_VERSION_COMPLETE;
}

} // namespace blendercad
