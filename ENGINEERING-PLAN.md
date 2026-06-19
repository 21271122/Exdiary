# Exdiary 工程化升级计划

## 目标

从当前 YAML 文件存储 + 单用户本地应用 → SQLite + 多用户 + 云端加密同步的 SaaS 平台。

---

## 一、当前架构回顾

### 数据流

```
用户浏览器 → Flask 路由 → 服务层 → 仓储层 → YAML 文件系统
                                              ↑
                                          AbstractExperimentRepository (ABC)
```

### 关键设计：ABC 接口隔离

所有数据操作都通过 `lib/repositories/base.py` 中的抽象基类定义。路由层、服务层、Agent 层**不知道底层是 YAML 还是数据库**。换存储只需新增一个实现类，其余代码零改动。

### 当前存储方式

| 数据 | 存储路径 | 格式 |
|------|---------|------|
| 实验记录 | `experiments/EXP-YYYY-NNN.yaml` | YAML |
| 分析报告 | `experiments/_analysis_history/ANAL-YYYY-NNN.yaml` | YAML |
| 对话线程 | `experiments/_threads/THR-YYYY-NNN.yaml` | YAML + index.yaml |
| 收藏/置顶 | `experiments/_favorites.yaml` | YAML |
| 更新日志 | `experiments/_update_logs/EXP-YYYY-NNN.yaml` | YAML |
| Agent 运行时状态 | `experiments/_threads/_current_state.yaml` | YAML |
| 压缩历史摘要 | `experiments/_threads/_global_context.yaml` | YAML |
| 冷存储 | `experiments/_history/{session_id}.jsonl` | JSONL |

### 当前架构的局限

| 问题 | 原因 | 影响 |
|------|------|------|
| 并发不安全 | 两个请求同时写同一个 YAML 文件 | 数据可能损坏 |
| 搜索慢 | 每次搜索遍历所有文件（O(n) 扫描） | 实验多了以后明显卡顿 |
| ID 可能重复 | 扫描目录计算 max+1，无锁 | 并发写入可能同 ID |
| 原子写入无保证 | 直接 `open + dump`，崩溃就坏文件 | 半截文件 |
| 无用户概念 | 没有登录，没有数据隔离 | 无法支持多用户 |

---

## 二、Phase 1: SQLite 本地迁移

### 2.1 SQLite 是什么

SQLite 是一个嵌入式数据库——不需要安装服务器，数据存在一个 `.db` 文件里。Python 自带 `sqlite3` 模块，零额外依赖。

和 YAML 文件对比：

| 特性 | YAML 文件 | SQLite |
|------|----------|--------|
| 并发安全 | 无保护 | WAL 模式支持 1 写 + N 读 |
| 查询速度 | 遍历所有文件 | B-tree 索引，毫秒级 |
| ID 自增 | 手动扫描目录 | 数据库保证唯一 |
| 原子写入 | 需手动实现 | 事务自动保证 |
| 可读性 | 编辑器直接打开 | 需 SQL 工具 |
| 备份 | 复制目录 | 复制一个 `.db` 文件 |

### 2.2 为什么改动量极小

当前代码已经面向 `AbstractExperimentRepository` 接口编程。新增一个 SQLite 实现类，然后改 `app.py` 一行：

```python
# 改前
exp_repo = YamlExperimentRepository(str(BASE_DIR / "experiments"))

# 改后
exp_repo = SqliteExperimentRepository(str(BASE_DIR / "data.db"))
```

路由层、服务层、Agent 代码——**全部不变**。

### 2.3 表结构设计

把 16 个 Schema 字段拍平。数组和对象用 JSON 字符串存储（SQLite 内置 `json_extract` 支持查询 JSON 内部字段）：

```sql
-- 实验主表
CREATE TABLE experiments (
    id            TEXT PRIMARY KEY,
    title         TEXT DEFAULT '',
    date          TEXT DEFAULT '',
    experimenter  TEXT DEFAULT '',
    status        TEXT DEFAULT 'planned',    -- planned|running|done|failed|repeated
    tags          TEXT DEFAULT '[]',         -- JSON 数组
    purpose       TEXT DEFAULT '',
    materials     TEXT DEFAULT '[]',         -- JSON 对象数组
    equipment     TEXT DEFAULT '[]',
    experimental_plan TEXT DEFAULT '[]',
    sop           TEXT DEFAULT '[]',
    process_parameters TEXT DEFAULT '[]',
    observations  TEXT DEFAULT '{}',         -- JSON 对象
    characterization TEXT DEFAULT '[]',
    results       TEXT DEFAULT '{}',         -- JSON 对象
    conclusion    TEXT DEFAULT '',
    next_steps    TEXT DEFAULT '[]',
    original_notes TEXT DEFAULT '',
    references    TEXT DEFAULT '[]',
    analyzed_in   TEXT DEFAULT '[]',
    attachments   TEXT DEFAULT '[]',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

-- 常用查询索引
CREATE INDEX idx_exp_status ON experiments(status);
CREATE INDEX idx_exp_date ON experiments(date);
CREATE INDEX idx_exp_experimenter ON experiments(experimenter);

-- 全文搜索（FTS5，SQLite 内置模块）
CREATE VIRTUAL TABLE experiments_fts USING fts5(
    title, purpose, conclusion, original_notes,
    content='experiments', content_rowid='rowid'
);
```

其余表类推：`analyses`、`threads`、`favorites`、`update_logs` 各一张表。Agent 运行时状态 `_current_state.yaml` 和压缩摘要 `_global_context.yaml` 保持 YAML 文件——它们是大块序列化数据，不是结构化记录。

### 2.4 搜索性能对比

```
YAML:  遍历 EXP-*.yaml → safe_load → 提取字段 → Python 遍历打分
       500 条实验：~800ms

SQLite: SELECT FROM experiments_fts WHERE MATCH → JOIN → 返回
        500 条实验：~3ms
```

### 2.5 实现代码骨架

```python
# lib/repositories/sqlite_experiment.py

import json
import sqlite3
from pathlib import Path
from lib.repositories.base import AbstractExperimentRepository

class SqliteExperimentRepository(AbstractExperimentRepository):

    _JSON_FIELDS = {"tags", "materials", "equipment", "experimental_plan",
                    "sop", "process_parameters", "observations",
                    "characterization", "results", "next_steps",
                    "references", "analyzed_in", "attachments"}

    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS experiments (...)")

    def next_id(self) -> str:
        from datetime import datetime
        year = datetime.now().strftime("%Y")
        row = self.db.execute(
            "SELECT id FROM experiments WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
            (f"EXP-{year}-%",)
        ).fetchone()
        if row:
            last = int(row["id"].split("-")[-1])
            return f"EXP-{year}-{last + 1:03d}"
        return f"EXP-{year}-001"

    def save(self, experiment: dict) -> str:
        exp_id = experiment.get("id") or self.next_id()
        experiment["id"] = exp_id
        row = _dict_to_row(experiment)
        row["updated_at"] = _now()
        placeholders = ", ".join("?" * len(row))
        columns = ", ".join(row.keys())
        self.db.execute(
            f"INSERT OR REPLACE INTO experiments ({columns}) VALUES ({placeholders})",
            list(row.values())
        )
        return exp_id

    def load(self, exp_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM experiments WHERE id = ?", (exp_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, title, date, experimenter, status, tags "
            "FROM experiments ORDER BY date DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_all_full(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM experiments ORDER BY date DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def update(self, exp_id: str, experiment: dict) -> bool:
        experiment["id"] = exp_id
        self.save(experiment)
        return True

    def delete(self, exp_id: str) -> bool:
        self.db.execute("DELETE FROM experiments WHERE id = ?", (exp_id,))
        return self.db.changes > 0

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]

    def search(self, query: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT e.* FROM experiments e "
            "JOIN experiments_fts f ON e.rowid = f.rowid "
            "WHERE experiments_fts MATCH ? ORDER BY rank LIMIT 20",
            (query,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def summarize_all(self, exp_ids: list[str] | None = None) -> str:
        if exp_ids:
            placeholders = ",".join("?" * len(exp_ids))
            rows = self.db.execute(
                f"SELECT * FROM experiments WHERE id IN ({placeholders})",
                exp_ids
            ).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM experiments").fetchall()
        parts = []
        for r in rows:
            d = _row_to_dict(r)
            parts.append(
                f"### {d['id']}: {d['title']}\n"
                f"Date: {d['date']} | Status: {d['status']} | Tags: {', '.join(d['tags'])}\n"
                f"Purpose: {(d.get('purpose') or '')[:300]}\n"
                f"Conclusion: {(d.get('conclusion') or '')[:300]}\n"
            )
        return "\n---\n".join(parts) if parts else "No experiments found."


def _dict_to_row(d: dict) -> dict:
    """dict → 扁平化行：数组/对象字段序列化为 JSON 字符串"""
    return {
        k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
        for k, v in d.items()
    }

def _row_to_dict(row) -> dict:
    """数据库行 → 还原嵌套 dict"""
    d = dict(row)
    for key in SqliteExperimentRepository._JSON_FIELDS:
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d

def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

### 2.7 从 YAML 迁移数据

```python
def migrate_yaml_to_sqlite(yaml_dir, db_path):
    """一次性迁移：所有 YAML 文件 → SQLite。YAML 保留不删，作为备份。"""
    repo = SqliteExperimentRepository(db_path)
    for yf in sorted(Path(yaml_dir).glob("EXP-*.yaml")):
        with open(yf, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        repo.save(data)
    print(f"迁移完成：{repo.count()} 条实验")
```

### 2.8 配置开关

```yaml
# config.yaml 新增
STORAGE_BACKEND: yaml    # yaml | sqlite（渐进切换）
DB_PATH: data.db
```

`app.py` 根据配置选择实现：

```python
if config.get("STORAGE_BACKEND") == "sqlite":
    exp_repo = SqliteExperimentRepository(config["DB_PATH"])
else:
    exp_repo = YamlExperimentRepository(str(BASE_DIR / "experiments"))
```

---

## 三、Phase 2: 多用户支持

### 3.1 用户认证流程

```
1. 注册: POST /api/auth/register {username, password}
   → bcrypt 哈希密码 → 存入 users 表 → 返回成功

2. 登录: POST /api/auth/login {username, password}
   → 验证明文 vs 哈希 → 生成 JWT token → 返回 {token, user_id}

3. 后续请求: Header 中带 Authorization: Bearer {token}
   → 后端解密得到 user_id → 所有查询自动过滤
```

**关键概念解释**：

- **bcrypt**：一种密码哈希算法。不存明文密码，只存哈希值。验证时把用户输入的密码用同样算法再哈希一次，对比结果。加 salt（每用户随机值）防止彩虹表攻击。
- **JWT（Json Web Token）**：一个签过名的 JSON。例如 `{"user_id": "abc123", "exp": 1718697600}`。后端用密钥签名，无法伪造。前端存在 localStorage，每次请求带在 Header 里。无状态——服务器不需要存 session。

### 3.2 用户隔离

所有表加 `user_id` 列：

```sql
CREATE TABLE experiments (
    user_id  TEXT NOT NULL,
    id       TEXT NOT NULL,
    ...
    PRIMARY KEY (user_id, id)
);
```

隔离通过**装饰器自动注入**，不依赖程序员记得加 `WHERE user_id = ?`：

```python
import functools
import jwt

def require_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            g.user_id = payload["user_id"]
        except jwt.InvalidTokenError:
            return jsonify({"error": "请先登录"}), 401
        return f(*args, **kwargs)
    return wrapper

# 所有数据路由加这个装饰器
@require_auth
@api_agent_bp.route("/message/stream", methods=["POST"])
def api_agent_message_stream():
    # g.user_id 自动可用
    ...
```

### 3.3 多数据库 vs 共享表

| 方案 | 实现 | 适用场景 |
|------|------|---------|
| 共享表 + user_id | 所有用户数据在同一张表，用 user_id 区分 | 十万级用户，单机 |
| 每人一个 `.db` 文件 | 每个用户独立数据库文件 | 百人级，强隔离需求 |

**十万用户选共享表**。SQLite 单表千万行在索引下查询毫秒级。`(user_id, id)` 联合主键保证同一用户的数据在 B-tree 中聚簇，查询性能接近独立数据库。

### 3.4 用户表

```sql
CREATE TABLE users (
    user_id       TEXT PRIMARY KEY,      -- UUID
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,          -- bcrypt 哈希
    created_at    TEXT DEFAULT (datetime('now'))
);
```

### 3.5 数据安全底线

- 密码用 bcrypt + salt，不可逆
- JWT 密钥存在环境变量中（不进版本控制）
- 生产环境必须 HTTPS（防止中间人截获 token）
- 登录接口加频率限制（`flask-limiter`），防止暴力破解

---

## 四、Phase 3: 加密云同步

### 4.1 为什么需要加密

数据存在云端。如果服务器被攻破、或云服务商内部人员访问，数据库文件暴露。加密保证：**即使文件被拿走，没有密钥无法解密**。

Exdiary 的实验数据可能有商业或学术敏感性。采用**端到端加密（E2EE）**——密钥只在用户设备上，服务器永远不解密。

### 4.2 SQLCipher — SQLite 透明加密

SQLCipher 是 SQLite 的加密版本。使用方式和普通 SQLite 一样，只多一行：

```python
import sqlcipher3  # 替代 import sqlite3

db = sqlcipher3.connect("data.db")
db.execute(f"PRAGMA key = '{encryption_key}'")
# 之后所有读写自动加密/解密
```

整个 `.db` 文件的每一页、WAL 日志、临时文件都被 AES-256 加密。密钥错误 → 文件打开就是乱码。

### 4.3 同步架构

Exdiary 是实验记录工具，不需要实时协作。采用**定期推送 + 启动拉取**：

```
┌──────────────┐                    ┌──────────────┐
│  用户设备 A    │                    │  云端服务器    │
│              │                    │              │
│  SQLCipher   │──── POST /sync ───→│  PostgreSQL  │
│  加密 .db    │                    │  (全量存储)    │
│              │←─── GET /sync ────│              │
└──────────────┘                    └──────────────┘
```

**推送**（本地 → 云端）：
- 触发：每次 `save()` 后标记 dirty；每 30 秒或切后台时推送
- 请求：`POST /sync` `{records: [...], last_sync: "..."}`
- 内容：加密后的实验数据块（服务器不解密，只存储和转发）

**拉取**（云端 → 本地）：
- 触发：启动时、用户点"同步"
- 请求：`GET /sync?since=2026-06-18T08:00:00`
- 响应：`{records: [...], server_time: "..."}`

**冲突解决**：同一实验在两台设备上都被修改 → **最后写入获胜（Last Write Wins）**，按 `updated_at` 时间戳。简单够用。后续可升级为版本号乐观锁：

```sql
-- 版本号方案
ALTER TABLE experiments ADD COLUMN version INTEGER DEFAULT 1;

-- 更新时检查版本号
UPDATE experiments SET ..., version = version + 1
WHERE id = ? AND version = ?;
-- 如果 rowcount == 0 → 版本号不匹配 → 被其他设备改过 → 提示用户手动处理
```

### 4.4 加密密钥管理（E2EE）

```
用户注册时：
  1. 用户输入密码 "exdiary_password"
  2. 设备端用 PBKDF2 从密码派生两个密钥：
     - auth_key → 用于登录认证（发给服务器的 JWT）
     - encryption_key → 用于本地数据库加密（永远不上传）

数据同步时：
  1. 本地从 SQLCipher 读出明文数据
  2. 用服务器公钥加密 → 上传密文
  3. 服务器存储的是加密块，无法解读
  4. 其他设备拉取后，用本地 encryption_key 解密
```

**服务器零知识**：服务器永远不知道 encryption_key，存储的永远是密文。即使服务器被拖库，攻击者只能拿到加密数据块。

### 4.5 备份

```
备份流程:
  1. 导出所有表为 JSON
  2. gzip 压缩
  3. 用 encryption_key 加密
  4. 上传至云存储: backups/{user_id}/{timestamp}.enc

还原流程:
  1. 下载加密备份文件
  2. 用 encryption_key 解密
  3. 解压 → JSON → 导入 SQLite
```

---

## 五、整体路线图

```
Phase 1 (第1-2周)          Phase 2 (第3周)           Phase 3 (第4-6周)
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│ SQLite 本地迁移   │  →   │ 多用户认证 + 隔离 │  →   │ 加密 + 云同步    │
│                 │      │                 │      │                 │
│ 新建 SQLite     │      │ 注册/登录 API     │      │ SQLCipher 加密   │
│ Repository      │      │ JWT 认证中间件    │      │ 同步 REST API    │
│ 数据迁移脚本     │      │ user_id 隔离      │      │ 冲突解决         │
│ 配置开关        │      │ 用户表            │      │ 加密备份         │
└─────────────────┘      └─────────────────┘      └─────────────────┘
```

### 详细任务清单

**Phase 1: SQLite 本地（3-5 天）**

- [ ] 新建 `lib/repositories/sqlite_experiment.py`：实现 `AbstractExperimentRepository` 全部 9 个方法
- [ ] 新建 `lib/repositories/sqlite_analysis.py`
- [ ] 新建 `lib/repositories/sqlite_favorites.py`
- [ ] 新建 `lib/repositories/sqlite_update_log.py`
- [ ] `app.py` 添加 `STORAGE_BACKEND` 配置开关
- [ ] 编写 YAML → SQLite 数据迁移脚本
- [ ] 单元测试：验证 save/load 往返 + list_all 排序 + search + count
- [ ] 手动验证：启动应用，完整走一遍创建/编辑/搜索/删除流程

**Phase 2a: 认证（1-2 天）**

- [ ] 新建 `routes/api_auth.py`：`POST /api/auth/register` + `POST /api/auth/login`
- [ ] 新建 `lib/auth.py`：JWT 签发/验证函数 + bcrypt 哈希
- [ ] `require_auth` 装饰器
- [ ] 创建 `users` 表
- [ ] 前端：登录/注册页面（最小可用版本：两个输入框 + 按钮）

**Phase 2b: 隔离（1 天）**

- [ ] 所有表添加 `user_id` 列
- [ ] 所有 Repository 方法加 `user_id` 参数，SQL 查询加 `WHERE user_id = ?`
- [ ] `require_auth` 自动注入 `g.user_id`
- [ ] 验证：两个用户登录，互不可见对方数据

**Phase 3a: 加密（0.5 天）**

- [ ] 安装 `sqlcipher3`
- [ ] 从 `encryption_key` 派生密钥（PBKDF2）
- [ ] 数据库打开时传入 `PRAGMA key`
- [ ] 密钥安全存储方案（环境变量 / 系统 keychain）

**Phase 3b: 同步（5-7 天）**

- [ ] 云端 API 服务（Flask / FastAPI）
- [ ] `POST /sync`：接收增量 + 存储
- [ ] `GET /sync?since=`：返回增量
- [ ] 本地：定时推送 dirty 记录
- [ ] 本地：启动时拉取增量
- [ ] 冲突解决：LWW + 版本号乐观锁
- [ ] 备份：定期全量导出 → 加密 → 上传

---

## 六、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 数据库 | SQLite + WAL | Python 自带，零运维，单机十万用户够用 |
| 加密 | SQLCipher | SQLite 官方加密方案，换 import 即可 |
| 密码哈希 | bcrypt | 工业标准，抗暴力破解 |
| 认证 | JWT (HS256) | 无状态，无需服务端 session |
| HTTP 框架 | Flask（不变） | 当前项目已有 |
| 云端数据库 | SQLite（百人）→ PostgreSQL（万人+） | 渐进式 |
| 云端框架 | Flask / FastAPI | Flask 保持一致性 |
| 同步冲突 | Last Write Wins | 简单，后续可升级 CRDT |

---

## 八、待补充的工业化要点

当前设计可支撑**内部使用和小团队试点（<100 用户）**。要支撑十万用户和生产级云同步，以下各维度需后续补强。所有补充项均在现有 ABC 接口隔离架构上叠加，不改业务层代码。

### 8.1 Schema Migration

**当前**: `CREATE TABLE IF NOT EXISTS`，无版本管理。

**需补充**: `PRAGMA user_version` 版本号 + 迁移链。每次加字段、改索引、拆表，追加一个 `if current < N` 块。接口不变。

### 8.2 连接管理

**当前**: 四个 Repository 各自 `sqlite3.connect(db_path)`。

**需补充**: `app.py` 创建共享连接 → 注入所有 Repository。每个请求复用同一连接。

### 8.3 JSON 字段索引

**当前**: `tags`、`materials` 等 JSON 字段无索引。标签筛选走全表扫描。

**需补充**: `experiment_tags` 关联表做多对多索引，或 SQLite 生成列 + 索引。

### 8.4 离线队列

**当前**: `_dirty` 集合内存存储，程序退出丢失。

**需补充**: 数据库内 `sync_queue` 表。每次 `save()`/`delete()` 插入一条，推送成功后删除。

### 8.5 冲突合并策略（升级 LWW）

**当前**: 整条覆盖。两台设备编辑同一实验的不同字段 → 后同步覆盖先同步，丢失数据。

**需补充**: 字段级合并。每个可冲突字段加时间戳，逐字段比较取较新值。

### 8.6 同步分页

**当前**: 一次性返回全部增量。数据量大的时候响应体积过大。

**需补充**: 分页参数 `?limit=100&cursor={last_id}`。客户端逐页拉取。

### 8.7 请求限流

**当前**: 同步和认证端点无保护。

**需补充**: `Flask-Limiter`，登录 5/min，同步 30/min。

### 8.8 健康检查 + 慢查询日志 + 增量备份 + 生产安全

- `GET /api/health` 运维端点
- 查询耗时 > 100ms 打 warning
- 全量备份改为增量（按 `updated_at` 变化）
- JWT 无默认密钥 + Flask Talisman 强制 HTTPS + access/refresh token 分离

---

## 七、新增文件规划

### Phase 1

```
lib/
  repositories/
    sqlite_experiment.py          # SQLite 实验仓储实现
    sqlite_experiment_doc.md      # 说明文档
    sqlite_analysis.py            # SQLite 分析报告仓储实现
    sqlite_analysis_doc.md        # 说明文档
    sqlite_favorites.py           # SQLite 收藏/置顶仓储实现
    sqlite_favorites_doc.md       # 说明文档
    sqlite_update_log.py          # SQLite 更新日志仓储实现
    sqlite_update_log_doc.md      # 说明文档
```

### Phase 2

```
lib/
  auth.py                         # JWT 签发/验证 + bcrypt 密码哈希
  auth_doc.md                     # 说明文档
routes/
  api_auth.py                     # 注册/登录 REST 端点
  api_auth_doc.md                 # 说明文档
```

### Phase 3

```
routes/
  api_sync.py                     # 云端同步 REST 端点 (PUT /sync, GET /sync)
  api_sync_doc.md                 # 说明文档
sync/
  __init__.py
  __init___doc.md
  sync_service.py                 # 同步调度：定时推送 + 启动拉取 + 冲突解决
  sync_service_doc.md
  backup.py                       # 全量导出 + 加密压缩 + 上传
  backup_doc.md
```
| 同步冲突 | Last Write Wins | 简单，后续可升级 CRDT |
