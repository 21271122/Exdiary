# Exdiary 本地论文 RAG 知识库设计方案

> 用户上传本领域论文 PDF → 系统自动提取实验方法段落 → 向量化存储 → Agent 根据用户实验描述检索相似论文的实验方法 → 注入 prompt 指导提取和追问。

---

## 一、总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        离线索引阶段                               │
│                                                                  │
│  用户上传 PDF                                                    │
│    → PyMuPDF 提取全文                                            │
│    → 按章节标题分割（优先定位 "Experimental"/"Methods" 段）      │
│    → sentence-transformers 向量化                                │
│    → ChromaDB 持久化存储                                         │
└─────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                        在线检索阶段                               │
│                                                                  │
│  用户描述实验                                                     │
│    → 向量化用户描述                                               │
│    → ChromaDB 检索 Top-K 相似段落                                │
│    → 构建 RAG 上下文                                             │
│    → 注入提取 prompt 或 Agent SYSTEM_PROMPT                      │
│    → LLM 参考论文中的参数、方法、表征手段来追问和提取             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| PDF 提取 | `PyMuPDF` (fitz) | 最稳定、支持中文、可提取段落结构 |
| 文本分块 | 按章节标题分割 | 论文的"实验方法"段是完整的语义单元 |
| 向量模型 | `sentence-transformers` + `all-MiniLM-L6-v2` | 384 维、80MB 模型、本地运行、中文支持可接受 |
| 向量存储 | `ChromaDB` | 纯 Python、零配置、持久化、支持元数据过滤 |
| 依赖量 | `pymupdf`, `chromadb`, `sentence-transformers` | 三个 pip 包 |

`sentence-transformers` 的 `all-MiniLM-L6-v2` 是英文模型。如果用户论文主要是中文，可以切换为 `shibing624/text2vec-base-chinese`（中文专用，470 万参数，~400MB）。

---

## 三、数据模型

### 3.1 论文元数据

```python
# experiments/_papers/index.yaml
{
  "papers": {
    "paper_001": {
      "id": "paper_001",
      "filename": "TiO2_doping_2024.pdf",
      "title": "Enhanced Photocatalytic Activity of N-Doped TiO2...",
      "authors": "Zhang et al.",
      "year": "2024",
      "journal": "Applied Catalysis B",
      "chunks": 12,           # 分割后总段数
      "method_chunks": 3,     # 实验方法段落数
      "uploaded_at": "2026-06-15 10:30:00",
      "indexed": true
    }
  }
}
```

### 3.2 ChromaDB 存储结构

每个分块作为一条记录，附带元数据：

```python
{
  "id": "paper_001_chunk_05",
  "document": "The TiO2 nanoparticles were synthesized via sol-gel method...",
  "metadata": {
    "paper_id": "paper_001",
    "title": "Enhanced Photocatalytic Activity...",
    "section": "Experimental Methods",    # 章节标题
    "chunk_index": 5,
    "year": 2024,
    "is_method_section": true             # 是否为实验方法段落
  }
}
```

检索时可以根据 `is_method_section` 过滤，优先返回实验方法段落。也可以不过滤，因为结果部分也可能包含用户关心的表征参数。

---

## 四、核心模块设计

### 4.1 目录结构

```
lib/
  rag.py                # RAG 核心模块：索引、检索、prompt 构建
  paper_store.py        # 论文元数据管理（CRUD + index.yaml）

experiments/
  _papers/
    index.yaml           # 论文元数据索引
    chroma/              # ChromaDB 持久化目录
    
uploads/
  papers/                # 用户上传的原始 PDF
    paper_001.pdf
    paper_002.pdf
```

### 4.2 `lib/paper_store.py` —— 论文元数据管理

```python
class PaperStore:
    def __init__(self, papers_dir: str):
        self.dir = Path(papers_dir)
        self.index_path = self.dir / "index.yaml"
    
    def add(self, filename: str, filepath: str) -> str:
        """注册一篇新论文，返回 paper_id。"""
    
    def get(self, paper_id: str) -> dict | None:
        """获取论文元数据。"""
    
    def list_all(self) -> list[dict]:
        """列出全部论文。"""
    
    def delete(self, paper_id: str) -> bool:
        """删除论文及其向量索引。"""
    
    def mark_indexed(self, paper_id: str, chunks: int, method_chunks: int):
        """标记论文已完成向量化。"""
```

### 4.3 `lib/rag.py` —— RAG 核心

```python
class PaperRAG:
    def __init__(self, papers_dir: str):
        # 初始化 ChromaDB client + embedding model
        self.client = chromadb.PersistentClient(
            path=str(Path(papers_dir) / "chroma"))
        self.collection = self.client.get_or_create_collection(
            name="paper_chunks",
            metadata={"hnsw:space": "cosine"})
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
    
    # ---- 索引 ----
    
    def index_pdf(self, paper_id: str, pdf_path: str) -> int:
        """提取 PDF 文本 → 分块 → 向量化 → 存入 ChromaDB。
        返回分块数。"""
        text = self._extract_text(pdf_path)
        chunks = self._split_by_sections(text)
        for i, chunk in enumerate(chunks):
            embedding = self.embedder.encode(chunk["text"])
            self.collection.add(
                ids=[f"{paper_id}_chunk_{i:03d}"],
                documents=[chunk["text"]],
                metadatas=[{
                    "paper_id": paper_id,
                    "section": chunk["section"],
                    "is_method": chunk.get("is_method", False),
                    "chunk_index": i
                }],
                embeddings=[embedding.tolist()]
            )
        return len(chunks)
    
    # ---- 检索 ----
    
    def search(self, query: str, top_k: int = 5,
               methods_only: bool = True) -> list[dict]:
        """检索与查询最相关的论文段落。
        返回 [{paper_id, section, text, similarity}, ...]"""
        embedding = self.embedder.encode(query)
        where = {"is_method": True} if methods_only else None
        results = self.collection.query(
            query_embeddings=[embedding.tolist()],
            n_results=top_k,
            where=where
        )
        return self._format_results(results)
    
    # ---- Prompt 构建 ----
    
    def build_context(self, query: str, top_k: int = 5) -> str:
        """将检索结果构建为可注入 prompt 的文本。"""
        results = self.search(query, top_k)
        if not results:
            return "（知识库中暂无相关论文）"
        
        parts = ["## 论文参考（来自你的知识库）\n"]
        for r in results:
            parts.append(
                f"**来源**: {r['paper_id']} - {r['section']}\n"
                f"```\n{r['text'][:600]}\n```\n"
            )
        parts.append(
            "\n请参考上述论文的实验方法和参数设置方式，"
            "提取用户实验中的对应信息。只提取用户实际提到的内容。"
        )
        return "\n".join(parts)
    
    # ---- 内部 ----
    
    def _extract_text(self, pdf_path: str) -> str:
        """PyMuPDF 提取全文。"""
        doc = fitz.open(pdf_path)
        text = []
        for page in doc:
            text.append(page.get_text("text"))
        return "\n".join(text)
    
    def _split_by_sections(self, text: str) -> list[dict]:
        """按章节标题分割文本。
        识别 "Experimental", "Methods", "实验方法", "材料与方法" 等标题。"""
        # 正则匹配常见章节标题模式
        pattern = r'(?:(?:^|\n)\s*(?:\d+[\.\)]\s*)?' \
                  r'(?:Experimental|Methods?|Materials?|' \
                  r'实验方法|材料与方法|实验部分|' \
                  r'Characterization|Synthesis|Preparation|' \
                  r'表征|合成|制备)' \
                  r'[^\n]*\n)'
        # ... 分割逻辑 ...
```

### 4.4 集成到提取流程

**`lib/services/extraction.py`**：

```python
class ExtractionService:
    def parse_notes(self, notes: str, rag_context: str = "") -> dict:
        prompt = _EXTRACTION_SYSTEM_PROMPT.replace(
            "{rag_context}", rag_context or "（未提供文献参考）")
        ...
```

**`routes/api_experiment.py`** 或 **Agent 流程中**：

```python
def _get_rag_context(query: str) -> str:
    try:
        from lib.rag import PaperRAG
        rag = PaperRAG(str(BASE_DIR / "experiments" / "_papers"))
        return rag.build_context(query)
    except Exception:
        return ""

# 在解析前：
rag_context = _get_rag_context(notes_plain[:500])
result = extraction_svc.parse_notes(notes_plain, rag_context=rag_context)
```

---

## 五、论文管理界面

在设置页面或独立页面增加论文管理功能：

- **上传**：拖拽或选择 PDF 文件，自动提取题目/作者/年份（从 PDF 元数据或文件名推断）
- **列表**：显示已索引的论文，含标题/作者/年份/分块数/索引状态
- **删除**：删除论文并从 ChromaDB 中移除对应向量
- **状态**：显示索引是否完成（大的 PDF 可能需要几秒）

API：
```
POST   /api/papers/upload     # 上传 PDF
GET    /api/papers             # 列出全部论文
DELETE /api/papers/<id>        # 删除论文
POST   /api/papers/<id>/reindex # 重新索引
```

---

## 六、对现有 PRIORITY_MAP 的处理

RAG 不是替代 PRIORITY_MAP——而是**补充它**：

```
if rag_available and rag_search_successful:
    追问策略 = RAG 上下文驱动的动态追问
elif experiment_type in PRIORITY_MAP:
    追问策略 = PRIORITY_MAP（8 种内置类型的稳定兜底）
else:
    追问策略 = 通用原则性追问（"这类实验通常关注哪些参数？"）
```

三层递进，保证任何情况下 Agent 都能问出有意义的问题。

---

## 七、实施步骤

| 步骤 | 内容 | 工作量 |
|------|------|--------|
| 1 | 安装依赖：`pip install pymupdf chromadb sentence-transformers` | 5 min |
| 2 | `lib/paper_store.py`（论文元数据管理） | 30 min |
| 3 | `lib/rag.py`（PDF 提取 + 分块 + 向量化 + 检索 + prompt 构建） | 1.5 h |
| 4 | 论文管理 API 路由（上传/列表/删除） | 40 min |
| 5 | 论文管理前端页面（简洁的上传+列表） | 40 min |
| 6 | 集成到提取流程和 Agent prompt | 30 min |
| 7 | 测试：上传 3-5 篇论文，验证检索和追问质量 | 30 min |

总计约 4 小时。

---

## 八、不做的事

- **不自动下载论文**。用户自行准备 PDF，系统只负责索引和检索。
- **不做论文内容理解/关系抽取**。只是向量检索 + 原文展示，不做知识图谱。
- **不处理扫描版 PDF**。OCR 需要额外依赖（Tesseract），暂不支持。要求用户上传文字型 PDF。
- **不替换 Schema**。RAG 只影响追问策略，不影响数据存储格式。
