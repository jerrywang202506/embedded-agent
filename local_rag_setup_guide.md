# 嵌入式 V 模型 Agent 本地 RAG 搭建方案

> **版本**：v1.0  
> **日期**：2026-06-06  
> **路径**：`C:\wbs\embedded-agent`  
> **目标**：吸收 GitHub 高星 RAG 精华，保持零 Docker、零外部服务、完全本地化

---

## 一、设计原则

| 原则 | 说明 |
|------|------|
| **吸收精华，拒绝照搬** | 吸收 RAGFlow/LlamaIndex/LangChain 的管道设计，但不用 Docker/ES/Redis |
| **单一文件，极简依赖** | 全部逻辑集中在 `knowledge_base.py`，核心依赖仅 `chromadb` + `pymupdf` |
| **显存零占用** | 嵌入用 nomic-embed-text (384维，Ollama)，不额外加载大模型 |
| **路径统一** | 所有路径基于 `C:\wbs\embedded-agent`，与现有 Agent 工程一致 |
| **失败回退** | 任何高级功能（pdfplumber/重排序）缺失时，自动降级到基础实现 |

---

## 二、吸收与保持对照表

| 高星方案 | 吸收什么 | 如何本地化 | 保持什么 |
|---------|---------|-----------|---------|
| **RAGFlow** | 版式感知解析（寄存器表/代码块识别） | pdfplumber 可选依赖，失败回退 PyMuPDF | 不用 Docker/ES/Infinity |
| **LlamaIndex** | 语义分块（按标题/函数/寄存器切分） | 纯 Python 正则实现 | 不用 Node/Index 抽象层 |
| **LangChain** | 混合检索（向量 + BM25 + RRF 融合） | 内存级倒排索引，无外部服务 | 不用 RetrievalQA 链式封装 |
| **RAGFlow** | 重排序（Rerank） | Qwen3:8b 本地打分，不额外装模型 | 不用独立 reranker 服务 |

---

## 三、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| **向量数据库** | ChromaDB (SQLite 后端) | 零服务，文件级持久化，支持元数据过滤 |
| **嵌入模型** | Ollama `nomic-embed-text` | 384维，已在你环境，RTX 5080 推理极快 |
| **PDF 解析** | PyMuPDF (主) + pdfplumber (可选增强) | PyMuPDF 已装，pdfplumber 增强表格识别 |
| **分块策略** | 语义分块（纯 Python） | 按 Markdown 标题 / 寄存器描述 / C 函数切分 |
| **检索融合** | 向量 + BM25 + RRF | 向量找语义，BM25 找精确术语，RRF 融合排名 |
| **重排序** | Qwen3:8b 本地打分 | 利用已有模型，不额外占显存 |

---

## 四、目录结构

```
C:\wbs\embedded-agent\
├── agent_core\
│   ├── __init__.py
│   ├── hybrid_ai.py              # 五引擎调度
│   ├── knowledge_base.py         # <- 本方案核心文件
│   ├── changeset.py
│   ├── tap_protocol.py
│   ├── tap_library.py
│   └── v_model_engine.py         # <- 需修改集成 RAG
├── knowledge\                     # <- 知识库文档目录
│   ├── stm32f4\
│   │   └── RM0090.pdf            # STM32F4 参考手册（自行下载）
│   ├── pico_sdk\
│   │   └── *.md                  # Pico SDK 文档（从 C:\Pico\pico-sdk\docs 复制）
│   ├── robot_framework\
│   │   └── user_guide.md         # Robot Framework 用户指南
│   ├── tap_protocol\
│   │   └── tap_v1_spec.md        # TAP 协议规范（自建）
│   └── .chroma_db\               # ChromaDB 持久化数据（自动生成）
├── skills\                        # Superpowers 技能模板
├── changes\                       # OpenSpec 归档（运行时生成）
├── workspace\                     # 运行时产出
└── run_agent.py
```

---

## 五、核心文件：agent_core/knowledge_base.py

**直接复制以下完整代码到 `C:\wbs\embedded-agent\agent_core\knowledge_base.py`**

```python
"""
Embedded V-Model Agent - Local RAG Knowledge Base
Absorbs: RAGFlow(parse) + LlamaIndex(chunk) + LangChain(hybrid retrieve) + RAGFlow(rerank)
Keeps: ChromaDB + Ollama nomic-embed-text + zero external services
"""

import chromadb
from chromadb.config import Settings
import fitz  # PyMuPDF
from pathlib import Path
import requests
import re
import math
from typing import List, Dict, Optional


class EmbeddedKnowledgeBase:
    """
    Local RAG Pipeline: Parse -> Chunk -> Embed -> Index -> Retrieve -> Rerank
    """

    def __init__(self, base_dir: str = "C:/wbs/embedded-agent/knowledge"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # ChromaDB local persistence (SQLite backend, zero services)
        self.client = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=str(self.base_dir / ".chroma_db")
        ))
        self.collection = self.client.get_or_create_collection(
            name="embedded_agent_kb",
            metadata={"hnsw:space": "cosine"}
        )

        self.ollama_url = "http://localhost:11434"
        self.model_embed = "nomic-embed-text"
        self.model_rerank = "qwen3:8b"  # reuse existing model, no extra VRAM

        # In-memory BM25 index (absorbs LangChain essence, no external deps)
        self.bm25_index: Dict[str, Dict] = {}
        self.doc_freq: Dict[str, int] = {}
        self.id_to_content: Dict[str, str] = {}

    # ==================== 1. Parse (absorbs RAGFlow essence) ====================

    def _parse_pdf(self, pdf_path: str) -> List[Dict]:
        """Layout-aware PDF parsing: prioritize register tables"""
        chunks = []
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        md_table = self._table_to_markdown(table)
                        if md_table:
                            chunks.append({
                                "type": "register_table",
                                "content": md_table,
                                "page": page.page_number
                            })
                    text = page.extract_text()
                    if text and text.strip():
                        chunks.append({
                            "type": "paragraph",
                            "content": text.strip(),
                            "page": page.page_number
                        })
        except ImportError:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                text = doc[page_num].get_text()
                if text.strip():
                    chunks.append({
                        "type": "paragraph",
                        "content": text.strip(),
                        "page": page_num + 1
                    })
        return chunks

    def _table_to_markdown(self, table: List[List]) -> str:
        if not table or len(table) < 2:
            return ""
        lines = []
        lines.append(" | ".join(str(c) if c else "" for c in table[0]))
        lines.append(" | ".join(["---"] * len(table[0])))
        for row in table[1:]:
            lines.append(" | ".join(str(c) if c else "" for c in row))
        return "\n".join(lines)

    def _parse_markdown(self, md_path: str) -> List[Dict]:
        content = Path(md_path).read_text(encoding="utf-8")
        return [{"type": "markdown", "content": content, "page": 1}]

    def _parse_code(self, code_path: str) -> List[Dict]:
        content = Path(code_path).read_text(encoding="utf-8")
        return [{"type": "code", "content": content, "page": 1}]

    # ==================== 2. Chunk (absorbs LlamaIndex essence) ====================

    def _chunk_semantic(self, raw_chunks: List[Dict], source: str) -> List[Dict]:
        results = []
        for chunk in raw_chunks:
            text = chunk["content"]
            c_type = chunk.get("type", "paragraph")

            if c_type == "register_table":
                results.append({"type": "register_table", "content": text, "source": source})
            elif c_type == "markdown" or "## " in text:
                sections = re.split(r'\n## ', text)
                for sec in sections:
                    if sec.strip():
                        results.append({"type": "section", "content": sec.strip(), "source": source})
            elif c_type == "code" or re.search(r'\b(void|int|uint|static|HAL_StatusTypeDef)\s+\w+\s*\(', text):
                funcs = re.split(r'(?=(?:\n|^)(?:void|int|uint|static|HAL_StatusTypeDef)\s+\w+\s*\()', text)
                for f in funcs:
                    if f.strip():
                        results.append({"type": "function", "content": f.strip(), "source": source})
            else:
                paragraphs = text.split("\n\n")
                current = ""
                for p in paragraphs:
                    if len(current) + len(p) > 800:
                        if current.strip():
                            results.append({"type": "paragraph", "content": current.strip(), "source": source})
                        current = p
                    else:
                        current += "\n\n" + p
                if current.strip():
                    results.append({"type": "paragraph", "content": current.strip(), "source": source})
        return results

    # ==================== 3. Embed (keeps Ollama) ====================

    def _embed(self, text: str) -> List[float]:
        r = requests.post(
            f"{self.ollama_url}/api/embeddings",
            json={"model": self.model_embed, "prompt": text},
            timeout=30
        )
        r.raise_for_status()
        return r.json()["embedding"]

    def _batch_embed(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t) for t in texts]

    # ==================== 4. Index (keeps ChromaDB) ====================

    def _build_bm25_index(self, chunks: List[Dict], ids: List[str]):
        for chunk, doc_id in zip(chunks, ids):
            tokens = self._tokenize(chunk["content"])
            freq = {}
            for t in tokens:
                freq[t] = freq.get(t, 0) + 1
            self.bm25_index[doc_id] = {"tokens": tokens, "freq": freq, "len": len(tokens)}
            self.id_to_content[doc_id] = chunk["content"]
            for t in set(tokens):
                self.doc_freq[t] = self.doc_freq.get(t, 0) + 1

    def _tokenize(self, text: str) -> List[str]:
        tokens = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b|\b0x[0-9A-Fa-f]+\b', text)
        return [t.lower() for t in tokens if len(t) > 2]

    def ingest(self, file_path: str, source: str):
        path = Path(file_path)
        if not path.exists():
            print(f"[KB] Skip missing file: {file_path}")
            return

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            raw_chunks = self._parse_pdf(str(path))
        elif suffix in (".md", ".txt"):
            raw_chunks = self._parse_markdown(str(path))
        elif suffix in (".c", ".h", ".cpp"):
            raw_chunks = self._parse_code(str(path))
        else:
            raw_chunks = [{"type": "text", "content": path.read_text(encoding="utf-8"), "page": 1}]

        semantic_chunks = self._chunk_semantic(raw_chunks, source)
        if not semantic_chunks:
            return

        texts = [c["content"] for c in semantic_chunks]
        embeddings = self._batch_embed(texts)
        ids = [f"{source}_{i}" for i in range(len(semantic_chunks))]
        metadatas = [{"source": source, "type": c["type"]} for c in semantic_chunks]

        self.collection.add(embeddings=embeddings, documents=texts, metadatas=metadatas, ids=ids)
        self._build_bm25_index(semantic_chunks, ids)
        print(f"[KB] Ingested {source}: {len(semantic_chunks)} chunks")

    # ==================== 5. Retrieve (absorbs LangChain essence) ====================

    def _bm25_score(self, query: str, doc_id: str, k1: float = 1.5, b: float = 0.75) -> float:
        query_tokens = self._tokenize(query)
        doc = self.bm25_index.get(doc_id)
        if not doc:
            return 0.0
        doc_tokens = doc["tokens"]
        doc_len = doc["len"]
        avg_len = 500
        N = len(self.bm25_index)

        score = 0.0
        for token in query_tokens:
            df = self.doc_freq.get(token, 1)
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
            tf = doc["freq"].get(token, 0)
            denom = tf + k1 * (1 - b + b * doc_len / avg_len)
            if denom > 0:
                score += idf * (tf * (k1 + 1)) / denom
        return score

    def _rrf_fuse(self, vec_results: Dict, bm25_results: List[tuple], k: int = 60) -> List[Dict]:
        scores = {}
        content_map = {}

        for rank, (doc, meta, dist) in enumerate(zip(
            vec_results.get("documents", [[]])[0],
            vec_results.get("metadatas", [[]])[0],
            vec_results.get("distances", [[]])[0]
        )):
            doc_id = meta.get("id", f"vec_{rank}")
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            content_map[doc_id] = doc

        for rank, (doc_id, score, _) in enumerate(bm25_results):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            if doc_id not in content_map:
                content_map[doc_id] = self.id_to_content.get(doc_id, "")

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        return [{"id": i, "rrf_score": scores[i], "content": content_map.get(i, "")} for i in sorted_ids]

    def query(self, question: str, n_results: int = 5, filter_source: Optional[str] = None) -> List[Dict]:
        query_embed = self._embed(question)
        where_clause = None
        if filter_source:
            where_clause = {"source": {"$eq": filter_source}}

        vec_results = self.collection.query(
            query_embeddings=[query_embed],
            n_results=n_results * 2,
            where=where_clause,
            include=["documents", "metadatas", "distances"]
        )

        bm25_candidates = []
        for doc_id, info in self.bm25_index.items():
            if filter_source and not doc_id.startswith(filter_source):
                continue
            score = self._bm25_score(question, doc_id)
            if score > 0:
                bm25_candidates.append((doc_id, score, info))
        bm25_candidates.sort(key=lambda x: x[1], reverse=True)
        top_bm25 = bm25_candidates[:n_results]

        fused = self._rrf_fuse(vec_results, top_bm25)
        return fused[:n_results]

    # ==================== 6. Rerank (absorbs RAGFlow essence, local) ====================

    def _rerank_by_llm(self, query: str, candidates: List[Dict]) -> List[Dict]:
        if not candidates:
            return candidates
        scored = []
        for c in candidates:
            prompt = (
                f"Rate relevance (0-10 integer only, no explanation).\n"
                f"Question: {query}\n"
                f"Document: {c['content'][:400]}\n"
                f"Score:"
            )
            try:
                r = requests.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model_rerank,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 5}
                    },
                    timeout=60
                )
                text = r.json().get("response", "5").strip()
                match = re.search(r'\b(\d+)\b', text)
                score = int(match.group(1)) if match else 5
                score = max(0, min(10, score))
            except Exception:
                score = 5
            scored.append((c, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored] + candidates[5:]

    def query_with_rerank(self, question: str, n_results: int = 5, filter_source: Optional[str] = None) -> List[Dict]:
        candidates = self.query(question, n_results=n_results * 2, filter_source=filter_source)
        return self._rerank_by_llm(question, candidates)[:n_results]

    # ==================== Prompt builder (for VModelEngine) ====================

    def build_prompt(self, question: str, system_prompt: str = "") -> str:
        context = self.query_with_rerank(question, n_results=3)
        context_str = "\n\n---\n".join([f"[Source: {c['id']}]\n{c['content'][:800]}" for c in context])
        prompt = f"""{system_prompt}

## Reference Knowledge Base (sorted by relevance)
{context_str}

## User Question
{question}

Answer based on the above references. If insufficient, state clearly."""
        return prompt

    # ==================== Batch ingestion tools ====================

    def ingest_directory(self, dir_path: str, source_prefix: str, pattern: str = "*"):
        target = Path(dir_path)
        if not target.exists():
            print(f"[KB] Directory not found: {dir_path}")
            return
        for f in target.rglob(pattern):
            source = f"{source_prefix}:{f.name}"
            self.ingest(str(f), source)

    def ingest_changes_archive(self, changes_dir: str = "C:/wbs/embedded-agent/changes"):
        changes_path = Path(changes_dir)
        if not changes_path.exists():
            return
        for change_dir in sorted(changes_path.glob("*/")):
            for md_file in ["proposal.md", "design.md", "brainstorm.md"]:
                fp = change_dir / md_file
                if fp.exists():
                    self.ingest(str(fp), f"archive:{change_dir.name}:{md_file}")

    def stats(self) -> Dict:
        count = self.collection.count()
        return {
            "total_documents": count,
            "bm25_indexed": len(self.bm25_index),
            "persist_dir": str(self.base_dir / ".chroma_db")
        }
```

---

## 六、集成到 VModelEngine

**修改 `C:\wbs\embedded-agent\agent_core\v_model_engine.py`**，在 `__init__` 中初始化知识库，并在关键阶段注入 RAG：

```python
from agent_core.knowledge_base import EmbeddedKnowledgeBase

class VModelEngine:
    def __init__(self, workspace="C:/wbs/embedded-agent/workspace", tap_port="COM10"):
        # ... existing code ...
        self.kb = EmbeddedKnowledgeBase()
        self._init_knowledge_base()

    def _init_knowledge_base(self):
        stats = self.kb.stats()
        if stats["total_documents"] > 0:
            print(f"[KB] Loaded: {stats['total_documents']} docs")
            return
        print("[KB] First time build...")
        pico_docs = Path("C:/Pico/pico-sdk/docs")
        if pico_docs.exists():
            self.kb.ingest_directory(str(pico_docs), "pico_sdk", "*.md")
        tap_spec = Path("C:/wbs/embedded-agent/knowledge/tap_protocol/tap_v1_spec.md")
        if tap_spec.exists():
            self.kb.ingest(str(tap_spec), "tap_protocol")
        self.kb.ingest_changes_archive()
        print(f"[KB] Build done: {self.kb.stats()}")

    def _stage_brainstorm(self, user_input: str):
        context = self.kb.query_with_rerank(
            f"STM32F4 UART GPIO test risk timing {user_input}", 
            n_results=3
        )
        context_str = "\n".join([c["content"][:500] for c in context])
        prompt = f"""Based on references, brainstorm technical risks:

References:
{context_str}

Requirement: {user_input}
Constraints: {chr(10).join(self.CONSTRAINTS)}

Answer:
1. Hardware interface/timing risks?
2. Protocol parsing edge cases?
3. Most error-prone syntax points in code generation?
4. Precise assertion standards?

Output Markdown."""
        content = self.ai.generate(prompt, engine="general", system=self.SYSTEM_PROMPT, think=True)
        self.cs.brainstorm_path.write_text(content, encoding="utf-8")

    def _stage_coding(self, req: dict) -> str:
        context = self.kb.query_with_rerank(
            "STM32F4 USART2 HAL UART_Init GPIO_Init PD12 PD5 register",
            n_results=3,
            filter_source=None
        )
        suite_path = self.cs.path("test_suite.robot")
        suite_content = suite_path.read_text(encoding="utf-8") if suite_path.exists() else ""
        context_str = "\n".join([c["content"][:600] for c in context])
        prompt = f"""Generate STM32F4 DUT firmware based on test case and references:

Test case:
{suite_content}

References:
{context_str}

Requirements:
- STM32 HAL library
- USART2 (PA2=TX, PA3=RX): loopback
- PD5: input(pull-down), detect TAF trigger
- PD12: output, toggle on UART receive
- Complete main.c with MX init code
- Comments in Chinese

Use ---FILE:main.c--- separator."""
        content = self.ai.generate(prompt, engine="coder", system=self.SYSTEM_PROMPT)
        # ... parse and save ...
```

---

## 七、落地步骤（今晚可执行）

### Step 1: Install dependencies

```powershell
C:\Users\OMEN\AppData\Local\Programs\Python\Python312\python.exe -m pip install chromadb pymupdf
# Optional table enhancement
C:\Users\OMEN\AppData\Local\Programs\Python\Python312\python.exe -m pip install pdfplumber
```

### Step 2: Create directories

```powershell
mkdir C:\wbs\embedded-agent\knowledge\stm32f4
mkdir C:\wbs\embedded-agent\knowledge\pico_sdk
mkdir C:\wbs\embedded-agent\knowledge\robot_framework
mkdir C:\wbs\embedded-agent\knowledge\tap_protocol
```

### Step 3: Create core file

Save Section 5 code to: `C:\wbs\embedded-agent\agent_core\knowledge_base.py`

### Step 4: Create TAP spec document

Create `C:\wbs\embedded-agent\knowledge\tap_protocol\tap_v1_spec.md`:

```markdown
# TAP Protocol v1.0 Specification

## Frame Format
- Header: 0xAA 0x55
- Length: 2 bytes (little-endian)
- Body: CMD (1B) + TEST_TYPE (1B) + Payload (N B)
- CRC8: MAXIM polynomial 0x31

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
```

### Step 5: Copy Pico SDK docs (optional)

```powershell
xcopy "C:\Pico\pico-sdk\docs\*.md" "C:\wbs\embedded-agent\knowledge\pico_sdk\" /S /I
```

### Step 6: Initialize knowledge base

```powershell
C:\Users\OMEN\AppData\Local\Programs\Python\Python312\python.exe -c "
import sys
sys.path.insert(0, 'C:/wbs/embedded-agent')
from agent_core.knowledge_base import EmbeddedKnowledgeBase
kb = EmbeddedKnowledgeBase()
kb.ingest('C:/wbs/embedded-agent/knowledge/tap_protocol/tap_v1_spec.md', 'tap_protocol')
import os
pico_dir = 'C:/wbs/embedded-agent/knowledge/pico_sdk'
if os.path.exists(pico_dir):
    kb.ingest_directory(pico_dir, 'pico_sdk', '*.md')
kb.ingest_changes_archive()
print(kb.stats())
"
```

### Step 7: Verify retrieval

```powershell
C:\Users\OMEN\AppData\Local\Programs\Python\Python312\python.exe -c "
import sys
sys.path.insert(0, 'C:/wbs/embedded-agent')
from agent_core.knowledge_base import EmbeddedKnowledgeBase
kb = EmbeddedKnowledgeBase()
results = kb.query_with_rerank('TAP protocol CRC8 frame format', n_results=3)
for r in results:
    print(f'ID: {r[\"id\"]}, Score: {r[\"rrf_score\"]:.3f}')
    print(r['content'][:300])
    print('---')
"
```

---

## 八、Verification Checklist

| Check | Command | Expected |
|-------|---------|----------|
| ChromaDB created | `Test-Path C:\wbs\embedded-agent\knowledge\.chroma_db` | `True` |
| Documents ingested | `kb.stats()` | `total_documents > 0` |
| Vector search works | `kb.query("TAP protocol")` | Returns TAP spec |
| BM25 search works | `kb.query("HAL_UART_Transmit")` | Returns UART docs |
| Rerank works | `kb.query_with_rerank("CRC8")` | Better order than pure vector |
| Prompt build works | `kb.build_prompt("How to config USART2")` | Includes references |

---

## 九、Integration Map

```
VModelEngine.run()
├── stage_brainstorm()     <- RAG: retrieve historical risks + tech docs
├── stage_requirements()   <- optional: retrieve similar requirement baselines
├── stage_system_design()  <- optional: retrieve test strategy templates
├── stage_architecture()   <- optional: retrieve interface definition history
├── stage_module_design()  <- RAG: retrieve Robot keyword syntax
├── stage_coding()         <- RAG: retrieve HAL API signatures + register defs
├── stage_unit_test()      <- no change
├── stage_integration_test() <- no change
├── stage_system_test()    <- no change
└── stage_acceptance()     <- optional: retrieve historical report templates
```

---

## 十、Next Steps

1. Download RM0090 from ST website -> `knowledge/stm32f4/`
2. Download Robot Framework user guide -> `knowledge/robot_framework/`
3. After each Agent run, auto-ingest new `changes/` archive into KB
4. Ingest `taf/pico/main.c` and `dut/stm32f4/main.c` as code knowledge for cross-project reuse

---

*Version: v1.0*  
*Generated: 2026-06-06*  
*Path: C:\wbs\embedded-agent*
