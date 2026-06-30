# Codex 嵌入式 C++ 大型项目 gtest 测试生成初始指导文档

> 适用对象：Codex、opencode、弱 AI 模型、测试生成 Agent、测试补充工具。  
> 适用项目：大型嵌入式 C++ 项目，尤其是能源控制器、控制器软件、通信模块、协议栈、依赖 `dependency.xml` 下载外部代码的项目。  
> 核心目标：让 AI 工具稳定、可控、可迁移地为复杂 C++ 项目补充 gtest 测试，并提高有效覆盖率，而不是机械生成传统 UT。

---

## 1. 背景与核心结论

本项目目标不是让 Codex “给每个类都写一个 UT”。

大型嵌入式 C++ 项目通常具有以下特征：

```text
1. 类依赖其他类，依赖链较深。
2. 依赖全局变量、单例、静态缓存、运行时注册表。
3. 接口多，模块间通过消息、事件、队列、总线报文交互。
4. 存在 OSAL、RTOS、HAL、驱动、NVM、通信协议、硬件寄存器。
5. 依赖代码可能由 dependency.xml 或类似 manifest 下载。
6. 主工程大量调用依赖库中的封装函数，例如 EMAP_MemFree、EMAP_QueueSend、EMAP_TimerStart。
7. 强行写纯 UT 容易段错误、编译不过、链接不过，或生成无效断言。
```

因此，本工具的基本原则是：

```text
稳定性 > 可编译 > 可运行 > 有效断言 > 覆盖率提升
```

Codex 必须先判断测试边界，再决定测试方法，最后才生成代码。

最重要的结论：

```text
纯逻辑 -> Unit Test
复杂类 -> Component Test
模块间消息 -> Message Interface Test
消息格式 / 协议 -> Message Conformance Test
生命周期复杂 -> Lifecycle Test / Smoke Test
错误路径 -> Fault Injection Test
历史 crash -> Regression Test
legacy 行为不清 -> Characterization Test
硬件强相关 -> HAL fake test / SIL / HIL
```

---

## 2. 总体架构：通用核心 + 项目适配层

AI 工具必须拆成两层：

```text
通用核心 Generic Core
+ 项目适配层 Project Profile
```

### 2.1 通用核心负责什么

通用核心包含跨项目通用的能力：

```text
1. 代码依赖分析。
2. 测试类型判定。
3. gtest / gmock 测试生成。
4. TestHarness / fixture / fake / shim / guard 生成。
5. dependency.xml / manifest 依赖解析框架。
6. 编译错误修复。
7. 运行失败分析。
8. sanitizer / coverage 结果分析。
9. 段错误风险规避。
10. 反模式检查。
```

这些能力不应绑定 OpenHarmony、F Prime、PX4 或能源控制器某一个项目。

### 2.2 项目适配层负责什么

项目适配层描述每个项目自己的风格和规则：

```text
1. 测试框架和测试宏。
2. 测试目录结构。
3. 构建系统。
4. 测试 target 命名规则。
5. fake / mock / shim / harness 放置目录。
6. dependency.xml 或其他 manifest 路径。
7. 覆盖率排除规则。
8. 项目专用依赖处理策略。
9. 项目专用测试类型命名。
10. 项目专用 CI 命令。
```

### 2.3 严禁把某一个项目风格写死到通用核心

例如，不允许在通用核心中默认：

```text
1. 使用 OpenHarmony 的 HWTEST / HWTEST_F。
2. 默认修改 BUILD.gn。
3. 默认存在 subsystem / bundle 结构。
4. 默认存在 F Prime 的 TesterBase / GTestBase。
5. 默认存在 PX4 的 uORB / parameter 测试框架。
6. 默认能源控制器存在 EMAP_MemFree。
```

这些必须放在对应 profile 中。

---

## 3. 推荐的项目 Profile 结构

每个项目应提供一个 `project_profile.yaml` 或等价配置。

### 3.1 能源控制器项目 profile 示例

```yaml
project_name: energy_controller
language: c++
test_framework: gtest
mock_framework: gmock

style:
  preferred_macros:
    - TEST
    - TEST_F
    - TEST_P
  forbidden_macros:
    - HWTEST
    - HWTEST_F
  fixture_naming: "{Target}Test"
  test_file_suffix: "_test.cpp"

build:
  system: cmake  # 或 make / ninja / 自研构建
  test_target_pattern: "{module}_test"
  run_command: "./build/tests/{test_binary}"
  coverage_command: "./tools/run_coverage.sh"

dependency:
  manifest: dependency.xml
  dependency_root:
    - external
    - deps
    - third_party
  use_real_dependency_headers: true
  host_shim_dir: tests/support/deps
  exclude_from_coverage:
    - external/.*
    - deps/.*
    - third_party/.*
    - generated/.*
    - tests/.*
    - mock/.*
    - fake/.*
    - shim/.*

test_support:
  fake_dir: tests/support/fakes
  harness_dir: tests/support/harness
  guard_dir: tests/support/guards
  builder_dir: tests/support/builders
  dependency_shim_dir: tests/support/deps

embedded_policy:
  hardware_access: require_hal_fake_or_hil
  rtos_access: fake_osal_for_host_gtest
  time_access: use_fake_clock
  async_access: use_fake_executor_or_join_in_teardown
  memory_api:
    EMAP_MemAlloc: real_or_host_shim
    EMAP_MemFree: real_or_host_shim

common_test_types:
  - Unit Test
  - Component Test
  - Message Interface Test
  - Message Conformance Test
  - Lifecycle Test
  - Resource Limit Test
  - Fault Injection Test
  - Regression Test
  - Characterization Test
  - Struct Layout / ABI Test
  - Power-cycle / Recovery Test
```

### 3.2 F Prime profile 示例

F Prime 可作为第一个验证项目，用于验证通用核心能力。

```yaml
project_name: fprime
language: c++
test_framework: googletest

style:
  use_project_harness: true
  harness_type:
    - TesterBase
    - GTestBase
    - Tester
  preferred_test_boundary:
    - component_interface
    - command
    - port
    - event
    - telemetry

build:
  system: fprime-util
  generate_ut_command: "fprime-util generate --ut"
  run_test_command: "fprime-util check"
  coverage_command: "fprime-util check --all --coverage"

rules:
  do_not_generate_plain_gtest_when_component_harness_exists: true
  test_through_component_interface: true
  avoid_direct_internal_state_mutation: true
  external_library_policy:
    real_library_if_host_linkable: true
    mock_or_stub_for_fault_injection: true
```

### 3.3 OpenHarmony profile 示例

OpenHarmony 只作为复杂工程验证样本，不应污染通用核心。

```yaml
project_name: openharmony
language: c++
test_framework: googletest

style:
  preferred_macros:
    - HWTEST
    - HWTEST_F
  forbidden_for_other_projects:
    - HWTEST
    - HWTEST_F

build:
  system: gn
  build_file: BUILD.gn

manifest:
  type: repo_xml
  examples:
    - manifest/default.xml
    - manifest/chromium_deps.xml
```

---

## 4. Codex 执行总流程

Codex 不允许直接开始写测试。必须按以下流程执行。

```text
Step 1: 项目风格发现
Step 2: 读取 project_profile.yaml
Step 3: 解析 dependency.xml / manifest
Step 4: 搜索现有测试风格
Step 5: 分析目标代码依赖
Step 6: 判断测试类型
Step 7: 输出测试生成判定报告
Step 8: 生成最小稳定测试
Step 9: 修改构建文件
Step 10: 编译测试
Step 11: 修复编译错误
Step 12: 运行测试
Step 13: 分析失败 / 段错误 / sanitizer 报告
Step 14: 生成 coverage 报告
Step 15: 根据未覆盖分支补充后续测试建议
Step 16: 输出人工 review 清单
```

---

## 5. 项目风格发现规则

Codex 每次进入一个项目，先执行风格发现。

需要搜索：

```bash
find . -iname "*test*"
rg "TEST_F|TEST_P|TEST|HWTEST|HWTEST_F" .
rg "gtest/gtest.h" .
rg "gmock/gmock.h" .
rg "dependency.xml|manifest|west.yml|project.yml|BUILD.gn|CMakeLists.txt" .
```

输出：

```text
Project style discovery:
- 当前项目测试框架是什么？
- 使用 TEST / TEST_F / TEST_P 还是项目自定义宏？
- 测试文件放在哪些目录？
- 测试 target 如何命名？
- 构建系统是什么？
- 依赖清单是什么？
- fake / mock / shim / harness 是否已有目录？
- 是否已有全局变量 guard？
- 是否已有 OSAL / HAL / memory host shim？
- 当前项目禁止使用哪些外部项目风格？
```

如果项目 profile 与扫描结果冲突，Codex 必须停止并提示，而不是自行猜测。

---

## 6. 测试类型判定总表

| 代码特征 | 推荐测试类型 | gtest 是否适合 | 说明 |
|---|---|---:|---|
| 无全局变量、无 IO、无时间、无硬件、输入输出确定 | Unit Test | 是 | 直接测函数或小类，优先表驱动 |
| 同一逻辑需要多组输入输出 | Table-Driven / Parameterized Test | 是 | 用 `TEST_P` 或表驱动减少重复 |
| 多个类型或多个实现要满足同一行为 | Typed Test / Interface Implementation Conformance Test | 是 | 适合 HAL、Storage、Bus、Queue 接口 |
| 类依赖多个内部类，但外部依赖可 fake | Component Test | 是 | 走真实 Init，fake 外部边界 |
| 类依赖全局变量、单例、静态缓存 | Component Test + Scoped Global Guard | 是 | 必须保存 / 恢复全局状态 |
| 需要验证发出的消息 ID、长度、payload、CRC、bitfield | Message Interface Test | 是 | 嵌入式模块间消息测试首选 |
| 需要验证收到消息后的解析、状态迁移、错误处理 | Message Interface Test / State Machine Test | 是 | 使用 fake bus / fake peer |
| 编解码、协议转换、字节序、checksum | Message Conformance Test | 是 | 对照 ICD / 协议文档 |
| 有 Init / Start / Stop / Shutdown / Reset | Lifecycle Test / Smoke Test | 是 | 专门测生命周期和资源清理 |
| 队列、内存池、ring buffer、MAX_XXX | Resource Limit Test | 是 | 测空、满、刚好满、溢出、复用 |
| callback / listener / observer / event 通知 | Callback / Observer Test | 是 | 验证调用次数、参数、顺序 |
| 下游失败、超时、错误码、队列满 | Fault Injection Test | 是 | 用 fake / mock 控制失败返回 |
| NVM、Flash、持久化状态、启动恢复 | Power-cycle / Recovery Test | 是 | 用 fake NVM 模拟重启 |
| 通信结构体、NVM 结构体、DMA buffer、共享内存 | Struct Layout / ABI Test | 是 | 校验 `sizeof`、`alignof`、`offsetof` |
| legacy 代码，没人清楚完整规格 | Characterization Test | 是 | 先锁住现有行为 |
| 历史 crash 输入 | Regression Test | 是 | 断言不 crash，返回可控错误 |
| 多模块真实连接运行 | Module Integration Test / SIL Test | 部分适合 | gtest 可作 runner，但依赖仿真环境 |
| 真实板卡、真实 IO、传感器、执行器 | HIL Test | 不建议仅用 gtest | 需要板级测试框架或硬件环境 |
| Parser / 外部二进制输入 / 协议 payload | Fuzz Test + Regression gtest | gtest 做回归 | fuzz 找问题，gtest 固化问题 |
| 性能敏感逻辑 | Benchmark / Perf Regression | 不建议普通 gtest | 使用 benchmark 工具或单独性能 job |

---

## 7. 测试类型判定决策树

Codex 必须按以下顺序判断。

```text
START

1. 目标是否是纯逻辑？
   条件：
   - 不访问全局变量
   - 不访问硬件
   - 不访问文件/网络/总线
   - 不依赖真实时间
   - 不启动线程/任务
   - 不需要复杂 Init
   - 输入输出确定

   是 -> Unit Test / Table-Driven Test
   否 -> 继续

2. 目标是否是复杂类或模块？
   条件：
   - 需要 Init / Shutdown
   - 依赖其他类
   - 依赖全局变量或单例
   - 依赖外部 IO / OSAL / HAL / bus / storage

   是 -> Component Test + TestHarness + fake + guard
   否 -> 继续

3. 目标是否是模块间消息收发？
   条件：
   - send / receive message
   - CAN / LIN / UART / SPI / Modbus / internal event
   - command / response
   - queue message
   - ICD-defined message

   是 -> Message Interface Test
   否 -> 继续

4. 目标是否是消息格式、编解码、协议符合性？
   条件：
   - payload layout
   - endian
   - bitfield
   - CRC / checksum
   - enum mapping
   - signal scale / offset

   是 -> Message Conformance Test
   否 -> 继续

5. 目标是否是生命周期？
   条件：
   - Init / Start / Stop / Shutdown / DeInit / Reset

   是 -> Lifecycle Test / Smoke Test
   否 -> 继续

6. 目标是否是错误路径？
   条件：
   - 下游失败
   - 超时
   - 队列满
   - storage 写失败
   - CRC 错误
   - 非法消息长度

   是 -> Fault Injection Test
   否 -> 继续

7. 目标是否直接访问硬件寄存器或真实板级 IO？
   是 -> 不生成 host gtest；需要 HAL fake / SIL / HIL
   否 -> 继续

8. 代码是否是 legacy 且规格不清？
   是 -> Characterization Test
   否 -> 继续

9. 是否是历史 crash 或线上 bug？
   是 -> Regression Test
   否 -> 继续

10. 默认选择：
   Component Test，而不是强行 Unit Test。
```

---

## 8. Codex 生成测试前必须输出的判定报告

任何修改前，Codex 必须输出以下内容。

```text
Target:
- 被测文件 / 类 / 函数 / 模块

Project style:
- 当前项目测试宏
- 当前项目测试目录
- 当前项目构建系统
- 是否有已有类似测试可参考

Dependency resolution:
- dependency.xml / manifest 路径
- 相关依赖包
- 本地依赖路径
- include 路径
- 相关符号定义位置

Observed dependencies:
- 依赖类
- 依赖全局变量
- 依赖单例
- 依赖 OSAL / HAL / RTOS / bus / storage / clock
- 是否直接访问硬件
- 是否涉及线程 / timer / callback

Observed dependency symbols:
- EMAP_MemFree: macro / extern function / static inline / weak / unknown
- EMAP_MemAlloc: ...
- EMAP_QueueSend: ...
- EMAP_TimerStart: ...

Selected test type:
- Unit Test / Component Test / Message Interface Test / Message Conformance Test /
  Lifecycle Test / Fault Injection Test / Regression Test / Characterization Test /
  SIL / HIL-required

Reason:
- 为什么选择该测试类型
- 为什么不选择纯 UT
- 为什么不直接 mock 所有依赖

Required support:
- TestHarness
- fixture
- fake
- mock
- scoped global guard
- dependency host shim
- test data
- builder

Safety risks:
- Init / Shutdown
- 依赖生命周期
- 全局状态恢复
- allocator ownership
- 异步线程停止
- ASan / UBSan / TSan

Planned files:
- 新增文件
- 修改文件
- 构建文件修改
```

没有这个判定报告，不允许直接写测试。

---

## 9. gtest 在本项目中的定位

gtest 是 C++ 开发者测试底座。

它负责：

```text
1. 组织测试用例。
2. 提供 ASSERT / EXPECT 断言。
3. 提供 TEST / TEST_F / TEST_P。
4. 提供 fixture / SetUp / TearDown。
5. 承载 fake / mock / shim / harness。
6. 支撑覆盖率统计。
7. 配合 ASan / UBSan / TSan 找内存和并发问题。
```

它不单独负责：

```text
1. 真实硬件电气行为。
2. 控制闭环实时性。
3. 真实 CAN/LIN/UART/SPI 物理层稳定性。
4. EMC、功耗、温升、长稳老化。
5. 整机安全认证。
6. HIL 系统验证。
```

结论：

```text
gtest 可以作为开发者测试主框架，
但不能把所有测试都写成传统类级 UT。
```

---

## 10. TestHarness / Fixture 规则

复杂类必须使用 TestHarness 或 `TEST_F` fixture。

### 10.1 禁止的写法

```cpp
TEST(FooTest, HandleOk) {
    FooService service;
    service.Handle(req);  // 高风险：未 Init，依赖未准备
}
```

### 10.2 推荐写法

```cpp
class FooServiceHarness {
public:
    FooServiceHarness() {
        global_guard_ = std::make_unique<ScopedGlobalConfigGuard>();

        fake_bus_ = std::make_unique<FakeBus>();
        fake_clock_ = std::make_unique<FakeClock>();
        fake_storage_ = std::make_unique<FakeStorage>();

        deps_.bus = fake_bus_.get();
        deps_.clock = fake_clock_.get();
        deps_.storage = fake_storage_.get();

        service_ = std::make_unique<FooService>(deps_);
    }

    ~FooServiceHarness() {
        Shutdown();
    }

    Status Init() {
        return service_->Init();
    }

    void Shutdown() {
        if (service_) {
            service_->Shutdown();
            service_.reset();
        }
        fake_storage_.reset();
        fake_clock_.reset();
        fake_bus_.reset();
        global_guard_.reset();
    }

    FooService& service() { return *service_; }
    FakeBus& bus() { return *fake_bus_; }
    FakeClock& clock() { return *fake_clock_; }
    FakeStorage& storage() { return *fake_storage_; }

private:
    std::unique_ptr<ScopedGlobalConfigGuard> global_guard_;
    std::unique_ptr<FakeBus> fake_bus_;
    std::unique_ptr<FakeClock> fake_clock_;
    std::unique_ptr<FakeStorage> fake_storage_;
    FooDeps deps_{};
    std::unique_ptr<FooService> service_;
};
```

测试：

```cpp
class FooServiceComponentTest : public ::testing::Test {
protected:
    void SetUp() override {
        harness = std::make_unique<FooServiceHarness>();
        ASSERT_TRUE(harness->Init().ok());
    }

    void TearDown() override {
        harness.reset();
    }

    std::unique_ptr<FooServiceHarness> harness;
};

TEST_F(FooServiceComponentTest, HandlesValidRequest) {
    auto result = harness->service().Handle(MakeValidRequest());

    ASSERT_TRUE(result.ok());
    EXPECT_EQ(harness->storage().WriteCount(), 1u);
}
```

---

## 11. 生命周期安全规则

### 11.1 Init 必须使用 ASSERT

错误：

```cpp
EXPECT_TRUE(sut->Init().ok());
sut->Handle(req);
```

正确：

```cpp
ASSERT_TRUE(sut->Init().ok());
sut->Handle(req);
```

规则：

```text
初始化成功、指针非空、资源创建成功：用 ASSERT。
业务结果校验：用 EXPECT。
```

### 11.2 被测对象必须先于依赖析构

C++ 成员析构顺序是声明顺序的逆序。

推荐：

```cpp
class FooHarness {
private:
    std::unique_ptr<FakeBus> bus_;
    std::unique_ptr<FakeClock> clock_;
    std::unique_ptr<FakeStorage> storage_;

    // sut 最后声明，因此最先析构
    std::unique_ptr<FooService> sut_;
};
```

### 11.3 TearDown 必须停止异步资源

```cpp
void TearDown() override {
    if (sut) {
        sut->Stop();
        sut->Join();
        sut->Shutdown();
        sut.reset();
    }
}
```

### 11.4 不允许用 sleep 等待异步

禁止：

```cpp
std::this_thread::sleep_for(std::chrono::seconds(1));
```

推荐：

```cpp
fake_clock->AdvanceMs(1000);
fake_executor->RunUntilIdle();
sut->Poll();
```

---

## 12. 全局变量 / 单例处理规则

如果被测代码依赖全局变量，必须使用 RAII guard。

禁止：

```cpp
g_config.enable = true;
// 测试结束不恢复
```

推荐：

```cpp
class ScopedGlobalConfigGuard {
public:
    ScopedGlobalConfigGuard()
        : old_config_(g_config) {}

    ~ScopedGlobalConfigGuard() {
        g_config = old_config_;
        GlobalCache::Clear();
        RuntimeRegistry::ResetForTest();
    }

private:
    Config old_config_;
};
```

使用：

```cpp
TEST_F(FooComponentTest, DisabledByConfig) {
    ScopedGlobalConfigGuard guard;
    g_config.enable_foo = false;

    ASSERT_TRUE(harness->Init().ok());

    auto result = harness->service().Handle(MakeValidRequest());

    ASSERT_FALSE(result.ok());
    EXPECT_EQ(result.error_code(), ErrorCode::Disabled);
}
```

如果单例无法恢复，Codex 应优先查找：

```text
ResetForTest()
SetForTest()
ClearForTest()
ScopedOverride
```

如果不存在，应建议新增测试缝，而不是让测试污染全局状态。

---

## 13. Fake / Mock 选择规则

### 13.1 优先 fake 外部边界

优先 fake：

```text
1. DB / storage / NVM / Flash。
2. CAN / LIN / UART / SPI / Modbus bus。
3. RPC / network / external module peer。
4. OSAL queue / RTOS task / timer。
5. clock / random。
6. HAL driver。
7. file system。
```

### 13.2 优先使用真实内部逻辑

优先使用真实对象：

```text
1. 纯逻辑类。
2. validator。
3. codec。
4. rule evaluator。
5. state machine core。
6. checksum / CRC。
```

### 13.3 gMock 只用于交互验证或一次性失败

适合用 gMock：

```text
1. 验证某个接口必须被调用。
2. 验证调用次数。
3. 验证调用顺序。
4. 模拟一次性错误。
5. fake 成本明显过高。
```

不推荐：

```text
MockA + MockB + MockC + MockD + MockE，然后写几十个 EXPECT_CALL。
```

这种测试脆弱，通常应该改为 Component Test + 少量 fake。

---

## 14. dependency.xml 依赖处理规则

能源控制器项目可能使用 `dependency.xml` 下载依赖代码，主工程大量使用依赖代码中的类型、宏、free function、OSAL、HAL、内存函数。

Codex 必须把依赖代码当成测试环境的一部分处理。

### 14.1 基本原则

```text
1. 先解析 dependency.xml。
2. 定位依赖本地路径。
3. 搜索符号真实声明和定义。
4. 真实类型、常量、结构体、宏必须 include 真实头文件。
5. host 可编译的依赖实现优先链接真实实现。
6. target-only 依赖在 host gtest 中使用 fake / shim。
7. 覆盖率统计应排除依赖代码、fake、shim、tests。
8. 不允许在测试里复制依赖 struct / enum / 宏。
```

### 14.2 dependency.xml 解析输出

Codex 必须输出：

```text
Dependency resolution:
- dependency.xml 路径
- 依赖包名称
- 依赖版本
- 下载路径
- include 路径
- source 路径
- library 路径
- 是否 host 可编译
- 是否 target-only
- 测试处理策略
```

建议维护：

```text
docs/testing/DEPENDENCY_TEST_MAP.md
```

示例：

```md
# Dependency Test Map

| Dependency | Local Path | Include Path | Host Build | Test Treatment |
|---|---|---|---|---|
| EMAP | external/emap | external/emap/include | partial | real headers + selected host shims |
| OSAL | external/osal | external/osal/include | no | fake for host gtest |
| HAL | external/hal | external/hal/include | no | HAL fake or HIL |
| CRC_LIB | external/crc | external/crc/include | yes | use real implementation |
```

---

## 15. 依赖符号处理规则：以 EMAP_MemFree 为例

如果项目代码调用：

```cpp
EMAP_MemFree(ptr);
```

Codex 不能自行声明或随便 mock。必须先查：

```bash
rg "EMAP_MemFree" .
rg "#define.*EMAP_MemFree" external/ deps/ third_party/
```

### 15.1 按符号形态处理

| 符号形态 | 处理策略 |
|---|---|
| 依赖头文件声明，依赖库有 host 可链接实现 | 使用真实实现 |
| 依赖头文件声明，但只有 target 平台实现 | 提供 host shim |
| `#define EMAP_MemFree free` | 按宏处理，不能 link wrap |
| `static inline void EMAP_MemFree(...)` | 不能链接替换，需要上层 seam |
| weak symbol | 测试 target 可提供 strong override |
| 普通 extern C 函数 | 可使用真实实现、host shim、link wrap |
| 硬件 / RTOS 相关函数 | host gtest 中 fake |

### 15.2 内存函数禁止空实现

禁止：

```cpp
void EMAP_MemFree(void*) {}
```

原因：

```text
1. 掩盖内存泄漏。
2. 掩盖 double free。
3. 掩盖 use-after-free。
4. 与生产行为不一致。
5. 让 ASan 诊断失效。
```

### 15.3 host shim 推荐写法

如果真实实现 target-only，但语义等价于 malloc/free：

```cpp
// tests/support/deps/emap_memory_host_shim.cc

#include "emap_memory.h"

#include <cstdlib>

extern "C" void* EMAP_MemAlloc(size_t size) {
    return std::malloc(size);
}

extern "C" void EMAP_MemFree(void* ptr) {
    std::free(ptr);
}
```

要求：

```text
1. 必须 include 真实依赖头文件。
2. 签名必须完全一致。
3. 只能链接到 test target。
4. 不进入生产 target。
5. 必须配合 ASan / UBSan。
```

### 15.4 需要验证 free function 调用时的可选方案

优先级：

```text
1. Adapter interface injection。
2. Host shim。
3. Linker --wrap。
4. Weak symbol override。
5. 最后才考虑编译期 seam。
```

长期推荐封装：

```cpp
class IMemoryApi {
public:
    virtual ~IMemoryApi() = default;
    virtual void* Alloc(size_t size) = 0;
    virtual void Free(void* ptr) = 0;
};

class EmapMemoryApi : public IMemoryApi {
public:
    void* Alloc(size_t size) override {
        return EMAP_MemAlloc(size);
    }

    void Free(void* ptr) override {
        EMAP_MemFree(ptr);
    }
};
```

---

## 16. 不同依赖类型的处理策略

| 依赖类型 | 处理策略 |
|---|---|
| 类型、常量、enum、struct、协议定义 | include 真实依赖头文件，不复制定义 |
| CRC、checksum、数学转换、bit 操作 | 优先使用真实实现 |
| 内存管理函数 | 真实实现或等价 host shim，禁止空实现 |
| OSAL / RTOS / queue / timer | host gtest 用 fake / shim |
| HAL / 硬件寄存器 | host gtest 不直接访问，使用 HAL fake 或 HIL |
| 外部模块通信 | fake bus / fake peer，写 Message Interface Test |
| target-only 库 | host shim / fake / 测试缝 |
| macro / static inline | 不能链接替换，必要时上层 seam |

---

## 17. Message Interface Test 规则

适用：

```text
1. CAN / LIN / UART / SPI / Modbus 报文。
2. 内部 event / command / response。
3. RTOS queue message。
4. 模块间消息。
5. ICD-defined message。
```

### 17.1 测试发送消息

```cpp
TEST_F(FooMessageInterfaceTest, SendsExpectedStartCommand) {
    ASSERT_TRUE(harness->Init().ok());

    harness->service().RequestStart();

    const auto& frames = harness->bus().SentFrames();
    ASSERT_EQ(frames.size(), 1u);

    EXPECT_EQ(frames[0].id, kStartCommandId);
    EXPECT_EQ(frames[0].dlc, 8u);
    EXPECT_EQ(frames[0].data[0], 0x01);
    EXPECT_EQ(frames[0].data[7], CalcCrc(frames[0].data));
}
```

### 17.2 测试接收消息

```cpp
TEST_F(FooMessageInterfaceTest, ParsesAckMessageAndEntersReadyState) {
    ASSERT_TRUE(harness->Init().ok());

    BusFrame ack{};
    ack.id = kAckMessageId;
    ack.dlc = 8;
    ack.data = MakeValidAckPayload();

    harness->service().OnFrameReceived(ack);

    EXPECT_EQ(harness->service().Status(), FooStatus::Ready);
}
```

### 17.3 优先断言可观察副作用

优先级：

```text
1. 发送了什么消息。
2. 输出了什么 event / telemetry。
3. 写入 fake storage 的内容。
4. callback 是否被调用。
5. 公开 status 是否变化。
6. 最后才考虑 ForTest 只读接口。
```

禁止：

```cpp
#define private public
#include "foo.h"
```

---

## 18. Message Conformance Test 规则

适用：

```text
1. Encode / Decode。
2. Pack / Unpack。
3. endian。
4. bitfield。
5. CRC / checksum。
6. scale / offset。
7. enum mapping。
8. 协议版本兼容。
```

示例：

```cpp
TEST(FooMessageConformanceTest, EncodesSpeedSignalAccordingToIcd) {
    FooStatus status{};
    status.speed_kph = 123.4;
    status.enabled = true;

    BusFrame frame = EncodeFooStatus(status);

    EXPECT_EQ(frame.id, kFooStatusId);
    EXPECT_EQ(frame.dlc, 8u);
    EXPECT_EQ(frame.data[0], 0xD2);
    EXPECT_EQ(frame.data[1], 0x04);
    EXPECT_TRUE(frame.data[2] & 0x01);
}
```

优先表驱动：

```cpp
struct EncodeCase {
    float input;
    uint8_t byte0;
    uint8_t byte1;
};

class FooSpeedEncodeTest : public ::testing::TestWithParam<EncodeCase> {};

TEST_P(FooSpeedEncodeTest, EncodesSpeed) {
    const auto& c = GetParam();

    auto frame = EncodeSpeed(c.input);

    EXPECT_EQ(frame.data[0], c.byte0);
    EXPECT_EQ(frame.data[1], c.byte1);
}
```

---

## 19. 能源控制器项目推荐测试场景

| 场景 | 推荐测试 |
|---|---|
| 电压 / 电流 / 温度采样值校验 | Unit Test / Boundary Test |
| SOC / SOH / 功率限制计算 | Unit Test / Table-Driven Test |
| 控制模式切换 | State Machine Test |
| 过压、欠压、过流、过温判定 | Boundary Test / Fault Injection Test |
| CAN / Modbus / 私有协议报文编码 | Message Conformance Test |
| 接收到外部模块报文后的状态更新 | Message Interface Test |
| 本模块发出的控制命令是否正确 | Message Interface Test |
| NVM 参数保存 / 恢复 | Power-cycle / Recovery Test |
| 下游驱动失败、队列满、超时 | Fault Injection Test |
| 历史段错误输入 | Regression Test |
| 结构体用于通信 / NVM / 共享内存 | Struct Layout / ABI Test |
| 类依赖全局变量和大量其他类 | Component Test + TestHarness |
| 真实 ADC / CAN / 继电器 / 接触器 | HIL，不只靠 gtest |

---

## 20. 覆盖率提升策略

覆盖率不是目标本身，有效测试才是目标。

### 20.1 不要一开始追全项目百分比

优先：

```text
1. 新增 / 修改代码覆盖率。
2. 核心安全逻辑覆盖率。
3. 协议编解码覆盖率。
4. 错误路径覆盖率。
5. 历史 bug 回归覆盖率。
```

### 20.2 优先补这些测试

```text
1. 纯逻辑函数。
2. 边界值。
3. 错误码分支。
4. 消息编码 / 解码。
5. 状态机状态迁移。
6. 下游失败 / 超时 / 队列满。
7. Init / Shutdown / 重复调用。
8. 历史 crash 输入。
9. NVM 掉电恢复。
10. 结构体布局 / ABI。
```

### 20.3 覆盖率报告排除项

排除：

```text
external/
deps/
third_party/
generated/
tests/
fake/
mock/
shim/
```

示例：

```bash
gcovr -r . \
  --exclude 'external/.*' \
  --exclude 'deps/.*' \
  --exclude 'third_party/.*' \
  --exclude 'generated/.*' \
  --exclude 'tests/.*' \
  --html --html-details \
  -o coverage.html
```

核心原则：

```text
测试可以使用依赖代码，
覆盖率门禁不应统计依赖代码。
```

### 20.4 覆盖率门禁建议

初期：

```text
全项目覆盖率：只记录，不强压。
新增 / 修改代码覆盖率：设门槛。
核心模块覆盖率：单独设目标。
```

示例：

```text
新增 / 修改代码 line coverage >= 70%
核心协议编解码模块 >= 80%
核心保护逻辑分支覆盖率逐步提升
全项目覆盖率按季度提升
```

---

## 21. Sanitizer 和段错误处理规则

复杂 C++ gtest 必须配合 sanitizer。

推荐至少有：

```text
ASan：发现越界、use-after-free、double free。
UBSan：发现未定义行为。
TSan：发现 data race。
LSan：发现内存泄漏。
```

编译示例：

```bash
CXXFLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer -g -O1"
LDFLAGS="-fsanitize=address,undefined"
```

运行建议：

```bash
ASAN_OPTIONS=halt_on_error=1:detect_leaks=1 ./foo_test
```

检查测试互相污染：

```bash
./foo_test --gtest_shuffle --gtest_repeat=100
```

段错误时：

```bash
gdb --args ./foo_test --gtest_filter=FooTest.Case
run
bt
```

段错误优先排查：

```text
1. 是否未 Init。
2. Init 是否失败但继续执行。
3. 依赖是否提前析构。
4. mock 是否返回局部变量地址。
5. 全局变量是否被污染。
6. 单例是否缓存了已释放对象。
7. 后台线程是否没停止。
8. 是否绕过真实构造约束。
9. malloc/free ownership 是否错误。
```

---

## 22. 禁止模式清单

Codex 不允许生成以下模式。

### 22.1 不允许绕过 Init

```cpp
FooService service;
service.Handle(req);
```

### 22.2 不允许空实现内存释放

```cpp
void EMAP_MemFree(void*) {}
```

### 22.3 不允许 mock 返回局部变量地址

```cpp
EXPECT_CALL(repo, GetUser()).WillOnce([] {
    User user;
    return &user;
});
```

### 22.4 不允许用 death test 包普通段错误

```cpp
EXPECT_DEATH(sut->Handle(req), "");
```

普通段错误是 bug，不是预期行为。

### 22.5 不允许直接改 private

```cpp
#define private public
#include "foo.h"
```

### 22.6 不允许真实 sleep 等待异步

```cpp
std::this_thread::sleep_for(std::chrono::seconds(1));
```

### 22.7 不允许复制依赖 struct / enum

```cpp
// 测试里自己重新写一份依赖里的结构体定义
struct EMAP_Message { ... };
```

### 22.8 不允许硬套其他项目风格

能源控制器项目中禁止生成：

```cpp
HWTEST_F(...)
```

除非能源项目 profile 明确允许。

---

## 23. 弱 AI / opencode 使用规则

弱模型更容易模仿最近看到的样例，因此必须强约束。

### 23.1 固定流程

```text
1. 扫描项目风格。
2. 读取 project_profile.yaml。
3. 解析 dependency.xml。
4. 输出判定报告。
5. 生成最小测试。
6. 编译。
7. 修复编译错误。
8. 运行。
9. 分析 coverage。
10. 输出 review 清单。
```

### 23.2 不允许直接下任务

不推荐：

```text
帮我给这个文件补测试。
```

推荐：

```text
先分析该文件依赖和测试类型，输出判定报告。
根据当前项目 profile 生成最小可运行 gtest。
不要使用非本项目测试宏。
不要编造依赖定义。
```

### 23.3 Few-shot 示例优先级

示例目录建议：

```text
examples/
  generic/
    component_test_with_fake_bus.md
    message_conformance_test.md
    dependency_host_shim.md

  energy_controller/
    emap_dependency_example.md
    can_message_interface_example.md
    scoped_global_config_guard_example.md

  fprime/
    component_harness_example.md

  openharmony/
    hwtest_style_example.md
    gn_build_example.md
```

弱模型在能源项目中必须优先使用 `examples/energy_controller/`，不得模仿 OpenHarmony 示例。

---

## 24. 验证路线：先 F Prime，再 PX4，再能源 mini-repo，再 OpenHarmony

### 24.1 第一个验证项目：NASA F Prime

推荐作为第一个项目。

原因：

```text
1. WSL / Linux 下较容易跑起来。
2. 嵌入式 C++ / flight software framework。
3. 组件化架构明显。
4. 自带 GoogleTest / TestHarness / 组件接口测试。
5. 适合验证通用核心：测试类型判定、harness 使用、接口断言、coverage、fault injection。
```

验证目标：

```text
1. 项目风格发现。
2. 识别 F Prime 的 Tester / GTestBase。
3. 不生成裸 gtest，而是复用项目 harness。
4. 通过 command / port / event / telemetry 断言。
5. 跑 coverage 后补 off-nominal case。
6. 输出哪些规则是通用核心，哪些是 F Prime profile。
```

### 24.2 第二个验证项目：PX4

适合作为进阶项目。

验证目标：

```text
1. 区分 Unit / Functional / SITL。
2. 识别 uORB / parameter / MAVLink 依赖。
3. 不强行把功能测试写成纯 UT。
4. 练习消息接口和协议测试。
```

### 24.3 第三个验证项目：仿能源控制器 mini-repo

必须做。

原因：

```text
OpenHarmony / F Prime / PX4 都不是你的真实项目风格。
真正防止过拟合的方法是做一个脱敏的能源控制器风格练习项目。
```

mini-repo 必须包含：

```text
1. dependency.xml。
2. EMAP_MemFree / EMAP_MemAlloc。
3. OSAL queue / timer。
4. HAL fake。
5. CAN / Modbus 消息接口。
6. 全局配置。
7. NVM fake。
8. 一个复杂类 Component Test。
9. 一个历史 crash Regression Test。
10. coverage 排除外部依赖。
```

### 24.4 第四个验证项目：OpenHarmony

OpenHarmony 适合作为复杂生态验证样本，但不建议作为第一个项目。

使用目的：

```text
1. 验证大型多仓项目扫描能力。
2. 验证复杂依赖和测试框架适配。
3. 验证 profile 隔离，避免 OpenHarmony 风格污染通用核心。
```

---

## 25. OpenHarmony 风格过拟合风险与规避

用 OpenHarmony 练工具不会必然导致工具只会 OpenHarmony 风格。

真正风险在于：

```text
1. prompt 写死 OpenHarmony 宏。
2. 示例全是 OpenHarmony。
3. RAG 文档全是 OpenHarmony。
4. 评测标准只看 OpenHarmony 是否通过。
5. 构建修复策略默认 BUILD.gn。
6. 工具默认假设 subsystem / bundle / HWTEST。
```

规避方法：

```text
1. OpenHarmony 只作为 openharmony profile。
2. 通用核心不包含 OpenHarmony 专属默认。
3. 能源项目使用 energy_controller profile。
4. 使用脱敏能源 mini-repo 做验收。
5. 能源项目 profile 禁止 HWTEST / HWTEST_F。
6. 每次生成前做项目风格发现。
7. 弱模型 few-shot 优先使用能源项目样例。
```

---

## 26. CI 分层建议

### 26.1 PR 必跑

```text
1. Unit Test。
2. Component Test。
3. Message Conformance Test。
4. Message Interface Test 中的 host fake 版本。
5. Regression Test。
6. ASan / UBSan 下的核心测试。
```

### 26.2 合入前或每日跑

```text
1. 更完整 Component Test。
2. Fault Injection Test。
3. Resource Limit Test。
4. Power-cycle / Recovery Test。
5. TSan 测试。
6. coverage 报告。
```

### 26.3 Nightly / 板端 / 仿真跑

```text
1. SIL。
2. HIL。
3. target gtest。
4. 长稳测试。
5. 性能 / 实时性测试。
6. fuzz。
```

---

## 27. 测试代码 Review 清单

生成的测试必须通过以下 review。

```text
1. 是否符合当前项目 profile？
2. 是否使用当前项目已有测试宏和目录？
3. 是否先输出了测试类型判定报告？
4. 复杂类是否有 TestHarness / fixture？
5. 是否走了真实 Init / Shutdown？
6. 被测对象是否比 fake 依赖先析构？
7. 全局变量是否用 RAII guard 恢复？
8. dependency.xml 依赖是否正确 include 真实头文件？
9. target-only 依赖是否用 fake / shim？
10. 内存函数是否没有空实现？
11. 是否避免直接改 private？
12. 是否避免 death test 包普通 crash？
13. 是否避免 sleep？
14. 断言是否有效，不只是调用函数？
15. 覆盖率提升是否来自本项目代码？
16. 测试是否可单独运行、乱序运行、重复运行？
17. ASan / UBSan 是否通过？
18. 是否引入非本项目风格，例如能源项目里出现 HWTEST_F？
```

---

## 28. 工具泛化验收标准

AI 工具不能只看 OpenHarmony 或 F Prime 通过率。

应同时评估：

```text
1. 编译通过率。
2. 运行通过率。
3. 项目风格符合率。
4. 依赖处理正确率。
5. 测试类型判定准确率。
6. 有效断言比例。
7. 覆盖率增量。
8. sanitizer 通过率。
9. 无段错误比例。
10. 人工 review 通过率。
```

特别检查：

```text
1. 是否能在 F Prime 中使用 F Prime harness。
2. 是否能在 PX4 中区分 Unit / Functional / SITL。
3. 是否能在能源 mini-repo 中处理 dependency.xml / EMAP_MemFree。
4. 是否能在 OpenHarmony 中使用 OpenHarmony profile。
5. 是否不会把 OpenHarmony 风格带到能源控制器项目。
```

---

## 29. Codex 最终 Prompt 模板

下面这段可直接作为 Codex / opencode 任务前缀。

```text
You are working on a large embedded C++ codebase that uses GoogleTest.

Do not blindly generate unit tests for every class or function.
First classify the target code and choose the safest developer-test type.

Architecture rule:
- Use the Generic Core rules for test type classification and safety.
- Use the current project's project_profile.yaml for style, macros, paths,
  build system, dependency handling, fakes, shims, and coverage exclusions.
- Never apply OpenHarmony, F Prime, PX4, or any other project's style unless
  the active project profile explicitly says so.

Before editing code, output:
1. Project style discovery
2. Target
3. dependency.xml / manifest resolution
4. Observed dependencies
5. Observed dependency symbols
6. Selected test type
7. Reason
8. Required fakes / mocks / shims / guards / harness
9. Lifecycle and ownership risks
10. Planned files to add or modify

Test type decision rules:
1. Use Unit Test only when the target is deterministic pure logic:
   no global state, no hardware, no RTOS, no bus, no file/network/storage,
   no real time, no complex Init, and no large collaborator graph.

2. Use Component Test when the target is a class/module that depends on
   other classes, global variables, singletons, Init/Shutdown lifecycle, or
   external dependencies that can be faked. Build a TestHarness. Use real
   internal logic where safe and fake only external boundaries.

3. Use Message Interface Test when the target sends or receives inter-module
   messages, bus frames, events, commands, responses, RTOS queue messages,
   CAN/LIN/UART/SPI/Modbus payloads, or ICD-defined messages. Verify message ID,
   length, payload bytes, endian, bitfields, checksum/CRC, sequence, and
   state transitions.

4. Use Message Conformance Test when the target is pack/unpack, encode/decode,
   signal scaling, enum mapping, byte order, CRC, or protocol compatibility.

5. Use Lifecycle Test when the target has Init, Start, Stop, Shutdown, DeInit,
   Reset, Suspend, or Resume.

6. Use Fault Injection Test for downstream failures, timeouts, queue full,
   storage failure, CRC error, invalid frame length, repeated/ordered messages,
   or unavailable dependencies.

7. Use Regression Test for known crash or bug inputs. The test must assert
   safe error handling and must not use death test to hide segmentation faults.

8. Use Characterization Test for legacy code whose full expected behavior is
   unclear. Capture current behavior using historical inputs or golden data
   before refactoring.

9. If the code directly touches hardware registers or real board IO, do not
   generate a host gtest unless there is a HAL seam that can be faked. If no
   seam exists, report that a HAL/test seam or HIL test is required.

Dependency handling rules:
- This project may use dependency.xml to download dependency source/header code.
- Before generating any gtest, inspect dependency.xml and resolve local
  dependency paths.
- When the target code calls dependency APIs such as EMAP_MemFree, do not invent
  declarations and do not blindly mock them. First locate the real declaration
  and determine whether the symbol is a macro, static inline function, extern
  function, weak symbol, template, or target-only library function.
- Use real dependency headers whenever possible to preserve real types,
  constants, struct layout, enums, macros, and ABI.
- Pure algorithm or protocol constants: use real dependency implementation.
- Memory APIs such as EMAP_MemAlloc / EMAP_MemFree: use real implementation if
  host-linkable; otherwise provide a host shim with exact signature and real
  malloc/free semantics. Never use an empty fake for memory free.
- OSAL / RTOS / queue / timer APIs: use fakes in host gtest.
- HAL / hardware register APIs: use HAL fake if a seam exists; otherwise report
  HIL-required or request a test seam.
- Message bus / peer module APIs: use fake bus / fake peer and write Message
  Interface Tests.
- Do not duplicate dependency structs/enums/macros in tests.
- Do not include both real and fake implementations of the same symbol in one
  test binary.

Mandatory safety rules:
- If production code requires Init(), the test must call Init() and use ASSERT.
- If production code requires Shutdown()/DeInit()/Stop(), call it in TearDown.
- The system under test must be destroyed before fake dependencies.
- Global variables and singletons must be restored using RAII guards.
- Do not rely on test execution order.
- Do not use sleep for timers or async code; use FakeClock/FakeExecutor.
- Do not use #define private public.
- Do not mock every internal collaborator. Prefer fake external boundaries.
- Do not use EXPECT_DEATH for ordinary crashes.
- Do not create empty implementations for memory free functions.

Generation order:
1. Smoke / Lifecycle test if the module has Init/Shutdown.
2. One happy-path Component Test or Message Interface Test.
3. One error/fault-injection test.
4. One regression test if a crash case exists.
5. Coverage-driven additional tests only after the first tests compile and run.

Prefer stable, compilable, maintainable tests over raw coverage increase.
```

---

## 30. 实施路线

### 阶段 1：建立工具基础

```text
1. 定义 project_profile.yaml schema。
2. 实现项目风格发现。
3. 实现 dependency.xml 解析。
4. 实现测试类型分类器。
5. 实现判定报告输出。
6. 实现基础 gtest 生成。
```

### 阶段 2：F Prime 验证通用核心

```text
1. WSL 安装 F Prime。
2. 让工具扫描项目风格。
3. 选一个 component。
4. 生成 Component Test。
5. 跑 fprime-util check。
6. 跑 coverage。
7. 输出通用规则 vs F Prime profile。
```

### 阶段 3：PX4 验证复杂嵌入式分层

```text
1. 选择依赖少的 unit test 模块。
2. 选择依赖 uORB / parameter 的 functional test 模块。
3. 验证 Unit / Functional / SITL 判定。
```

### 阶段 4：仿能源控制器 mini-repo 验证目标风格

```text
1. 构造 dependency.xml。
2. 加 EMAP_MemFree / EMAP_MemAlloc。
3. 加 OSAL / HAL / CAN / NVM fake。
4. 加全局变量和复杂类。
5. 让工具生成测试。
6. 确认没有 OpenHarmony/F Prime 风格泄漏。
```

### 阶段 5：真实能源控制器低风险模块试点

优先选择：

```text
1. 协议编解码模块。
2. 功率限制计算模块。
3. 配置解析模块。
4. 保护阈值判断模块。
5. 消息路由模块。
```

暂不优先选择：

```text
1. 主控制器大类。
2. 真实硬件驱动。
3. 板级初始化。
4. 实时任务调度核心。
5. 安全保护闭环核心。
```

### 阶段 6：正式接入 CI 和覆盖率门禁

```text
1. PR 必跑 host gtest。
2. 核心测试跑 ASan/UBSan。
3. coverage 排除外部依赖。
4. 新增 / 修改代码设覆盖率门槛。
5. nightly 跑更多 fault injection / TSan / SIL。
6. HIL 独立管理。
```

---

## 31. 最终原则

Codex 必须遵守以下最终原则：

```text
1. 不要盲目补 UT。
2. 先识别项目风格。
3. 先解析依赖。
4. 先判断测试类型。
5. 复杂类先建 TestHarness。
6. 外部边界 fake，内部纯逻辑尽量真实。
7. 全局变量必须 guard。
8. dependency.xml 依赖必须真实 include。
9. EMAP_MemFree 这类内存函数不能空实现。
10. 硬件强相关不要硬写 host gtest。
11. 覆盖率提升必须服务于有效断言。
12. OpenHarmony / F Prime / PX4 只能作为 profile，不能污染通用核心。
13. 能源控制器项目必须用自己的 profile 和脱敏样例验收。
```

一句话总结：

```text
本工具不是“UT 自动生成器”，而是“嵌入式 C++ 开发者测试生成与覆盖率提升工具”。
它必须基于通用测试判定核心，并通过项目适配层遵守每个项目自己的测试框架、依赖体系和工程风格。
```
