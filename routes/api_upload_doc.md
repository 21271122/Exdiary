# routes/api_upload.py — 说明文档

## 文件作用摘要

图片上传 API 蓝图 `api_upload_bp`，URL 前缀 `/api`。处理 Quill 编辑器中的图片粘贴/拖拽上传。

---

## 代码块详细说明

### 路由函数

- `api_upload_image()` — POST `/api/upload-image`:
  - 请求: multipart/form-data — `image` 文件 + `exp_id` 字段 (默认 "_draft")
  - 流程:
    1. 创建 `uploads/{exp_id}/` 目录
    2. 保留原始扩展名（限制 6 种: png/jpg/jpeg/gif/webp/bmp, 其余默认 .png）
    3. 生成 `{uuid4().hex[:8]}{ext}` 文件名 → 保存
  - 返回: `{ok: True, url: "/uploads/{exp_id}/{filename}"}`
  - 图片迁移: 新建实验确认后，`ExperimentService.move_draft_images()` 将 `_draft/` 中的临时图片移入正式目录
