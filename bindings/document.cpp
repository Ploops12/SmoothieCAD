#include "blendercad/document.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace py = pybind11;
using namespace blendercad;

namespace {

[[noreturn]] void invalid(const std::string& message) {
    throw std::invalid_argument(message);
}

void require_dict(const py::handle& value, const char* context) {
    if (!py::isinstance<py::dict>(value)) invalid(std::string(context) + " must be an object");
}

py::dict as_dict(const py::handle& value, const char* context) {
    require_dict(value, context);
    return py::reinterpret_borrow<py::dict>(value);
}

void require_fields(const py::dict& object, std::initializer_list<const char*> fields,
                    const char* context) {
    if (object.size() != fields.size()) invalid(std::string(context) + " has unknown or missing fields");
    for (const auto* field : fields) {
        if (!object.contains(field)) invalid(std::string(context) + " is missing field '" + field + "'");
    }
}

py::list as_list(const py::handle& value, const char* context) {
    if (!py::isinstance<py::list>(value)) invalid(std::string(context) + " must be an array");
    return py::reinterpret_borrow<py::list>(value);
}

std::string as_string(const py::handle& value, const char* context) {
    if (!py::isinstance<py::str>(value)) invalid(std::string(context) + " must be a string");
    return py::cast<std::string>(value);
}

double as_number(const py::handle& value, const char* context) {
    if (PyBool_Check(value.ptr()) ||
        (!PyFloat_Check(value.ptr()) && !PyLong_Check(value.ptr()))) {
        invalid(std::string(context) + " must be a finite number");
    }
    const auto number = py::cast<double>(value);
    if (!std::isfinite(number)) invalid(std::string(context) + " must be a finite number");
    return number;
}

std::uint64_t as_revision(const py::handle& value) {
    if (PyBool_Check(value.ptr()) || !PyLong_Check(value.ptr())) {
        invalid("revision must be a nonnegative integer");
    }
    try {
        return py::cast<std::uint64_t>(value);
    } catch (const py::cast_error&) {
        invalid("revision is outside the supported integer range");
    } catch (const py::error_already_set&) {
        invalid("revision is outside the supported integer range");
    }
}

std::uint32_t as_version(const py::handle& value, const char* context) {
    if (PyBool_Check(value.ptr()) || !PyLong_Check(value.ptr())) {
        invalid(std::string(context) + " must be an integer version");
    }
    try {
        return py::cast<std::uint32_t>(value);
    } catch (const py::cast_error&) {
        invalid(std::string(context) + " is outside the supported integer range");
    } catch (const py::error_already_set&) {
        invalid(std::string(context) + " is outside the supported integer range");
    }
}

py::object required(const py::dict& object, const char* field, const char* context) {
    if (!object.contains(field)) invalid(std::string(context) + " is missing field '" + field + "'");
    return py::reinterpret_borrow<py::object>(object[field]);
}

Vector3 parse_vector3(const py::handle& value, const char* context) {
    const auto values = as_list(value, context);
    if (values.size() != 3) invalid(std::string(context) + " must contain exactly three coordinates");
    return {as_number(values[0], context), as_number(values[1], context),
            as_number(values[2], context)};
}

Point2 parse_point2(const py::handle& value, const char* context) {
    const auto values = as_list(value, context);
    if (values.size() != 2) invalid(std::string(context) + " must contain exactly two coordinates");
    return {as_number(values[0], context), as_number(values[1], context)};
}

py::list vector3_snapshot(const Vector3& vector) {
    py::list result;
    result.append(vector.x);
    result.append(vector.y);
    result.append(vector.z);
    return result;
}

py::list point2_snapshot(const Point2& point) {
    py::list result;
    result.append(point.x);
    result.append(point.y);
    return result;
}

SketchProblem parse_sketch_problem(const py::dict& sketch, bool allow_parameters = false) {
    const auto plane = as_dict(required(sketch, "plane", "sketch"), "sketch plane");
    require_fields(plane, {"origin", "x_axis", "y_axis"}, "sketch plane");
    SketchProblem problem;
    problem.plane.origin = parse_vector3(plane["origin"], "plane origin");
    problem.plane.x_axis = parse_vector3(plane["x_axis"], "plane x_axis");
    problem.plane.y_axis = parse_vector3(plane["y_axis"], "plane y_axis");

    for (const auto& raw_entity : as_list(required(sketch, "entities", "sketch"), "entities")) {
        const auto entity = as_dict(raw_entity, "entity");
        const auto type = as_string(required(entity, "type", "entity"), "entity type");
        if (type == "line") {
            require_fields(entity, {"id", "type", "start", "end"}, "line entity");
            problem.entities.push_back({DocumentId::parse(as_string(entity["id"], "entity id")),
                Line2{parse_point2(entity["start"], "line start"),
                     parse_point2(entity["end"], "line end")}});
        } else if (type == "circle") {
            require_fields(entity, {"id", "type", "center", "radius"}, "circle entity");
            problem.entities.push_back({DocumentId::parse(as_string(entity["id"], "entity id")),
                Circle2{parse_point2(entity["center"], "circle center"),
                       as_number(entity["radius"], "circle radius")}});
        } else {
            invalid("unsupported sketch entity type: " + type);
        }
    }

    for (const auto& raw_constraint : as_list(required(sketch, "constraints", "sketch"), "constraints")) {
        const auto constraint = as_dict(raw_constraint, "constraint");
        const bool bound = allow_parameters && constraint.contains("parameter");
        if (bound) require_fields(constraint, {"id", "type", "targets", "parameter"}, "parameter-bound constraint");
        else require_fields(constraint, {"id", "type", "targets", "value"}, "literal constraint");
        const auto type = as_string(constraint["type"], "constraint type");
        SketchConstraintKind kind;
        if (type == "coincident") kind = SketchConstraintKind::Coincident;
        else if (type == "horizontal") kind = SketchConstraintKind::Horizontal;
        else if (type == "vertical") kind = SketchConstraintKind::Vertical;
        else if (type == "parallel") kind = SketchConstraintKind::Parallel;
        else if (type == "perpendicular") kind = SketchConstraintKind::Perpendicular;
        else if (type == "distance") kind = SketchConstraintKind::Distance;
        else if (type == "length") kind = SketchConstraintKind::Length;
        else if (type == "radius") kind = SketchConstraintKind::Radius;
        else if (type == "diameter") kind = SketchConstraintKind::Diameter;
        else if (type == "distance_x") kind = SketchConstraintKind::DistanceX;
        else if (type == "distance_y") kind = SketchConstraintKind::DistanceY;
        else invalid("unsupported sketch constraint type: " + type);

        SketchConstraint parsed{DocumentId::parse(as_string(constraint["id"], "constraint id")),
                                kind, {}, bound ? 0.0 : as_number(constraint["value"], "constraint value")};
        if (bound) parsed.parameter = as_string(constraint["parameter"], "constraint parameter");
        for (const auto& raw_target : as_list(constraint["targets"], "constraint targets")) {
            const auto target = as_dict(raw_target, "constraint target");
            require_fields(target, {"entity", "element"}, "constraint target");
            std::optional<DocumentId> entity_id;
            if (!target["entity"].is_none()) {
                entity_id = DocumentId::parse(as_string(target["entity"], "target entity id"));
            }
            const auto element_name = as_string(target["element"], "target element");
            SketchElement element;
            if (element_name == "geometry") element = SketchElement::Geometry;
            else if (element_name == "line_start") element = SketchElement::LineStart;
            else if (element_name == "line_end") element = SketchElement::LineEnd;
            else if (element_name == "circle_center") element = SketchElement::CircleCenter;
            else if (element_name == "origin") element = SketchElement::Origin;
            else invalid("unsupported sketch reference element: " + element_name);
            parsed.targets.push_back({std::move(entity_id), element});
        }
        problem.constraints.push_back(std::move(parsed));
    }
    return problem;
}

DepthInput parse_depth_argument(const py::handle& value, const std::string& context = "feature depth") {
    if (PyBool_Check(value.ptr())) invalid(context + " must be a number or parameter name, not bool");
    if (PyFloat_Check(value.ptr()) || PyLong_Check(value.ptr())) {
        return as_number(value, context.c_str());
    }
    if (py::isinstance<py::str>(value)) return as_string(value, (context + " parameter").c_str());
    invalid(context + " must be a number or parameter name");
}

DepthInput parse_depth_snapshot(const py::handle& value, const char* context = "feature depth") {
    const auto depth = as_dict(value, context);
    if (depth.size() == 1 && depth.contains("value_mm")) {
        return as_number(depth["value_mm"], context);
    }
    if (depth.size() == 1 && depth.contains("parameter")) {
        return as_string(depth["parameter"], context);
    }
    invalid(std::string(context) + " must contain exactly one of value_mm or parameter");
}

PersistentReference parse_target_fields(const py::dict& reference) {
    const auto kind_name = as_string(reference["kind"], "reference kind");
    TopologyKind kind;
    if (kind_name == "face") kind = TopologyKind::Face;
    else if (kind_name == "edge") kind = TopologyKind::Edge;
    else if (kind_name == "vertex") kind = TopologyKind::Vertex;
    else invalid("unsupported reference topology kind: " + kind_name);
    return {DocumentId::parse(as_string(reference["body"], "reference body id")), kind,
            {DocumentId::parse(as_string(reference["feature"], "reference feature id")),
             as_string(reference["key"], "reference key")}};
}

FeatureDefinition parse_feature_snapshot(const py::handle& value, bool allow_fillet) {
    const auto feature = as_dict(value, "feature");
    const auto type = as_string(required(feature, "type", "feature"), "feature type");
    if (type == "fillet") {
        if (!allow_fillet) invalid("fillet features require document schema 7");
        require_fields(feature, {"id", "name", "version", "type", "body", "input", "radius", "edges", "suppressed"}, "fillet feature");
    } else {
        require_fields(feature, {"id", "name", "version", "type", "body", "sketch",
                                 "input", "depth", "suppressed"}, "feature");
    }
    const auto version = as_version(feature["version"], "feature version");
    if (version != 1) invalid("unsupported feature definition version");
    const auto name = as_string(feature["name"], "feature name");
    const auto id = DocumentId::parse(as_string(feature["id"], "feature id"));
    const auto body = DocumentId::parse(as_string(feature["body"], "feature body id"));
    if (!PyBool_Check(feature["suppressed"].ptr())) invalid("feature suppressed field must be a boolean");
    const bool suppressed = py::cast<bool>(feature["suppressed"]);

    if (type == "fillet") {
        const auto input = DocumentId::parse(as_string(feature["input"], "fillet input id"));
        auto radius = parse_depth_snapshot(feature["radius"], "fillet radius");
        std::vector<PersistentReference> edges;
        for (const auto& raw_edge : as_list(feature["edges"], "fillet edges")) {
            const auto edge = as_dict(raw_edge, "fillet edge target");
            require_fields(edge, {"body", "kind", "feature", "key"}, "fillet edge target");
            edges.push_back(parse_target_fields(edge));
        }
        return {id, name, version, body, FilletFeature{input, std::move(radius), std::move(edges)}, suppressed};
    }
    const auto sketch = DocumentId::parse(as_string(feature["sketch"], "feature sketch id"));
    auto depth = parse_depth_snapshot(feature["depth"]);

    if (type == "extrusion") {
        if (!feature["input"].is_none()) invalid("extrusion input must be null");
        return {id, name, version, body, ExtrusionFeature{sketch, std::move(depth)}, suppressed};
    }
    if (type == "subtractive_extrusion") {
        if (feature["input"].is_none()) invalid("subtractive extrusion input must be a feature identity");
        const auto input = DocumentId::parse(as_string(feature["input"], "feature input id"));
        return {id, name, version, body,
                SubtractiveExtrusionFeature{input, sketch, std::move(depth)}, suppressed};
    }
    invalid("unsupported feature type: " + type);
}

ReferenceDefinition parse_reference_snapshot(const py::handle& value) {
    const auto reference = as_dict(value, "reference");
    require_fields(reference, {"id", "name", "version", "body", "kind", "feature", "key"}, "reference");
    if (as_version(reference["version"], "reference version") != 1) invalid("unsupported reference definition version");
    return {DocumentId::parse(as_string(reference["id"], "reference id")),
            as_string(reference["name"], "reference name"), parse_target_fields(reference)};
}

py::dict target_snapshot(const PersistentReference& target) {
    const char* kind;
    switch (target.kind) {
    case TopologyKind::Face: kind = "face"; break;
    case TopologyKind::Edge: kind = "edge"; break;
    case TopologyKind::Vertex: kind = "vertex"; break;
    default: invalid("invalid reference topology kind");
    }
    py::dict result;
    result["body"] = target.body.to_string();
    result["kind"] = kind;
    result["feature"] = target.anchor.feature.to_string();
    result["key"] = target.anchor.key;
    return result;
}

py::dict reference_snapshot(const ReferenceDefinition& reference) {
    auto result = target_snapshot(reference.target);
    result["id"] = reference.id.to_string();
    result["name"] = reference.name;
    result["version"] = 1;
    return result;
}

const char* constraint_name(SketchConstraintKind kind) {
    switch (kind) {
    case SketchConstraintKind::Coincident: return "coincident";
    case SketchConstraintKind::Horizontal: return "horizontal";
    case SketchConstraintKind::Vertical: return "vertical";
    case SketchConstraintKind::Parallel: return "parallel";
    case SketchConstraintKind::Perpendicular: return "perpendicular";
    case SketchConstraintKind::Distance: return "distance";
    case SketchConstraintKind::Length: return "length";
    case SketchConstraintKind::Radius: return "radius";
    case SketchConstraintKind::Diameter: return "diameter";
    case SketchConstraintKind::DistanceX: return "distance_x";
    case SketchConstraintKind::DistanceY: return "distance_y";
    }
    invalid("unknown sketch constraint kind");
}

const char* element_name(SketchElement element) {
    switch (element) {
    case SketchElement::Geometry: return "geometry";
    case SketchElement::LineStart: return "line_start";
    case SketchElement::LineEnd: return "line_end";
    case SketchElement::CircleCenter: return "circle_center";
    case SketchElement::Origin: return "origin";
    }
    invalid("unknown sketch reference element");
}

py::object depth_value(const DepthInput& depth) {
    if (const auto* value = std::get_if<double>(&depth)) return py::float_(*value);
    return py::str(std::get<std::string>(depth));
}

py::dict depth_snapshot(const DepthInput& depth) {
    py::dict result;
    if (const auto* value = std::get_if<double>(&depth)) result["value_mm"] = *value;
    else result["parameter"] = std::get<std::string>(depth);
    return result;
}

py::dict feature_snapshot(const FeatureDefinition& feature) {
    py::dict result;
    result["id"] = feature.id.to_string();
    result["name"] = feature.name;
    result["version"] = feature.version;
    result["type"] = feature_type(feature);
    result["body"] = feature.body.to_string();
    if (const auto input = feature_input(feature)) result["input"] = input->to_string();
    else result["input"] = py::none();
    if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) {
        result["radius"] = depth_snapshot(fillet->radius);
        py::list edges;
        for (const auto& edge : fillet->edges) edges.append(target_snapshot(edge));
        result["edges"] = std::move(edges);
    } else {
        result["sketch"] = feature_sketch(feature)->to_string();
        result["depth"] = depth_snapshot(feature_depth(feature));
    }
    result["suppressed"] = feature.suppressed;
    return result;
}

std::vector<std::string> identity_strings(const std::vector<DocumentId>& identities) {
    std::vector<std::string> result;
    result.reserve(identities.size());
    for (const auto& id : identities) result.push_back(id.to_string());
    return result;
}

py::dict sketch_snapshot(const SketchDefinition& sketch) {
    py::dict result;
    result["id"] = sketch.id.to_string();
    result["name"] = sketch.name;
    result["version"] = std::ranges::any_of(sketch.problem.constraints,
        [](const auto& constraint) { return constraint.parameter.has_value(); }) ? 2 : 1;

    py::dict plane;
    plane["origin"] = vector3_snapshot(sketch.problem.plane.origin);
    plane["x_axis"] = vector3_snapshot(sketch.problem.plane.x_axis);
    plane["y_axis"] = vector3_snapshot(sketch.problem.plane.y_axis);
    result["plane"] = std::move(plane);

    py::list entities;
    for (const auto& entity : sketch.problem.entities) {
        py::dict item;
        item["id"] = entity.id.to_string();
        if (const auto* line = std::get_if<Line2>(&entity.geometry)) {
            item["type"] = "line";
            item["start"] = point2_snapshot(line->start);
            item["end"] = point2_snapshot(line->end);
        } else {
            const auto& circle = std::get<Circle2>(entity.geometry);
            item["type"] = "circle";
            item["center"] = point2_snapshot(circle.center);
            item["radius"] = circle.radius;
        }
        entities.append(std::move(item));
    }
    result["entities"] = std::move(entities);

    py::list constraints;
    for (const auto& constraint : sketch.problem.constraints) {
        py::dict item;
        item["id"] = constraint.id.to_string();
        item["type"] = constraint_name(constraint.kind);
        if (constraint.parameter) item["parameter"] = *constraint.parameter;
        else item["value"] = constraint.value;
        py::list targets;
        for (const auto& target : constraint.targets) {
            py::dict reference;
            if (target.entity) reference["entity"] = target.entity->to_string();
            else reference["entity"] = py::none();
            reference["element"] = element_name(target.element);
            targets.append(std::move(reference));
        }
        item["targets"] = std::move(targets);
        constraints.append(std::move(item));
    }
    result["constraints"] = std::move(constraints);
    return result;
}

Document parse_snapshot(const py::object& raw) {
    try {
        const auto snapshot = as_dict(raw, "document snapshot");
        const auto schema = as_version(required(snapshot, "schema_version", "document snapshot"),
                                       "document schema version");
        if (schema == 1) {
            require_fields(snapshot, {"schema_version", "document_id", "revision",
                                      "geometric_tolerance_mm", "parameters"}, "schema 1 snapshot");
        } else if (schema == 2) {
            require_fields(snapshot, {"schema_version", "document_id", "revision",
                                      "geometric_tolerance_mm", "parameters", "sketches"},
                           "schema 2 snapshot");
        } else if (schema == 3) {
            require_fields(snapshot, {"schema_version", "document_id", "revision",
                                      "geometric_tolerance_mm", "parameters", "sketches",
                                      "features", "rollback_before"}, "schema 3 snapshot");
        } else if (schema == 4 || schema == 5 || schema == 6 || schema == Document::current_schema_version) {
            require_fields(snapshot, {"schema_version", "document_id", "revision",
                                      "geometric_tolerance_mm", "parameters", "sketches",
                                      "features", "rollback_before", "references"}, "document snapshot");
        } else {
            invalid("unsupported BlenderCAD document schema version");
        }

        std::vector<Parameter> parameters;
        for (const auto& raw_parameter : as_list(required(snapshot, "parameters", "document snapshot"),
                                                  "parameters")) {
            const auto parameter = as_dict(raw_parameter, "parameter");
            if (schema >= 5 && parameter.contains("expression")) {
                require_fields(parameter, {"name", "expression", "unit"}, "expression parameter");
                parameters.push_back({as_string(parameter["name"], "parameter name"), 0.0,
                                      parse_unit(as_string(parameter["unit"], "parameter unit")),
                                      as_string(parameter["expression"], "parameter expression")});
            } else {
                require_fields(parameter, {"name", "value", "unit"}, "literal parameter");
                parameters.push_back({as_string(parameter["name"], "parameter name"),
                                      as_number(parameter["value"], "parameter value"),
                                      parse_unit(as_string(parameter["unit"], "parameter unit"))});
            }
        }

        std::vector<SketchDefinition> sketches;
        if (schema >= 2) {
            for (const auto& raw_sketch : as_list(snapshot["sketches"], "sketches")) {
                const auto sketch = as_dict(raw_sketch, "sketch");
                require_fields(sketch, {"id", "name", "version", "plane", "entities", "constraints"},
                               "sketch");
                const auto version = as_version(sketch["version"], "sketch version");
                if (version != 1 && !(schema >= 6 && version == 2)) {
                    invalid("unsupported sketch definition version");
                }
                SketchProblem problem = parse_sketch_problem(sketch, version == 2);
                sketches.push_back({DocumentId::parse(as_string(sketch["id"], "sketch id")),
                                    as_string(sketch["name"], "sketch name"), std::move(problem)});
            }
        }

        std::vector<FeatureDefinition> features;
        std::optional<DocumentId> rollback_before;
        if (schema >= 3) {
            for (const auto& raw_feature : as_list(snapshot["features"], "features")) {
                features.push_back(parse_feature_snapshot(raw_feature, schema >= 7));
            }
            if (!snapshot["rollback_before"].is_none()) {
                rollback_before = DocumentId::parse(as_string(snapshot["rollback_before"],
                                                              "rollback_before"));
            }
        }

        std::vector<ReferenceDefinition> references;
        if (schema >= 4) {
            for (const auto& raw_reference : as_list(snapshot["references"], "references")) {
                references.push_back(parse_reference_snapshot(raw_reference));
            }
        }

        return Document::restore(
            DocumentId::parse(as_string(required(snapshot, "document_id", "document snapshot"),
                                        "document id")),
            as_revision(required(snapshot, "revision", "document snapshot")),
            as_number(required(snapshot, "geometric_tolerance_mm", "document snapshot"),
                      "geometric tolerance"),
            std::move(parameters), std::move(sketches), std::move(features),
            std::move(rollback_before), std::move(references));
    } catch (const py::error_already_set& error) {
        invalid(std::string("invalid document snapshot: ") + error.what());
    } catch (const py::cast_error& error) {
        invalid(std::string("invalid document snapshot: ") + error.what());
    } catch (const std::invalid_argument& error) {
        invalid(std::string("invalid document snapshot: ") + error.what());
    }
}

} // namespace

void bind_document(py::module_& module) {
    py::enum_<ParameterUnit>(module, "ParameterUnit")
        .value("MILLIMETRE", ParameterUnit::Millimetre)
        .value("DEGREE", ParameterUnit::Degree)
        .value("UNITLESS", ParameterUnit::Unitless);

    py::class_<Parameter>(module, "Parameter")
        .def_readonly("name", &Parameter::name)
        .def_readonly("value", &Parameter::value)
        .def_readonly("expression", &Parameter::expression)
        .def_property_readonly("unit", [](const Parameter& parameter) {
            return std::string(unit_name(parameter.unit));
        });

    py::class_<SketchDefinition>(module, "Sketch")
        .def_property_readonly("id", [](const SketchDefinition& sketch) {
            return sketch.id.to_string();
        })
        .def_readonly("name", &SketchDefinition::name)
        .def_property_readonly("problem", [](const SketchDefinition& sketch) {
            return sketch.problem;
        });

    py::class_<FeatureDefinition>(module, "Feature")
        .def_property_readonly("id", [](const FeatureDefinition& feature) {
            return feature.id.to_string();
        })
        .def_readonly("name", &FeatureDefinition::name)
        .def_readonly("version", &FeatureDefinition::version)
        .def_property_readonly("type", &feature_type)
        .def_property_readonly("body_id", [](const FeatureDefinition& feature) {
            return feature.body.to_string();
        })
        .def_property_readonly("sketch_id", [](const FeatureDefinition& feature) -> py::object {
            if (const auto sketch = feature_sketch(feature)) return py::str(sketch->to_string());
            return py::none();
        })
        .def_property_readonly("input_id", [](const FeatureDefinition& feature) -> py::object {
            if (const auto input = feature_input(feature)) return py::str(input->to_string());
            return py::none();
        })
        .def_property_readonly("depth", [](const FeatureDefinition& feature) -> py::object {
            if (std::holds_alternative<FilletFeature>(feature.parameters)) return py::none();
            return depth_value(feature_depth(feature));
        })
        .def_property_readonly("radius", [](const FeatureDefinition& feature) -> py::object {
            if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) return depth_value(fillet->radius);
            return py::none();
        })
        .def_property_readonly("edges", [](const FeatureDefinition& feature) {
            if (const auto* fillet = std::get_if<FilletFeature>(&feature.parameters)) return fillet->edges;
            return std::vector<PersistentReference>{};
        })
        .def_readonly("suppressed", &FeatureDefinition::suppressed)
        .def_property_readonly("dependencies", [](const FeatureDefinition& feature) {
            return identity_strings(feature_dependencies(feature));
        });

    py::class_<Document>(module, "Document")
        .def(py::init<>())
        .def_property_readonly("id", [](const Document& document) {
            return document.id().to_string();
        })
        .def_property_readonly("schema_version", &Document::schema_version)
        .def_property_readonly("revision", &Document::revision)
        .def_property_readonly("geometric_tolerance_mm", &Document::geometric_tolerance_mm)
        .def_property_readonly("parameters", [](const Document& document) {
            return document.parameters();
        })
        .def_property_readonly("sketches", [](const Document& document) {
            return document.sketches();
        })
        .def_property_readonly("features", [](const Document& document) {
            return document.features();
        })
        .def_property_readonly("references", [](const Document& document) {
            return document.references();
        })
        .def_property_readonly("rollback_before", [](const Document& document) -> py::object {
            if (document.rollback_before()) return py::str(document.rollback_before()->to_string());
            return py::none();
        })
        .def("get_sketch", [](const Document& document, const std::string& id) {
            return document.sketch(DocumentId::parse(id)).problem;
        })
        .def("get_feature", [](const Document& document, const std::string& id) {
            return document.feature(DocumentId::parse(id));
        })
        .def("get_reference", [](const Document& document, const std::string& id) {
            return document.reference(DocumentId::parse(id));
        })
        .def("add_reference", [](Document& document, std::string name, const PersistentReference& target) {
            return document.add_reference(std::move(name), target).to_string();
        }, py::arg("name"), py::arg("target"))
        .def("repair_reference", [](Document& document, const std::string& id, const PersistentReference& target) {
            document.repair_reference(DocumentId::parse(id), target);
        }, py::arg("id"), py::arg("target"))
        .def("erase_reference", [](Document& document, const std::string& id) {
            document.erase_reference(DocumentId::parse(id));
        }, py::arg("id"))
        .def("length_value", [](const Document& document, const py::object& value) {
            return document.length_value(parse_depth_argument(value));
        })
        .def("set_geometric_tolerance_mm", &Document::set_geometric_tolerance_mm)
        .def("add_parameter", [](Document& document, std::string name, double value,
                                  const std::string& unit) {
            document.add_parameter(std::move(name), value, parse_unit(unit));
        }, py::arg("name"), py::arg("value"), py::arg("unit") = "unitless")
        .def("set_parameter_value", &Document::set_parameter_value)
        .def("add_parameter_expression", [](Document& document, std::string name, std::string expression,
                                             const std::string& unit) {
            document.add_parameter_expression(std::move(name), std::move(expression), parse_unit(unit));
        }, py::arg("name"), py::arg("expression"), py::arg("unit") = "unitless")
        .def("set_parameter_expression", &Document::set_parameter_expression,
             py::arg("name"), py::arg("expression"))
        .def("evaluated_sketch", [](const Document& document, const std::string& id) {
            return document.evaluated_sketch(DocumentId::parse(id));
        }, py::arg("sketch_id"))
        .def("set_sketch_dimension", [](Document& document, const std::string& sketch,
                                        const std::string& constraint, const py::object& value) {
            document.set_sketch_dimension(DocumentId::parse(sketch), DocumentId::parse(constraint),
                                          parse_depth_argument(value, "sketch dimension"));
        }, py::arg("sketch_id"), py::arg("constraint_id"), py::arg("value"))
        .def("add_sketch", [](Document& document, std::string name, const SketchProblem& problem) {
            return document.add_sketch(std::move(name), problem).to_string();
        })
        .def("replace_sketch", [](Document& document, const std::string& id,
                                   const SketchProblem& problem) {
            document.replace_sketch(DocumentId::parse(id), problem);
        })
        .def("rename_sketch", [](Document& document, const std::string& id,
                                  std::string name) {
            document.rename_sketch(DocumentId::parse(id), std::move(name));
        })
        .def("add_extrusion", [](Document& document, std::string name, const std::string& sketch,
                                 const py::object& depth) {
            return document.add_extrusion(std::move(name), DocumentId::parse(sketch),
                                          parse_depth_argument(depth)).to_string();
        }, py::arg("name"), py::arg("sketch_id"), py::arg("depth"))
        .def("add_subtractive_extrusion", [](Document& document, std::string name,
                                              const std::string& input, const std::string& sketch,
                                              const py::object& depth) {
            return document.add_subtractive_extrusion(std::move(name), DocumentId::parse(input),
                    DocumentId::parse(sketch), parse_depth_argument(depth)).to_string();
        }, py::arg("name"), py::arg("input_id"), py::arg("sketch_id"), py::arg("depth"))
        .def("add_fillet", [](Document& document, std::string name, const std::string& input,
                               std::vector<PersistentReference> edges, const py::object& radius) {
            return document.add_fillet(std::move(name), DocumentId::parse(input), std::move(edges),
                                       parse_depth_argument(radius, "fillet radius")).to_string();
        }, py::arg("name"), py::arg("input_id"), py::arg("edges"), py::arg("radius"))
        .def("set_fillet_radius", [](Document& document, const std::string& id, const py::object& radius) {
            document.set_fillet_radius(DocumentId::parse(id), parse_depth_argument(radius, "fillet radius"));
        }, py::arg("feature_id"), py::arg("radius"))
        .def("set_fillet_edges", [](Document& document, const std::string& id, std::vector<PersistentReference> edges) {
            document.set_fillet_edges(DocumentId::parse(id), std::move(edges));
        }, py::arg("feature_id"), py::arg("edges"))
        .def("set_feature_depth", [](Document& document, const std::string& id,
                                      const py::object& depth) {
            document.set_feature_depth(DocumentId::parse(id), parse_depth_argument(depth));
        })
        .def("set_feature_suppressed", [](Document& document, const std::string& id,
                                           const py::object& suppressed) {
            if (!PyBool_Check(suppressed.ptr())) invalid("suppressed must be a boolean");
            document.set_feature_suppressed(DocumentId::parse(id), py::cast<bool>(suppressed));
        })
        .def("reorder_features", [](Document& document, const py::object& raw_order) {
            std::vector<DocumentId> order;
            for (const auto& raw_id : as_list(raw_order, "feature order")) {
                order.push_back(DocumentId::parse(as_string(raw_id, "feature identity")));
            }
            document.reorder_features(order);
        })
        .def("set_rollback_before", [](Document& document, const py::object& raw_id) {
            std::optional<DocumentId> id;
            if (!raw_id.is_none()) id = DocumentId::parse(as_string(raw_id, "rollback feature identity"));
            document.set_rollback_before(std::move(id));
        })
        .def("erase_feature", [](Document& document, const std::string& id,
                                  const py::object& cascade) {
            if (!PyBool_Check(cascade.ptr())) invalid("cascade must be a boolean");
            document.erase_feature(DocumentId::parse(id), py::cast<bool>(cascade));
        }, py::arg("id"), py::arg("cascade") = false)
        .def("erase_sketch", [](Document& document, const std::string& id,
                                 const py::object& cascade) {
            if (!PyBool_Check(cascade.ptr())) invalid("cascade must be a boolean");
            document.erase_sketch(DocumentId::parse(id), py::cast<bool>(cascade));
        }, py::arg("id"), py::arg("cascade") = false)
        .def("to_snapshot", [](const Document& document) {
            py::dict snapshot;
            snapshot["schema_version"] = document.schema_version();
            snapshot["document_id"] = document.id().to_string();
            snapshot["revision"] = document.revision();
            snapshot["geometric_tolerance_mm"] = document.geometric_tolerance_mm();
            py::list parameters;
            for (const auto& parameter : document.parameters()) {
                py::dict item;
                item["name"] = parameter.name;
                if (parameter.expression) item["expression"] = *parameter.expression;
                else item["value"] = parameter.value;
                item["unit"] = std::string(unit_name(parameter.unit));
                parameters.append(std::move(item));
            }
            snapshot["parameters"] = std::move(parameters);
            py::list sketches;
            for (const auto& sketch : document.sketches()) sketches.append(sketch_snapshot(sketch));
            snapshot["sketches"] = std::move(sketches);
            py::list features;
            for (const auto& feature : document.features()) features.append(feature_snapshot(feature));
            snapshot["features"] = std::move(features);
            if (document.rollback_before()) snapshot["rollback_before"] = document.rollback_before()->to_string();
            else snapshot["rollback_before"] = py::none();
            py::list references;
            for (const auto& reference : document.references()) references.append(reference_snapshot(reference));
            snapshot["references"] = std::move(references);
            return snapshot;
        })
        .def_static("from_snapshot", &parse_snapshot);
}
