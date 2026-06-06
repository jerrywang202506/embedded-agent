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
    
    STATUS_OK      = 0x00
    STATUS_BUSY    = 0x01
    STATUS_FAIL    = 0x02
    STATUS_TIMEOUT = 0x03
    STATUS_ERROR   = 0xFF
    
    def __init__(self, port: str = "COM11", baudrate: int = 115200):
        self.ser = serial.Serial(port, baudrate, timeout=3)
        time.sleep(2.0)
        self.ser.reset_input_buffer()
    
    def _crc8(self, data: bytes) -> int:
        crc = 0x00
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = ((crc << 1) ^ 0x31) if (crc & 0x80) else (crc << 1)  # ✅ Python语法
            crc &= 0xFF
        return crc
    
    def _send(self, cmd: int, test_type: int, payload: bytes = b'') -> dict:
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
            raise ConnectionError("TAP 响应超时或长度不足")
        
        if self._crc8(resp_body) != resp_crc[0]:
            raise ConnectionError("TAP 响应 CRC 错误")
        
        return {
            "cmd": resp_body[0],
            "test_type": resp_body[1],
            "status": resp_body[2],
            "payload": resp_body[3:]
        }
    
    def config_gpio(self, pin: int, mode: int, expected: int, timeout_ms: int):
        payload = struct.pack("<BBBB", pin, mode, expected, min(timeout_ms, 255))
        return self._send(self.CMD_CONFIG, self.TEST_GPIO, payload)
    
    def config_uart(self, baudrate: int, tx_data: bytes, expected_rx: bytes, timeout_ms: int):
        # 注意：TAF固件中UART波特率固定115200，timeout硬编码1000ms
        # payload格式必须与TAF匹配：[tx_len:1B][rx_len:1B][dlen:1B][data...]
        tx_len = min(len(tx_data), 32)
        rx_len = min(len(expected_rx), 32)
        data = tx_data[:tx_len] + expected_rx[:rx_len]
        payload = bytes([tx_len, rx_len, len(data)]) + data
        return self._send(self.CMD_CONFIG, self.TEST_UART, payload)
    
    def execute(self):
        return self._send(self.CMD_EXECUTE, 0x00)
    
    def reset_dut(self):
        return self._send(self.CMD_RESET, 0x00)
    
    def close(self):
        self.ser.close()