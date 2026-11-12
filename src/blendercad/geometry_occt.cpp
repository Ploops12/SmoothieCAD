#include "blendercad/geometry.hpp"

#include <BRepCheck_Analyzer.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepGProp.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRep_Tool.hxx>
#include <GCPnts_QuasiUniformDeflection.hxx>
#include <GProp_GProps.hxx>
#include <DESTEP_Parameters.hxx>
#include <Poly_Triangulation.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <Standard_Version.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Shape.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_ListOfShape.hxx>
#include <gp_Circ.hxx>

#include <cmath>
#include <algorithm>
#include <atomic>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <unordered_map>

namespace blendercad {
namespace {

// STEP sessions may initialize shared OCCT exchange registries.
std::mutex exchange_mutex;

gp_Pnt point_in_plane(Point2 point, const SketchPlane& plane) {
    if (!std::isfinite(point.x) || !std::isfinite(point.y)) {
        throw std::invalid_argument("profile coordinates must be finite");
    }
    return gp_Pnt(plane.origin.x + point.x * plane.x_axis.x + point.y * plane.y_axis.x,
                  plane.origin.y + point.x * plane.x_axis.y + point.y * plane.y_axis.y,
                  plane.origin.z + point.x * plane.x_axis.z + point.y * plane.y_axis.z);
}

gp_Vec plane_normal(const SketchPlane& plane) {
    for (const auto vector : {plane.origin, plane.x_axis, plane.y_axis}) {
        if (!std::isfinite(vector.x) || !std::isfinite(vector.y) || !std::isfinite(vector.z)) {
            throw std::invalid_argument("sketch plane must contain finite coordinates");
        }
    }
    const gp_Vec x(plane.x_axis.x, plane.x_axis.y, plane.x_axis.z);
    const gp_Vec y(plane.y_axis.x, plane.y_axis.y, plane.y_axis.z);
    if (std::abs(x.SquareMagnitude() - 1.0) > 1e-10 ||
        std::abs(y.SquareMagnitude() - 1.0) > 1e-10 || std::abs(x.Dot(y)) > 1e-10) {
        throw std::invalid_argument("sketch plane axes must be orthonormal");
    }
    return x.Crossed(y);
}

void append_point(std::vector<double>& output, const gp_Pnt& point) {
    output.insert(output.end(), {point.X(), point.Y(), point.Z()});
}

double maximum_tolerance(const TopoDS_Shape& shape) {
    double maximum = 0.0;
    for (TopExp_Explorer explorer(shape, TopAbs_VERTEX); explorer.More(); explorer.Next()) {
        maximum = std::max(maximum, BRep_Tool::Tolerance(TopoDS::Vertex(explorer.Current())));
    }
    for (TopExp_Explorer explorer(shape, TopAbs_EDGE); explorer.More(); explorer.Next()) {
        maximum = std::max(maximum, BRep_Tool::Tolerance(TopoDS::Edge(explorer.Current())));
    }
    for (TopExp_Explorer explorer(shape, TopAbs_FACE); explorer.More(); explorer.Next()) {
        maximum = std::max(maximum, BRep_Tool::Tolerance(TopoDS::Face(explorer.Current())));
    }
    return maximum;
}

class OcctGeometryAdapter final : public GeometryAdapter {
public:
    explicit OcctGeometryAdapter(double budget_mm)
        : adapter_id_(next_adapter_id_.fetch_add(1)), budget_mm_(budget_mm) {
        if (adapter_id_ == 0) throw std::overflow_error("geometry adapter identity space exhausted");
        validate_dimension(budget_mm, "geometric budget");
    }

    ShapeHandle make_box(double x_mm, double y_mm, double z_mm) override {
        validate_dimension(x_mm, "x");
        validate_dimension(y_mm, "y");
        validate_dimension(z_mm, "z");
        return store(BRepPrimAPI_MakeBox(x_mm, y_mm, z_mm).Shape());
    }

    ShapeHandle extrude_polygon(const std::vector<Point2>& points, const SketchPlane& plane,
                                double depth_mm) override {
        validate_dimension(depth_mm, "extrusion depth");
        const auto normal = plane_normal(plane);
        if (points.size() < 3) throw std::invalid_argument("closed profile needs at least three points");
        BRepBuilderAPI_MakePolygon polygon;
        for (const auto point : points) polygon.Add(point_in_plane(point, plane));
        polygon.Close();
        if (!polygon.IsDone()) throw std::invalid_argument("could not construct closed profile");
        BRepBuilderAPI_MakeFace face(polygon.Wire());
        if (!face.IsDone() || !BRepCheck_Analyzer(face.Face()).IsValid()) {
            throw std::invalid_argument("profile does not define a valid planar face");
        }
        return store(BRepPrimAPI_MakePrism(face.Face(), normal * depth_mm).Shape());
    }

    ShapeHandle extrude_circle(Point2 center, double radius_mm, const SketchPlane& plane,
                               double depth_mm) override {
        validate_dimension(radius_mm, "circle radius");
        validate_dimension(depth_mm, "extrusion depth");
        const auto normal = plane_normal(plane);
        const gp_Ax2 frame(point_in_plane(center, plane), gp_Dir(normal),
                           gp_Dir(plane.x_axis.x, plane.x_axis.y, plane.x_axis.z));
        const auto edge = BRepBuilderAPI_MakeEdge(gp_Circ(frame, radius_mm)).Edge();
        const auto wire = BRepBuilderAPI_MakeWire(edge).Wire();
        const auto face = BRepBuilderAPI_MakeFace(wire).Face();
        return store(BRepPrimAPI_MakePrism(face, normal * depth_mm).Shape());
    }

    ShapeHandle subtract(ShapeHandle body, ShapeHandle tool) override {
        TopTools_ListOfShape arguments;
        TopTools_ListOfShape tools;
        arguments.Append(lookup(body));
        tools.Append(lookup(tool));
        BRepAlgoAPI_Cut cut;
        cut.SetArguments(arguments);
        cut.SetTools(tools);
        cut.SetNonDestructive(true);
        cut.Build();
        if (!cut.IsDone()) throw std::runtime_error("OCCT subtraction failed");
        return store(cut.Shape());
    }

    DisplayMesh tessellate(ShapeHandle handle, double linear_deflection_mm,
                           double angular_deflection_rad) const override {
        validate_dimension(linear_deflection_mm, "linear deflection");
        validate_dimension(angular_deflection_rad, "angular deflection");
        if (angular_deflection_rad > 3.141592653589793) {
            throw std::invalid_argument("angular deflection must not exceed pi radians");
        }
        // Meshing attaches mutable caches to TShapes. Copy topology/geometry so
        // concurrent queries of the authoritative shape cannot observe that work.
        const auto shape = BRepBuilderAPI_Copy(lookup(handle), true, false).Shape();
        BRepMesh_IncrementalMesh mesher(shape, linear_deflection_mm, false, angular_deflection_rad, false);
        if (!mesher.IsDone()) throw std::runtime_error("OCCT tessellation failed");

        DisplayMesh result;
        TopTools_IndexedMapOfShape faces;
        TopExp::MapShapes(shape, TopAbs_FACE, faces);
        result.face_count = static_cast<std::uint32_t>(faces.Extent());
        for (int index = 1; index <= faces.Extent(); ++index) {
            const auto face = TopoDS::Face(faces(index));
            TopLoc_Location location;
            const auto triangulation = BRep_Tool::Triangulation(face, location);
            if (triangulation.IsNull()) throw std::runtime_error("OCCT face has no triangulation");
            const auto base = result.positions.size() / 3;
            if (base + triangulation->NbNodes() > std::numeric_limits<std::uint32_t>::max()) {
                throw std::overflow_error("display mesh exceeds 32-bit index capacity");
            }
            for (int node = 1; node <= triangulation->NbNodes(); ++node) {
                append_point(result.positions, triangulation->Node(node).Transformed(location.Transformation()));
            }
            for (int triangle = 1; triangle <= triangulation->NbTriangles(); ++triangle) {
                int a, b, c;
                triangulation->Triangle(triangle).Get(a, b, c);
                if (face.Orientation() == TopAbs_REVERSED) std::swap(b, c);
                for (const auto node : {a, b, c}) {
                    result.triangles.push_back(static_cast<std::uint32_t>(base + node - 1));
                }
                result.triangle_faces.push_back(static_cast<std::uint32_t>(index - 1));
            }
        }
        TopTools_IndexedMapOfShape edges;
        TopExp::MapShapes(shape, TopAbs_EDGE, edges);
        for (int index = 1; index <= edges.Extent(); ++index) {
            const auto edge = TopoDS::Edge(edges(index));
            if (!BRep_Tool::Degenerated(edge)) {
                const BRepAdaptor_Curve curve(edge);
                const GCPnts_QuasiUniformDeflection sampling(curve, linear_deflection_mm);
                if (!sampling.IsDone()) throw std::runtime_error("OCCT edge tessellation failed");
                for (int node = 1; node <= sampling.NbPoints(); ++node) {
                    append_point(result.edge_positions, sampling.Value(node));
                }
            }
            result.edge_offsets.push_back(result.edge_positions.size() / 3);
        }
        TopTools_IndexedMapOfShape vertices;
        TopExp::MapShapes(shape, TopAbs_VERTEX, vertices);
        for (int index = 1; index <= vertices.Extent(); ++index) {
            append_point(result.cad_vertices, BRep_Tool::Pnt(TopoDS::Vertex(vertices(index))));
        }
        return result;
    }

    void write_step(ShapeHandle handle, const std::string& path) const override {
        const auto shape = lookup(handle);
        std::lock_guard lock(exchange_mutex);
        STEPControl_Writer writer;
        DESTEP_Parameters parameters;
        parameters.WriteUnit = UnitsMethods_LengthUnit_Millimeter;
        if (writer.Transfer(shape, STEPControl_AsIs, parameters) != IFSelect_RetDone ||
            writer.Write(path.c_str()) != IFSelect_RetDone) {
            throw std::runtime_error("could not write STEP file: " + path);
        }
    }

    ShapeHandle read_step(const std::string& path) override {
        std::lock_guard lock(exchange_mutex);
        STEPControl_Reader reader;
        if (reader.ReadFile(path.c_str()) != IFSelect_RetDone) {
            throw std::runtime_error("could not read STEP file: " + path);
        }
        reader.SetSystemLengthUnit(1.0); // OCCT model length unit: millimetres.
        if (reader.TransferRoots() == 0) throw std::runtime_error("STEP file contains no transferable shapes");
        return store(reader.OneShape());
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

    double max_tolerance_mm(ShapeHandle handle) const override {
        return maximum_tolerance(lookup(handle));
    }

    void release(ShapeHandle handle) override {
        validate_owner(handle);
        std::lock_guard lock(mutex_);
        if (shapes_.erase(handle.value) == 0) {
            throw std::invalid_argument("unknown or released geometry handle");
        }
    }

private:
    ShapeHandle store(TopoDS_Shape shape) {
        if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid() ||
            !TopExp_Explorer(shape, TopAbs_SOLID).More()) {
            throw std::runtime_error("operation did not produce a valid solid");
        }
        if (maximum_tolerance(shape) > budget_mm_) {
            throw std::runtime_error("result exceeds the configured geometric tolerance budget");
        }
        std::lock_guard lock(mutex_);
        if (next_handle_ == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("geometry handle space exhausted");
        }
        const ShapeHandle handle{adapter_id_, next_handle_++};
        shapes_.emplace(handle.value, std::move(shape));
        return handle;
    }
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
    const double budget_mm_;
    mutable std::mutex mutex_;
    std::unordered_map<std::uint64_t, TopoDS_Shape> shapes_;
    std::uint64_t next_handle_{1};
};

} // namespace

std::unique_ptr<GeometryAdapter> make_occt_geometry_adapter(double geometric_budget_mm) {
    return std::make_unique<OcctGeometryAdapter>(geometric_budget_mm);
}

const char* occt_version() noexcept {
    return OCC_VERSION_COMPLETE;
}

} // namespace blendercad
