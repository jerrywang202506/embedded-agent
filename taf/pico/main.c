#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/gpio.h"
#include <stdio.h>          /* 修复1: 添加 stdio.h，提供 stdout 和 fflush */

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

/* DUT通信: UART1 */
#define UART_DUT uart1
#define UART_DUT_TX_PIN 4
#define UART_DUT_RX_PIN 5

/* DUT控制 */
#define GPIO_OUT_PIN 2   /* TAF输出 -> DUT PD5 */
#define GPIO_IN_PIN  3   /* DUT PD12 -> TAF输入 */
#define GPIO_RST_PIN 6   /* DUT NRST */
#define LED_PIN 25       /* 板载LED */

/* 测试配置 */
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

/* 通过USB CDC发送原始二进制帧到PC */
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

/* 执行测试 */
void tap_execute(void) {
    if (!g_cfg.active) {
        tap_send(CMD_EXECUTE, 0, STATUS_ERROR, NULL, 0);
        return;
    }
    
    /* 清空UART接收FIFO，避免残留数据影响测试结果 */
    while (uart_is_readable(UART_DUT)) {
        uart_getc(UART_DUT);
    }
    
    uint8_t res[64] = {0};
    uint8_t st = STATUS_OK;
    uint16_t rlen = 0;
    
    if (g_cfg.type == TEST_GPIO) {
        /* 修复2: 使用 g_cfg.p.gpio.expected 而不是 .val */
        gpio_put(GPIO_OUT_PIN, g_cfg.p.gpio.expected);
        sleep_ms(g_cfg.p.gpio.timeout);
        res[0] = gpio_get(GPIO_IN_PIN);
        rlen = 1;
    }
    else if (g_cfg.type == TEST_UART) {
        /* 修复3: 去掉 volatile，复制到局部缓冲区 */
        uint8_t tx_buf[64];
        for (uint8_t i = 0; i < g_cfg.p.uart.tx_len && i < 64; i++) {
            tx_buf[i] = g_cfg.p.uart.data[i];
        }
        uart_write_blocking(UART_DUT, tx_buf, g_cfg.p.uart.tx_len);
        
        /* 接收DUT回环 */
        absolute_time_t timeout = make_timeout_time_ms(g_cfg.p.uart.timeout);
        uint16_t idx = 0;
        while (absolute_time_diff_us(get_absolute_time(), timeout) > 0 
               && idx < g_cfg.p.uart.rx_len) {
            if (uart_is_readable(UART_DUT)) {
                res[idx++] = uart_getc(UART_DUT);
            }
        }
        rlen = idx;
        if (idx < g_cfg.p.uart.rx_len) st = STATUS_TIMEOUT;
    }
    
    tap_send(CMD_EXECUTE, g_cfg.type, st, res, rlen);
    g_cfg.active = 0;
}

/* 解析TAP帧 */
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
            else {
                tap_send(CMD_CONFIG, tt, STATUS_ERROR, NULL, 0);
            }
            break;
            
        case CMD_EXECUTE:
            tap_execute();
            break;
            
        case CMD_RESET:
            gpio_put(GPIO_RST_PIN, 1);
            sleep_ms(100);
            gpio_put(GPIO_RST_PIN, 0);
            tap_send(CMD_RESET, 0, STATUS_OK, NULL, 0);
            break;
            
        default:
            tap_send(cmd, tt, STATUS_ERROR, NULL, 0);
    }
}

int main(void) {
    stdio_init_all();
    
    /* 初始化UART1 (与DUT STM32F4通信) */
    uart_init(UART_DUT, 115200);
    gpio_set_function(UART_DUT_TX_PIN, GPIO_FUNC_UART);
    gpio_set_function(UART_DUT_RX_PIN, GPIO_FUNC_UART);
    uart_set_format(UART_DUT, 8, 1, UART_PARITY_NONE);
    
    /* 初始化GPIO */
    gpio_init(GPIO_OUT_PIN); gpio_set_dir(GPIO_OUT_PIN, GPIO_OUT); gpio_put(GPIO_OUT_PIN, 0);
    gpio_init(GPIO_IN_PIN);  gpio_set_dir(GPIO_IN_PIN, GPIO_IN);  gpio_pull_down(GPIO_IN_PIN);
    gpio_init(GPIO_RST_PIN); gpio_set_dir(GPIO_RST_PIN, GPIO_OUT); gpio_put(GPIO_RST_PIN, 0);
    gpio_init(LED_PIN);      gpio_set_dir(LED_PIN, GPIO_OUT);
    
    /* 等待USB连接就绪 */
    while (!stdio_usb_connected()) { tight_loop_contents(); }
    gpio_put(LED_PIN, 1);  /* LED常亮 = TAF就绪 */
    
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
                if (rx_idx >= 4 + bl + 1) {
                    tap_process(rx_buf, rx_idx);
                    rx_idx = 0;
                }
            }
            if (rx_idx >= 256) rx_idx = 0;
        }
    }
}