---
name: deepseek-api
description: 本项目的接口背景——修改 agent 或添加工具代码前先读，避免踩 DeepSeek 兼容接口的坑
---

# DeepSeek 兼容接口注意事项

本项目通过 Anthropic 官方 SDK 连接 DeepSeek 的 Anthropic 兼容接口
（`base_url=https://api.deepseek.com/anthropic`），修改代码时必须遵守：

1. **工具函数必须返回字符串**。返回数字或其他类型，请求会报 400
   （`tool_result.content: expected a string or a list`），用 `str()` 转换或用 `json.dumps()` 序列化。
2. **模型名映射**：`claude-opus-5` → `deepseek-v4-pro`（最强）；
   `claude-haiku-*` / `claude-sonnet-*` → `deepseek-v4-flash`（更快更便宜）。
3. **消息里的 thinking 块必须原样回传**，不能删除或改写（多轮对话时尤其注意）。
4. **Windows 命令输出常是 GBK 编码**，解码要 UTF-8 优先、GBK 兜底。
5. 新增有副作用的工具要记得考虑权限：`DENY_LIST`（硬拒绝）、
   `PROTECTED_FILES`（项目核心文件保护）、`PERMISSION_RULES`（询问用户）三道闸门。
6. 文件读写必须经过 `_safe_path`，把路径限制在项目目录内。
