import sys
from pathlib import Path
from agent_core.v_model_engine import VModelEngine

if __name__ == "__main__":
    default_req = ("验证STM32F4 USART2回环功能：TAF(PICO2)发送'HelloSTM32'，"
                   "DUT应在10ms内原样返回，同时GPIO PD5触发时PD12翻转电平")
    
    req = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else default_req
    
    # COM口号根据设备管理器中PICO2的USB CDC端口调整
    engine = VModelEngine(tap_port="COM11")
    engine.run(req)