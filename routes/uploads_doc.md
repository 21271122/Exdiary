# routes/uploads.py — 说明文档

## 文件作用摘要

上传文件静态服务路由蓝图 `uploads_bp`。为 `/uploads/` 路径下的用户上传图片提供 HTTP 访问。

---

## 代码块详细说明

### 路由函数

- `serve_upload(filepath)` — GET `/uploads/<path:filepath>`: 提供上传文件的静态访问
  - 实现: `send_from_directory(str(g.base_dir / "uploads"), filepath)`
  - 注意: Flask 的 `send_from_directory` 已包含路径安全校验
