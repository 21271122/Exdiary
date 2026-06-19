# sync/backup.py — 说明文档

## 文件作用摘要

数据备份模块。定期全量导出 SQLite 数据 → gzip 压缩 → 加密 → 上传到云端存储。用户可随时下载加密备份并在本地还原。

---

## 代码块详细说明

### 模块级函数

- `export_all(repo: SqliteExperimentRepository) -> dict` — 导出全部数据为 JSON dict。包含 experiments、analyses、favorites、update_logs 四个键。每个键对应 `list_all_full()` 的返回值。
- `compress_and_encrypt(data: dict, encryption_key: str) -> bytes` — JSON 序列化 → gzip 压缩 → AES-256 加密。返回密文。加密用 `cryptography` 库的 Fernet 或 AES-GCM。
- `upload_backup(encrypted_data: bytes, user_id: str, api_base_url: str) -> None` — 上传加密备份到云端。文件名格式 `backups/{user_id}/{timestamp}.enc`。调用云端的 `PUT /api/sync/backup` 端点。
- `download_backup(user_id: str, filename: str, api_base_url: str) -> bytes` — 从云端下载加密备份。
- `decrypt_and_restore(encrypted_data: bytes, encryption_key: str) -> dict` — 解密 → 解压 → JSON 解析。反向操作 `compress_and_encrypt`。
- `run_backup(repo, encryption_key, user_id, api_base_url) -> None` — 一键备份。串联 export_all → compress_and_encrypt → upload_backup。
- `schedule_backup(repo, encryption_key, user_id, api_base_url, interval_hours: int = 24) -> None` — 定时备份。启动后台线程，每 `interval_hours` 小时执行一次 `run_backup`。
