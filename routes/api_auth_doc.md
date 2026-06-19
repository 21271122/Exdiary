# routes/api_auth.py — 说明文档

## 文件作用摘要

用户认证 API 蓝图 `api_auth_bp`，URL 前缀 `/api/auth`。处理注册、登录。所有响应为 JSON。

---

## 代码块详细说明

### 蓝图

- `api_auth_bp = Blueprint("api_auth", __name__)` — 注册在 `/api/auth` 下

### 路由函数

- `api_register()` — POST `/api/auth/register`
  - 请求体: `{username: str, password: str}`（password 最短 6 位）
  - 检查用户名是否已存在 → bcrypt 哈希密码 → 插入 `users` 表 → 返回 `{ok: true, user_id}` 或 `{ok: false, error}`
  - 使用 `lib/auth.py` 的 `hash_password`

- `api_login()` — POST `/api/auth/login`
  - 请求体: `{username: str, password: str}`
  - 查 `users` 表 → `verify_password` 验证 → 签发 JWT → 返回 `{ok: true, token, user_id}` 或 `{ok: false, error}`
  - 使用 `lib/auth.py` 的 `verify_password` 和 `create_token`
