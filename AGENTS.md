# AGENTS.md — 嵌入式 V 模型自动化测试 Agent

> 本文档面向 AI Coding Agent。阅读前请确认你已通读全文，不要对项目做任何假设。

---

## 1. 项目概述

本项目是一个**嵌入式硬件在环（HIL）自动化测试 Agent**，基于 **V 模型生命周期 + OpenSpec + Superpowers** 三大框架构建，运行在 Windows 本地 PC 上，通过串口控制物理硬件完成全自动化测试。

### 核心目标
接收一条自然语言测试需求（如"验证 STM32F4 USART2 回环功能"），Agent 自动完成：
1. 需求结构化（JSON 需求矩阵）
2. 架构设计与风险评估
3. 生成 Robot Framework 测试用例
4. 生成 DUT 固件代码（STM32 HAL）
5. 编译检查、硬件在环执行、失败分析
6. 输出验收报告

### 系统角色
| 角色 | 硬件 | 固件 | 职责 |
|------|------|------|------|
| **PC Agent** | Windows 笔记本 | Python 3.12 | 主控、AI 调度、TAP 协议客户端 |
| **TAF** (Test Agent Firmware) | PICO 2 (RP2350) | Pico SDK C11 | 确定性测试代理：执行 GPIO/UART 测试动作 |
| **DUT** (Device Under Test) | STM32F4Discovery (STM32F407VG) | STM32 HAL C11 | 被测件：回环、GPIO 响应 |

---

## 2. 技术栈与依赖

### 2.1 PC 端（Python）
- **Python 3.12**（解释器路径已硬编码在部分命令中）
- **Robot Framework** — 测试执行框架
- **pyserial** — TAP 协议串口通信
- **requests** — 调用本地 Ollama API
- **Ollama** (localhost:11434) — 本地大模型推理引擎
- **Kimi CLI** — 云端大模型（跨阶段审查、长代码生成）

### 2.2 本地 Ollama 模型（必须提前拉取）
| 引擎角色 | 模型名 | 用途 |
|----------|--------|------|
| general | `qwen3:14b` | Brainstorm、架构设计、需求分析、报告生成 |
| fast | `qwen3:8b` | Robot 用例生成、日志摘要 |
| coder | `qwen2.5-coder:14b` | STM32/PICO C 代码、寄存器/协议/CRC8 实现 |

### 2.3 嵌入式端
- **TAF**: Pico SDK 2.0+，CMake + MSYS2 MINGW64 编译
- **DUT**: STM32CubeIDE / Makefile + `arm-none-eabi-gcc`

### 2.4 通信协议
- **TAP v1.0** (Test Agent Protocol)：PC <-> PICO2 通过 USB CDC 串口
  - 帧头：`0xAA 0x55`
  - 小端序长度字段
  - CRC8-MAXIM（多项式 `0x31`）
  - 命令：CONFIG(0x01)、EXECUTE(0x02)、QUERY(0x03)、RESET(0x10)
- **TAF <-> DUT**: UART 直连（PICO2 UART1 GP4/GP5 <-> STM32 USART2 PA2/PA3）

---

## 3. 目录结构

```
C:\wbs\embedded-agent\
├── agent_core\               # Python 核心包
│   ├── __init__.py
│   ├── hybrid_ai.py          # 五引擎 AI 调度器（Ollama x3 + Kimi x2）
│   ├── changeset.py          # OpenSpec 变更集管理（proposal/tasks/design）
│   ├── tap_protocol.py       # TAP 协议 PC 端客户端（串口二进制帧）
│   ├── tap_library.py        # Robot Framework 关键字库（TAPLibrary）
│   └── v_model_engine.py     # V 模型 9 阶段引擎 + Superpowers 流程
├── taf\pico\                 # TAF 固件（PICO2 RP2350）
│   ├── main.c                # TAP 协议状态机 + GPIO/UART 测试执行
│   └── CMakeLists.txt        # Pico SDK 构建配置
├── dut\stm32f4\              # DUT 固件参考（STM32F407VG）
│   └── main.c                # STM32 HAL 回环 + GPIO 响应（需嵌入 CubeIDE 工程）
├── changes\                   # OpenSpec "真相档案馆"（运行时生成，每次变更一个子目录）
├── workspace\                 # 运行时产出（运行时生成）
├── tests\                     # 预留测试目录（当前为空）
├── run_agent.py              # Agent 主入口
├── verify_env.py             # 环境预检脚本（串口、模型、编译器、目录）
└── embedded_agent_project_doc.md  # 完整项目文档（含接线表、修复记录）
```

---

## 4. 硬件接线（必须确认后再运行 Agent）

以下接线信息与 `embedded_agent_project_doc.md` 保持一致。`dut/stm32f4/main.c` 与 `v_model_engine.py` 的 `CONSTRAINTS` 已同步更新。

| PICO 2 (TAF) | STM32F4Discovery (DUT) | 功能 |
|--------------|------------------------|------|
| GP4 (UART1_TX) | PA3 (USART2_RX) | TAF 发送 -> DUT 接收 |
| GP5 (UART1_RX) | PA2 (USART2_TX) | DUT 发送 -> TAF 接收 |
| GP2 (GPIO_OUT) | PD5 (GPIO_Input, Pull-down) | TAF 触发信号 |
| GP3 (GPIO_IN) | PD12 (GPIO_Output, Green LED) | DUT 反馈信号 |
| GP6 (GPIO_OUT) | NRST | DUT 硬复位控制 |
| GND | GND | 共地 |
| USB -> PC | — | TAP 协议 + 5V 供电 |

> **Agent 提示**：`run_agent.py`、`tap_protocol.py`、`tap_library.py` 默认串口号均为 `COM10`。每次硬件变化后必须统一修改所有串口号。

---

## 5. 环境准备与验证

### 5.1 必须预先安装并配置的工具
1. **Ollama** 运行中，且已拉取 `qwen3:14b`、`qwen3:8b`、`qwen2.5-coder:14b`
2. **Kimi CLI** 可用（`kimi --version` 通过）
3. **Python 包**：`robotframework`、`pyserial`
4. **arm-none-eabi-gcc**（Arm GNU Toolchain）
5. **MSYS2 MINGW64** + **Pico SDK**（`PICO_SDK_PATH` 环境变量已设置）
6. **STM32CubeIDE**（用于 DUT 工程创建与烧录）

### 5.2 环境验证命令
```bash
# 在项目根目录执行
python verify_env.py
```
该脚本会依次检查：Ollama 三模型、Kimi CLI、Python 包、ARM GCC、可用串口、目录结构。

### 5.3 TAF 固件编译（MSYS2 MINGW64）
```bash
cd /c/embedded-agent/taf/pico
rm -rf build
mkdir build && cd build
cmake .. -G "MinGW Makefiles"
mingw32-make -j$(nproc)
```
产出 `pico_taf.uf2`，按住 PICO2 BOOTSEL 拖放烧录。

### 5.4 DUT 固件编译
- 在 STM32CubeIDE 中创建 STM32F407VG 工程，配置 USART2（PA2/PA3）、PD5（Input, Pull-down）、PD12（Output，绿灯）。
- 将 `dut/stm32f4/main.c` 的内容复制到 CubeIDE 用户代码区，编译烧录。

---

## 6. 运行 Agent

### 6.1 入口命令
```bash
python run_agent.py [需求描述]
```
不带参数时执行默认需求：验证 STM32F4 USART2 回环 + GPIO PD5/PD12 响应。

### 6.2 V 模型 9 阶段流程
Agent 内部按以下顺序执行（代码见 `v_model_engine.py`）：

| 阶段 | 名称 | AI 引擎 | 产出物 |
|------|------|---------|--------|
| B | Brainstorm | qwen3:14b /think | `brainstorm.md` |
| 1 | 需求分析 | qwen3:14b | `requirements_matrix.json` |
| 2 | 系统设计 | qwen3:14b | `test_strategy.md` |
| 3 | 架构设计 | qwen3:14b /think | `design.md` |
| 4 | 模块设计 (TDD) | qwen3:8b | `test_suite.robot` |
| 5 | DUT 编码 | qwen2.5-coder:14b | `dut_code/main.c` |
| 6 | 单元测试 | — | `unit_test_result.json` |
| 7 | 集成测试 (HIL) | — | `integration_result.json` + `robot_results/` |
| 8 | 系统测试 | — | 占位 |
| 9 | 验收报告 | qwen3:14b | `acceptance_report.md` |

所有产出物写入 `workspace/changes/<时间戳>_<需求摘要>/`。

### 6.3 失败自动分析
若 Robot Framework 执行返回非零，`v_model_engine.py` 会自动解析 `output.xml`，提取失败用例名，并调用 `qwen3:14b` 生成 `failure_analysis.md`（中文排查步骤）。

---

## 7. 代码风格与开发约定

### 7.1 语言与注释
- **所有代码注释使用中文**（包括 Python 和 C）。
- Python 代码中的字符串输出也使用中文。

### 7.2 不可违反的硬性约束
以下约束来自 `v_model_engine.py` 的 `CONSTRAINTS` 列表，代码生成时必须遵守：
1. TAF 使用 PICO2 (RP2350, Pico SDK)，DUT 使用 STM32F4Discovery (STM32F407VG, HAL)。
2. TAP 协议 v1.0：帧头 `0xAA 0x55` + CRC8-MAXIM(`0x31`)。**禁止修改帧格式**。
3. TAF-DUT 通信：PICO2 UART1(GP4/GP5) <-> STM32F4 USART2(PA2/PA3)。
4. GPIO 测试：PICO2 GP2 <-> STM32F4 PD5，PICO2 GP3 <-> STM32F4 PD12。
5. DUT 复位：PICO2 GP6 -> STM32F4 NRST。
6. **禁止混用 Pico SDK 与 STM32 HAL 语法**。
7. 不得使用非标准库或第三方闭源组件。

### 7.3 C 代码规范
- PICO 端：C11，Pico SDK 标准 API（`pico/stdlib.h`、`hardware/uart.h`、`hardware/gpio.h`）。
- STM32 端：HAL 库，`stm32f4xx_hal.h`，MX 初始化代码由 CubeMX 生成，用户逻辑写在 `USER CODE BEGIN/END` 区域。
- CRC8 实现必须精确匹配 `calc_crc8` 算法（`0x00` 初值，`0x31` 多项式）。

### 7.4 Python 规范
- 使用标准库 + `requests`、`serial`、`robot.api`。
- 串口默认波特率 115200，超时 3 秒。
- `TAPClient` 在初始化后 `sleep(2.0)` 并清空输入缓冲区，以等待 USB CDC 就绪。

---

## 8. 测试策略

### 8.1 测试层次
| 层次 | 方式 | 工具/位置 |
|------|------|-----------|
| 单元测试 | 静态检查 + 编译 | `arm-none-eabi-gcc`、代码存在性/关键字检查 |
| 集成测试 | 硬件在环 (HIL) | Robot Framework + TAPLibrary + 物理串口 |
| 系统测试 | 全链路 | 占位（当前无额外逻辑） |

### 8.2 Robot Framework 用例规范
- 必须使用 `Library TAPLibrary`。
- 使用 Variables 区定义波特率、超时、引脚，**禁止硬编码**。
- 每个 Test Case 必须含 `[Documentation]`。
- 标准用例覆盖：UART 回环、GPIO 响应、集成链路。

### 8.3 通过标准
- 所有 Robot Framework 测试用例 **PASS**。
- TAF 与 DUT 通信时序误差 **< 1ms**。
- DUT 代码通过 `arm-none-eabi-gcc` 编译**零警告**。

---

## 9. 关键配置项（Agent 必须检查）

### 9.1 串口号
以下 3 处必须保持一致，与实际设备管理器中的 PICO2 USB CDC 端口号一致：
1. `run_agent.py`: `VModelEngine(tap_port="COM10")`
2. `agent_core/tap_protocol.py`: `TAPClient(port="COM10")`
3. `agent_core/tap_library.py`: `TAPLibrary(port="COM10")`

### 9.2 Ollama 地址
- `agent_core/hybrid_ai.py` 默认 `http://localhost:11434`
- `verify_env.py` 也使用同一地址

### 9.3 工作目录
- `v_model_engine.py` 默认根据文件位置自动推算 workspace（项目根目录下的 `workspace` 文件夹）
- 若项目被移动，此路径需要同步修改

---

## 10. 安全与风险注意事项

1. **物理硬件安全**：Agent 会控制 DUT 的 NRST 引脚进行硬复位，确保 GP6 -> NRST 接线正确，避免短路。
2. **串口独占**：`TAPClient` 打开串口后会独占该 COM 端口，运行 Agent 期间不要打开其他串口工具（如 putty、串口助手）。
3. **AI 生成代码风险**：所有 AI 生成的 C 代码必须在烧录前人工确认，尤其是 GPIO 方向、上下拉配置、时钟初始化。
4. **PICO2 CRLF 问题**：`CMakeLists.txt` 中已禁用 `PICO_STDIO_ENABLE_CRLF_SUPPORT`，确保 TAP 二进制帧不会被换行符破坏。切勿删除该编译定义。
5. **环境隔离**：项目没有虚拟环境配置（无 `requirements.txt` / `pyproject.toml`），依赖直接安装在系统 Python 中。修改时需小心不要破坏全局环境。

---

## 11. 已知问题与陷阱（Agent 必读）

| 问题 | 影响 | 应对 |
|------|------|------|
| PICO2 烧录后 USB CDC 枚举需 1~2 秒 | `TAPClient` 已 `sleep(2.0)`，若仍失败可延长 |
| PICO2 烧录后 USB CDC 枚举需 1~2 秒 | `TAPClient` 已 `sleep(2.0)`，若仍失败可延长 |
| PowerShell 不支持 `&&` | 使用分号 `;` 或换行执行 |
| MSYS2 找不到 `arm-none-eabi-gcc` | 需创建 `/opt/arm-toolchain` 符号链接并 export PATH |
| `nmake` 不可用 | 项目实际使用 `mingw32-make`，不要尝试 `nmake` |

---

## 12. 修改后必须更新的文件

若你修改了以下内容，请同步更新本 `AGENTS.md`：
- 目录结构变化
- 新增/删除 Python 包依赖
- 构建命令变化（如从 MSYS2 切换到 VS Build Tools）
- TAP 协议帧格式变化（原则上禁止）
- 硬件接线或引脚分配变化
- 新增 AI 引擎或模型名称变化

---

*文档生成时间：2026-06-05*
*基于仓库实际文件内容编写，未引入外部假设。*
