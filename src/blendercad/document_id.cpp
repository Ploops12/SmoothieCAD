#include "blendercad/document_id.hpp"

#include <algorithm>
#include <array>
#include <random>
#include <stdexcept>

namespace blendercad {
namespace {

int hex_value(char character) {
    if (character >= '0' && character <= '9') return character - '0';
    if (character >= 'a' && character <= 'f') return character - 'a' + 10;
    if (character >= 'A' && character <= 'F') return character - 'A' + 10;
    return -1;
}

} // namespace

DocumentId DocumentId::generate() {
    std::random_device source;
    Bytes bytes{};
    for (auto& byte : bytes) {
        byte = static_cast<std::uint8_t>(source() & 0xffU);
    }

    // RFC 4122 version 4 UUID and variant bits.
    bytes[6] = static_cast<std::uint8_t>((bytes[6] & 0x0fU) | 0x40U);
    bytes[8] = static_cast<std::uint8_t>((bytes[8] & 0x3fU) | 0x80U);
    return DocumentId(bytes);
}

DocumentId DocumentId::parse(std::string_view text) {
    if (text.size() != 36 || text[8] != '-' || text[13] != '-' ||
        text[18] != '-' || text[23] != '-') {
        throw std::invalid_argument("document identity must be a canonical UUID");
    }

    Bytes bytes{};
    std::size_t byte_index = 0;
    int high_nibble = -1;
    for (std::size_t index = 0; index < text.size(); ++index) {
        if (index == 8 || index == 13 || index == 18 || index == 23) continue;
        const char character = text[index];
        const int value = hex_value(character);
        if (value < 0) throw std::invalid_argument("document identity contains non-hexadecimal data");
        if (high_nibble < 0) {
            high_nibble = value;
        } else {
            bytes[byte_index++] = static_cast<std::uint8_t>((high_nibble << 4) | value);
            high_nibble = -1;
        }
    }
    return DocumentId(bytes);
}

std::string DocumentId::to_string() const {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(36);
    for (std::size_t index = 0; index < bytes_.size(); ++index) {
        if (index == 4 || index == 6 || index == 8 || index == 10) result.push_back('-');
        result.push_back(digits[(bytes_[index] >> 4) & 0x0fU]);
        result.push_back(digits[bytes_[index] & 0x0fU]);
    }
    return result;
}

} // namespace blendercad
