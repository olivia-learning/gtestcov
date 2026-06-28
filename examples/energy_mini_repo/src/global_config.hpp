#pragma once

struct GlobalConfig {
    bool enable_power_limit = true;
    int max_current_amps = 100;
};

extern GlobalConfig g_config;
