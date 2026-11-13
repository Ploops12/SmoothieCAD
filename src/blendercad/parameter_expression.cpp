#include "blendercad/parameter_expression.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <numbers>
#include <stdexcept>
#include <string>
#include <system_error>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace blendercad {
namespace {

constexpr std::size_t maximum_expression_length = 65536;
constexpr std::size_t maximum_syntax_depth = 128;
constexpr std::size_t maximum_dependency_depth = 512;

// Intermediate dimensions are not restricted to the three stored unit types:
// (width * height) / width must be permitted to cancel back to a length.
struct Dimension final {
    int length{};
    int angle{};
    friend bool operator==(const Dimension&, const Dimension&) = default;
};

Dimension dimension_for(ParameterUnit unit) {
    switch (unit) {
    case ParameterUnit::Millimetre: return {1, 0};
    case ParameterUnit::Degree: return {0, 1};
    case ParameterUnit::Unitless: return {};
    }
    throw std::invalid_argument("invalid parameter unit");
}

std::string describe(Dimension dimension) {
    if (dimension == Dimension{}) return "unitless";
    std::string result;
    auto append = [&](std::string_view name, int power) {
        if (power == 0) return;
        if (!result.empty()) result += '*';
        result += name;
        if (power != 1) result += '^' + std::to_string(power);
    };
    append("mm", dimension.length);
    append("deg", dimension.angle);
    return result;
}

struct Quantity final {
    double value{};
    Dimension dimension{};
};

enum class Operation { Literal, Reference, Negate, Add, Subtract, Multiply, Divide };

struct Instruction final {
    Operation operation{};
    std::size_t offset{};
    Quantity literal{};
    std::size_t reference{};
};

struct Program final {
    std::vector<Instruction> instructions;
    std::vector<std::size_t> dependencies;
};

bool ascii_digit(char character) { return character >= '0' && character <= '9'; }
bool identifier_start(char character) {
    return (character >= 'a' && character <= 'z')
        || (character >= 'A' && character <= 'Z') || character == '_';
}
bool identifier_continue(char character) { return identifier_start(character) || ascii_digit(character); }
bool whitespace(char character) {
    return character == ' ' || character == '\t' || character == '\r'
        || character == '\n' || character == '\f' || character == '\v';
}

[[noreturn]] void fail(const Parameter& owner, std::string message) {
    throw std::invalid_argument("parameter '" + owner.name + "': " + std::move(message));
}

[[noreturn]] void fail_at(const Parameter& owner, std::size_t offset, std::string message) {
    fail(owner, "expression at byte " + std::to_string(offset + 1) + ": " + std::move(message));
}

// Syntax recursion is bounded; output is postfix so arbitrarily long flat
// arithmetic does not produce recursive AST destruction or evaluation.
class Parser final {
public:
    Parser(const Parameter& owner, const std::unordered_map<std::string, std::size_t>& names)
        : owner_(owner), text_(*owner.expression), names_(names) {}

    Program compile() {
        if (text_.size() > maximum_expression_length) {
            fail(owner_, "expression exceeds maximum length of 65536 bytes");
        }
        sum(0);
        skip_space();
        if (position_ != text_.size()) {
            fail_at(owner_, position_, "unexpected token; expected an arithmetic operator");
        }
        return std::move(program_);
    }

private:
    const Parameter& owner_;
    std::string_view text_;
    const std::unordered_map<std::string, std::size_t>& names_;
    std::size_t position_{};
    Program program_;
    std::unordered_set<std::size_t> referenced_;

    void skip_space() {
        while (position_ < text_.size() && whitespace(text_[position_])) ++position_;
    }

    void emit(Operation operation, std::size_t offset) {
        program_.instructions.push_back({operation, offset, {}, 0});
    }

    void sum(std::size_t depth) {
        product(depth);
        for (;;) {
            skip_space();
            if (position_ == text_.size() || (text_[position_] != '+' && text_[position_] != '-')) return;
            const auto offset = position_;
            const auto operation = text_[position_++] == '+' ? Operation::Add : Operation::Subtract;
            product(depth);
            emit(operation, offset);
        }
    }

    void product(std::size_t depth) {
        unary(depth);
        for (;;) {
            skip_space();
            if (position_ == text_.size() || (text_[position_] != '*' && text_[position_] != '/')) return;
            const auto offset = position_;
            const auto operation = text_[position_++] == '*' ? Operation::Multiply : Operation::Divide;
            unary(depth);
            emit(operation, offset);
        }
    }

    void unary(std::size_t depth) {
        if (depth > maximum_syntax_depth) {
            fail_at(owner_, position_, "syntax nesting exceeds maximum depth of 128");
        }
        skip_space();
        if (position_ < text_.size() && (text_[position_] == '+' || text_[position_] == '-')) {
            const auto offset = position_;
            const bool negate = text_[position_++] == '-';
            unary(depth + 1);
            if (negate) emit(Operation::Negate, offset);
            return;
        }
        primary(depth);
    }

    void primary(std::size_t depth) {
        skip_space();
        if (position_ == text_.size()) fail_at(owner_, position_, "expected a number, parameter reference or '('");
        const auto offset = position_;
        const char character = text_[position_];
        if (character == '(') {
            ++position_;
            sum(depth + 1);
            skip_space();
            if (position_ == text_.size() || text_[position_] != ')') {
                fail_at(owner_, position_, "expected ')' to close parenthesized expression");
            }
            ++position_;
        } else if (ascii_digit(character) || character == '.') {
            number();
        } else if (identifier_start(character)) {
            reference(identifier(), offset);
        } else if (character == '"') {
            reference(quoted_reference(), offset);
        } else {
            fail_at(owner_, position_, "expected a number, parameter reference or '('");
        }
    }

    std::string identifier() {
        const auto start = position_++;
        while (position_ < text_.size() && identifier_continue(text_[position_])) ++position_;
        return std::string(text_.substr(start, position_ - start));
    }

    std::string quoted_reference() {
        const auto start = position_++;
        std::string result;
        while (position_ < text_.size()) {
            const unsigned char character = text_[position_++];
            if (character == '"') return result;
            if (character == '\\') {
                if (position_ == text_.size()) fail_at(owner_, position_, "unfinished quoted-reference escape");
                const char escaped = text_[position_++];
                if (escaped != '"' && escaped != '\\') {
                    fail_at(owner_, position_ - 1, "quoted references permit only escaped quotes and backslashes");
                }
                result += escaped;
            } else {
                if (character < 0x20 || character == 0x7f) {
                    fail_at(owner_, position_ - 1, "quoted parameter names cannot contain control characters");
                }
                result += static_cast<char>(character);
            }
        }
        fail_at(owner_, start, "unterminated quoted parameter reference");
    }

    void reference(std::string name, std::size_t offset) {
        const auto found = names_.find(name);
        if (found == names_.end()) fail_at(owner_, offset, "unknown parameter reference '" + name + "'");
        program_.instructions.push_back({Operation::Reference, offset, {}, found->second});
        if (referenced_.insert(found->second).second) program_.dependencies.push_back(found->second);
    }

    void number() {
        const auto start = position_;
        bool digits = false;
        while (position_ < text_.size() && ascii_digit(text_[position_])) {
            digits = true;
            ++position_;
        }
        if (position_ < text_.size() && text_[position_] == '.') {
            ++position_;
            while (position_ < text_.size() && ascii_digit(text_[position_])) {
                digits = true;
                ++position_;
            }
        }
        if (!digits) fail_at(owner_, start, "a numeric literal requires digits");
        if (position_ < text_.size() && (text_[position_] == 'e' || text_[position_] == 'E')) {
            ++position_;
            if (position_ < text_.size() && (text_[position_] == '+' || text_[position_] == '-')) ++position_;
            const auto exponent_start = position_;
            while (position_ < text_.size() && ascii_digit(text_[position_])) ++position_;
            if (position_ == exponent_start) fail_at(owner_, position_, "scientific notation requires exponent digits");
        }
        double value{};
        const char* begin = text_.data() + start;
        const char* end = text_.data() + position_;
        const auto converted = std::from_chars(begin, end, value, std::chars_format::general);
        if (converted.ec != std::errc{} || converted.ptr != end || !std::isfinite(value)) {
            fail_at(owner_, start, "numeric literal is outside the finite double-precision range");
        }
        skip_space();
        Dimension dimension;
        double factor = 1;
        if (position_ < text_.size() && identifier_start(text_[position_])) {
            const auto unit_offset = position_;
            const auto unit = identifier();
            if (unit == "mm") dimension.length = 1;
            else if (unit == "cm") { dimension.length = 1; factor = 10; }
            else if (unit == "m") { dimension.length = 1; factor = 1000; }
            else if (unit == "um") { dimension.length = 1; factor = 0.001; }
            else if (unit == "deg") dimension.angle = 1;
            else if (unit == "rad") { dimension.angle = 1; factor = 180 / std::numbers::pi; }
            else fail_at(owner_, unit_offset, "unknown unit suffix '" + unit + "'; use mm, cm, m, um, deg or rad");
        }
        value *= factor;
        if (!std::isfinite(value)) fail_at(owner_, start, "unit conversion produced a nonfinite value");
        program_.instructions.push_back({Operation::Literal, start, {value, dimension}, 0});
    }
};

Quantity evaluate(const Parameter& owner, const Program& program, const std::vector<Parameter>& parameters) {
    std::vector<Quantity> stack;
    for (const auto& instruction : program.instructions) {
        switch (instruction.operation) {
        case Operation::Literal:
            stack.push_back(instruction.literal);
            continue;
        case Operation::Reference: {
            const auto& parameter = parameters[instruction.reference];
            stack.push_back({parameter.value, dimension_for(parameter.unit)});
            continue;
        }
        case Operation::Negate:
            stack.back().value = -stack.back().value;
            continue;
        default: break;
        }
        const auto right = stack.back();
        stack.pop_back();
        auto& left = stack.back();
        switch (instruction.operation) {
        case Operation::Add: case Operation::Subtract:
            if (left.dimension != right.dimension) {
                fail_at(owner, instruction.offset, "cannot add/subtract " + describe(left.dimension)
                        + " and " + describe(right.dimension));
            }
            left.value = instruction.operation == Operation::Add ? left.value + right.value : left.value - right.value;
            break;
        case Operation::Multiply:
            left.value *= right.value;
            left.dimension.length += right.dimension.length;
            left.dimension.angle += right.dimension.angle;
            break;
        case Operation::Divide:
            if (right.value == 0) fail_at(owner, instruction.offset, "division by zero");
            left.value /= right.value;
            left.dimension.length -= right.dimension.length;
            left.dimension.angle -= right.dimension.angle;
            break;
        default: break;
        }
        if (!std::isfinite(left.value)) fail_at(owner, instruction.offset, "arithmetic produced a nonfinite value");
    }
    const auto result = stack.back();
    const auto expected = dimension_for(owner.unit);
    if (result.dimension != expected) {
        fail(owner, "expression dimension " + describe(result.dimension)
             + " does not match declared unit " + std::string(unit_name(owner.unit)));
    }
    return result;
}

} // namespace

void resolve_parameter_expressions(std::vector<Parameter>& parameters) {
    std::unordered_map<std::string, std::size_t> names;
    for (std::size_t index = 0; index < parameters.size(); ++index) names.emplace(parameters[index].name, index);
    std::vector<Program> programs(parameters.size());
    for (std::size_t index = 0; index < parameters.size(); ++index) {
        const auto& parameter = parameters[index];
        if (parameter.expression) programs[index] = Parser(parameter, names).compile();
        else if (!std::isfinite(parameter.value)) fail(parameter, "literal value must be finite");
    }
    enum class State { Unseen, Active, Done };
    struct Frame final { std::size_t parameter; std::size_t next_dependency{}; };
    std::vector<State> state(parameters.size(), State::Unseen);
    std::vector<std::size_t> depths(parameters.size());
    std::vector<Frame> stack;
    for (std::size_t root = 0; root < parameters.size(); ++root) {
        if (state[root] == State::Done) continue;
        stack.push_back({root});
        state[root] = State::Active;
        while (!stack.empty()) {
            auto& frame = stack.back();
            const auto index = frame.parameter;
            const auto& dependencies = programs[index].dependencies;
            if (frame.next_dependency < dependencies.size()) {
                const auto dependency = dependencies[frame.next_dependency++];
                if (state[dependency] == State::Active) {
                    std::string cycle;
                    const auto begin = std::find_if(stack.begin(), stack.end(), [&](const auto& item) {
                        return item.parameter == dependency;
                    });
                    for (auto item = begin; item != stack.end(); ++item) {
                        if (!cycle.empty()) cycle += " -> ";
                        cycle += "'" + parameters[item->parameter].name + "'";
                    }
                    fail(parameters[index], "dependency cycle: " + cycle + " -> '" + parameters[dependency].name + "'");
                }
                if (state[dependency] == State::Unseen) {
                    if (stack.size() >= maximum_dependency_depth) {
                        fail(parameters[index], "dependency chain exceeds maximum depth of 512");
                    }
                    state[dependency] = State::Active;
                    stack.push_back({dependency});
                }
                continue;
            }
            std::size_t depth = 1;
            for (const auto dependency : dependencies) depth = std::max(depth, depths[dependency] + 1);
            if (depth > maximum_dependency_depth) {
                fail(parameters[index], "dependency chain exceeds maximum depth of 512");
            }
            depths[index] = depth;
            if (parameters[index].expression) {
                parameters[index].value = evaluate(parameters[index], programs[index], parameters).value;
            }
            state[index] = State::Done;
            stack.pop_back();
        }
    }
}

} // namespace blendercad
