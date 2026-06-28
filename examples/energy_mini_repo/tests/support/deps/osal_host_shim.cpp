#include "osal_queue.h"

#include <cstddef>
#include <cstdint>

extern "C" int OSAL_QueueSend(std::uint32_t, const void*, std::size_t) {
    return 0;
}

extern "C" int OSAL_TimerStart(std::uint32_t, std::uint32_t) {
    return 0;
}
