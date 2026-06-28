#pragma once

#include "can_codec.hpp"

class INvmStore {
public:
    virtual ~INvmStore() = default;
    virtual bool WriteLimit(int amps) = 0;
    virtual bool ReadLimit(int* amps) = 0;
};

class EnergyService {
public:
    explicit EnergyService(INvmStore* nvm);
    bool Init();
    void Shutdown();
    bool HandleCommand(const CanFrame& frame);
    bool SendStatus();
    int limit_amps() const { return limit_amps_; }

private:
    INvmStore* nvm_;
    bool initialized_ = false;
    int limit_amps_ = 0;
};
