#include "power_limit.hpp"

int CalculatePowerLimitWatts(int voltage_mv, int current_limit_amps) {
    if (voltage_mv <= 0 || current_limit_amps <= 0) {
        return 0;
    }
    return voltage_mv * current_limit_amps / 1000;
}
