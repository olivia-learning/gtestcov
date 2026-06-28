#pragma once

#include <cstddef>
#include <cstdint>

extern "C" int OSAL_QueueSend(std::uint32_t queue_id, const void* data, std::size_t size);
extern "C" int OSAL_TimerStart(std::uint32_t timer_id, std::uint32_t timeout_ms);
