# 嵌入式 V 模型 Agent 项目完整文档

## 一、项目概述

**项目名称**：嵌入式自动化测试 Agent（V 模型 + OpenSpec + Superpowers）
**目标**：实现 STM32F4Discovery + PICO2 的硬件在环自动化测试，覆盖 V 模型全生命周期。
**核心架构**：五引擎 + 双框架

### 五引擎
| 引擎 | 模型 | 用途 |
|------|------|------|
| 通用推理 | Qwen3:14b (本地 Ollama) | Brainstorming、架构设计、需求分析 |
| 快速响应 | Qwen3:8b (本地 Ollama) | Robot 用例生成、日志摘要 |
| 底层代码 | Qwen2.5-Coder:14b (本地 Ollama) | C 代码生成、寄存器操作、TAP 协议 |
| 复杂审查 | Kimi CLI (云端) | 跨阶段一致性检查、长代码重构 |
| 代码专精 | Kimi Coder (云端) | 遗留代码分析、多文件重构 |

### 双框架
| 框架 | 职责 | 产出物 |
|------|------|--------|
| **OpenSpec** (What) | 需求契约 | proposal.md、tasks.json、design.md、真相档案馆 changes/ |
| **Superpowers** (How) | 执行流程 | Brainstorm -> Design -> TDD -> Review |

---

## 二、硬件架构

### 角色分配
| 角色 | 硬件 | 固件 |
|------|------|------|
| **TAF** (Test Agent Firmware) | **PICO 2 (RP2350)** | Pico SDK + TAP 协议状态机 + USB CDC |
| **DUT** (Device Under Test) | **STM32F4Discovery (STM32F407VG)** | STM32 HAL + UART 回环 + GPIO 响应 |

### 接线表
| PICO 2 (TAF) | STM32F4Discovery (DUT) | 功能 |
|--------------|------------------------|------|
| **GP4** (UART1_TX) | **PA3** (USART2_RX) | TAF 发送 -> DUT 接收 |
| **GP5** (UART1_RX) | **PA2** (USART2_TX) | DUT 发送 -> TAF 接收 |
| **GP2** (GPIO_OUT) | **PD5** (GPIO_Input, Pull-down) | TAF 触发信号 |
| **GP3** (GPIO_IN) | **PD12** (GPIO_Output, Green LED) | DUT 反馈（视觉可见） |
| **GP6** (GPIO_OUT) | **NRST** (复位引脚) | DUT 硬复位 |
| **GND** | **GND** | 共地 |
| **USB** -> PC | -- | TAP 协议 + 5V 供电 |

**注意**：PA5/PA6 被 LIS3DSH 加速度计占用，PC7 被 I2S3_MCK 占用，不可使用。

---

## 三、TAF 固件：PICO 2

### 文件：taf/pico/main.c

```c
#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/gpio.h"
#include <stdio.h>

#define FRAME_HEAD_0 0xAA
#define FRAME_HEAD_1 0x55

#define CMD_CONFIG  0x01
#define CMD_EXECUTE 0x02
#define CMD_QUERY   0x03
#define CMD_RESET   0x10

#define TEST_GPIO 0x01
#define TEST_UART 0x02

#define STATUS_OK      0x00
#define STATUS_BUSY    0x01
#define STATUS_FAIL    0x02
#define STATUS_TIMEOUT 0x03
#define STATUS_ERROR   0xFF

#define UART_DUT uart1
#define UART_DUT_TX_PIN 4
#define UART_DUT_RX_PIN 5

#define GPIO_OUT_PIN 2
#define GPIO_IN_PIN  3
#define GPIO_RST_PIN 6
#define LED_PIN 25

volatile struct {
    uint8_t active;
    uint8_t type;
    union {
        struct { uint8_t pin; uint8_t mode; uint8_t expected; uint8_t timeout; } gpio;
        struct { uint8_t tx_len; uint8_t rx_len; uint8_t data[64]; uint16_t timeout; } uart;
    } p;
} g_cfg = {0};

uint8_t calc_crc8(uint8_t *d, uint16_t len) {
    uint8_t crc = 0x00;
    for (uint16_t i = 0; i < len; i++) {
        crc ^= d[i];
        for (uint8_t j = 0; j < 8; j++)
            crc = (crc & 0x80) ? ((crc << 1) ^ 0x31) : (crc << 1);
    }
    return crc;
}

void tap_send(uint8_t cmd, uint8_t type, uint8_t status, uint8_t *pld, uint16_t plen) {
    uint8_t tx[128];
    uint16_t n = 0;
    tx[n++] = FRAME_HEAD_0;
    tx[n++] = FRAME_HEAD_1;
    uint16_t bl = 3 + plen;
    tx[n++] = bl & 0xFF;
    tx[n++] = (bl >> 8) & 0xFF;
    tx[n++] = cmd;
    tx[n++] = type;
    tx[n++] = status;
    for (uint16_t i = 0; i < plen; i++) tx[n++] = pld[i];
    tx[n] = calc_crc8(&tx[4], bl);
    for (uint16_t i = 0; i < n + 1; i++) putchar_raw(tx[i]);
    fflush(stdout);
}

void tap_execute(void) {
    if (!g_cfg.active) {
        tap_send(CMD_EXECUTE, 0, STATUS_ERROR, NULL, 0);
        return;
    }
    uint8_t res[64] = {0};
    uint8_t st = STATUS_OK;
    uint16_t rlen = 0;

    if (g_cfg.type == TEST_GPIO) {
        gpio_put(GPIO_OUT_PIN, g_cfg.p.gpio.expected);
        sleep_ms(g_cfg.p.gpio.timeout);
        res[0] = gpio_get(GPIO_IN_PIN);
        rlen = 1;
    }
    else if (g_cfg.type == TEST_UART) {
        uint8_t tx_buf[64];
        for (uint8_t i = 0; i < g_cfg.p.uart.tx_len && i < 64; i++)
            tx_buf[i] = g_cfg.p.uart.data[i];
        uart_write_blocking(UART_DUT, tx_buf, g_cfg.p.uart.tx_len);

        absolute_time_t timeout = make_timeout_time_ms(g_cfg.p.uart.timeout);
        uint16_t idx = 0;
        while (absolute_time_diff_us(get_absolute_time(), timeout) > 0
               && idx < g_cfg.p.uart.rx_len) {
            if (uart_is_readable(UART_DUT)) res[idx++] = uart_getc(UART_DUT);
        }
        rlen = idx;
        if (idx < g_cfg.p.uart.rx_len) st = STATUS_TIMEOUT;
    }

    tap_send(CMD_EXECUTE, g_cfg.type, st, res, rlen);
    g_cfg.active = 0;
}

void tap_process(uint8_t *frm, uint16_t len) {
    if (len < 6 || frm[0] != FRAME_HEAD_0 || frm[1] != FRAME_HEAD_1) return;
    uint16_t bl = frm[2] | (frm[3] << 8);
    if (len < 4 + bl + 1) return;
    uint8_t cmd = frm[4];
    uint8_t tt = frm[5];
    if (calc_crc8(&frm[4], bl) != frm[4 + bl]) {
        tap_send(cmd, tt, STATUS_ERROR, NULL, 0);
        return;
    }
    switch (cmd) {
        case CMD_CONFIG:
            g_cfg.active = 1;
            g_cfg.type = tt;
            if (tt == TEST_GPIO && bl >= 6) {
                g_cfg.p.gpio.pin = frm[6];
                g_cfg.p.gpio.mode = frm[7];
                g_cfg.p.gpio.expected = frm[8];
                g_cfg.p.gpio.timeout = frm[9];
                tap_send(CMD_CONFIG, TEST_GPIO, STATUS_OK, NULL, 0);
            }
            else if (tt == TEST_UART && bl >= 8) {
                g_cfg.p.uart.tx_len = frm[6];
                g_cfg.p.uart.rx_len = frm[7];
                uint8_t dlen = frm[8];
                for (uint8_t i = 0; i < dlen && i < 64; i++)
                    g_cfg.p.uart.data[i] = frm[9 + i];
                g_cfg.p.uart.timeout = 1000;
                tap_send(CMD_CONFIG, TEST_UART, STATUS_OK, NULL, 0);
            }
            else tap_send(CMD_CONFIG, tt, STATUS_ERROR, NULL, 0);
            break;
        case CMD_EXECUTE: tap_execute(); break;
        case CMD_RESET:
            gpio_put(GPIO_RST_PIN, 1); sleep_ms(100);
            gpio_put(GPIO_RST_PIN, 0);
            tap_send(CMD_RESET, 0, STATUS_OK, NULL, 0);
            break;
        default: tap_send(cmd, tt, STATUS_ERROR, NULL, 0);
    }
}

int main(void) {
    stdio_init_all();
    uart_init(UART_DUT, 115200);
    gpio_set_function(UART_DUT_TX_PIN, GPIO_FUNC_UART);
    gpio_set_function(UART_DUT_RX_PIN, GPIO_FUNC_UART);
    uart_set_format(UART_DUT, 8, 1, UART_PARITY_NONE);

    gpio_init(GPIO_OUT_PIN); gpio_set_dir(GPIO_OUT_PIN, GPIO_OUT); gpio_put(GPIO_OUT_PIN, 0);
    gpio_init(GPIO_IN_PIN);  gpio_set_dir(GPIO_IN_PIN, GPIO_IN);  gpio_pull_down(GPIO_IN_PIN);
    gpio_init(GPIO_RST_PIN); gpio_set_dir(GPIO_RST_PIN, GPIO_OUT); gpio_put(GPIO_RST_PIN, 0);
    gpio_init(LED_PIN);      gpio_set_dir(LED_PIN, GPIO_OUT);

    while (!stdio_usb_connected()) { tight_loop_contents(); }
    gpio_put(LED_PIN, 1);

    uint8_t rx_buf[256];
    uint16_t rx_idx = 0;
    while (1) {
        int ch = getchar_timeout_us(0);
        if (ch != PICO_ERROR_TIMEOUT) {
            uint8_t b = (uint8_t)ch;
            if (rx_idx == 0 && b != FRAME_HEAD_0) continue;
            if (rx_idx == 1 && b != FRAME_HEAD_1) { rx_idx = 0; continue; }
            rx_buf[rx_idx++] = b;
            if (rx_idx >= 4) {
                uint16_t bl = rx_buf[2] | (rx_buf[3] << 8);
                if (rx_idx >= 4 + bl + 1) { tap_process(rx_buf, rx_idx); rx_idx = 0; }
            }
            if (rx_idx >= 256) rx_idx = 0;
        }
    }
}
```

### 文件：taf/pico/CMakeLists.txt

```cmake
cmake_minimum_required(VERSION 3.13)
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(pico_taf C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(pico_taf main.c)

target_link_libraries(pico_taf pico_stdlib hardware_uart hardware_gpio)

target_compile_definitions(pico_taf PRIVATE PICO_STDIO_ENABLE_CRLF_SUPPORT=0)

pico_add_extra_outputs(pico_taf)
pico_enable_stdio_usb(pico_taf 1)
pico_enable_stdio_uart(pico_taf 0)
```

### 编译步骤（MSYS2 MINGW64）

```bash
# 环境变量（已写入 ~/.bashrc）
export PATH="/opt/arm-toolchain/bin:$PATH"
export PICO_TOOLCHAIN_PATH="/opt/arm-toolchain"
export PICO_SDK_PATH="/c/Pico/pico-sdk"

cd /c/embedded-agent/taf/pico
rm -rf build
mkdir build && cd build
cmake .. -G "MinGW Makefiles"
mingw32-make -j$(nproc)
```

**烧录**：按住 BOOTSEL，拖放 build/pico_taf.uf2 到 RPI-RP2 盘符。

---

## 四、DUT 固件：STM32F4Discovery

### CubeMX 配置

| 外设 | 配置 |
|------|------|
| **USART2** | Mode: Asynchronous, Baud Rate: 115200, PA2=TX, PA3=RX |
| **PD5** | GPIO_Input, Pull-down |
| **PD12** | GPIO_Output, Push-Pull, Low (Green LED) |
| **Toolchain** | Makefile |

### 文件：Core/Src/main.c（USER CODE BEGIN 3 区域）

```c
/* USER CODE BEGIN 3 */

uint8_t rx_byte;

/* 非阻塞接收1字节 - USART2 */
HAL_StatusTypeDef status = HAL_UART_Receive(&huart2, &rx_byte, 1, 5);
if (status == HAL_OK)
{
    /* 回环：原样发回 */
    HAL_UART_Transmit(&huart2, &rx_byte, 1, 100);

    /* 翻转 PD12（绿灯），给 TAF 反馈 */
    HAL_GPIO_TogglePin(GPIOD, GPIO_PIN_12);
}

/* 检测 PD5 输入：如果高电平则点亮绿灯 */
if (HAL_GPIO_ReadPin(GPIOD, GPIO_PIN_5) == GPIO_PIN_SET) {
    HAL_GPIO_WritePin(GPIOD, GPIO_PIN_12, GPIO_PIN_SET);
} else {
    HAL_GPIO_WritePin(GPIOD, GPIO_PIN_12, GPIO_PIN_RESET);
}

/* USER CODE END 3 */
```

### 编译烧录（MSYS2 + STM32CubeIDE）

```bash
# MSYS2 编译
cd /c/wbs/test/STMF32F4_Agent
make -j$(nproc)
```

**烧录**：通过 STM32CubeIDE -> Run（ST-Link），或 OpenOCD/pyocd。

---

## 五、PC 端 Agent 代码

### 项目目录结构

```
C:\wbs\embedded-agent\
├── agent_core\
│   ├── __init__.py
│   ├── hybrid_ai.py          # 五引擎调度器
│   ├── changeset.py          # OpenSpec 规范目录
│   ├── tap_protocol.py       # TAP 协议 PC 端
│   ├── tap_library.py        # Robot Framework 库
│   └── v_model_engine.py     # V 模型 + Superpowers 引擎
├── taf\pico\                 # TAF 固件
├── dut\stm32f4\              # DUT 固件参考
├── changes\                   # OpenSpec 真相档案馆
├── workspace\                 # 运行时产出
├── run_agent.py              # Agent 入口
└── verify_env.py             # 环境验证
```

### 关键文件：agent_core/tap_protocol.py

**注意**：Python 语法修复（三元运算符）。

```python
import serial
import struct
import time

class TAPClient:
    FRAME_HEAD = b'\xAA\x55'
    CMD_CONFIG  = 0x01
    CMD_EXECUTE = 0x02
    CMD_QUERY   = 0x03
    CMD_RESET   = 0x10
    TEST_GPIO = 0x01
    TEST_UART = 0x02
    STATUS_OK = 0x00
    STATUS_FAIL = 0x02
    STATUS_TIMEOUT = 0x03
    STATUS_ERROR = 0xFF

    def __init__(self, port="COM10", baudrate=115200):
        self.ser = serial.Serial(port, baudrate, timeout=3)
        time.sleep(2.0)
        self.ser.reset_input_buffer()

    def _crc8(self, data: bytes) -> int:
        crc = 0x00
        for b in data:
            crc ^= b
            for _ in range(8):
                # Python 语法：不能用 C 的 ? :，必须用 if else
                crc = ((crc << 1) ^ 0x31) if (crc & 0x80) else (crc << 1)
            crc &= 0xFF
        return crc

    def _send(self, cmd, test_type, payload=b''):
        body = bytes([cmd, test_type]) + payload
        length = len(body)
        frame = self.FRAME_HEAD + struct.pack("<H", length) + body
        frame += bytes([self._crc8(body)])
        self.ser.reset_input_buffer()
        self.ser.write(frame)

        head = self.ser.read(2)
        if head != self.FRAME_HEAD:
            raise ConnectionError(f"TAP 帧头错误: {head.hex()}")
        resp_len = struct.unpack("<H", self.ser.read(2))[0]
        resp_body = self.ser.read(resp_len)
        resp_crc = self.ser.read(1)
        if not resp_body or not resp_crc:
            raise ConnectionError("TAP 响应超时")
        if self._crc8(resp_body) != resp_crc[0]:
            raise ConnectionError("TAP CRC 错误")
        return {
            "cmd": resp_body[0],
            "test_type": resp_body[1],
            "status": resp_body[2],
            "payload": resp_body[3:]
        }

    def config_gpio(self, pin, mode, expected, timeout_ms):
        payload = struct.pack("<BBBB", pin, mode, expected, min(timeout_ms, 255))
        return self._send(self.CMD_CONFIG, self.TEST_GPIO, payload)

    def config_uart(self, baudrate, tx_data, expected_rx, timeout_ms):
        tx_len = min(len(tx_data), 32)
        rx_len = min(len(expected_rx), 32)
        payload = struct.pack("<IBB", baudrate, tx_len, rx_len)
        payload += tx_data[:tx_len] + expected_rx[:rx_len]
        payload += struct.pack("<H", timeout_ms)
        return self._send(self.CMD_CONFIG, self.TEST_UART, payload)

    def execute(self):
        return self._send(self.CMD_EXECUTE, 0x00)

    def reset_dut(self):
        return self._send(self.CMD_RESET, 0x00)

    def close(self):
        self.ser.close()
```

### 验证脚本

```powershell
C:\Users\OMEN\AppData\Local\Programs\Python\Python312\python.exe -c "
from agent_core.tap_protocol import TAPClient
tap = TAPClient('COM10')
print('复位:', tap.reset_dut())
print('GPIO配置(PD5):', tap.config_gpio(5, 0, 1, 100))
print('执行:', tap.execute())
print('UART配置:', tap.config_uart(115200, b'Hello', b'Hello', 2000))
print('执行:', tap.execute())
tap.close()
"
```

---

## 六、环境配置清单

### 已安装工具

| 工具 | 版本/路径 | 验证命令 |
|------|----------|---------|
| Python 3.12 | C:\Users\OMEN\AppData\Local\Programs\Python\Python312\python.exe | python --version |
| Ollama | localhost:11434 | ollama ps |
| Qwen3:14b | 本地 GPU (RTX 5080) | ollama list |
| Qwen3:8b | 本地 GPU | ollama list |
| Qwen2.5-Coder:14b | 本地 GPU | ollama list |
| Kimi CLI | 云端 API | kimi --version |
| arm-none-eabi-gcc | C:\Program Files (x86)\Arm GNU Toolchain... | arm-none-eabi-gcc --version |
| MSYS2 MINGW64 | C:\msys64 | gcc --version |
| Pico SDK | C:\Pico\pico-sdk | ls $PICO_SDK_PATH |
| pyserial | Python 包 | pip show pyserial |
| robotframework | Python 包 | pip show robotframework |

### MSYS2 环境变量（已写入 ~/.bashrc）

```bash
export PATH="/opt/arm-toolchain/bin:$PATH"
export PICO_TOOLCHAIN_PATH="/opt/arm-toolchain"
export PICO_SDK_PATH="/c/Pico/pico-sdk"
```

**符号链接创建**：
```bash
mkdir -p /opt
ln -s "/c/Program Files (x86)/Arm GNU Toolchain arm-none-eabi/13.3 rel1" /opt/arm-toolchain
```

---

## 七、与 mattpocock/skills 的互补分析

### 已互补落地的 4 个技能

| 技能 | 落地方式 | 文件位置 |
|------|---------|---------|
| /to-prd | 合并到 OpenSpec proposal.md | skills/to-prd.md |
| /grill-me | Brainstorming 后增加人机确认 | v_model_engine.py |
| /caveman | Qwen3:8b 极简模式 | hybrid_ai.py |
| /git-guardrails | Git 危险命令拦截 | agent_core/safety.py |

### 不需要互补的技能（已覆盖）

- /diagnose -> _analyze_failure() 自动解析 Robot XML
- /triage -> V 模型阶段本身就是优先级
- /handoff -> changes/ 目录比对话存档更持久
- /zoom-out -> stage_architecture 已强制输出全局视图

---

## 八、已知问题与修复记录

| 问题 | 原因 | 修复 |
|------|------|------|
| && 在 PowerShell 无效 | PowerShell 语法差异 | 用 ; 或分步执行 |
| cmake .. 指向错误目录 | mkdir build && cd build 失败 | 确认当前目录后再执行 cmake |
| nmake 未找到 | 未安装 VS Build Tools | 改用 MSYS2 + mingw32-make |
| arm-none-eabi-gcc 未找到 | MSYS2 PATH 不包含 Windows 编译器 | 创建 /opt/arm-toolchain 符号链接 |
| pico_sdk_import.cmake 未找到 | PICO_SDK_PATH 环境变量未设置 | [Environment]::SetEnvironmentVariable + export |
| TinyUSB 警告 | 子模块未初始化 | git submodule update --init |
| putchar_raw 未定义 | Pico SDK 版本问题 | 确认 SDK 2.0+，或使用 putchar |
| SyntaxError: invalid syntax | Python 混入 C 三元运算符 ? : | 改为 ... if ... else ... |
| USART1 不可用 | 被 USB OTG 占用 | 改用 USART2 (PA2/PA3) |
| PA5/PA6 不可用 | 被 LIS3DSH 加速度计占用 | 改用 PD5/PD12 |
| PC7 不可用 | 被 I2S3_MCK 占用 | 最终改用 PD5/PD12 |

---

## 九、下一步行动建议

1. **完成硬件接线**：确认 PICO2 GP2->PD5, GP3->PD12, GP4->PA3, GP5->PA2, GP6->NRST, GND->GND
2. **运行验证脚本**：确认 TAP 协议通信正常
3. **运行完整 Agent**：python run_agent.py
4. **引入 skills 模板**：创建 skills/to-prd.md, skills/grill-me.md, skills/caveman.md, skills/git-guardrails.md
5. **固化环境变量**：确保 ~/.bashrc 已写入，新开会话无需重新 export

---

*文档生成时间：2026-06-05*
*项目路径：C:\wbs\embedded-agent*
