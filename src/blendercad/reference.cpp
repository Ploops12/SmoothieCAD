#include "blendercad/reference.hpp"

#include <stdexcept>
#include <algorithm>
#include <charconv>
#include <optional>
#include <string_view>
#include <unordered_map>
#include <unordered_set>

namespace blendercad {
namespace {

using IdentityMap = std::unordered_map<std::string, DocumentId>;

std::string remapped_id(std::string_view id, const IdentityMap& identities) {
    const auto parsed = DocumentId::parse(id);
    const auto found = identities.find(parsed.to_string());
    return found == identities.end() ? parsed.to_string() : found->second.to_string();
}

std::optional<std::size_t> read_size(std::string_view text, std::size_t& position) {
    const auto end = text.find(':', position);
    if (end == std::string_view::npos || end == position) return {};
    const auto digits = text.substr(position, end - position);
    std::size_t value{};
    const auto result = std::from_chars(digits.data(), digits.data() + digits.size(), value);
    if (result.ec != std::errc{} || result.ptr != digits.data() + digits.size()
            || std::to_string(value) != digits) return {};
    position = end + 1;
    return value;
}

// The format is produced by TopologyReferences, not an arbitrary UUID-bearing
// string. Do not normalize malformed/opaque keys into valid names by accident.
std::string remap_key(std::string_view key, const IdentityMap& identities, unsigned depth = 0) {
    if (depth > 128) throw std::invalid_argument("reference cloning exceeds the provenance nesting limit; repair its input first");
    const std::string_view prefixes[] = {"side:", "edge:start:", "edge:end:", "edge:sweep:", "vertex:start:", "vertex:end:"};
    for (const auto prefix : prefixes) {
        if (!key.starts_with(prefix)) continue;
        const auto source = key.substr(prefix.size());
        if (source.size() < 36) return std::string(key);
        const auto suffix = source.substr(36);
        const bool vertex = prefix == "edge:sweep:" || prefix.starts_with("vertex:");
        if ((!vertex && !suffix.empty()) || (vertex && suffix != ":start" && suffix != ":end" && suffix != ":seam")) return std::string(key);
        try {
            const auto id = source.substr(0, 36);
            // DocumentId parsing must not canonicalize an unknown spelling.
            if (DocumentId::parse(id).to_string() != id) return std::string(key);
            return std::string(prefix) + remapped_id(id, identities) + std::string(suffix);
        } catch (const std::invalid_argument&) { return std::string(key); }
    }
    const std::string_view prefix = key.starts_with("derived:") ? "derived:" : key.starts_with("boundary:") ? "boundary:" : "";
    const auto kind = prefix.size();
    if (prefix.empty() || key.size() <= kind + 2 || key[kind + 1] != ':' || key[kind] < '0' || key[kind] > '2') return std::string(key);
    std::size_t position = kind + 2;
    std::string result(key.substr(0, position));
    std::string previous;
    std::vector<std::string> remapped_origins;
    while (position < key.size()) {
        const auto size = read_size(key, position);
        if (!size || *size > key.size() - position) return std::string(key);
        const auto origin = key.substr(position, *size);
        position += *size;
        if (!previous.empty() && origin <= previous) return std::string(key);
        previous = origin;
        if (origin.size() < 40 || origin[36] != '/') return std::string(key);
        std::size_t nested_position = 37;
        const auto nested_size = read_size(origin, nested_position);
        if (!nested_size || *nested_size == 0 || *nested_size != origin.size() - nested_position) return std::string(key);
        std::string id;
        try {
            const auto original_id = origin.substr(0, 36);
            if (DocumentId::parse(original_id).to_string() != original_id) return std::string(key);
            id = remapped_id(original_id, identities);
        } catch (const std::invalid_argument&) { return std::string(key); }
        const auto nested = remap_key(origin.substr(nested_position), identities, depth + 1);
        const auto remapped = id + '/' + std::to_string(nested.size()) + ':' + nested;
        remapped_origins.push_back(remapped);
    }
    // The provenance builder uses a sorted set of origin signatures. New UUIDs
    // can change that order even though every encoded identity has fixed size.
    std::ranges::sort(remapped_origins);
    if (std::adjacent_find(remapped_origins.begin(), remapped_origins.end()) != remapped_origins.end()) {
        throw std::invalid_argument("reference cloning merges distinct provenance origins");
    }
    for (const auto& origin : remapped_origins) result += std::to_string(origin.size()) + ':' + origin;
    return result;
}

} // namespace

void validate_reference(const PersistentReference& reference) {
    switch (reference.kind) {
    case TopologyKind::Face: case TopologyKind::Edge: case TopologyKind::Vertex: break;
    default: throw std::invalid_argument("invalid reference topology kind");
    }
    const auto& key = reference.anchor.key;
    if (key.empty() || key.size() > 65536) throw std::invalid_argument("reference key must contain 1..65536 bytes");
    for (const unsigned char c : key) {
        if (c < 0x21 || c > 0x7e) throw std::invalid_argument("reference keys must use printable non-whitespace ASCII");
    }
}

PersistentReference remap_reference(const PersistentReference& reference,
    const std::vector<std::pair<DocumentId, DocumentId>>& mappings) {
    validate_reference(reference);
    IdentityMap identities;
    std::unordered_set<std::string> outputs;
    for (const auto& [source, destination] : mappings) {
        if (!identities.emplace(source.to_string(), destination).second || !outputs.insert(destination.to_string()).second) {
            throw std::invalid_argument("reference cloning requires unique source and destination identities");
        }
    }
    auto result = reference;
    if (const auto found = identities.find(result.body.to_string()); found != identities.end()) result.body = found->second;
    if (const auto found = identities.find(result.anchor.feature.to_string()); found != identities.end()) result.anchor.feature = found->second;
    result.anchor.key = remap_key(result.anchor.key, identities);
    validate_reference(result);
    return result;
}

} // namespace blendercad
