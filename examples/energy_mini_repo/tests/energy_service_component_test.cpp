#include "energy_service_harness.hpp"

#include "can_codec.hpp"
#include "crc8.h"

#include <gtest/gtest.h>

namespace {

CanFrame MakeCommand(int amps) {
    CanFrame frame{};
    frame.id = 0x421;
    frame.dlc = 8;
    frame.data[0] = static_cast<unsigned char>(amps);
    frame.data[7] = Crc8(frame.data.data(), 7);
    return frame;
}

}  // namespace

class EnergyServiceComponentTest : public ::testing::Test {
protected:
    void SetUp() override {
        ASSERT_TRUE(harness.Init());
    }

    void TearDown() override {
        harness.Shutdown();
    }

    EnergyServiceHarness harness;
};

TEST_F(EnergyServiceComponentTest, PersistsAcceptedCommandLimit) {
    ASSERT_TRUE(harness.service().HandleCommand(MakeCommand(35)));

    EXPECT_EQ(harness.service().limit_amps(), 35);
    EXPECT_EQ(harness.nvm().write_count, 1);
    EXPECT_EQ(harness.nvm().stored_limit, 35);
}

TEST_F(EnergyServiceComponentTest, RejectsStorageFailure) {
    harness.nvm().fail_write = true;

    EXPECT_FALSE(harness.service().HandleCommand(MakeCommand(35)));
    EXPECT_EQ(harness.nvm().write_count, 0);
}
