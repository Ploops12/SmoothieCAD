#include "blendercad/document.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

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

SketchProblem parse_sketch_problem(const py::dict& sketch) {
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
        require_fields(constraint, {"id", "type", "targets", "value"}, "constraint");
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
                                kind, {}, as_number(constraint["value"], "constraint value")};
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

py::dict sketch_snapshot(const SketchDefinition& sketch) {
    py::dict result;
    result["id"] = sketch.id.to_string();
    result["name"] = sketch.name;
    result["version"] = 1;

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
        item["value"] = constraint.value;
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
        } else if (schema == Document::current_schema_version) {
            require_fields(snapshot, {"schema_version", "document_id", "revision",
                                      "geometric_tolerance_mm", "parameters", "sketches"},
                           "document snapshot");
        } else {
            invalid("unsupported BlenderCAD document schema version");
        }

        std::vector<Parameter> parameters;
        for (const auto& raw_parameter : as_list(required(snapshot, "parameters", "document snapshot"),
                                                  "parameters")) {
            const auto parameter = as_dict(raw_parameter, "parameter");
            require_fields(parameter, {"name", "value", "unit"}, "parameter");
            parameters.push_back({as_string(parameter["name"], "parameter name"),
                                  as_number(parameter["value"], "parameter value"),
                                  parse_unit(as_string(parameter["unit"], "parameter unit"))});
        }

        std::vector<SketchDefinition> sketches;
        if (schema == Document::current_schema_version) {
            for (const auto& raw_sketch : as_list(snapshot["sketches"], "sketches")) {
                const auto sketch = as_dict(raw_sketch, "sketch");
                require_fields(sketch, {"id", "name", "version", "plane", "entities", "constraints"},
                               "sketch");
                if (as_version(sketch["version"], "sketch version") != 1) {
                    invalid("unsupported sketch definition version");
                }
                SketchProblem problem = parse_sketch_problem(sketch);
                sketches.push_back({DocumentId::parse(as_string(sketch["id"], "sketch id")),
                                    as_string(sketch["name"], "sketch name"), std::move(problem)});
            }
        }

        return Document::restore(
            DocumentId::parse(as_string(required(snapshot, "document_id", "document snapshot"),
                                        "document id")),
            as_revision(required(snapshot, "revision", "document snapshot")),
            as_number(required(snapshot, "geometric_tolerance_mm", "document snapshot"),
                      "geometric tolerance"),
            std::move(parameters), std::move(sketches));
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
        .def("get_sketch", [](const Document& document, const std::string& id) {
            return document.sketch(DocumentId::parse(id)).problem;
        })
        .def("set_geometric_tolerance_mm", &Document::set_geometric_tolerance_mm)
        .def("add_parameter", [](Document& document, std::string name, double value,
                                  const std::string& unit) {
            document.add_parameter(std::move(name), value, parse_unit(unit));
        }, py::arg("name"), py::arg("value"), py::arg("unit") = "unitless")
        .def("set_parameter_value", &Document::set_parameter_value)
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
                item["value"] = parameter.value;
                item["unit"] = std::string(unit_name(parameter.unit));
                parameters.append(std::move(item));
            }
            snapshot["parameters"] = std::move(parameters);
            py::list sketches;
            for (const auto& sketch : document.sketches()) sketches.append(sketch_snapshot(sketch));
            snapshot["sketches"] = std::move(sketches);
            return snapshot;
        })
        .def_static("from_snapshot", &parse_snapshot);
}
