#include "blendercad/geometry.hpp"

#include <BRepCheck_Analyzer.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
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
#include <TopTools_ListIteratorOfListOfShape.hxx>
#include <gp_Circ.hxx>

#include <cmath>
#include <algorithm>
#include <array>
#include <atomic>
#include <limits>
#include <mutex>
#include <optional>
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

    template <typename BooleanOperation>
    static std::vector<TopologyHistoryEntry> history_for(BooleanOperation& operation,
                                                         const TopoDS_Shape& input,
                                                         const GeometryTopology& input_topology,
                                                         const TopologyMaps& result_maps) {
        const auto input_maps = topology_maps(input);
        std::vector<TopologyHistoryEntry> history;
        for (std::size_t slot = 0; slot < input_maps.size(); ++slot) {
            if (input_maps[slot].Extent() != input_topology.counts[slot]) {
                throw std::runtime_error("input topology metadata does not match its authoritative shape");
            }
            for (int index = 1; index <= input_maps[slot].Extent(); ++index) {
                const auto source = input_maps[slot](index);
                TopologyHistoryEntry entry{{public_kind(slot), static_cast<std::uint32_t>(index - 1)}, {}, {},
                                            operation.IsDeleted(source)};

                // Modified is a same-kind continuation. Preserve only shapes
                // actually reported by OCCT, then explicitly include an
                // unchanged IsSame member when the boolean kept it in the result.
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
