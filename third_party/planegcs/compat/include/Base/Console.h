#pragma once

#include <cstdarg>
#include <cstdio>
#include <iostream>
#include <mutex>

// FreeCAD's solver currently sends optional diagnostics through Base::Console.
// Keep the same call surface while routing messages to the host process streams;
// this is a local compatibility shim, not a FreeCAD runtime component.
namespace Base {

class ConsoleWriter final {
public:
    void log(const char* format, ...) const {
        std::lock_guard lock(output_mutex());
        va_list arguments;
        va_start(arguments, format);
        std::vfprintf(stderr, format, arguments);
        va_end(arguments);
    }

    void warning(const char* format, ...) const {
        std::lock_guard lock(output_mutex());
        std::fputs("PlaneGCS warning: ", stderr);
        va_list arguments;
        va_start(arguments, format);
        std::vfprintf(stderr, format, arguments);
        va_end(arguments);
    }

private:
    static std::mutex& output_mutex() {
        static std::mutex mutex;
        return mutex;
    }
};

inline ConsoleWriter& Console() {
    static ConsoleWriter writer;
    return writer;
}

} // namespace Base
