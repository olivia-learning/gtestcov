#include "emap_memory.h"

#include <cstdlib>

extern "C" void* EMAP_MemAlloc(std::size_t size) {
    return std::malloc(size);
}

extern "C" void EMAP_MemFree(void* ptr) {
    std::free(ptr);
}
