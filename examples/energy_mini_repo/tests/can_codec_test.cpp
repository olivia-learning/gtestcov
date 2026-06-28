#include "can_codec.hpp"

#include <gtest/gtest.h>

TEST(CanCodecConformanceTest, EncodesEnergyStatusFrame) {
    EnergyStatus status{};
    status.voltage_mv = 48000;
    status.current_amps = 25;
    status.enabled = true;

    CanFrame frame = EncodeEnergyStatus(status);

    EXPECT_EQ(frame.id, 0x321u);
    EXPECT_EQ(frame.dlc, 8u);
    EXPECT_EQ(frame.data[0], 0x80u);
    EXPECT_EQ(frame.data[1], 0xBBu);
    EXPECT_EQ(frame.data[2], 25u);
    EXPECT_EQ(frame.data[3], 1u);
}
