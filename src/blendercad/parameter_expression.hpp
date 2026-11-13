#pragma once

#include "blendercad/document.hpp"

namespace blendercad {

// Internal definition-graph evaluation. Callers pass detached parameters with
// validated names/units and unique names. Literal values remain unchanged;
// expression caches are replaced only from their definitions. No document,
// feature, solver or geometry state is accessed. Throws invalid_argument with
// owning-parameter context for invalid definitions or arithmetic.
void resolve_parameter_expressions(std::vector<Parameter>& parameters);

} // namespace blendercad
