#include "energy_service.hpp"

#include "emap_memory.h"
#include "global_config.hpp"
#include "hal_adc.h"
#include "osal_queue.h"

EnergyService::EnergyService(INvmStore* nvm) : nvm_(nvm) {}

bool EnergyService::Init() {
    if (nvm_ == nullptr) {
        return false;
    }
    int saved_limit = 0;
    if (!nvm_->ReadLimit(&saved_limit)) {
        saved_limit = g_config.max_current_amps;
    }
    limit_amps_ = saved_limit;
    initialized_ = true;
    return true;
}

void EnergyService::Shutdown() {
    initialized_ = false;
}

bool EnergyService::HandleCommand(const CanFrame& frame) {
    if (!initialized_) {
        return false;
    }
    int requested_amps = 0;
    if (!DecodeEnergyCommand(frame, &requested_amps)) {
        return false;
    }
    if (!g_config.enable_power_limit || requested_amps > g_config.max_current_amps) {
        return false;
    }
    void* scratch = EMAP_MemAlloc(16);
    if (scratch == nullptr) {
        return false;
    }
    EMAP_MemFree(scratch);
    if (!nvm_->WriteLimit(requested_amps)) {
        return false;
    }
    limit_amps_ = requested_amps;
    return true;
}

bool EnergyService::SendStatus() {
    if (!initialized_) {
        return false;
    }
    EnergyStatus status{};
    status.voltage_mv = HAL_ReadAdcMillivolts(0);
    status.current_amps = limit_amps_;
    status.enabled = g_config.enable_power_limit;
    CanFrame frame = EncodeEnergyStatus(status);
    return OSAL_QueueSend(1, &frame, sizeof(frame)) == 0;
}
