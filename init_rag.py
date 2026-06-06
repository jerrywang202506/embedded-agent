#!/usr/bin/env python3
"""本地 RAG 知识库初始化脚本 - 优先摄入高价值资料"""

import os
import sys
from pathlib import Path

# 强制无缓冲输出，便于查看后台任务进度
os.environ["PYTHONUNBUFFERED"] = "1"

# 将项目根目录加入路径
project_root = Path(__file__).parent.resolve()
sys.path.insert(0, str(project_root))

from agent_core.knowledge_base import EmbeddedKnowledgeBase


# 低价值文档关键词（跳过）
SKIP_PDF_KEYWORDS = [
    "functional-safety-packages",  # 安全包，与当前项目关系不大
    "tn0516",  # PMSM FOC SDK
    "um3461",  # 自测试库
    "es0182",  # 勘误表，chunk 多但价值有限
    "an3997",  # 音频播放
    "an3998",  # PDM 音频解码
    "an3990",  # 固件升级
    "um1467",  # 软件环境入门（已摄入过）
    "um1472",  # 发现板入门（已摄入过）
    "um2052",  # 开发工具入门（已摄入过）
]

# Robot Framework 中低价值的子目录/文件
SKIP_RF_PATTERNS = ["images", "_files", ".js", ".css", ".png"]


def should_ingest_html(fp: Path) -> bool:
    """判断 HTML 文件是否值得摄入"""
    if fp.suffix.lower() not in (".html", ".htm"):
        return False
    s = str(fp).lower()
    for p in SKIP_RF_PATTERNS:
        if p in s:
            return False
    return True


def should_ingest_pdf(fp: Path) -> bool:
    """判断 PDF 是否值得摄入"""
    s = fp.name.lower()
    for kw in SKIP_PDF_KEYWORDS:
        if kw in s:
            return False
    return True


def main():
    print("=" * 60)
    print("本地 RAG 知识库初始化 - 高价值资料优先")
    print("=" * 60)

    kb = EmbeddedKnowledgeBase()
    stats_before = kb.stats()
    print(f"[Init] 初始化前: {stats_before}")

    docs_to_ingest = []

    # 1. TAP 协议规范
    tap_spec = project_root / "knowledge/tap_protocol/tap_v1_spec.md"
    if tap_spec.exists():
        docs_to_ingest.append((str(tap_spec), "tap_protocol"))

    # 2. 项目文档
    for doc_file, source in [
        (project_root / "AGENTS.md", "agents_doc"),
        (project_root / "embedded_agent_project_doc.md", "project_doc"),
    ]:
        if doc_file.exists():
            docs_to_ingest.append((str(doc_file), source))

    # 3. 代码知识
    for fp, source in [
        (project_root / "taf/pico/main.c", "taf_code"),
        (project_root / "dut/stm32f4/main.c", "dut_code"),
    ]:
        if fp.exists():
            docs_to_ingest.append((str(fp), source))

    # 4. STM32F4 PDF（筛选高价值）
    stm32_dir = project_root / "knowledge/stm32f4"
    if stm32_dir.exists():
        pdfs = sorted(stm32_dir.glob("*.pdf"))
        for pdf in pdfs:
            if should_ingest_pdf(pdf):
                source = f"stm32f4:{pdf.stem[:60]}"
                docs_to_ingest.append((str(pdf), source))

    # 5. Pico SDK
    pico_dir = project_root / "knowledge/pico_sdk"
    if pico_dir.exists():
        for f in sorted(pico_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".pdf", ".md", ".txt"):
                docs_to_ingest.append((str(f), f"pico_sdk:{f.name}"))

    # 6. Robot Framework HTML（筛选高价值）
    rf_dir = project_root / "knowledge/robot_framework"
    if rf_dir.exists():
        htmls = [f for f in sorted(rf_dir.rglob("*")) if f.is_file() and should_ingest_html(f)]
        for hf in htmls:
            rel = hf.relative_to(rf_dir)
            docs_to_ingest.append((str(hf), f"robot_framework:{rel}"))

    # 7. 变更集归档
    changes_path = project_root / "changes"
    if changes_path.exists():
        for change_dir in sorted(changes_path.glob("*/")):
            for md_file in ["proposal.md", "design.md", "brainstorm.md"]:
                fp = change_dir / md_file
                if fp.exists():
                    docs_to_ingest.append((str(fp), f"archive:{change_dir.name}:{md_file}"))

    print(f"[Init] 计划摄入 {len(docs_to_ingest)} 个文档")

    for idx, (fp, source) in enumerate(docs_to_ingest, 1):
        print(f"[{idx}/{len(docs_to_ingest)}] 摄入 {source} ...")
        try:
            kb.ingest(fp, source)
        except Exception as e:
            print(f"  [WARN] 失败: {e}")

    stats_after = kb.stats()
    print(f"[Init] 初始化后: {stats_after}")

    # 验证检索
    print("\n" + "=" * 60)
    print("验证检索功能")
    print("=" * 60)

    test_queries = [
        "TAP protocol CRC8 frame format",
        "STM32F4 USART2 HAL UART_Init",
        "PICO2 GPIO UART pin assignment",
        "Robot Framework keyword syntax",
        "HAL_UART_Transmit DMA",
    ]

    for q in test_queries:
        print(f"\n[Query] {q}")
        results = kb.query(q, n_results=2)
        for r in results:
            print(f"  -> {r['id']} (rrf={r['rrf_score']:.3f}): {r['content'][:120]}...")

    print("\n" + "=" * 60)
    print("[PASS] RAG 初始化完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
