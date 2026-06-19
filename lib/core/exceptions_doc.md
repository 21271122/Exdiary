# lib/core/exceptions.py — 说明文档

## 文件作用摘要

Exdiary 统一异常类定义。提供 5 层异常体系，便于上层代码按异常类型做差异化处理。

> **当前状态**: 所有 5 个异常类均已定义，但**均未被项目中任何代码实际使用或导入**。全局 Grep 搜索 `from lib.core.exceptions import` 和 `from lib.core import exceptions` 均为零匹配。代码中当前仍使用标准异常（`RuntimeError`, `ValueError`）和 try-except 兜底。这些类是为未来的精细化异常处理预留的。

---

## 代码块详细说明

### `ExdiaryError` (Exception)
- **作用**: 所有 Exdiary 自定义异常的基类
- **继承**: `Exception`
- **被继承**: 被以下 4 个子类继承
- **被调用情况**: 无外部使用

### `ExtractionError` (ExdiaryError)
- **作用**: 结构化提取失败异常（LLM 返回无法解析、function calling 失败等场景预留）
- **被调用情况**: 无外部使用

### `StorageError` (ExdiaryError)
- **作用**: 数据持久化失败异常（文件读写错误、YAML 解析失败等场景预留）
- **被调用情况**: 无外部使用

### `AgentError` (ExdiaryError)
- **作用**: Agent 运行时异常（工具执行失败、状态不一致等场景预留）
- **被调用情况**: 无外部使用

### `ConfigurationError` (ExdiaryError)
- **作用**: 配置错误异常（API Key 缺失、模型名无效、端口冲突等场景预留）
- **被调用情况**: 无外部使用
