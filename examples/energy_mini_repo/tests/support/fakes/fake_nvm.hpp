#pragma once

#include "energy_service.hpp"

class FakeNvmStore : public INvmStore {
public:
    bool WriteLimit(int amps) override {
        if (fail_write) {
            return false;
        }
        write_count++;
        stored_limit = amps;
        return true;
    }

    bool ReadLimit(int* amps) override {
        if (fail_read || amps == nullptr) {
            return false;
        }
        *amps = stored_limit;
        return true;
    }

    bool fail_write = false;
    bool fail_read = false;
    int write_count = 0;
    int stored_limit = 42;
};
