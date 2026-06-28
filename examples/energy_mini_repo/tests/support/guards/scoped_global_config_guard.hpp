#pragma once

#include "global_config.hpp"

class ScopedGlobalConfigGuard {
public:
    ScopedGlobalConfigGuard() : saved_(g_config) {}
    ~ScopedGlobalConfigGuard() { g_config = saved_; }

private:
    GlobalConfig saved_;
};
