# 图片任务理解能力状态

## 当前结论（2026-09-07 正式客户端路径验证后）

- CampusFlow 已有单张 JPG/JPEG/PNG 校验、5MB 产品限制、Base64 Data URI 和
  OpenAI-compatible `image_url` 消息构造；图片字节不会写入本地档案或日志。
- 阿里云官方 Qwen 视觉文档确认：**指定的视觉模型**可通过 Chat Completions 的
  `content=[image_url, text]` 接收 URL 或 Base64 Data URI。
- 当前配置的非敏感模型标识为 `tju-llm`。2026-09-07，用户在正常 PowerShell 与天津大学
  校园网络环境中复用正式 `call_tju_llm_messages` / Data URI 路径发送无敏感合成 PNG：
  HTTP 200，1.61 秒返回，准确读出“CampusFlow 图片测试 314159”和蓝色圆形。
- 这证明当前部署与消息格式的接口级图片理解可用，因此正式入口已开启。
- 该结论不等于真实作业截图质量或复杂二维课表已经验收；两类结果都必须先预览、再由用户确认。

## 依据

- Qwen 视觉理解（官方）：https://help.aliyun.com/en/model-studio/vision
- Qwen-VL OpenAI 兼容接口（官方）：
  https://help.aliyun.com/zh/model-studio/qwen-vl-compatible-with-openai
- 仓库接线：`src/p2_tju_live_adapter.py`、`src/task_estimation.py`。

官方文档说明通用协议；天津大学当前部署能力以2026-09-07上述实测为准。

## 下一步网页业务验收

1. 在正式网页“难以估计任务时间？让campusflow帮你估”中验收一张普通任务截图，检查范围、模糊处和估时是否合理。
2. 在“个人设置 → 我的课表”验收一张真实教务课表，重点检查跨节、周次和多教学班歧义。
3. 继续检查日志与 SQLite 中没有 Base64、图片正文或凭据。
