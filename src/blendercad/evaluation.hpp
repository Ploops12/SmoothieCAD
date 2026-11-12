#pragma once

#include "blendercad/document.hpp"
#include "blendercad/geometry.hpp"
#include "blendercad/sketch_solver.hpp"

#include <memory>

namespace blendercad {

enum class EvaluationState { UpToDate, Failed, Blocked, Suppressed, RolledBack };

struct FeatureEvaluation final {
    DocumentId feature;
    DocumentId body;
    EvaluationState state{EvaluationState::Failed};
    std::string diagnostic;
    std::optional<SketchSolveReport> sketch_report;
    bool reused{};
    bool has_last_valid{};
};

struct BodyEvaluation final {
    DocumentId body;
    DocumentId feature;
    EvaluationState state{EvaluationState::Failed};
    bool has_display{};
};

struct EvaluationReport final {
    DocumentId document;
    std::uint64_t revision{};
    std::vector<FeatureEvaluation> features;
    std::vector<BodyEvaluation> bodies;
    std::size_t computed_count{};
    std::size_t reused_count{};
};

struct EvaluatedDisplay final {
    DocumentId document;
    DocumentId body;
    DocumentId feature;
    DocumentId geometry_feature;
    std::uint64_t revision{};
    std::uint64_t geometry_revision{};
    std::uint64_t geometry_generation{};
    bool current{};
    DisplayMesh mesh;
};

// Owns disposable evaluated geometry and last-valid displays, never design
// definitions. Calls are synchronous; callers must not concurrently mutate the
// supplied document. Queries compare the complete source snapshot, not merely a
// revision that could be repeated by undo or divergent edits.
class FeatureEvaluator final {
public:
    FeatureEvaluator();
    ~FeatureEvaluator();
    FeatureEvaluator(const FeatureEvaluator&) = delete;
    FeatureEvaluator& operator=(const FeatureEvaluator&) = delete;
    // Copy the complete cache state for isolated speculative evaluation. Cached
    // immutable shapes retain shared ownership and stay in their original
    // geometry adapter handle namespace.
    [[nodiscard]] std::unique_ptr<FeatureEvaluator> fork() const;
    [[nodiscard]] EvaluationReport evaluate(const Document& document);
    [[nodiscard]] bool matches_document(const Document& document) const;
    [[nodiscard]] double volume_mm3(const Document& document, const DocumentId& body) const;
    [[nodiscard]] EvaluatedDisplay tessellate(const Document& document, const DocumentId& body,
                                             double linear_deflection_mm, double angular_deflection_rad) const;
    void write_step(const Document& document, const DocumentId& body, const std::string& path) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace blendercad
