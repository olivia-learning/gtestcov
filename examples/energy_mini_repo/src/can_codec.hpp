#pragma once

#include <array>
#include <cstdint>

struct CanFrame {
    std::uint32_t id = 0;
    std::uint8_t dlc = 0;
    std::array<std::uint8_t, 8> data{};
};

struct EnergyStatus {
    int voltage_mv = 0;
    int current_amps = 0;
    bool enabled = false;
};

CanFrame EncodeEnergyStatus(const EnergyStatus& status);
bool DecodeEnergyCommand(const CanFrame& frame, int* requested_amps);
