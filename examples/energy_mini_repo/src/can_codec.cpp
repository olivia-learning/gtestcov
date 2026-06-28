#include "can_codec.hpp"

#include "crc8.h"

CanFrame EncodeEnergyStatus(const EnergyStatus& status) {
    CanFrame frame{};
    frame.id = 0x321;
    frame.dlc = 8;
    frame.data[0] = static_cast<std::uint8_t>(status.voltage_mv & 0xFF);
    frame.data[1] = static_cast<std::uint8_t>((status.voltage_mv >> 8) & 0xFF);
    frame.data[2] = static_cast<std::uint8_t>(status.current_amps & 0xFF);
    frame.data[3] = status.enabled ? 1 : 0;
    frame.data[7] = Crc8(frame.data.data(), 7);
    return frame;
}

bool DecodeEnergyCommand(const CanFrame& frame, int* requested_amps) {
    if (requested_amps == nullptr || frame.id != 0x421 || frame.dlc != 8) {
        return false;
    }
    if (Crc8(frame.data.data(), 7) != frame.data[7]) {
        return false;
    }
    *requested_amps = frame.data[0];
    return true;
}
