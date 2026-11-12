#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <string_view>

namespace blendercad {

// A stable UUID value for persisted document and entity identities.
class DocumentId final {
public:
    using Bytes = std::array<std::uint8_t, 16>;

    static DocumentId generate();
    static DocumentId parse(std::string_view text);

    [[nodiscard]] std::string to_string() const;
    [[nodiscard]] const Bytes& bytes() const noexcept { return bytes_; }

    friend bool operator==(const DocumentId&, const DocumentId&) = default;

private:
    explicit DocumentId(Bytes bytes) : bytes_(bytes) {}
    Bytes bytes_;
};

} // namespace blendercad
