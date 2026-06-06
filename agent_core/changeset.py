import json
import time
from pathlib import Path

class Changeset:
    def __init__(self, base_dir: Path, description: str):
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_desc = "".join(c if c.isalnum() else "_" for c in description)[:30]
        self.dir = base_dir / f"changes/{ts}_{safe_desc}"
        self.dir.mkdir(parents=True, exist_ok=True)
        
        self.proposal_path = self.dir / "proposal.md"
        self.tasks_path = self.dir / "tasks.json"
        self.design_path = self.dir / "design.md"
        self.brainstorm_path = self.dir / "brainstorm.md"
        self.review_path = self.dir / "review.md"
    
    def write_proposal(self, user_input: str, constraints: list):
        content = f"""# 变更提案 (Proposal)

## 需求描述
{user_input}

## 硬性约束
{chr(10).join(f"- {c}" for c in constraints)}

## 禁止事项
- 不得修改 TAP 协议帧格式 (0xAA 0x55 头 + CRC8-MAXIM)
- 不得混用 Pico SDK 与 STM32 HAL 语法
- 不得使用非标准库或第三方闭源组件

## 通过标准
- 所有 Robot Framework 测试用例 PASS
- TAF 与 DUT 通信时序误差 < 1ms
- 代码通过 arm-none-eabi-gcc 编译零警告
"""
        self.proposal_path.write_text(content, encoding="utf-8")
        return self.proposal_path
    
    def write_tasks(self, tasks: list):
        self.tasks_path.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.tasks_path
    
    def load_proposal(self) -> str:
        return self.proposal_path.read_text(encoding="utf-8") if self.proposal_path.exists() else ""
    
    def load_tasks(self) -> list:
        if not self.tasks_path.exists():
            return []
        return json.loads(self.tasks_path.read_text(encoding="utf-8"))
    
    def update_task_status(self, task_id: str, status: str, artifact: str = ""):
        tasks = self.load_tasks()
        for t in tasks:
            if t["id"] == task_id:
                t["status"] = status
                t["artifact"] = artifact
                t["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.write_tasks(tasks)
    
    def path(self, filename: str) -> Path:
        return self.dir / filename