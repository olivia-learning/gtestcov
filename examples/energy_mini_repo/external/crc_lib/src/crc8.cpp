#include "crc8.h"

std::uint8_t Crc8(const std::uint8_t* data, std::size_t size) {
    std::uint8_t crc = 0;
    for (std::size_t i = 0; i < size; ++i) {
        crc ^= data[i];
    }
    return crc;
}
