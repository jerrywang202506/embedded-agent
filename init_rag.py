#!/usr/bin/env python3
"""本地 RAG 知识库初始化脚本"""

import sys
from pathlib import Path

# 将项目根目录加入路径
project_root = Path(__file__).parent.resolve()
sys.path.insert(0, str(project_root))

from agent_core.knowledge_base import EmbeddedKnowledgeBase

def main():
    print("="*60)
    print("本地 RAG 知识库初始化")
    print("="*60)

    kb = EmbeddedKnowledgeBase()
    stats_before = kb.stats()
    print(f"[Init] 初始化前: {stats_before}")

    # 1. 摄入 TAP 协议规范
    tap_spec = project_root / "knowledge/tap_protocol/tap_v1_spec.md"
    if tap_spec.exists():
        kb.ingest(str(tap_spec), "tap_protocol")

    # 2. 摄入 Pico SDK 文档
    pico_docs = Path("C:/Pico/pico-sdk/docs")
    if pico_docs.exists():
        kb.ingest_directory(str(pico_docs), "pico_sdk", "*.md")

    # 3. 摄入项目文档
    for doc_file, source in [
        (project_root / "AGENTS.md", "agents_doc"),
        (project_root / "embedded_agent_project_doc.md", "project_doc"),
    ]:
        if doc_file.exists():
            kb.ingest(str(doc_file), source)

    # 4. 摄入代码知识
    kb.ingest_code_knowledge()

    # 5. 摄入变更集归档（如果有）
    kb.ingest_changes_archive()

    stats_after = kb.stats()
    print(f"[Init] 初始化后: {stats_after}")

    # 验证检索
    print("\n" + "="*60)
    print("验证检索功能")
    print("="*60)

    test_queries = [
        "TAP protocol CRC8 frame format",
        "STM32F4 USART2 HAL UART_Init",
        "PICO2 GPIO UART pin assignment",
    ]

    for q in test_queries:
        print(f"\n[Query] {q}")
        results = kb.query(q, n_results=2)
        for r in results:
            print(f"  -> {r['id']} (rrf={r['rrf_score']:.3f}): {r['content'][:120]}...")

    print("\n" + "="*60)
    print("[PASS] RAG 初始化完成")
    print("="*60)

if __name__ == "__main__":
    main()
