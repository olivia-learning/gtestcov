#pragma once

#include "energy_service.hpp"
#include "fake_nvm.hpp"
#include "scoped_global_config_guard.hpp"

#include <memory>

class EnergyServiceHarness {
public:
    EnergyServiceHarness() {
        guard_ = std::make_unique<ScopedGlobalConfigGuard>();
        nvm_ = std::make_unique<FakeNvmStore>();
        service_ = std::make_unique<EnergyService>(nvm_.get());
    }

    ~EnergyServiceHarness() {
        Shutdown();
    }

    bool Init() {
        return service_->Init();
    }

    void Shutdown() {
        if (service_) {
            service_->Shutdown();
            service_.reset();
        }
        nvm_.reset();
        guard_.reset();
    }

    EnergyService& service() { return *service_; }
    FakeNvmStore& nvm() { return *nvm_; }

private:
    std::unique_ptr<ScopedGlobalConfigGuard> guard_;
    std::unique_ptr<FakeNvmStore> nvm_;
    std::unique_ptr<EnergyService> service_;
};
