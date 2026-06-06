from robot.api.deco import keyword
from robot.api import logger
from agent_core.tap_protocol import TAPClient

class TAPLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    
    def __init__(self, port="COM11"):
        self.tap = TAPClient(port)
        self.last_result = None
    
    @keyword("配置 GPIO 测试")
    def config_gpio_test(self, pin, mode, expected, timeout_ms=1000):
        r = self.tap.config_gpio(int(pin), int(mode), int(expected), int(timeout_ms))
        logger.info(f"GPIO配置: status={r['status']}")
        return r["status"] == TAPClient.STATUS_OK
    
    @keyword("配置 UART 回环测试")
    def config_uart_loopback(self, baudrate, tx_data, expected_rx, timeout_ms=2000):
        tx = tx_data.encode() if isinstance(tx_data, str) else tx_data
        rx = expected_rx.encode() if isinstance(expected_rx, str) else expected_rx
        r = self.tap.config_uart(int(baudrate), tx, rx, int(timeout_ms))
        logger.info(f"UART配置: status={r['status']}")
        return r["status"] == TAPClient.STATUS_OK
    
    @keyword("执行测试")
    def execute_test(self):
        r = self.tap.execute()
        self.last_result = r
        logger.info(f"执行结果: status={r['status']}, payload={r['payload'].hex()}")
        return r["status"] == TAPClient.STATUS_OK
    
    @keyword("断言测试通过")
    def assert_test_passed(self):
        if self.last_result is None:
            raise AssertionError("尚未执行测试")
        if self.last_result["status"] != TAPClient.STATUS_OK:
            raise AssertionError(f"测试失败, status={self.last_result['status']}")
    
    @keyword("复位 DUT")
    def reset_dut(self):
        r = self.tap.reset_dut()
        logger.info(f"DUT复位: status={r['status']}")
        # DUT硬复位后需要时间重新初始化USART2
        import time
        time.sleep(1.5)
        return r["status"] == TAPClient.STATUS_OK
    
    def __del__(self):
        self.tap.close()