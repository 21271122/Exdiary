---
name: git-sync
description: 将当前更改提交并推送到 GitHub 远程仓库。
---

# Git 同步 — Exdiary 项目

## 项目关键信息

- **项目路径**: 当前目录
- **虚拟环境**: conda env `exdiary`（路径 `D:\conda_envs\exdiary`）
- **启动方式**: `python app.py`
- **Git 远程**: `https://github.com/21271122/Exdiary.git`
- **主分支**: `main`
- **用户**: `21271122`（邮箱 `2937595110@QQ.com`）

## Git 推送（需代理）

GitHub 连接需要代理，代理地址 `http://127.0.0.1:17891`。

推送前先确认代理已配置：

```bash
git config --global http.proxy http://127.0.0.1:17891
git config --global https.proxy http://127.0.0.1:17891
```

然后执行推送：

```bash
cd "D:\Projects\Exdiarys\Exdiary-v1.1" && git push
```

## 敏感文件（已 gitignore，不可提交）

- `config.yaml` — 包含 DeepSeek API Key
- `experiments/` — 实验数据
- `uploads/` — 上传图片
- `__pycache__/`, `*.pyc`
-更新过程中其它敏感文件

## 项目技术栈

- Python 3.12 + Flask
- 前端：Pico CSS + Quill 富文本编辑器 + htmx + marked
- AI：DeepSeek API（OpenAI 兼容，function calling + chat）
- 存储：YAML 文件系统
- 模板：Jinja2

## 注意事项

- 本项目无许可证，代码仅供学习交流
- 所有输出使用中文
- Git 代理设置是 `--global`，会影响其他仓库
- 如果不希望全局走代理，推送后可执行 `git config --global --unset http.proxy` 和 `git config --global --unset https.proxy` 取消
