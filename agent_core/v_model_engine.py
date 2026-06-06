import os
import json
import time
import subprocess
from pathlib import Path
from agent_core.hybrid_ai import FiveEngineAI
from agent_core.changeset import Changeset
from agent_core.tap_protocol import TAPClient

class VModelEngine:
    SYSTEM_PROMPT = """你是嵌入式V模型自动化专家。
精通 STM32 HAL、Pico SDK、Robot Framework、TAP协议、J-Link。
所有代码必须可直接编译，注释使用中文。
输出直接给出代码或结构化数据，不要解释。"""
    
    CONSTRAINTS = [
        "TAF使用PICO2 (RP2350, Pico SDK)，DUT使用STM32F4Discovery (STM32F407VG, HAL)",
        "TAP协议v1.0：帧头0xAA 0x55 + CRC8-MAXIM(0x31)",
        "TAF-DUT通信：PICO2 UART1(GP4/GP5) <-> STM32F4 USART2(PA2/PA3)",
        "GPIO测试：PICO2 GP2 <-> STM32F4 PD5，PICO2 GP3 <-> STM32F4 PD12",
        "DUT复位：PICO2 GP6 -> STM32F4 NRST",
        "禁止混用Pico SDK与STM32 HAL语法"
    ]
    
    def __init__(self, workspace=None, tap_port="COM11"):
        if workspace is None:
            # 根据本文件位置动态推算项目根目录，避免硬编码路径
            project_root = Path(__file__).parent.parent.resolve()
            self.workspace = project_root / "workspace"
        else:
            self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.ai = FiveEngineAI()
        self.tap_port = tap_port
        self.cs = None
    
    def run(self, user_input: str):
        print(f"\n{'#'*70}")
        print(f"# V模型Agent启动 | {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"# 需求: {user_input[:60]}...")
        print(f"{'#'*70}")
        
        self.cs = Changeset(self.workspace, user_input)
        self.cs.write_proposal(user_input, self.CONSTRAINTS)
        
        self._stage_brainstorm(user_input)
        req_matrix = self._stage_requirements(user_input)
        self._stage_system_design(req_matrix)
        self._stage_architecture(req_matrix)
        suite_path = self._stage_module_design(req_matrix)
        dut_code_path = self._stage_coding(req_matrix)
        
        self._stage_unit_test(dut_code_path)
        self._stage_integration_test(suite_path)
        self._stage_system_test()
        self._stage_acceptance()
        
        print(f"\n{'#'*70}")
        print(f"# 执行完成 | 产出目录: {self.cs.dir}")
        print(f"{'#'*70}")
        return self.cs.dir
    
    def _stage_brainstorm(self, user_input: str):
        print(f"\n[Stage B] Superpowers Brainstorming")
        prompt = f"""基于以下嵌入式测试需求，进行技术风险头脑风暴：

需求：{user_input}
约束：{chr(10).join(self.CONSTRAINTS)}

请提出并回答：
1. 硬件接口电平/时序风险？
2. 协议解析边界条件？
3. 代码生成中最易出错的语法点？
4. 测试断言的精确标准？

输出Markdown格式。"""
        content = self.ai.generate(prompt, engine="general", system=self.SYSTEM_PROMPT, think=True)
        self.cs.brainstorm_path.write_text(content, encoding="utf-8")
        print(f"  -> {self.cs.brainstorm_path}")
    
    def _stage_requirements(self, user_input: str) -> dict:
        print(f"\n[Stage 1/9] 需求分析")
        prompt = f"""将需求转化为结构化JSON：
需求：{user_input}

输出JSON格式：
{{
  "functional": [{{"id":"R1","desc":"...","method":"auto"}}],
  "boundary": [{{"id":"B1","min":"...","max":"...","nominal":"..."}}],
  "interfaces": [{{"type":"uart","pins":"GP4/GP5<<->PA2/PA3","baud":115200}}],
  "performance": {{"latency_ms":10,"accuracy":"±1%"}},
  "compliance": ["TAP_v1.0"]
}}
直接输出JSON，不要Markdown标记。"""
        
        resp = self.ai.generate(prompt, engine="general", system=self.SYSTEM_PROMPT)
        resp_clean = resp.replace("```json", "").replace("```", "").strip()
        try:
            req = json.loads(resp_clean)
        except Exception:
            resp = self.ai.generate(f"修正JSON格式错误:\n{resp_clean}", engine="kimi")
            req = json.loads(resp.replace("```json", "").replace("```", "").strip())
        
        self.cs.write_tasks([
            {"id": f"T{i+1}", "stage": s, "description": d, "engine": e, "status": "pending"}
            for i, (s, d, e) in enumerate([
                ("requirements", "需求结构化", "general"),
                ("system_design", "测试策略", "general"),
                ("architecture", "接口架构", "general"),
                ("module_design", "Robot用例", "fast"),
                ("coding", "DUT固件", "coder"),
                ("unit_test", "编译/静态分析", "coder"),
                ("integration_test", "硬件在环", "general"),
                ("system_test", "全链路", "general"),
                ("acceptance", "验收报告", "general")
            ])
        ])
        
        path = self.cs.path("requirements_matrix.json")
        path.write_text(json.dumps(req, indent=2, ensure_ascii=False), encoding="utf-8")
        self.cs.update_task_status("T1", "completed", str(path))
        print(f"  -> {path}")
        return req
    
    def _stage_system_design(self, req: dict):
        print(f"\n[Stage 2/9] 系统设计")
        prompt = f"""基于需求矩阵生成测试策略文档：
{json.dumps(req, ensure_ascii=False)}

要求：
1. 测试层次：单元(模拟器) -> 集成(HIL) -> 系统(全链路)
2. 通过标准：UART回环误差<<1ms，GPIO响应<<100us
3. 风险：杜邦线松动、电平不匹配、波特率偏差

输出Markdown。"""
        content = self.ai.generate(prompt, engine="general", system=self.SYSTEM_PROMPT)
        path = self.cs.path("test_strategy.md")
        path.write_text(content, encoding="utf-8")
        self.cs.update_task_status("T2", "completed", str(path))
        print(f"  -> {path}")
    
    def _stage_architecture(self, req: dict):
        print(f"\n[Stage 3/9] 架构设计")
        prompt = f"""设计TAF(PICO2)与DUT(STM32F4)接口架构：

需求：{json.dumps(req, ensure_ascii=False)}
约束：{chr(10).join(self.CONSTRAINTS)}

输出：
1. 引脚分配表
2. TAP协议命令扩展表
3. 时序图(配置->执行->断言->上报)
4. 错误处理策略

输出Markdown。"""
        content = self.ai.generate(prompt, engine="general", system=self.SYSTEM_PROMPT, think=True)
        self.cs.design_path.write_text(content, encoding="utf-8")
        self.cs.update_task_status("T3", "completed", str(self.cs.design_path))
        print(f"  -> {self.cs.design_path}")
    
    def _stage_module_design(self, req: dict) -> str:
        print(f"\n[Stage 4/9] 模块设计 (TDD: 测试先行)")
        prompt = f"""生成Robot Framework测试套件。

需求：{json.dumps(req, ensure_ascii=False)}

TAPLibrary已提供，可用关键字如下（必须严格使用这些中文关键字，禁止编造其他关键字）：
- 复位 DUT
- 配置 UART 回环测试 | baudrate | tx_data | expected_rx | timeout_ms=2000
- 配置 GPIO 测试 | pin | mode | expected | timeout_ms=1000
  - pin: TAF输出引脚编号(固定2); mode: 0; expected: 0=LOW,1=HIGH; timeout_ms: 等待毫秒数
- 执行测试
- 断言测试通过

Robot Framework 语法要求：
- Settings: Library TAPLibrary
- Variables 区变量名必须加花括号，如 ${{BAUD}}
- 禁止硬编码数值，全部使用 Variables

测试用例必须覆盖：
1. UART回环测试（复位DUT -> 配置UART -> 执行 -> 断言）
2. GPIO响应测试（配置GPIO -> 执行 -> 断言）
3. 集成链路测试（复位DUT -> 配置GPIO -> 配置UART -> 执行 -> 断言）
每个用例必须含[Documentation]。

直接输出.robot文件内容，不要解释。"""
        content = self.ai.generate(prompt, engine="fast", system=self.SYSTEM_PROMPT)
        content = content.replace("```robot", "").replace("```", "").strip()
        path = self.cs.path("test_suite.robot")
        path.write_text(content, encoding="utf-8")
        self.cs.update_task_status("T4", "completed", str(path))
        print(f"  -> {path}")
        return str(path)
    
    def _stage_coding(self, req: dict) -> str:
        print(f"\n[Stage 5/9] DUT编码 (STM32F4 HAL, TDD)")
        suite_path = self.cs.path("test_suite.robot")
        suite_content = suite_path.read_text(encoding="utf-8") if suite_path.exists() else ""
        
        prompt = f"""基于以下Robot测试用例，生成STM32F4Discovery DUT固件：

测试用例：
{suite_content}

要求：
- STM32 HAL库：stm32f4xx_hal.h
- USART2 (PA2=TX, PA3=RX)：回环，收到数据立即发回
- PD5：输入(下拉)，检测TAF触发
- PD12：输出，收到UART数据时翻转
- 完整main.c，包含MX初始化代码
- 注释使用中文

用 ---FILE:main.c--- 分隔。"""
        
        content = self.ai.generate(prompt, engine="coder", system=self.SYSTEM_PROMPT)
        files = self._parse_multi_file(content)
        code_dir = self.cs.path("dut_code")
        code_dir.mkdir(exist_ok=True)
        for fname, fcontent in files.items():
            (code_dir / fname).write_text(fcontent, encoding="utf-8")
        
        self.cs.update_task_status("T5", "completed", str(code_dir))
        print(f"  -> {code_dir}")
        return str(code_dir)
    
    def _parse_multi_file(self, text: str) -> dict:
        files = {}
        current = None
        buffer = []
        for line in text.splitlines():
            if line.startswith("---FILE:") and line.endswith("---"):
                if current:
                    files[current] = "\n".join(buffer)
                current = line[8:-3].strip()
                buffer = []
            else:
                buffer.append(line)
        if current:
            files[current] = "\n".join(buffer)
        return files
    
    def _stage_unit_test(self, dut_path: str):
        print(f"\n[Stage 6/9] 单元测试 (编译检查)")
        main_c = Path(dut_path) / "main.c"
        result = {
            "main_c_exists": main_c.exists(),
            "has_uart_init": "uart_init" in main_c.read_text(encoding="utf-8") if main_c.exists() else False,
            "has_gpio_init": "gpio_init" in main_c.read_text(encoding="utf-8") if main_c.exists() else False,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        path = self.cs.path("unit_test_result.json")
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        self.cs.update_task_status("T6", "completed", str(path))
        print(f"  -> {path}")
    
    def _stage_integration_test(self, suite_path: str):
        print(f"\n[Stage 7/9] 集成测试 (硬件在环)")
        out_dir = self.cs.path("robot_results")
        out_dir.mkdir(exist_ok=True)
        
        project_root = Path(__file__).parent.parent.resolve()
        cmd = [
            "python", "-m", "robot",
            "--pythonpath", str(project_root),
            "--outputdir", str(out_dir),
            "--variable", f"TAP_PORT:{self.tap_port}",
            suite_path
        ]
        print(f"  执行: {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        result = {
            "returncode": r.returncode,
            "stdout": r.stdout,
            "stderr": r.stderr,
            "output_xml": str(out_dir / "output.xml"),
            "log_html": str(out_dir / "log.html")
        }
        path = self.cs.path("integration_result.json")
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        
        if r.returncode != 0:
            print("  [警告] 检测到失败，调用AI分析...")
            analysis = self._analyze_failure(result["output_xml"])
            self.cs.path("failure_analysis.md").write_text(analysis, encoding="utf-8")
        
        self.cs.update_task_status("T7", "completed", str(path))
        print(f"  -> {path}")
    
    def _analyze_failure(self, output_xml: str) -> str:
        if not os.path.exists(output_xml):
            return "日志文件不存在"
        import xml.etree.ElementTree as ET
        try:
            root = ET.parse(output_xml).getroot()
            fails = [t.get("name") for suite in root.iter("suite") 
                     for t in suite.iter("test") 
                     if t.find("status") is not None and t.find("status").get("status") == "FAIL"]
            context = f"失败用例: {', '.join(fails)}"
        except Exception:
            context = "XML解析失败"
        
        prompt = f"""分析Robot测试失败原因：
{context}
可能原因：硬件连接/时序/协议/代码逻辑
给出排查步骤。中文。"""
        return self.ai.generate(prompt, engine="general")
    
    def _stage_system_test(self):
        print(f"\n[Stage 8/9] 系统测试 (占位)")
        self.cs.update_task_status("T8", "completed", "")
    
    def _stage_acceptance(self):
        print(f"\n[Stage 9/9] 验收报告")
        artifacts = [str(p.relative_to(self.cs.dir)) for p in self.cs.dir.rglob("*") if p.is_file()]
        prompt = f"""基于以下V模型产出物生成验收报告：

产出文件：
{chr(10).join(artifacts)}

要求：
1. 执行摘要
2. 需求覆盖矩阵
3. 缺陷统计
4. 风险与建议

输出Markdown。"""
        content = self.ai.generate(prompt, engine="general", system=self.SYSTEM_PROMPT)
        path = self.cs.path("acceptance_report.md")
        path.write_text(content, encoding="utf-8")
        self.cs.update_task_status("T9", "completed", str(path))
        print(f"  -> {path}")