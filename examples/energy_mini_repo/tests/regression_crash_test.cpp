#include "can_codec.hpp"

#include <gtest/gtest.h>

TEST(EnergyRegressionTest, DecodeRejectsNullOutputInsteadOfCrashing) {
    CanFrame frame{};
    frame.id = 0x421;
    frame.dlc = 8;

    EXPECT_FALSE(DecodeEnergyCommand(frame, nullptr));
}
