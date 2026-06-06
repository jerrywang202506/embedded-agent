# TAP Protocol v1.0 Specification

## Frame Format
- Header: `0xAA 0x55`
- Length: 2 bytes (little-endian)
- Body: CMD (1B) + TEST_TYPE (1B) + Payload (N B)
- CRC8: MAXIM polynomial `0x31`

## Commands
| CMD | Name | Description |
|-----|------|-------------|
| 0x01 | CONFIG | Configure test parameters |
| 0x02 | EXECUTE | Execute configured test |
| 0x03 | QUERY | Query TAF status |
| 0x10 | RESET | Reset DUT via NRST |

## Test Types
| Type | Name | Description |
|------|------|-------------|
| 0x01 | GPIO | Pin level test |
| 0x02 | UART | Loopback test |
| 0x03 | ADC | Analog read |

## CRC8 Algorithm
```c
uint8_t crc8(uint8_t *data, uint16_t len) {
    uint8_t crc = 0x00;
    for (uint16_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++)
            crc = (crc & 0x80) ? ((crc << 1) ^ 0x31) : (crc << 1);
    }
    return crc;
}
```

## Hardware Interface
- TAF (PICO2) UART1 TX (GP4) -> DUT (STM32F4) USART2 RX (PA3)
- TAF (PICO2) UART1 RX (GP5) -> DUT (STM32F4) USART2 TX (PA2)
- TAF (PICO2) GP2 (GPIO_OUT) -> DUT PD5 (GPIO_Input, Pull-down)
- TAF (PICO2) GP3 (GPIO_IN) -> DUT PD12 (GPIO_Output, Green LED)
- TAF (PICO2) GP6 (GPIO_OUT) -> DUT NRST (Hard Reset)

## Timing Constraints
- DUT response latency must be < 10ms for UART loopback
- GPIO toggle detection timeout: 1000ms default
- TAF-DUT baud rate: 115200
