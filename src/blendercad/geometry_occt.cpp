#include "blendercad/geometry.hpp"

#include <BRepCheck_Analyzer.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepGProp.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepFilletAPI_MakeFillet.hxx>
#include <BRepLProp_SLProps.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRep_Tool.hxx>
#include <GCPnts_QuasiUniformDeflection.hxx>
#include <GProp_GProps.hxx>
#include <IntCurvesFace_ShapeIntersector.hxx>
#include <DESTEP_Parameters.hxx>
#include <Poly_Triangulation.hxx>
#include <Precision.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <Standard_Version.hxx>
#include <Standard_Failure.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Shape.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_ListOfShape.hxx>
#include <TopTools_ListIteratorOfListOfShape.hxx>
#include <gp_Circ.hxx>
#include <gp_Cylinder.hxx>
#include <gp_Sphere.hxx>
#include <gp_Lin.hxx>

#include <cmath>
#include <algorithm>
#include <array>
#include <atomic>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

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

Vector3 public_point(const gp_Pnt& point) {
    if (!std::isfinite(point.X()) || !std::isfinite(point.Y()) || !std::isfinite(point.Z())) {
        throw std::runtime_error("kernel query returned a non-finite point");
    }
    return {point.X(), point.Y(), point.Z()};
}

gp_Pnt query_point(Vector3 point, const char* context) {
    if (!std::isfinite(point.x) || !std::isfinite(point.y) || !std::isfinite(point.z)) {
        throw std::invalid_argument(std::string(context) + " must contain finite coordinates");
    }
    return {point.x, point.y, point.z};
}

std::size_t query_slot(TopologyKind kind) {
    switch (kind) {
    case TopologyKind::Face: return 0;
    case TopologyKind::Edge: return 1;
    case TopologyKind::Vertex: return 2;
    }
    throw std::invalid_argument("invalid topology kind");
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

using TopologyMaps = std::array<TopTools_IndexedMapOfShape, 3>;

std::optional<std::size_t> topology_slot_if_supported(TopAbs_ShapeEnum kind) {
    switch (kind) {
    case TopAbs_FACE: return 0;
    case TopAbs_EDGE: return 1;
    case TopAbs_VERTEX: return 2;
    default: return std::nullopt;
    }
}

std::size_t topology_slot(TopAbs_ShapeEnum kind) {
    const auto slot = topology_slot_if_supported(kind);
    if (!slot) throw std::invalid_argument("topology history contains a non face/edge/vertex element");
    return *slot;
}

TopologyKind public_kind(std::size_t slot) {
    switch (slot) {
    case 0: return TopologyKind::Face;
    case 1: return TopologyKind::Edge;
    default: return TopologyKind::Vertex;
    }
}

TopologyMaps topology_maps(const TopoDS_Shape& shape) {
    TopologyMaps maps;
    TopExp::MapShapes(shape, TopAbs_FACE, maps[0]);
    TopExp::MapShapes(shape, TopAbs_EDGE, maps[1]);
    TopExp::MapShapes(shape, TopAbs_VERTEX, maps[2]);
    return maps;
}

int find_same(const TopTools_IndexedMapOfShape& map, const TopoDS_Shape& shape) {
    for (int index = 1; index <= map.Extent(); ++index) {
        if (map(index).IsSame(shape)) return index;
    }
    return 0;
}

std::optional<TopologyElement> find_output_element(const TopologyMaps& maps, const TopoDS_Shape& shape) {
    const auto maybe_slot = topology_slot_if_supported(shape.ShapeType());
    if (!maybe_slot) return std::nullopt;
    const auto slot = *maybe_slot;
    const int index = find_same(maps[slot], shape);
    if (index == 0) return std::nullopt;
    return TopologyElement{public_kind(slot), static_cast<std::uint32_t>(index - 1)};
}

void append_unique(std::vector<TopologyElement>& elements, TopologyElement element) {
    if (std::find(elements.begin(), elements.end(), element) == elements.end()) elements.push_back(element);
}

struct PrismSeedRelation final {
    TopoDS_Shape shape;
    TopologySeedRole role;
    std::uint32_t profile_index;
};

template <typename Prism>
void append_prism_seeds(std::vector<PrismSeedRelation>& seeds, Prism& prism, const TopoDS_Face& profile,
                        const std::vector<TopoDS_Edge>& edges,
                        const std::vector<TopoDS_Vertex>& vertices) {
    seeds.push_back({prism.FirstShape(profile), TopologySeedRole::StartCap, 0});
    seeds.push_back({prism.LastShape(profile), TopologySeedRole::EndCap, 0});
    for (std::size_t index = 0; index < edges.size(); ++index) {
        for (TopTools_ListIteratorOfListOfShape generated(prism.Generated(edges[index])); generated.More(); generated.Next()) {
            seeds.push_back({generated.Value(), TopologySeedRole::Side, static_cast<std::uint32_t>(index)});
        }
        seeds.push_back({prism.FirstShape(edges[index]), TopologySeedRole::StartEdge, static_cast<std::uint32_t>(index)});
        seeds.push_back({prism.LastShape(edges[index]), TopologySeedRole::EndEdge, static_cast<std::uint32_t>(index)});
    }
    for (std::size_t index = 0; index < vertices.size(); ++index) {
        for (TopTools_ListIteratorOfListOfShape generated(prism.Generated(vertices[index])); generated.More(); generated.Next()) {
            seeds.push_back({generated.Value(), TopologySeedRole::SweepEdge, static_cast<std::uint32_t>(index)});
        }
        seeds.push_back({prism.FirstShape(vertices[index]), TopologySeedRole::StartVertex, static_cast<std::uint32_t>(index)});
        seeds.push_back({prism.LastShape(vertices[index]), TopologySeedRole::EndVertex, static_cast<std::uint32_t>(index)});
    }
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
        std::vector<TopoDS_Vertex> vertices;
        vertices.reserve(points.size());
        for (const auto point : points) {
            BRepBuilderAPI_MakeVertex vertex(point_in_plane(point, plane));
            if (!vertex.IsDone()) throw std::invalid_argument("could not construct profile vertex");
            vertices.push_back(vertex.Vertex());
        }
        std::vector<TopoDS_Edge> edges;
        edges.reserve(points.size());
        BRepBuilderAPI_MakeWire wire;
        for (std::size_t index = 0; index < points.size(); ++index) {
            BRepBuilderAPI_MakeEdge edge(vertices[index], vertices[(index + 1) % points.size()]);
            if (!edge.IsDone()) throw std::invalid_argument("could not construct profile edge");
            edges.push_back(edge.Edge());
            wire.Add(edges.back());
        }
        if (!wire.IsDone()) throw std::invalid_argument("could not construct closed profile");
        BRepBuilderAPI_MakeFace face(wire.Wire());
        if (!face.IsDone() || !BRepCheck_Analyzer(face.Face()).IsValid()) {
            throw std::invalid_argument("profile does not define a valid planar face");
        }
        BRepPrimAPI_MakePrism prism(face.Face(), normal * depth_mm);
        if (!prism.IsDone()) throw std::runtime_error("could not extrude profile");
        std::vector<PrismSeedRelation> seeds;
        append_prism_seeds(seeds, prism, face.Face(), edges, vertices);
        return store(prism.Shape(), seeds);
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
        BRepPrimAPI_MakePrism prism(face, normal * depth_mm);
        if (!prism.IsDone()) throw std::runtime_error("could not extrude circle");
        TopTools_IndexedMapOfShape vertex_map;
        TopExp::MapShapes(edge, TopAbs_VERTEX, vertex_map);
        std::vector<TopoDS_Edge> edges{edge};
        std::vector<TopoDS_Vertex> vertices;
        vertices.reserve(vertex_map.Extent());
        for (int index = 1; index <= vertex_map.Extent(); ++index) vertices.push_back(TopoDS::Vertex(vertex_map(index)));
        std::vector<PrismSeedRelation> seeds;
        append_prism_seeds(seeds, prism, face, edges, vertices);
        return store(prism.Shape(), seeds);
    }

    ShapeHandle subtract(ShapeHandle body, ShapeHandle tool) override {
        const auto argument_record = lookup_record(body);
        const auto tool_record = lookup_record(tool);
        TopTools_ListOfShape arguments;
        TopTools_ListOfShape tools;
        arguments.Append(argument_record.shape);
        tools.Append(tool_record.shape);
        BRepAlgoAPI_Cut cut;
        cut.SetArguments(arguments);
        cut.SetTools(tools);
        cut.SetNonDestructive(true);
        cut.Build();
        if (!cut.IsDone()) throw std::runtime_error("OCCT subtraction failed");
        const auto result_shape = cut.Shape();
        const auto result_maps = topology_maps(result_shape);
        GeometryTopology result_topology;
        for (std::size_t slot = 0; slot < result_maps.size(); ++slot) {
            result_topology.counts[slot] = static_cast<std::uint32_t>(result_maps[slot].Extent());
        }
        result_topology.argument_history = history_for(cut, argument_record.shape, argument_record.topology, result_maps);
        result_topology.tool_history = history_for(cut, tool_record.shape, tool_record.topology, result_maps);
        return store(result_shape, {}, std::move(result_topology), true);
    }

    ShapeHandle fillet(ShapeHandle shape, const std::vector<std::uint32_t>& edge_indices,
                       double radius_mm) override {
        const auto argument = lookup_record(shape);
        if (!std::isfinite(radius_mm) || radius_mm <= 0) {
            throw std::invalid_argument("fillet radius must be finite and positive");
        }
        if (edge_indices.empty()) throw std::invalid_argument("fillet requires at least one CAD edge");
        const auto original_maps = topology_maps(argument.shape);
        std::unordered_set<std::uint32_t> selected;
        for (const auto index : edge_indices) {
            if (index >= static_cast<std::uint32_t>(original_maps[1].Extent())) {
                throw std::invalid_argument("fillet edge index is out of range for this evaluated shape");
            }
            if (!selected.insert(index).second) {
                throw std::invalid_argument("fillet edge indices must be unique");
            }
        }

        // The local-operation builder can change its argument's TShapes and
        // underlying curves/surfaces even if construction later fails. Never
        // give it authoritative cached geometry or share the argument's meshes.
        BRepBuilderAPI_Copy copier(argument.shape, true, false);
        if (!copier.IsDone()) throw std::runtime_error("could not copy CAD geometry for filleting");
        BRepFilletAPI_MakeFillet builder(copier.Shape());
        for (const auto index : edge_indices) {
            const auto copied = copier.ModifiedShape(original_maps[1](static_cast<int>(index + 1)));
            if (copied.IsNull() || copied.ShapeType() != TopAbs_EDGE) {
                throw std::runtime_error("could not map selected CAD edge into the fillet copy");
            }
            const auto edge = TopoDS::Edge(copied);
            builder.Add(radius_mm, edge);
            if (builder.Contour(edge) == 0) {
                throw std::runtime_error("OCCT could not initialize a fillet contour for a selected edge");
            }
        }
        builder.Build();
        if (!builder.IsDone()) throw std::runtime_error("OCCT constant-radius fillet failed");
        const auto result_shape = builder.Shape();
        const auto result_maps = topology_maps(result_shape);
        GeometryTopology topology;
        for (std::size_t slot = 0; slot < result_maps.size(); ++slot) {
            topology.counts[slot] = static_cast<std::uint32_t>(result_maps[slot].Extent());
        }
        // History source ordinals always address the original authoritative
        // input. Query the builder with its copied counterpart, including exact
        // unchanged IsSame elements; never match geometry by proximity.
        topology.argument_history = history_for(builder, argument.shape, argument.topology, result_maps, &copier);
        append_boundary_history(topology.argument_history, result_maps);
        return store(result_shape, {}, std::move(topology), true);
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
        const auto source = lookup_record(handle);
        BRepBuilderAPI_Copy copier(source.shape, true, false);
        const auto shape = copier.Shape();
        BRepMesh_IncrementalMesh mesher(shape, linear_deflection_mm, false, angular_deflection_rad, false);
        if (!mesher.IsDone()) throw std::runtime_error("OCCT tessellation failed");

        DisplayMesh result;
        const auto source_maps = topology_maps(source.shape);
        const auto copied_maps = topology_maps(shape);
        for (std::size_t slot = 0; slot < source_maps.size(); ++slot) {
            if (source_maps[slot].Extent() != source.topology.counts[slot] ||
                copied_maps[slot].Extent() != source.topology.counts[slot]) {
                throw std::runtime_error("copied tessellation topology does not match authoritative topology numbering");
            }
        }
        result.face_count = source.topology.counts[0];
        for (std::uint32_t index = 0; index < result.face_count; ++index) {
            const auto source_face = source_maps[0](static_cast<int>(index + 1));
            const auto mapped = copier.ModifiedShape(source_face);
            if (mapped.IsNull() || find_same(copied_maps[0], mapped) == 0) {
                throw std::runtime_error("could not map authoritative face into tessellation copy");
            }
            // ModifiedShape identifies the copied TShape but does not preserve
            // this face's accumulated orientation inside the authoritative
            // solid. Restore it before translating OCCT triangle winding.
            const auto face = TopoDS::Face(mapped.Oriented(source_face.Orientation()));
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
                result.triangle_faces.push_back(index);
            }
        }
        for (std::uint32_t index = 0; index < source.topology.counts[1]; ++index) {
            const auto source_edge = source_maps[1](static_cast<int>(index + 1));
            const auto mapped = copier.ModifiedShape(source_edge);
            if (mapped.IsNull() || find_same(copied_maps[1], mapped) == 0) {
                throw std::runtime_error("could not map authoritative edge into tessellation copy");
            }
            const auto edge = TopoDS::Edge(mapped);
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
        for (std::uint32_t index = 0; index < source.topology.counts[2]; ++index) {
            const auto source_vertex = source_maps[2](static_cast<int>(index + 1));
            const auto mapped = copier.ModifiedShape(source_vertex);
            if (mapped.IsNull() || find_same(copied_maps[2], mapped) == 0) {
                throw std::runtime_error("could not map authoritative vertex into tessellation copy");
            }
            append_point(result.cad_vertices, BRep_Tool::Pnt(TopoDS::Vertex(mapped)));
        }
        if (result.edge_offsets.size() != static_cast<std::size_t>(source.topology.counts[1]) + 1 ||
            result.cad_vertices.size() / 3 != source.topology.counts[2]) {
            throw std::runtime_error("tessellation arrays diverged from authoritative topology counts");
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

    GeometryTopology topology(ShapeHandle handle) const override {
        return lookup_record(handle).topology;
    }

    TopologyMeasure measure(ShapeHandle handle, TopologyElement element) const override {
        const auto target = lookup_element(handle, element);
        TopologyMeasure result{element, {}, {}, {}, {}};
        if (element.kind == TopologyKind::Vertex) {
            result.center_mm = public_point(BRep_Tool::Pnt(TopoDS::Vertex(target)));
            return result;
        }
        GProp_GProps properties;
        if (element.kind == TopologyKind::Edge) {
            const auto edge = TopoDS::Edge(target);
            BRepGProp::LinearProperties(edge, properties);
            result.length_mm = properties.Mass();
            if (BRep_Tool::Degenerated(edge)) {
                TopTools_IndexedMapOfShape vertices;
                TopExp::MapShapes(edge, TopAbs_VERTEX, vertices);
                if (vertices.Extent() == 0) throw std::runtime_error("degenerate edge has no measurable point");
                result.center_mm = public_point(BRep_Tool::Pnt(TopoDS::Vertex(vertices(1))));
                return result;
            }
            const BRepAdaptor_Curve curve(edge);
            if (curve.GetType() == GeomAbs_Circle) result.radius_mm = curve.Circle().Radius();
        } else {
            const auto face = TopoDS::Face(target);
            BRepGProp::SurfaceProperties(face, properties);
            result.area_mm2 = properties.Mass();
            const BRepAdaptor_Surface surface(face, true);
            if (surface.GetType() == GeomAbs_Cylinder) result.radius_mm = surface.Cylinder().Radius();
            else if (surface.GetType() == GeomAbs_Sphere) result.radius_mm = surface.Sphere().Radius();
        }
        if (!std::isfinite(properties.Mass()) || properties.Mass() < 0) {
            throw std::runtime_error("kernel query returned an invalid topology measure");
        }
        result.center_mm = public_point(properties.CentreOfMass());
        return result;
    }

    TopologyPoint closest_point(ShapeHandle handle, TopologyElement element, Vector3 point_mm) const override {
        const auto point = query_point(point_mm, "query point");
        const auto target = lookup_element(handle, element);
        // Unlike projection onto a supporting surface/curve, distance to this
        // topological subshape respects trimming wires, holes and endpoints.
        BRepExtrema_DistShapeShape distance(BRepBuilderAPI_MakeVertex(point).Vertex(), target,
                                           std::min(budget_mm_, Precision::Confusion()));
        if (!distance.IsDone() || distance.NbSolution() == 0 || !std::isfinite(distance.Value())) {
            throw std::runtime_error("could not find the closest point on trimmed CAD topology");
        }
        return {element, public_point(distance.PointOnShape2(1)), distance.Value()};
    }

    std::optional<FaceHit> ray_cast(ShapeHandle handle, Vector3 origin_mm, Vector3 direction,
                                  double max_distance_mm) const override {
        const auto origin = query_point(origin_mm, "ray origin");
        (void)query_point(direction, "ray direction");
        const auto direction_scale = std::max({std::abs(direction.x), std::abs(direction.y), std::abs(direction.z)});
        if (direction_scale <= 0) {
            throw std::invalid_argument("ray direction must have finite nonzero length");
        }
        const Vector3 scaled{direction.x / direction_scale, direction.y / direction_scale, direction.z / direction_scale};
        const auto magnitude = std::hypot(scaled.x, scaled.y, scaled.z);
        if (!std::isfinite(max_distance_mm) || max_distance_mm <= 0) {
            throw std::invalid_argument("ray maximum distance must be finite and positive");
        }
        const auto shape = lookup(handle);
        const auto maps = topology_maps(shape);
        const gp_Dir unit(scaled.x / magnitude, scaled.y / magnitude, scaled.z / magnitude);
        IntCurvesFace_ShapeIntersector intersector;
        intersector.Load(shape, std::min(budget_mm_, Precision::Confusion()));
        intersector.Perform(gp_Lin(origin, unit), 0, max_distance_mm);
        if (!intersector.IsDone()) throw std::runtime_error("CAD ray intersection failed");
        std::optional<FaceHit> nearest;
        for (int index = 1; index <= intersector.NbPnt(); ++index) {
            const double distance = intersector.WParameter(index);
            if (!std::isfinite(distance) || distance < 0 || distance > max_distance_mm) continue;
            if (nearest && distance >= nearest->distance_mm) continue;
            const int face_index = find_same(maps[0], intersector.Face(index));
            if (face_index == 0) throw std::runtime_error("ray hit has no authoritative CAD face identity");
            const auto face = TopoDS::Face(maps[0](face_index));
            std::optional<Vector3> normal;
            try {
                const BRepAdaptor_Surface surface(face, true);
                BRepLProp_SLProps properties(surface, intersector.UParameter(index), intersector.VParameter(index),
                                            1, std::min(budget_mm_, Precision::Confusion()));
                if (properties.IsNormalDefined() && (face.Orientation() == TopAbs_FORWARD || face.Orientation() == TopAbs_REVERSED)) {
                    auto value = properties.Normal();
                    if (face.Orientation() == TopAbs_REVERSED) value.Reverse();
                    normal = Vector3{value.X(), value.Y(), value.Z()};
                }
            } catch (const Standard_Failure&) {
                // A valid trimmed intersection can lie at a surface singularity
                // where differential normal evaluation is undefined.
                normal.reset();
            }
            nearest = FaceHit{static_cast<std::uint32_t>(face_index - 1), public_point(intersector.Pnt(index)), normal, distance};
        }
        return nearest;
    }

    void release(ShapeHandle handle) override {
        validate_owner(handle);
        std::lock_guard lock(mutex_);
        if (shapes_.erase(handle.value) == 0) {
            throw std::invalid_argument("unknown or released geometry handle");
        }
    }

private:
    struct StoredShape final {
        TopoDS_Shape shape;
        GeometryTopology topology;
    };

    TopoDS_Shape lookup_element(ShapeHandle handle, TopologyElement element) const {
        const auto shape = lookup(handle);
        const auto maps = topology_maps(shape);
        const auto slot = query_slot(element.kind);
        if (element.index >= static_cast<std::uint32_t>(maps[slot].Extent())) {
            throw std::invalid_argument("topology index is out of range for this evaluated shape");
        }
        return maps[slot](static_cast<int>(element.index + 1));
    }

    template <typename ShapeOperation>
    static std::vector<TopologyHistoryEntry> history_for(ShapeOperation& operation,
                                                         const TopoDS_Shape& input,
                                                         const GeometryTopology& input_topology,
                                                         const TopologyMaps& result_maps,
                                                         const BRepBuilderAPI_Copy* input_copy = nullptr) {
        const auto input_maps = topology_maps(input);
        std::vector<TopologyHistoryEntry> history;
        for (std::size_t slot = 0; slot < input_maps.size(); ++slot) {
            if (input_maps[slot].Extent() != input_topology.counts[slot]) {
                throw std::runtime_error("input topology metadata does not match its authoritative shape");
            }
            for (int index = 1; index <= input_maps[slot].Extent(); ++index) {
                const auto original = input_maps[slot](index);
                const auto source = input_copy ? input_copy->ModifiedShape(original) : original;
                if (source.IsNull() || source.ShapeType() != original.ShapeType()) {
                    throw std::runtime_error("operation history could not map an authoritative input element");
                }
                TopologyHistoryEntry entry{{public_kind(slot), static_cast<std::uint32_t>(index - 1)}, {}, {},
                                            operation.IsDeleted(source)};

                // Modified is a same-kind continuation. Preserve only shapes
                // actually reported by OCCT, then explicitly include an
                // unchanged IsSame member when the operation kept it in the result.
                const auto modified = operation.Modified(source);
                for (TopTools_ListIteratorOfListOfShape iterator(modified); iterator.More(); iterator.Next()) {
                    const auto& output = iterator.Value();
                    const auto output_slot = topology_slot_if_supported(output.ShapeType());
                    if (!output_slot || *output_slot != slot) continue;
                    if (const auto element = find_output_element(result_maps, output)) append_unique(entry.modified, *element);
                }
                const int unchanged = find_same(result_maps[slot], source);
                if (unchanged != 0) {
                    append_unique(entry.modified, {public_kind(slot), static_cast<std::uint32_t>(unchanged - 1)});
                }

                // Generated can legitimately cross dimensions (for example,
                // a cut face can generate boundary edges); retain every direct
                // face/edge/vertex output with its actual result numbering.
                const auto generated = operation.Generated(source);
                for (TopTools_ListIteratorOfListOfShape iterator(generated); iterator.More(); iterator.Next()) {
                    if (const auto element = find_output_element(result_maps, iterator.Value())) {
                        append_unique(entry.generated, *element);
                    }
                }
                history.push_back(std::move(entry));
            }
        }
        return history;
    }

    static void append_boundary_history(std::vector<TopologyHistoryEntry>& history,
                                         const TopologyMaps& result_maps) {
        // MakeFillet reports the new round face but can omit its boundary
        // edges/vertices from Generated. Preserve that omission in the direct
        // kernel history and record a distinct, exact face-incidence relation.
        // Existing traced output elements keep their established naming path.
        std::array<std::vector<bool>, 3> traced;
        for (std::size_t slot = 0; slot < result_maps.size(); ++slot) {
            traced[slot].resize(static_cast<std::size_t>(result_maps[slot].Extent()));
        }
        auto mark = [&](TopologyElement target) {
            const auto slot = query_slot(target.kind);
            if (target.index >= traced[slot].size()) throw std::runtime_error("operation history target index is out of range");
            traced[slot][target.index] = true;
        };
        for (const auto& entry : history) {
            for (const auto target : entry.modified) mark(target);
            for (const auto target : entry.generated) mark(target);
        }
        for (auto& entry : history) {
            std::vector<TopologyElement> faces;
            for (const auto target : entry.modified) {
                if (target.kind == TopologyKind::Face) append_unique(faces, target);
            }
            for (const auto target : entry.generated) {
                if (target.kind == TopologyKind::Face) append_unique(faces, target);
            }
            for (const auto face : faces) {
                const auto& output = result_maps[0](static_cast<int>(face.index + 1));
                for (const auto kind : {TopAbs_EDGE, TopAbs_VERTEX}) {
                    for (TopExp_Explorer boundary(output, kind); boundary.More(); boundary.Next()) {
                        const auto element = find_output_element(result_maps, boundary.Current());
                        if (element && !traced[query_slot(element->kind)][element->index]) {
                            append_unique(entry.boundary_generated, *element);
                        }
                    }
                }
            }
        }
    }

    ShapeHandle store(TopoDS_Shape shape, const std::vector<PrismSeedRelation>& seeds = {},
                      GeometryTopology topology = {}, bool topology_counts_supplied = false) {
        if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid() ||
            !TopExp_Explorer(shape, TopAbs_SOLID).More()) {
            throw std::runtime_error("operation did not produce a valid solid");
        }
        if (maximum_tolerance(shape) > budget_mm_) {
            throw std::runtime_error("result exceeds the configured geometric tolerance budget");
        }
        const auto maps = topology_maps(shape);
        for (std::size_t slot = 0; slot < maps.size(); ++slot) {
            const auto count = static_cast<std::uint32_t>(maps[slot].Extent());
            if (topology_counts_supplied && topology.counts[slot] != count) {
                throw std::runtime_error("topology history count does not match result shape");
            }
            topology.counts[slot] = count;
        }
        topology.seeds.reserve(seeds.size());
        for (const auto& seed : seeds) {
            if (seed.shape.IsNull()) throw std::runtime_error("OCCT construction did not report an expected topology seed");
            const auto slot = topology_slot(seed.shape.ShapeType());
            const int index = find_same(maps[slot], seed.shape);
            if (index == 0) throw std::runtime_error("OCCT construction seed is not present in the output solid");
            topology.seeds.push_back({{public_kind(slot), static_cast<std::uint32_t>(index - 1)},
                                      seed.role, seed.profile_index});
        }
        std::lock_guard lock(mutex_);
        if (next_handle_ == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("geometry handle space exhausted");
        }
        const ShapeHandle handle{adapter_id_, next_handle_++};
        shapes_.emplace(handle.value, StoredShape{std::move(shape), std::move(topology)});
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
        return found->second.shape;
    }

    StoredShape lookup_record(ShapeHandle handle) const {
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
    std::unordered_map<std::uint64_t, StoredShape> shapes_;
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
