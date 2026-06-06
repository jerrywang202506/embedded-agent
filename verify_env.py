import requests
import subprocess
import sys
from pathlib import Path

def check(name, fn):
    try:
        fn()
        print(f"[OK] {name}")
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        return False

def main():
    print("="*60)
    print("嵌入式Agent环境验证 (TAF=PICO2 | DUT=STM32F4)")
    print("="*60)
    
    results = []
    
    def _ollama():
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        required = ["qwen3:14b", "qwen3:8b", "qwen2.5-coder:14b"]
        missing = []
        for req in required:
            if not any(req in m or m.startswith(req) for m in models):
                missing.append(req)
        if missing:
            raise RuntimeError(f"缺少模型: {missing}")
    results.append(check("Ollama + 三模型", _ollama))
    
    def _kimi():
        r = subprocess.run(["kimi", "--version"], capture_output=True, timeout=5)
        assert r.returncode == 0
    results.append(check("Kimi CLI", _kimi))
    
    def _py():
        import robot, serial
    results.append(check("Python包 (robotframework, pyserial)", _py))
    
    def _gcc():
        r = subprocess.run(["arm-none-eabi-gcc", "--version"], capture_output=True, timeout=5)
        assert r.returncode == 0
    results.append(check("arm-none-eabi-gcc", _gcc))
    
    def _serial():
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        print(f"   可用串口: {ports}")
        if not ports:
            raise RuntimeError("无可用串口")
    results.append(check("串口设备", _serial))
    
    def _dirs():
        for d in ["agent_core", "changes", "workspace", "taf/pico", "dut/stm32f4"]:
            assert Path(d).exists(), f"缺少目录: {d}"
    results.append(check("项目目录结构", _dirs))
    
    print("="*60)
    if all(results):
        print("[PASS] 环境验证通过，可执行: python run_agent.py")
    else:
        print("[WARN] 部分检查失败，请修复后重试")
    print("="*60)

if __name__ == "__main__":
    main()