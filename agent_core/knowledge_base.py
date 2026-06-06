"""
嵌入式 V 模型 Agent - 本地 RAG 知识库
吸收: RAGFlow(解析) + LlamaIndex(分块) + LangChain(混合检索) + RAGFlow(重排序)
保持: ChromaDB + Ollama nomic-embed-text + 零外部服务
所有注释使用中文
"""

import chromadb
import fitz  # PyMuPDF
from pathlib import Path
import requests
import re
import math
from typing import List, Dict, Optional


class EmbeddedKnowledgeBase:
    """
    本地 RAG 流水线: 解析 -> 分块 -> 嵌入 -> 索引 -> 检索 -> 重排序
    """

    def __init__(self, base_dir: Optional[str] = None):
        # 动态推算项目根目录，避免硬编码路径
        if base_dir is None:
            project_root = Path(__file__).parent.parent.resolve()
            self.base_dir = project_root / "knowledge"
        else:
            self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # ChromaDB 本地持久化 (SQLite 后端，零服务)
        # 修正: 新版 ChromaDB (1.x) 使用 PersistentClient
        persist_path = str(self.base_dir / ".chroma_db")
        self.client = chromadb.PersistentClient(path=persist_path)
        self.collection = self.client.get_or_create_collection(
            name="embedded_agent_kb",
            metadata={"hnsw:space": "cosine"}
        )

        self.ollama_url = "http://localhost:11434"
        self.model_embed = "nomic-embed-text"
        self.model_rerank = "qwen3:8b"  # 复用已有模型，不额外占用显存
        self.embed_dim = 768  # nomic-embed-text 实际输出 768 维

        # 内存级 BM25 倒排索引 (吸收 LangChain 思想，无外部依赖)
        self.bm25_index: Dict[str, Dict] = {}
        self.doc_freq: Dict[str, int] = {}
        self.id_to_content: Dict[str, str] = {}

        # 若 ChromaDB 已有数据但 BM25 索引为空（进程重启），自动重建
        self._rebuild_bm25_if_needed()

    # ==================== BM25 索引重建 ====================

    def _rebuild_bm25_if_needed(self):
        """从持久化的 ChromaDB 重建内存 BM25 索引"""
        total = self.collection.count()
        if total == 0:
            return
        if len(self.bm25_index) >= total:
            return
        print(f"[KB] 重建 BM25 索引: {total} 条文档")
        all_data = self.collection.get(include=["documents", "metadatas"])
        ids = all_data["ids"]
        docs = all_data["documents"]
        for doc_id, doc_text in zip(ids, docs):
            if doc_id in self.bm25_index:
                continue
            tokens = self._tokenize(doc_text)
            freq = {}
            for t in tokens:
                freq[t] = freq.get(t, 0) + 1
            self.bm25_index[doc_id] = {"tokens": tokens, "freq": freq, "len": len(tokens)}
            self.id_to_content[doc_id] = doc_text
            for t in set(tokens):
                self.doc_freq[t] = self.doc_freq.get(t, 0) + 1

    # ==================== 1. 解析 (吸收 RAGFlow 精华) ====================

    def _parse_pdf(self, pdf_path: str) -> List[Dict]:
        """版式感知 PDF 解析: 优先识别寄存器表"""
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

    # ==================== 2. 分块 (吸收 LlamaIndex 精华) ====================

    MAX_CHUNK_SIZE = 1500  # 嵌入模型上下文长度限制，超过则进一步切分

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
                        results.extend(self._split_oversized(sec.strip(), "section", source))
            elif c_type == "code" or re.search(r'\b(void|int|uint|static|HAL_StatusTypeDef)\s+\w+\s*\(', text):
                funcs = re.split(r'(?=(?:\n|^)(?:void|int|uint|static|HAL_StatusTypeDef)\s+\w+\s*\()', text)
                for f in funcs:
                    if f.strip():
                        results.extend(self._split_oversized(f.strip(), "function", source))
            else:
                paragraphs = text.split("\n\n")
                current = ""
                for p in paragraphs:
                    if len(current) + len(p) > 800:
                        if current.strip():
                            results.extend(self._split_oversized(current.strip(), "paragraph", source))
                        current = p
                    else:
                        current += "\n\n" + p
                if current.strip():
                    results.extend(self._split_oversized(current.strip(), "paragraph", source))
        return results

    def _split_oversized(self, text: str, c_type: str, source: str) -> List[Dict]:
        """若文本超过 MAX_CHUNK_SIZE，按段落或行进一步切分"""
        if len(text) <= self.MAX_CHUNK_SIZE:
            return [{"type": c_type, "content": text, "source": source}]
        parts = []
        lines = text.split("\n")
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > self.MAX_CHUNK_SIZE:
                if current.strip():
                    parts.append({"type": c_type, "content": current.strip(), "source": source})
                current = line
            else:
                current += "\n" + line
        if current.strip():
            parts.append({"type": c_type, "content": current.strip(), "source": source})
        return parts

    # ==================== 3. 嵌入 (保持 Ollama) ====================

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量嵌入: 使用 Ollama /api/embed 接口，一次性处理多条文本"""
        if not texts:
            return []
        try:
            r = requests.post(
                f"{self.ollama_url}/api/embed",
                json={"model": self.model_embed, "input": texts},
                timeout=60
            )
            r.raise_for_status()
            data = r.json()
            embeddings = data.get("embeddings", [])
            # 若 API 返回单条格式（旧版），做兼容处理
            if not embeddings and "embedding" in data:
                return [data["embedding"]]
            return embeddings
        except Exception:
            # 降级: 逐条调用 /api/embeddings
            return [self._embed_single(t) for t in texts]

    def _embed_single(self, text: str) -> List[float]:
        # 若文本超长，截断以避免模型上下文溢出
        safe_text = text[:self.MAX_CHUNK_SIZE] if len(text) > self.MAX_CHUNK_SIZE else text
        r = requests.post(
            f"{self.ollama_url}/api/embeddings",
            json={"model": self.model_embed, "prompt": safe_text},
            timeout=30
        )
        r.raise_for_status()
        return r.json()["embedding"]

    # ==================== 4. 索引 (保持 ChromaDB) ====================

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
            print(f"[KB] 跳过缺失文件: {file_path}")
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
        embeddings = self._embed_batch(texts)
        ids = [f"{source}_{i}" for i in range(len(semantic_chunks))]
        metadatas = [{"source": source, "type": c["type"]} for c in semantic_chunks]

        # 使用 upsert 避免重复 ID 报错，支持重复运行
        self.collection.upsert(embeddings=embeddings, documents=texts, metadatas=metadatas, ids=ids)
        self._build_bm25_index(semantic_chunks, ids)
        print(f"[KB] 已摄入 {source}: {len(semantic_chunks)} 个分块")

    # ==================== 5. 检索 (吸收 LangChain 精华) ====================

    def _bm25_score(self, query: str, doc_id: str, k1: float = 1.5, b: float = 0.75) -> float:
        query_tokens = self._tokenize(query)
        doc = self.bm25_index.get(doc_id)
        if not doc:
            return 0.0
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

        # 修正: 从 vec_results["ids"][0] 获取文档 ID，而非从 metadata 中查找
        vec_ids = vec_results.get("ids", [[]])[0]
        vec_docs = vec_results.get("documents", [[]])[0]
        vec_metas = vec_results.get("metadatas", [[]])[0]
        vec_dists = vec_results.get("distances", [[]])[0]

        for rank, doc_id in enumerate(vec_ids):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            content_map[doc_id] = vec_docs[rank] if rank < len(vec_docs) else ""

        for rank, (doc_id, score, _) in enumerate(bm25_results):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            if doc_id not in content_map:
                content_map[doc_id] = self.id_to_content.get(doc_id, "")

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        return [{"id": i, "rrf_score": scores[i], "content": content_map.get(i, "")} for i in sorted_ids]

    def query(self, question: str, n_results: int = 5, filter_source: Optional[str] = None) -> List[Dict]:
        query_embed = self._embed_single(question)
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

    # ==================== 6. 重排序 (吸收 RAGFlow 精华，本地运行) ====================

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

    # ==================== Prompt 构造器 (供 VModelEngine 使用) ====================

    def build_prompt(self, question: str, system_prompt: str = "") -> str:
        context = self.query_with_rerank(question, n_results=3)
        context_str = "\n\n---\n".join([f"[来源: {c['id']}]\n{c['content'][:800]}" for c in context])
        prompt = f"""{system_prompt}

## 参考知识库 (按相关性排序)
{context_str}

## 用户问题
{question}

基于以上参考资料回答。如果资料不足，请明确说明。"""
        return prompt

    # ==================== 批量摄入工具 ====================

    def ingest_directory(self, dir_path: str, source_prefix: str, pattern: str = "*"):
        target = Path(dir_path)
        if not target.exists():
            print(f"[KB] 目录不存在: {dir_path}")
            return
        for f in target.rglob(pattern):
            source = f"{source_prefix}:{f.name}"
            self.ingest(str(f), source)

    def ingest_changes_archive(self, changes_dir: Optional[str] = None):
        """摄入 OpenSpec 变更集归档，支持动态路径"""
        if changes_dir is None:
            project_root = self.base_dir.parent
            changes_path = project_root / "changes"
        else:
            changes_path = Path(changes_dir)
        if not changes_path.exists():
            return
        for change_dir in sorted(changes_path.glob("*/")):
            for md_file in ["proposal.md", "design.md", "brainstorm.md"]:
                fp = change_dir / md_file
                if fp.exists():
                    self.ingest(str(fp), f"archive:{change_dir.name}:{md_file}")

    def ingest_code_knowledge(self):
        """摄入项目源码作为知识（TAF/DUT 代码复用）"""
        project_root = self.base_dir.parent
        code_files = [
            (project_root / "taf/pico/main.c", "taf_code"),
            (project_root / "dut/stm32f4/main.c", "dut_code"),
        ]
        for fp, source in code_files:
            if fp.exists():
                self.ingest(str(fp), source)

    def stats(self) -> Dict:
        count = self.collection.count()
        return {
            "total_documents": count,
            "bm25_indexed": len(self.bm25_index),
            "persist_dir": str(self.base_dir / ".chroma_db")
        }
