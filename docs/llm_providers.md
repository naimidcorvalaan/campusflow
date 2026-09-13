# 模型服务配置与受限 Cloud 验收

CampusFlow 的既有 adapter 通过 `llm_provider` 调用 `TJUProvider` 或
`DeepSeekProvider`。两者提供相同的文本 completion / 多模态 chat 接口。
规划、路线事实、进度、稳定引用、What-if 和原子发布继续使用原有流程。

## 本地与 Cloud

未配置 `CAMPUSFLOW_LLM_PROVIDER` 时仍使用 `tju`，继续读取
`TJU_LLM_BASE_URL`、`TJU_LLM_MODEL`、`TJU_LLM_API_KEY`。
不根据操作系统或部署平台猜测 provider。Windows 启动器、数据目录和默认校区不变。

Cloud 在 Streamlit 的 Secrets 中设置以下**顶层**字段；不要把真实密钥写入仓库：

```toml
CAMPUSFLOW_LLM_PROVIDER = "deepseek"
DEEPSEEK_API_KEY = "<set in Streamlit Cloud only>"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_VISION_MODEL = "deepseek-flash"
TZ = "Asia/Shanghai"
CAMPUSFLOW_DATA_DIR = "/tmp/campusflow"
```

Streamlit 将这些顶层 Secrets 注入服务端进程环境；本地继续支持 process env / `.env`，
process env 不被 `.env` 覆盖。DeepSeek 只强制要求 API key，其余三个配置有上述默认值。
文字模型和视觉模型保留独立配置项；带图请求只选视觉配置，失败时不改投文字模型。

## 当前官方协议

本次直接打开的[官方模型页](https://api-docs.deepseek.com/quick_start/pricing/)
已推荐 `deepseek-flash`（DeepSeek-V4.1-Flash，含视觉能力），并说明
`deepseek-v4-flash` 和 `deepseek-v4-flash-vision-exp` 对应模型已退役，旧名称请求会转到
新 Flash。搜索缓存仍存在旧模型表。因此示例使用当前推荐名称，保留模型配置能力。
这不是自动把图片交给不支持视觉的文本模型。

接口采用 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)：
`POST /chat/completions`，非流式，`thinking.type=disabled`，保留原调用方的
`temperature`、`max_tokens` 和 timeout。图片复用现有 PNG/JPEG Data URI、
`image_url` user content 数组和图片大小/页数限制，不引入 OCR 或 Files API。

只取最终 `message.content`，忽略 `reasoning_content`。不全局强制 JSON response format：
原流程含不同类型的输出，继续由各阶段现有提示词、JSON 校验与有界修复处理。
空输出、HTTP 响应非 JSON 或无合法 content 作为安全服务错误；回答中的 code fence、
额外文字、格式不合法由原业务解析器决定接受或修复。

## 调用边界与视觉失败

- Provider 每次调用只发一次 HTTP 请求；无自动重试、无跨 provider 回退，不跟随重定向。
- P2 保留 2048 输出 token / 120 秒请求参数；独立工作量恢复保留 768 token / 30 秒。
- 文字、DOCX、文本 PDF 使用文字路径；任务图、课表截图、扫描/混合 PDF 的视觉页使用视觉路径。
- 混合 PDF 视觉服务失败时，借用原有的一次识别修复额度，仅请求理解已读文字。
  图片页不再附带，来源限制写入上下文，结果强制部分覆盖。最多仍为原有三次调用
  （材料请求、一次识别修复、必要时一次工作量估计）。
- 纯图片/扫描 PDF 无可读文字时明确提示图片理解暂不可用。课表沿用原错误边界，原课表不变。
- DOCX 中可读取的文字不依赖视觉服务；未读取内容沿用原覆盖限制。

## 安全诊断

两家服务复用原有 allowlist 日志字段：provider、stage、category、exception_type、
cause_type、status、timeout、response_received、安全 endpoint host/path。
DNS、connect/read timeout、TLS、HTTP 401/402/403/404/429/5xx 和响应解析错误可区分。
不输出 key、Authorization、请求/响应正文、原始异常字符串、traceback 或用户材料。
未知 host、个性化路径及与 key 相同的字段会被遮蔽；DeepSeek 配置中的 userinfo/query/fragment
在发送前被拒绝。密钥只用于服务端 HTTP header，未进入 UI、浏览器 JS 或页面 state。

## 一次最小真实验收

自动测试和本地预览全部使用 fake HTTP，不能证明真实网络、余额、账户权限或模型质量。
用户自行在 DeepSeek 平台创建密钥/充值，再更新 Cloud Secrets。保持 Cloud 外层受限访问。

1. 部署用户确认后的版本，入口仍为 `src/p2_live_main.py`，Python 3.12。
2. 任务估时输入「填写姓名、学院，写约100字自我评价，最后核对。」，点击「帮我看看」。
   检查有时间区间和依据；先不加入计划。
3. 上传一张内容清楚、无个人信息的简单任务图片，确认视觉结果对应实际图片内容。
4. 再测试一份文字+扫描页的合成 PDF、课表截图预览，最后做一次完整规划及进度更新。
5. 服务错误时查看 Cloud Logs 的分类字段：402 检查余额，401 检查 key，404 检查模型/地址，
   429 稍后再试；网络/TLS 问题依据分类处理。不要复制请求正文或密钥到日志/问题反馈中。

本轮不处理 SQLite 持久化、公开匿名访问、认证或费用面板；`/tmp` 中的档案可能在重启/重新部署后丢失。
