#pragma once

#include "blendercad/document_id.hpp"

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace blendercad {

enum class ParameterUnit {
    Millimetre,
    Degree,
    Unitless,
};

[[nodiscard]] std::string_view unit_name(ParameterUnit unit) noexcept;
[[nodiscard]] ParameterUnit parse_unit(std::string_view unit);

struct Parameter final {
    std::string name;
    double value{};
    ParameterUnit unit{ParameterUnit::Unitless};
};

// Owns persistent design definitions. Evaluated kernel objects are deliberately
// held by GeometryAdapter and are not part of this snapshot.
class Document final {
public:
    static constexpr std::uint32_t current_schema_version = 1;
    static constexpr double default_geometric_tolerance_mm = 0.001;

    Document();
    explicit Document(DocumentId id);
    static Document restore(DocumentId id, std::uint64_t revision,
                            double tolerance_mm, std::vector<Parameter> parameters);

    [[nodiscard]] const DocumentId& id() const noexcept { return id_; }
    [[nodiscard]] std::uint32_t schema_version() const noexcept { return current_schema_version; }
    [[nodiscard]] std::uint64_t revision() const noexcept { return revision_; }
    [[nodiscard]] double geometric_tolerance_mm() const noexcept { return geometric_tolerance_mm_; }
    [[nodiscard]] const std::vector<Parameter>& parameters() const noexcept { return parameters_; }

    void set_geometric_tolerance_mm(double tolerance);
    void add_parameter(std::string name, double value, ParameterUnit unit);
    void set_parameter_value(std::string_view name, double value);

private:
    DocumentId id_;
    std::uint64_t revision_{0};
    double geometric_tolerance_mm_{default_geometric_tolerance_mm};
    std::vector<Parameter> parameters_;
};

} // namespace blendercad
