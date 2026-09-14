# CampusFlow 比赛交付与公开仓库分工

更新日期：2026-09-15。公开仓库已发布 [v1.0.0-rc1（Pre-release）](https://github.com/naimidcorvalaan/campusflow/releases/tag/v1.0.0-rc1)，对应 `ba4f4e0`。
比赛最终提交以天津大学官方 GitLab 的 `agent2026-campusflow` 与比赛平台数据为准，GitHub Release 不能代替官方交付。

## 官方参赛要求

官方[技术实现赛道提交方式](https://agent2026.tju.edu.cn/ai-competition/introduction/docs/提交方式-技术实现赛道.html)要求项目按 `agent2026-{简称}` 命名，位于本人学号命名空间，完成后设为 Internal，并包含：

- 最终源码、`README.md` 与 `DESIGN.md`。
- 演示视频或截图。本项目四张最终图已满足演示材料形式，不强制额外录视频。
- 本人从[附件2](https://agent2026.tju.edu.cn/ai-competition/introduction/docs/附件下载.html)下载、填写及签署的 `版权合规承诺书.docx`，放官方仓库根目录。
- 使用分配给参赛项目的专属 API 地址；本人在 [ai.tju.edu.cn](https://ai.tju.edu.cn) 核对用量。真实 Key 不进入任何代码仓库。

## 当前核对结果

| 项目 | 状态 |
|---|---|
| 公开源码 | `5e4454b` 已恢复估时 → 手动采用分钟 → 加入计划，用户真实验收通过；本地已提交，尚未 push |
| 已发布 RC1 | GitHub API 确认已发布且为 Pre-release；ZIP 为 587,146 字节，仍是原验收包，**不包含 `5e4454b` 的估时修复** |
| 正式前端与首次配置 | 公开源码的时空视觉已冻结，首次 TJU 页面配置已完成；两者均已进入 RC1 |
| 官方仓库本地版本 | `main` 和本地记录的 `origin/main` 为 `b23d837`，已有交付材料提交；产品代码尚未同步公开仓库的正式时空展示、首次模型配置及后续估时修复 |
| 官方 README / DESIGN 与截图 | 文档存在，但仍对应较早的产品代码，需随代码同步核对；四张演示图已与公开冻结图逐字节一致 |
| 官方版权材料 | 本地已跟踪根目录 `版权合规承诺书.pdf`；未找到官方列出的 `版权合规承诺书.docx`，需本人确认格式要求或补齐。只核对文件存在，不审阅个人信息或签署内容 |
| GitLab 远端与可见性 | 本次远端读取因 TLS 连接失败未能核验；本地 `origin/main` 不等于实时远端状态，Internal 仍需本人登录确认 |
| 比赛平台用量 | 需本人在 ai.tju.edu.cn 确认项目专属接口与用量累计；本次未读取 Key、未调用模型 |

官方交付仍需同步最终代码与对应文档、确认版权文件格式、远端版本和 Internal，并人工核对平台用量。

## 公开保留的材料

- [当前行动与真实移动](assets/readme/today.png)、[连续时间线](assets/readme/timeline.png)、[任务估时](assets/readme/task-estimate.png)、[个性化设置](assets/readme/settings.png)。
- 两张独立路线图和必要设计源文件；不提交批量验收截图。
- [前端冻结](FRONTEND_FREEZE.md)及 [RC 发布验收](releases/v1.0.0-rc1-validation.md)记录。

公开仓库不得同步签署承诺书中的学号、签名等个人信息、真实 API Key、私人 SQLite 或用户材料。
本人签署的比赛承诺书只进入天津大学官方参赛仓库；这里不放空白或代填版本。

## 正式配置与交付

Windows 用户从页面填写自己的 TJU 服务地址、模型名与 Key。比赛使用平台分配的项目 Base URL，客户端会补 `/chat/completions`；进程环境与 `.env` 优先于页面保存值，需本人核对旧配置没有覆盖比赛地址。

RC1 包已完成独立环境首启、二次启动和两校区路线验证，发布时重新下载的 GitHub 资产与验收包 SHA-256 一致。此次只同步文档，不重新打包或发布。
当前公开源码支持文字、图片、Word、PDF 的任务估时与课表截图导入；估时采用修复已通过 focused 225、full 2679 项及用户真实验收。实际服务权限与比赛用量由本人在平台核对。
