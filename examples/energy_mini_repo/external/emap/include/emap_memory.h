#pragma once

#include <cstddef>

extern "C" void* EMAP_MemAlloc(std::size_t size);
extern "C" void EMAP_MemFree(void* ptr);
