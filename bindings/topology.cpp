#include "blendercad/geometry.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace blendercad;

void bind_topology(py::module_& module) {
    py::enum_<TopologyKind>(module, "TopologyKind")
        .value("FACE", TopologyKind::Face)
        .value("EDGE", TopologyKind::Edge)
        .value("VERTEX", TopologyKind::Vertex);
    py::enum_<TopologySeedRole>(module, "TopologySeedRole")
        .value("START_CAP", TopologySeedRole::StartCap)
        .value("END_CAP", TopologySeedRole::EndCap)
        .value("SIDE", TopologySeedRole::Side)
        .value("START_EDGE", TopologySeedRole::StartEdge)
        .value("END_EDGE", TopologySeedRole::EndEdge)
        .value("SWEEP_EDGE", TopologySeedRole::SweepEdge)
        .value("START_VERTEX", TopologySeedRole::StartVertex)
        .value("END_VERTEX", TopologySeedRole::EndVertex);
    py::class_<TopologyElement>(module, "TopologyElement")
        .def(py::init<TopologyKind, std::uint32_t>())
        .def_readonly("kind", &TopologyElement::kind)
        .def_readonly("index", &TopologyElement::index);
    py::class_<TopologySeed>(module, "TopologySeed")
        .def_property_readonly("target", [](const TopologySeed& seed) { return seed.target; })
        .def_readonly("role", &TopologySeed::role)
        .def_readonly("profile_index", &TopologySeed::profile_index);
    py::class_<TopologyHistoryEntry>(module, "TopologyHistoryEntry")
        .def_property_readonly("source", [](const TopologyHistoryEntry& entry) { return entry.source; })
        .def_property_readonly("modified", [](const TopologyHistoryEntry& entry) { return entry.modified; })
        .def_property_readonly("generated", [](const TopologyHistoryEntry& entry) { return entry.generated; })
        .def_readonly("deleted", &TopologyHistoryEntry::deleted)
        .def_property_readonly("boundary_generated", [](const TopologyHistoryEntry& entry) { return entry.boundary_generated; });
    py::class_<GeometryTopology>(module, "GeometryTopology")
        .def_property_readonly("counts", [](const GeometryTopology& topology) { return topology.counts; })
        .def_property_readonly("seeds", [](const GeometryTopology& topology) { return topology.seeds; })
        .def_property_readonly("argument_history", [](const GeometryTopology& topology) { return topology.argument_history; })
        .def_property_readonly("tool_history", [](const GeometryTopology& topology) { return topology.tool_history; });
    module.def("geometry_topology", [](const GeometryAdapter& adapter, ShapeHandle shape) {
        return adapter.topology(shape);
    }, py::arg("adapter"), py::arg("shape"));
}
