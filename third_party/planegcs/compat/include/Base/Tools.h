#pragma once

#include <chrono>

// Minimal replacement for the timing utility used only by PlaneGCS diagnostics.
namespace Base {

class TimeElapsed final {
public:
    using Clock = std::chrono::steady_clock;
    TimeElapsed() : start_(Clock::now()) {}

    static double diffTimeF(const TimeElapsed& start, const TimeElapsed& end) {
        return std::chrono::duration<double>(end.start_ - start.start_).count();
    }

private:
    Clock::time_point start_;
};

} // namespace Base
