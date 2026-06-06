import requests
import subprocess
import time
from typing import Optional, Literal

class FiveEngineAI:
    """
    五引擎AI调度器：
    本地：qwen3:14b(通用) / qwen3:8b(快速) / qwen2.5-coder:14b(底层代码)
    云端：kimi cli(审查) / kimi coder(长代码)
    """
    
    def __init__(self, ollama_url="http://localhost:11434"):
        self.ollama_url = ollama_url
        self.kimi_available = self._check_kimi()
        self.local_models = {
            "general": "qwen3:14b",
            "fast": "qwen3:8b",
            "coder": "qwen2.5-coder:14b"
        }
        self._warmup()
    
    def _check_kimi(self) -> bool:
        try:
            r = subprocess.run(["kimi", "--version"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False
    
    def _warmup(self):
        for role, model in self.local_models.items():
            try:
                requests.post(f"{self.ollama_url}/api/generate", json={
                    "model": model, "prompt": "hi", "stream": False
                }, timeout=30)
                print(f"[AI] {model} ({role}) 预热完成")
            except Exception as e:
                print(f"[AI] ⚠️ {model} 预热失败: {e}")
    
    def generate(self, prompt: str,
                 engine: Literal["general", "fast", "coder", "kimi", "kimi_coder"] = "general",
                 system: str = "",
                 think: bool = False,
                 context_file: Optional[str] = None,
                 temperature: float = 0.3) -> str:
        if engine in ("kimi", "kimi_coder"):
            return self._kimi_generate(prompt, context_file, engine)
        return self._local_generate(prompt, engine, system, think, temperature)
    
    def _local_generate(self, prompt: str, engine: str, system: str, think: bool, temperature: float) -> str:
        model = self.local_models[engine]
        final_prompt = prompt
        if model.startswith("qwen3"):
            final_prompt = ("/think\n" if think else "/no_think\n") + prompt
        
        payload = {
            "model": model,
            "prompt": final_prompt,
            "system": system,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": 32768,
                "num_gpu": 99,
                "num_predict": 4096
            }
        }
        r = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=180)
        r.raise_for_status()
        return r.json().get("response", "")
    
    def _kimi_generate(self, prompt: str, context_file: Optional[str], mode: str) -> str:
        if not self.kimi_available:
            raise RuntimeError("Kimi CLI 不可用")
        cmd = ["kimi", "chat", "-m", "kimi-latest"]
        if mode == "kimi_coder":
            cmd = ["kimi", "chat", "-m", "kimi-coder-latest"]
        if context_file:
            cmd.extend(["-f", context_file])
        r = subprocess.run(cmd, input=prompt, text=True, capture_output=True,
                          encoding="utf-8", timeout=180)
        if r.returncode != 0:
            raise RuntimeError(f"Kimi 错误: {r.stderr}")
        return r.stdout.strip()