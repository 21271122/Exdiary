# lib/auth.py — 说明文档

## 文件作用摘要

认证模块。处理 JWT token 签发/验证、bcrypt 密码哈希、认证中间件装饰器。被 `routes/api_auth.py` 和所有需认证的路由使用。

---

## 代码块详细说明

### 模块级常量

- `SECRET_KEY: str` — JWT 签名密钥。从环境变量 `JWT_SECRET` 读取，不存在则用默认值（开发用）。生产环境必须设置。

### 模块级函数

- `hash_password(password: str) -> str` — bcrypt 哈希密码。自动加 salt。返回哈希字符串。
- `verify_password(password: str, password_hash: str) -> bool` — 验证明文密码和哈希是否匹配。
- `create_token(user_id: str, expires_delta: timedelta = 24h) -> str` — 签发 JWT token。payload 含 `user_id` + `exp`（过期时间）。返回 token 字符串。
- `decode_token(token: str) -> dict` — 验证并解码 JWT。成功返回 payload dict（含 user_id）；失败抛 `jwt.InvalidTokenError`。
- `require_auth(f: Callable) -> Callable` — Flask 路由装饰器。从 `Authorization: Bearer {token}` Header 中提取 token → 解码得到 user_id → 注入 `flask.g.user_id`。token 无效或缺失返回 401。

### 被调用情况

- `routes/api_auth.py` — 注册和登录端点调用 `hash_password` / `verify_password` / `create_token`
- 所有数据路由 — 通过 `@require_auth` 装饰器自动注入 `g.user_id`
