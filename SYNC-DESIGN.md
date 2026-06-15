# Exdiary 云同步服务 — 实施方案

## 一、目标

为 Exdiary 提供跨设备实验数据同步能力。同一个用户在不同设备（实验室电脑、办公室电脑）上使用 Exdiary 时，实验记录自动保持一致。

**不做的事**：多人实时协作、文件级二进制同步、离线优先的 CRDT。

**核心约束**：服务端只做转发和存储，不访问用户实验数据明文逻辑；成本可控（年成本 ~350 元）；代码量小（~500 行 Python）。

---

## 二、架构

```
┌──────────────────────────┐       ┌──────────────────────────┐
│      设备 A (实验室)       │       │      设备 B (办公室)       │
│                           │       │                           │
│  Exdiary Flask App        │       │  Exdiary Flask App        │
│  ┌─────────────────────┐  │       │  ┌─────────────────────┐  │
│  │ experiments/*.yaml  │  │       │  │ experiments/*.yaml  │  │
│  │ 本地 YAML 文件        │  │       │  │ 本地 YAML 文件        │  │
│  └─────────┬───────────┘  │       │  └─────────┬───────────┘  │
│            │              │       │            │              │
│  ┌─────────▼───────────┐  │       │  ┌─────────▼───────────┐  │
│  │   SyncClient        │  │       │  │   SyncClient        │  │
│  │  - Push (版本号比对) │  │       │  │  - Pull (时间戳拉取) │  │
│  │  - 待推送队列        │  │       │  │  - 冲突提示          │  │
│  │  - 后台线程          │  │       │  │  - 后台线程          │  │
│  └─────────┬───────────┘  │       │  └─────────┬───────────┘  │
│            │              │       │            │              │
└────────────┼──────────────┘       └────────────┼──────────────┘
             │                                   │
             │        HTTPS + JWT Token          │
             │                                   │
    ┌────────▼───────────────────────────────────▼────────┐
    │                 Sync API Server                      │
    │                                                      │
    │  Flask 应用 (独立进程，部署在云服务器)                  │
    │  ┌────────────────────────────────────────────────┐  │
    │  │  /api/auth/register    邮箱注册                  │  │
    │  │  /api/auth/login       登录，返回 JWT             │  │
    │  │  /api/sync/push        推送实验 (版本号比对)       │  │
    │  │  /api/sync/pull        拉取实验 (时间戳)          │  │
    │  │  /api/sync/upload      上传图片                   │  │
    │  │  /api/sync/download    下载图片                   │  │
    │  └────────────────────────────────────────────────┘  │
    │                                                      │
    │  SQLite 数据库                                       │
    │  ├─ users        用户表                               │
    │  ├─ experiments  实验数据表 (user_id, exp_id,         │
    │  │               version, data_json, updated_at)      │
    │  └─ images       图片元数据表 (user_id, exp_id,       │
    │                  filename, stored_path)               │
    │                                                      │
    │  磁盘存储                                             │
    │  ├─ data/sync.db       SQLite 数据库文件               │
    │  ├─ uploads/           图片文件                        │
    │  └─ backups/           每日备份 (SQLite dump)          │
    └──────────────────────────────────────────────────────┘
```

---

## 三、数据模型

### 3.1 SQLite 表结构

```sql
-- 用户表
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,          -- UUID
    email       TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,           -- bcrypt
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    sync_enabled INTEGER NOT NULL DEFAULT 1
);

-- 实验数据表
CREATE TABLE IF NOT EXISTS experiments (
    user_id     TEXT NOT NULL,
    exp_id      TEXT NOT NULL,             -- EXP-YYYY-NNN
    version     INTEGER NOT NULL DEFAULT 0,
    data_json   TEXT NOT NULL,             -- 完整实验 dict 的 JSON 序列化
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, exp_id)
);

-- 图片元数据表
CREATE TABLE IF NOT EXISTS images (
    user_id      TEXT NOT NULL,
    exp_id       TEXT NOT NULL,
    filename     TEXT NOT NULL,             -- uuid8.png
    content_type TEXT NOT NULL DEFAULT 'image/png',
    file_size    INTEGER NOT NULL DEFAULT 0,
    stored_path  TEXT NOT NULL,             -- uploads/{user_id}/{exp_id}/{filename}
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, exp_id, filename)
);
```

**设计要点**：

1. **不拆分实验字段**。整个实验 dict 序列化为 JSON 存入 `data_json`。同步粒度是"一条实验"，不是"一个字段"。这避免了服务端需要理解 16 字段 Schema，服务端只做存储和版本比对。

2. **版本号单调递增**。每次成功的 push 将 `version` 递增 1。版本号用于冲突检测，不做向量时钟或因果追踪。

3. **图片分离存储**。YAML 中的图片引用路径（如 `/uploads/EXP-001/abc.png`）在同步时不修改。服务端图片以 `{user_id}/{exp_id}/{filename}` 路径存储，客户端 pull 时按需下载。

4. **为什么用 SQLite 而不是 MySQL/PostgreSQL**：
   - 部署零成本：一个文件，Flask 进程启动时自动创建
   - 几百用户的 CRUD 完全够用
   - 备份简单：cp 一个文件
   - 迁移简单：将来想换 PostgreSQL，一个导出脚本即可

### 3.2 客户端本地状态

客户端在 `experiments/` 目录下维护一个轻量级同步状态文件：

```json
// experiments/_sync_state.json
{
    "user_id": "abc123-uuid",
    "server_url": "https://sync.exdiary.app",
    "last_pull_at": "2026-06-13T14:20:00",
    "pending_push": [
        {"exp_id": "EXP-2026-026", "local_version": 2}
    ],
    "device_name": "实验室台式机"
}
```

这个文件不在 git 中（加入 `.gitignore`），只用于本地同步状态记录。

---

## 四、同步协议

### 4.1 Pull（拉取远程更新）

客户端发起，告诉服务端"我上次拉到什么时间了"：

```
POST /api/sync/pull
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
    "last_pull_at": "2026-06-13T10:00:00"
}
```

服务端处理：
1. 从 JWT 解析 user_id
2. 查询 `SELECT * FROM experiments WHERE user_id = ? AND updated_at > ?`
3. 返回所有在此时间之后更新的实验

```
HTTP 200
{
    "ok": true,
    "changes": [
        {
            "exp_id": "EXP-2026-003",
            "version": 5,
            "data": { /* 完整的实验 dict */ },
            "updated_at": "2026-06-13T11:20:00"
        }
    ],
    "server_time": "2026-06-13T14:30:00"
}
```

客户端处理（`sync_client.py` 中的 `_do_pull()`）：

```python
def _do_pull(self):
    """从服务端拉取更新，合并到本地 YAML 文件。"""
    state = self._load_state()
    resp = self._request("POST", "/api/sync/pull", {
        "last_pull_at": state.get("last_pull_at", "2000-01-01T00:00:00")
    })
    if not resp.get("ok"):
        return

    for change in resp.get("changes", []):
        exp_id = change["exp_id"]
        remote_version = change["version"]
        remote_data = change["data"]

        # 读本地版本
        local_exp = self.store.load(exp_id)
        local_version = local_exp.get("_sync_version", 0) if local_exp else 0

        if local_exp is None:
            # 本地没有 → 直接写入
            self._write_local(exp_id, remote_data, remote_version)
        elif local_version < remote_version:
            # 远程更新 → 覆盖本地
            self._write_local(exp_id, remote_data, remote_version)
        elif local_version > remote_version:
            # 本地有未推送的修改 → 保留本地，标记冲突待处理
            self._mark_conflict(exp_id, local_exp, remote_data)
        # local_version == remote_version: 无需操作

    # 更新 last_pull_at
    state["last_pull_at"] = resp.get("server_time")
    self._save_state(state)
```

### 4.2 Push（推送本地修改）

客户端发起，把本地修改过的实验发上去：

```
POST /api/sync/push
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
    "changes": [
        {
            "exp_id": "EXP-2026-003",
            "base_version": 4,
            "data": { /* 完整的实验 dict */ }
        }
    ]
}
```

服务端处理（逐条）：

```python
def _handle_push(user_id, changes):
    results = []
    for change in changes:
        exp_id = change["exp_id"]
        client_version = change["base_version"]
        new_data = change["data"]

        # 查服务端当前版本
        row = db.execute(
            "SELECT version, data_json FROM experiments WHERE user_id=? AND exp_id=?",
            (user_id, exp_id)
        ).fetchone()

        if row is None:
            # 新实验首次 push
            db.execute(
                "INSERT INTO experiments (user_id, exp_id, version, data_json, updated_at) "
                "VALUES (?, ?, 1, ?, datetime('now'))",
                (user_id, exp_id, json.dumps(new_data, ensure_ascii=False))
            )
            results.append({"exp_id": exp_id, "status": "accepted", "new_version": 1})

        elif row["version"] == client_version:
            # 版本匹配 → 接受
            new_version = row["version"] + 1
            db.execute(
                "UPDATE experiments SET version=?, data_json=?, updated_at=datetime('now') "
                "WHERE user_id=? AND exp_id=?",
                (new_version, json.dumps(new_data, ensure_ascii=False), user_id, exp_id)
            )
            results.append({"exp_id": exp_id, "status": "accepted", "new_version": new_version})

        else:
            # 版本冲突 → 拒绝，返回服务端最新版本
            results.append({
                "exp_id": exp_id,
                "status": "conflict",
                "server_version": row["version"],
                "server_data": json.loads(row["data_json"])
            })
    
    return {"ok": True, "results": results}
```

客户端处理冲突：

```python
def _handle_push_result(self, results):
    for item in results:
        if item["status"] == "accepted":
            # 更新本地版本号
            exp = self.store.load(item["exp_id"])
            if exp:
                exp["_sync_version"] = item["new_version"]
                self.store.save(exp)
            # 从待推送队列移除
            self._remove_from_pending(item["exp_id"])

        elif item["status"] == "conflict":
            # 保存远程版本到临时文件
            conflict_path = self.store.path / f"{item['exp_id']}.conflict.yaml"
            with open(conflict_path, "w", encoding="utf-8") as f:
                yaml.dump(item["server_data"], f, allow_unicode=True)
            # 通知前端
            self._notify_conflict(item["exp_id"])
```

### 4.3 冲突处理 UI

当前端检测到冲突（`_sync_state.json` 中有 `conflicts` 字段），在页面上显示提示：

```
⚠️ EXP-2026-008 在另一台设备上被修改过，存在冲突。
  [保留本地版本]  [使用远程版本]  [手动合并]
```

实现为 `view.html` 中插入一个冲突提示条，三个按钮：
- "保留本地" → 删除 `.conflict.yaml`，将本地版本 push（`base_version` 设为服务端返回的 `server_version`）
- "使用远程" → 删除本地文件，写入远程版本
- "手动合并" → 将两边数据并排展示在对比视图中，用户编辑后保存

---

## 五、图片同步

### 5.1 上传

客户端在 push 实验后，检查 `data_json` 中引用的图片路径，如果本地存在且服务端没有，上传：

```
POST /api/sync/upload
Authorization: Bearer <jwt_token>
Content-Type: multipart/form-data

exp_id: EXP-2026-003
file: <binary>
filename: abc123.png
```

服务端保存到 `uploads/{user_id}/{exp_id}/{filename}`，在 `images` 表中记录元数据。

### 5.2 下载

客户端在 pull 实验后，检查 `data_json` 中引用的图片路径在本地是否存在。如果不存在，下载：

```
GET /api/sync/download/<exp_id>/<filename>
Authorization: Bearer <jwt_token>
```

服务端从 `uploads/{user_id}/{exp_id}/{filename}` 读取并返回。

### 5.3 图片同步的容错

- 图片上传失败 → 不影响实验数据同步。下次 push 时重试。
- 图片下载失败 → 不影响实验数据显示。图片位置显示占位符。
- 图片重复上传 → 服务端按 `(user_id, exp_id, filename)` 去重，幂等。

---

## 六、认证系统

### 6.1 注册

```
POST /api/auth/register
Content-Type: application/json

{
    "email": "user@example.com",
    "password": "min-8-chars"
}
```

服务端：
1. 校验 email 格式、密码长度 ≥ 8
2. 检查 email 是否已注册
3. `password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())`
4. `user_id = uuid.uuid4().hex`
5. 写入 users 表
6. 返回 JWT token（有效期 30 天）

### 6.2 登录

```
POST /api/auth/login
Content-Type: application/json

{
    "email": "user@example.com",
    "password": "..."
}
```

返回 JWT token。

### 6.3 JWT 中间件

```python
import jwt
from functools import wraps
from flask import request, g

JWT_SECRET = os.environ.get("JWT_SECRET", os.urandom(32).hex())
JWT_EXPIRY_DAYS = 30

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"ok": False, "error": "未登录"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            g.user_id = payload["user_id"]
        except jwt.ExpiredSignatureError:
            return jsonify({"ok": False, "error": "登录已过期"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"ok": False, "error": "无效的登录凭证"}), 401
        return f(*args, **kwargs)
    return decorated
```

### 6.4 不做的事

- 不做邮箱验证（发送验证邮件需要邮件服务，成本增加，且对个人开发者、几百用户规模来说不是必要的）
- 不做 OAuth / 第三方登录
- 不做手机号验证
- 不做验证码

用户量大了再加这些，初期只验证"邮箱格式合法 + 密码 ≥ 8 位"。

---

## 七、客户端同步逻辑

### 7.1 集成位置

同步逻辑作为 Exdiary Flask App 内的一个模块，不是独立进程：

```
lib/
  sync_client.py        # 新增：同步客户端
  ...
```

在 `app.py` 的启动流程中初始化同步客户端：

```python
# app.py 中新增
from lib.sync_client import SyncClient

sync_client = None
if config.get("SYNC_ENABLED", "false").lower() == "true":
    sync_client = SyncClient(
        store=store,
        server_url=config.get("SYNC_SERVER_URL", ""),
        auth_token=config.get("SYNC_TOKEN", ""),
    )
    sync_client.start()  # 启动后台同步线程
```

### 7.2 SyncClient 实现

```python
# lib/sync_client.py
import json
import time
import threading
import requests
from pathlib import Path

class SyncClient:
    """Exdiary 同步客户端。在后台线程中定期拉取远程更新，
    在每次实验保存时推送本地修改。"""

    def __init__(self, store, server_url, auth_token):
        self.store = store
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self._state_path = Path(store.path) / "_sync_state.json"
        self._running = False
        self._thread = None
        self._conflicts = []       # 当前未解决的冲突列表
        self._callbacks = []       # 冲突通知回调

    # ---- 生命周期 ----

    def start(self):
        """启动后台同步线程，每 60 秒 pull 一次。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while self._running:
            try:
                self._do_pull()
            except Exception:
                pass  # 静默重试，不影响用户
            time.sleep(60)

    # ---- 状态管理 ----

    def _load_state(self):
        if self._state_path.exists():
            with open(self._state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"last_pull_at": "2000-01-01T00:00:00", "pending_push": []}

    def _save_state(self, state):
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    # ---- HTTP 请求 ----

    def _request(self, method, path, data=None, files=None):
        url = f"{self.server_url}{path}"
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        elif method == "POST":
            if files:
                resp = requests.post(url, headers=headers, files=files, timeout=60)
            else:
                resp = requests.post(url, headers=headers, json=data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ---- Push ----

    def push_experiment(self, exp_id):
        """保存实验后立即调用。将本地修改推送到服务端。"""
        exp = self.store.load(exp_id)
        if not exp:
            return
        local_version = exp.get("_sync_version", 0)
        push_data = {
            "changes": [{
                "exp_id": exp_id,
                "base_version": local_version,
                "data": self._sanitize_for_sync(exp),
            }]
        }
        try:
            result = self._request("POST", "/api/sync/push", push_data)
            self._handle_push_result(result.get("results", []))
        except requests.RequestException:
            # 网络不可达 → 加入待推送队列
            state = self._load_state()
            pending = state.get("pending_push", [])
            if exp_id not in [p["exp_id"] for p in pending]:
                pending.append({"exp_id": exp_id, "local_version": local_version})
            state["pending_push"] = pending
            self._save_state(state)

    def _handle_push_result(self, results):
        """处理 push 返回结果：接受、冲突、或加入待重试队列。"""
        for item in results:
            if item["status"] == "accepted":
                exp = self.store.load(item["exp_id"])
                if exp:
                    exp["_sync_version"] = item["new_version"]
                    self.store.save(exp)
                self._remove_from_pending(item["exp_id"])
            elif item["status"] == "conflict":
                self._mark_conflict(item["exp_id"], item.get("server_data"))

    # ---- Pull ----

    def _do_pull(self):
        """从服务端拉取更新。先处理待推送队列，再拉取。"""
        state = self._load_state()
        # 先推待推送队列中的实验
        for pending in list(state.get("pending_push", [])):
            try:
                self.push_experiment(pending["exp_id"])
            except Exception:
                break  # 推不上去就等下次

        # 拉取远程更新
        resp = self._request("POST", "/api/sync/pull", {
            "last_pull_at": state.get("last_pull_at", "2000-01-01T00:00:00")
        })
        if not resp.get("ok"):
            return

        for change in resp.get("changes", []):
            exp_id = change["exp_id"]
            remote_version = change["version"]
            remote_data = change["data"]

            local_exp = self.store.load(exp_id)
            local_version = local_exp.get("_sync_version", 0) if local_exp else 0

            if local_exp is None or local_version < remote_version:
                self._write_local(exp_id, remote_data, remote_version)
            elif local_version > remote_version:
                # 本地有未推送修改，但远端也有新版本 → 冲突
                self._mark_conflict(exp_id, remote_data)

        state["last_pull_at"] = resp.get("server_time")
        self._save_state(state)

    # ---- 辅助方法 ----

    def _sanitize_for_sync(self, exp):
        """移除内部字段（_sync_version）后返回用于同步的 dict。"""
        return {k: v for k, v in exp.items() if not k.startswith("_sync")}

    def _write_local(self, exp_id, data, version):
        """将远程数据写入本地 YAML 文件。"""
        data["_sync_version"] = version
        self.store.save(data)

    def _mark_conflict(self, exp_id, server_data):
        """标记一条实验存在冲突。"""
        self._conflicts.append({
            "exp_id": exp_id,
            "server_data": server_data,
            "detected_at": time.time(),
        })
        # 通知前端（如果已注册回调）
        for cb in self._callbacks:
            cb(exp_id)

    def _remove_from_pending(self, exp_id):
        state = self._load_state()
        state["pending_push"] = [p for p in state.get("pending_push", []) 
                                 if p["exp_id"] != exp_id]
        self._save_state(state)

    def on_conflict(self, callback):
        """注册冲突通知回调。callback(exp_id)。"""
        self._callbacks.append(callback)

    def get_conflicts(self):
        """返回当前未解决的冲突列表。"""
        return list(self._conflicts)

    def resolve_conflict(self, exp_id, resolution):
        """解决冲突。resolution: 'keep_local' | 'use_remote' | data_dict。"""
        self._conflicts = [c for c in self._conflicts if c["exp_id"] != exp_id]
        if resolution == "use_remote":
            # 找到冲突记录中的 server_data，写入本地
            pass  # 需要保存冲突时存下 server_data
        # 'keep_local': 强制 push 本地版本
        # data_dict: 用户手动合并的结果
```

### 7.3 在 app.py 中挂载

```python
# app.py 中新增

# 启动同步客户端
sync_client = None
sync_config = load_sync_config()  # 从 config.yaml 读取同步相关配置
if sync_config.get("enabled"):
    sync_client = SyncClient(
        store=store,
        server_url=sync_config["server_url"],
        auth_token=sync_config.get("token", ""),
    )
    sync_client.start()

# 在保存实验的路由中调用 push
@app.route("/experiments/<exp_id>/save-json", methods=["POST"])
def save_experiment_json(exp_id):
    # ... 现有保存逻辑 ...
    store.update(exp_id, data)
    
    # 新增：触发同步推送
    if sync_client:
        sync_client.push_experiment(exp_id)
    
    return jsonify({"ok": True})

# 冲突查询 API（前端轮询或 WebSocket 通知）
@app.route("/api/sync/conflicts")
def api_sync_conflicts():
    if not sync_client:
        return jsonify({"conflicts": []})
    return jsonify({"conflicts": sync_client.get_conflicts()})
```

---

## 八、服务端实现

### 8.1 目录结构

```
sync-server/
  server.py            # Flask 应用主文件 (~300 行)
  requirements.txt     # flask, bcrypt, pyjwt, requests
  data/
    sync.db            # SQLite 数据库（首次运行时自动创建）
  uploads/             # 图片文件存储
  backups/             # 每日备份
```

### 8.2 server.py 完整框架

```python
"""
Exdiary Sync API Server
~~~~~~~~~~~~~~~~~~~~~~~~
Flask 应用，提供用户认证和实验数据同步的 API 接口。
"""

import os
import json
import sqlite3
import uuid
import bcrypt
import jwt
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, request, jsonify, g, send_from_directory

# ---- 配置 ----
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "sync.db"
UPLOAD_DIR = BASE_DIR / "uploads"
BACKUP_DIR = BASE_DIR / "backups"
JWT_SECRET = os.environ.get("JWT_SECRET", os.urandom(32).hex())
JWT_EXPIRY_DAYS = 30
MAX_PUSH_BATCH = 50   # 每次 push 最多 50 条实验
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB 文件大小限制

# ---- 应用初始化 ----
app = Flask(__name__)

def init_db():
    """初始化数据库和目录。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                email       TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                sync_enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS experiments (
                user_id     TEXT NOT NULL,
                exp_id      TEXT NOT NULL,
                version     INTEGER NOT NULL DEFAULT 0,
                data_json   TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, exp_id)
            );
            CREATE TABLE IF NOT EXISTS images (
                user_id      TEXT NOT NULL,
                exp_id       TEXT NOT NULL,
                filename     TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'image/png',
                file_size    INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, exp_id, filename)
            );
        """)
        conn.commit()

init_db()

# ---- 数据库工具 ----

def get_db():
    """获取数据库连接（每个请求复用同一个连接）。"""
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# ---- 认证中间件 ----

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"ok": False, "error": "未登录"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            g.user_id = payload["user_id"]
        except jwt.ExpiredSignatureError:
            return jsonify({"ok": False, "error": "登录已过期，请重新登录"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"ok": False, "error": "无效的登录凭证"}), 401
        return f(*args, **kwargs)
    return decorated

# ---- 认证路由 ----

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "无效的请求数据"}), 400

    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    # 基本校验
    if not email or "@" not in email or "." not in email:
        return jsonify({"ok": False, "error": "请输入有效的邮箱地址"}), 400
    if len(password) < 8:
        return jsonify({"ok": False, "error": "密码至少需要 8 位"}), 400

    db = get_db()
    existing = db.execute("SELECT user_id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        return jsonify({"ok": False, "error": "该邮箱已被注册"}), 409

    user_id = uuid.uuid4().hex
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    db.execute(
        "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
        (user_id, email, password_hash)
    )
    db.commit()

    token = _make_token(user_id)
    return jsonify({"ok": True, "token": token, "user_id": user_id})


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "无效的请求数据"}), 400

    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user:
        return jsonify({"ok": False, "error": "邮箱未注册"}), 401

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"ok": False, "error": "密码错误"}), 401

    token = _make_token(user["user_id"])
    return jsonify({"ok": True, "token": token, "user_id": user["user_id"]})


def _make_token(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@app.route("/api/auth/me", methods=["GET"])
@require_auth
def me():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE user_id = ?", (g.user_id,)).fetchone()
    if not user:
        return jsonify({"ok": False, "error": "用户不存在"}), 404
    return jsonify({
        "ok": True,
        "user_id": user["user_id"],
        "email": user["email"],
        "created_at": user["created_at"],
    })

# ---- 同步路由 ----

@app.route("/api/sync/push", methods=["POST"])
@require_auth
def sync_push():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "无效的请求数据"}), 400

    changes = data.get("changes", [])
    if not changes or not isinstance(changes, list):
        return jsonify({"ok": False, "error": "changes 不能为空"}), 400
    if len(changes) > MAX_PUSH_BATCH:
        return jsonify({"ok": False, "error": f"每次最多推送 {MAX_PUSH_BATCH} 条"}), 400

    db = get_db()
    results = []

    for change in changes:
        exp_id = (change.get("exp_id") or "").strip()
        base_version = change.get("base_version", 0)
        new_data = change.get("data")

        if not exp_id or not isinstance(new_data, dict):
            results.append({"exp_id": exp_id or "(空)", "status": "rejected",
                          "reason": "exp_id 或 data 无效"})
            continue

        row = db.execute(
            "SELECT version FROM experiments WHERE user_id = ? AND exp_id = ?",
            (g.user_id, exp_id)
        ).fetchone()

        if row is None:
            # 新实验
            db.execute(
                "INSERT INTO experiments (user_id, exp_id, version, data_json, updated_at) "
                "VALUES (?, ?, 1, ?, datetime('now'))",
                (g.user_id, exp_id, json.dumps(new_data, ensure_ascii=False))
            )
            results.append({"exp_id": exp_id, "status": "accepted", "new_version": 1})
        elif row["version"] == base_version:
            # 版本匹配
            new_version = row["version"] + 1
            db.execute(
                "UPDATE experiments SET version = ?, data_json = ?, updated_at = datetime('now') "
                "WHERE user_id = ? AND exp_id = ?",
                (new_version, json.dumps(new_data, ensure_ascii=False), g.user_id, exp_id)
            )
            results.append({"exp_id": exp_id, "status": "accepted", "new_version": new_version})
        else:
            # 版本冲突
            conflict_row = db.execute(
                "SELECT version, data_json FROM experiments WHERE user_id = ? AND exp_id = ?",
                (g.user_id, exp_id)
            ).fetchone()
            results.append({
                "exp_id": exp_id,
                "status": "conflict",
                "server_version": conflict_row["version"],
                "server_data": json.loads(conflict_row["data_json"]),
            })

    db.commit()
    return jsonify({"ok": True, "results": results})


@app.route("/api/sync/pull", methods=["POST"])
@require_auth
def sync_pull():
    data = request.get_json() or {}
    last_pull_at = data.get("last_pull_at", "2000-01-01T00:00:00")

    db = get_db()
    rows = db.execute(
        "SELECT exp_id, version, data_json, updated_at FROM experiments "
        "WHERE user_id = ? AND updated_at > ? ORDER BY updated_at ASC",
        (g.user_id, last_pull_at)
    ).fetchall()

    changes = []
    for row in rows:
        changes.append({
            "exp_id": row["exp_id"],
            "version": row["version"],
            "data": json.loads(row["data_json"]),
            "updated_at": row["updated_at"],
        })

    return jsonify({
        "ok": True,
        "changes": changes,
        "server_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
    })


# ---- 图片上传/下载 ----

@app.route("/api/sync/upload", methods=["POST"])
@require_auth
def sync_upload():
    exp_id = request.form.get("exp_id", "").strip()
    file = request.files.get("file")
    if not exp_id or not file:
        return jsonify({"ok": False, "error": "缺少 exp_id 或 file"}), 400

    filename = file.filename or f"{uuid.uuid4().hex[:8]}.png"
    ext = Path(filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        ext = ".png"
        filename = f"{uuid.uuid4().hex[:8]}{ext}"

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_FILE_SIZE:
        return jsonify({"ok": False, "error": "文件大小超过 10MB 限制"}), 400

    user_dir = UPLOAD_DIR / g.user_id / exp_id
    user_dir.mkdir(parents=True, exist_ok=True)
    file.save(str(user_dir / filename))

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO images (user_id, exp_id, filename, content_type, file_size) "
        "VALUES (?, ?, ?, ?, ?)",
        (g.user_id, exp_id, filename, file.content_type or "image/png", file_size)
    )
    db.commit()

    return jsonify({"ok": True, "filename": filename})


@app.route("/api/sync/download/<exp_id>/<filename>", methods=["GET"])
@require_auth
def sync_download(exp_id, filename):
    # 安全校验：防止路径穿越
    if ".." in exp_id or ".." in filename or "/" in exp_id or "\\" in exp_id:
        return jsonify({"ok": False, "error": "非法路径"}), 400

    file_path = UPLOAD_DIR / g.user_id / exp_id / filename
    if not file_path.exists():
        return jsonify({"ok": False, "error": "文件不存在"}), 404

    return send_from_directory(str(UPLOAD_DIR / g.user_id / exp_id), filename)


# ---- 健康检查 ----

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "status": "running"})

# ---- 启动 ----

if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", "5001"))
    print(f"Exdiary Sync API Server starting on port {port}")
    print(f"Database: {DB_PATH}")
    print(f"Uploads:  {UPLOAD_DIR}")
    app.run(host="0.0.0.0", port=port, debug=False)
```

### 8.3 requirements.txt

```
flask>=3.0
bcrypt>=4.0
pyjwt>=2.8
gunicorn>=21.2
```

### 8.4 部署

在云服务器（2核4G 轻量应用服务器，CentOS/Ubuntu）上：

```bash
# 1. 安装依赖
cd /opt/exdiary-sync
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 设置环境变量
export JWT_SECRET=$(python3 -c "import os; print(os.urandom(32).hex())")
export PORT=5001

# 3. 用 gunicorn 启动（生产环境）
gunicorn -w 2 -b 0.0.0.0:5001 server:app --daemon --access-logfile /var/log/exdiary-sync.log

# 4. 配置 Nginx 反向代理 + HTTPS
# /etc/nginx/sites-available/exdiary-sync
server {
    listen 443 ssl;
    server_name sync.exdiary.app;

    ssl_certificate     /etc/letsencrypt/live/sync.exdiary.app/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sync.exdiary.app/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 20m;
    }
}

# 5. 配置每日备份（crontab）
# 0 3 * * * cp /opt/exdiary-sync/data/sync.db /opt/exdiary-sync/backups/sync-$(date +\%Y\%m\%d).db
```

---

## 九、隐私与安全

### 9.1 数据传输

- 全链路 HTTPS（Let's Encrypt 免费证书）
- JWT token 30 天过期，过期后需重新登录
- 不传输明文密码（注册/登录时密码在 HTTPS 加密通道中传输，服务端 bcrypt 哈希存储）

### 9.2 数据存储

- 实验数据以 JSON 明文存储在服务端 SQLite 数据库中（为了同步必须可读）
- 图片以原始格式存储在服务端磁盘
- **不做端到端加密**（理由：如果用户忘记密码，E2EE 意味着数据永久不可恢复，对实验数据来说风险不可接受）

### 9.3 隐私声明

在注册页面和 README 中明确声明：

> 你的数据如何被处理：
> - 传输过程中全程加密（HTTPS）
> - 存储在服务端时可被服务端程序读取（为了提供同步功能）
> - 我们不会查看、使用、出售、或分享你的实验数据
> - 你可以随时导出全部数据并删除账户

### 9.4 SQL 注入防护

所有数据库查询使用参数化查询（`?` 占位符），不使用字符串拼接。Flask 的 `jsonify()` 自动对输出进行 HTML 转义。

---

## 十、与现有代码的集成点

### 10.1 配置文件新增项 (`config.yaml`)

```yaml
# 在现有配置项基础上新增
SYNC_ENABLED: "false"
SYNC_SERVER_URL: ""
SYNC_TOKEN: ""
```

### 10.2 设置页面新增 (`templates/settings.html`)

在设置页面的"Server"区域下新增云同步配置：

```html
<hr style="border:3px solid var(--black);margin:1.5rem 0">
<h3>Cloud Sync</h3>
<label>
  <strong>Enable Sync</strong>
  <small>Sync experiments across devices</small>
</label>
<input type="checkbox" name="SYNC_ENABLED" value="true">

<label>
  <strong>Sync Server</strong>
  <small>Server URL, e.g. https://sync.exdiary.app</small>
</label>
<input type="text" name="SYNC_SERVER_URL" placeholder="https://sync.exdiary.app">

<label>
  <strong>Account</strong>
  <small>Login to your sync account</small>
</label>
<div id="sync-auth-area">
  <!-- JS 动态渲染登录/注册表单 -->
</div>
```

### 10.3 实验保存钩子 (`app.py`)

在 `save_experiment_json`、`api_parse_confirm`、`api_agent_confirm` 等保存路由中增加：

```python
if sync_client:
    sync_client.push_experiment(exp_id)
```

### 10.4 冲突指示器（前端）

在 `base.html` 或 `view.html` 的导航栏附近增加一个冲突指示器：

```html
<div id="sync-conflict-indicator" style="display:none">
  <span style="color:var(--red);font-weight:700">⚠ Sync Conflict</span>
</div>
```

JavaScript 定期轮询 `/api/sync/conflicts`，有冲突时显示指示器并弹出处理界面。

---

## 十一、复杂度边界

| 需要实现的 | 不需要实现的 |
|-----------|-------------|
| SQLite 三张表 | 数据库集群 / 读写分离 |
| Push + Pull 两个接口 | WebSocket 实时推送 |
| 版本号冲突检测 | OT / CRDT 操作转换 |
| JWT 鉴权 | OAuth / SSO / 第三方登录 |
| 邮箱 + 密码注册 | 手机验证码 / 邮箱验证 |
| HTTPS (Let's Encrypt) | WAF / DDoS 防护 |
| 后台 sync 线程 | 独立守护进程 |
| 每日备份 (cp SQLite) | 异地灾备 / 多活 |
| ~500 行 Python (server + client) | 微服务 / 消息队列 / K8s |

总计新增代码量约 ~700 行 Python（~350 行服务端 + ~200 行客户端 + ~100 行前端 JS + ~50 行配置和 HTML），即可实现完整的跨设备同步能力。
