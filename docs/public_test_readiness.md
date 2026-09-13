# CampusFlow 公网测试版准备与认证路径调研

查询日期：2026-09-06。本文区分公开事实、架构建议和本仓库已经实现的能力；它不表示
CampusFlow 已经公网部署。

## 公开事实

### 天津大学统一身份认证

- 天津大学信息与网络中心的[统一身份认证说明](https://its.tju.edu.cn/xfw/qbfw/tyshenrz/tysfrz.htm)
  将 TjuKey 服务对象写为学生和教职工，用于学校管理和服务系统。
- 信息与网络中心的[单点登录服务说明](https://its.tju.edu.cn/info/1302/1188.htm)
  将 TjuSSO 服务对象写为“校内部门”，申请入口为融合门户办事大厅的《单点登录系统
  （SSO）申请》。公开页面给出 CAS 服务地址，并特别提示应用仍需自行进行身份识别和
  权限控制；页面还称该服务处于开发测试阶段，不建议把它作为高稳定性系统的唯一认证。
- 本次公开检索没有找到学生个人项目的公开申请路径，也没有找到该服务支持 OIDC、
  OAuth 2.0 或 SAML 的公开说明。因此 CampusFlow 不能自行假定可接入；应由指导单位或
  信息与网络中心确认申请主体、CAS 属性和授权范围。

### 比赛模型服务网络边界

- 大赛官方[API 使用指南](https://agent2026.tju.edu.cn/docs/API%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97)
  公开了仓库专属 OpenAI-compatible 端点、Bearer Key 和 `tju-llm`，并给出
  60 次/分钟、3000 次/小时的限制及 401/429/500 含义。
- 大赛[常见问题](https://agent2026.tju.edu.cn/ai-competition/introduction/docs/faq.html)
  同样说明专属接口及限流，但没有说明公网服务器来源 IP、白名单或校外访问条件。
- 信息与网络中心关于[网页版 AI 服务](https://its.tju.edu.cn/info/1158/1464.htm)的说明称
  校外访问 `ai.tju.edu.cn` 需要 VPN。这是网页服务公开事实，不能直接证明比赛 API
  端点也具有完全相同的网络策略。服务器能否直连比赛 API 仍需学校说明或后续经授权的
  非图片最小网络验证；本轮没有探测。

### Streamlit 1.31.1

- 仓库实际锁定 `streamlit==1.31.1`。Streamlit 官方
  [2025 release notes](https://docs.streamlit.io/develop/quick-reference/release-notes/2025)
  显示原生 `st.login()` / `st.logout()` OIDC 支持在 1.42.0 引入，因此当前版本没有适合
  本项目的原生登录接口。
- 官方[认证文档](https://docs.streamlit.io/develop/concepts/connections/authentication)说明
  新版认证需要 OIDC provider、callback URL、client secret 和 cookie secret；OIDC 只做
  身份认证，业务授权仍由应用负责。
- 官方[部署说明](https://docs.streamlit.io/knowledge-base/deploy/deploy-streamlit-domain-port-80)
  建议用 Nginx/Apache 等反向代理。Streamlit 页面依赖 WebSocket，代理必须传递 Upgrade /
  Connection headers；HTTPS 应在反向代理终止。

后续原型进一步确认：1.42 是登录能力起点，`st.user` 在 1.45 才 GA；两者都要求
Python 3.9+。独立 1.45.1 环境的关键回归和真实离线页面已经跑通，但正式依赖仍保持
1.31.1。具体证据与建议见 `docs/authentication_prototype.md`。

## 两个阶段

### A. 可信小范围测试版（3–10 人）

推荐结构：

```text
Browser
  → HTTPS reverse proxy
  → mature authentication gateway
  → Streamlit CampusFlow（单进程）
  → persistent volume / SQLite
  → TJU Qwen API
```

测试版最终优先采用 Streamlit 原生 OIDC：先迁移到 Python 3.9+、Streamlit 1.45+ 的
回归版本，再对接成熟 OIDC provider 和 5–10 人 allowlist，而不是让 CampusFlow 自建密码。
oauth2-proxy + Nginx 是备选网关，但不能在 1.31.1 下依赖私有 WebSocket header API。

代码现已完成 provider subject → internal `user_id` 的服务端映射与完全离线 Alice/Bob
原型；真实 IdP 注册、cookie/client secret、回调和真实登录仍未配置。学号只可在认证后
作为可选档案资料，不能继续作为登录凭证。

SQLite 对单进程、3–10 人低并发测试通常足够，但必须：

- 只运行一个 Streamlit 进程，不能让多个 worker 共享写入；
- 数据目录使用持久卷并限制文件权限；
- 定期用 SQLite online backup API 备份；
- 接受单点故障、短时写锁和维护时停机恢复的限制。

API Key 只写在服务器受限环境文件中，由 Streamlit 服务进程读取，绝不能发到浏览器、
反向代理 access log 或仓库。公网服务器到 TJU API 的网络可达性是测试版上线前阻断项。

### B. 真正公网开放

在 A 之外还必须完成：可信认证与账号绑定、细粒度授权、PostgreSQL 等服务端数据库、
HTTPS 与安全响应头、rate limiting、审计、监控、备份恢复演练、数据导出/删除、隐私说明
和模型服务容量评估。本轮不实现这些生产能力。

## 本仓库已经落实

- `AuthenticationContext` 将认证状态、provider 与内部 `user_id` 放在规划业务之前；
  不包含密码、token、provider subject 或学号。schema v4 的 `external_identity_links` 已把
  `(provider, subject)` 稳定映射到随机内部 `user_id`。
- `scripts/offline_auth_preview.py` 使用合成 Alice/Bob 服务端断言跑通切换、恢复和退出；
  URL/query/header 不能选择身份。它不是生产登录。
- `deploy/campusflow.service.example`：单进程、非 root、只监听 loopback 的 systemd 示例。
- `deploy/nginx-campusflow.conf.example`：HTTPS、外部 auth gateway 和 WebSocket 代理骨架。
- `deploy/campusflow.env.example`：仅变量名/占位值；实际文件须由 root 持有并设为 0600。
- `scripts/deployment_check.py`：启动前检查变量是否存在和数据目录是否落入仓库，不打印值。
- `scripts/server_readiness.py`：检查 Streamlit `/_stcore/health`。
- `scripts/sqlite_profile_backup.py`：SQLite 在线备份、只读验证和 restore dry-run。

没有实现真实 OIDC/CAS 登录，也没有信任任意浏览器传来的 `X-Forwarded-User`。1.31 的
私有 header 读取实验只作为兼容性证据，正式代码不导入。真实 provider、代理信任范围、
TLS 和 secret 尚未配置，因此仍不能称为公网可用。

## 测试服务器启动清单

1. 建立受限的 `campusflow` 系统用户、虚拟环境和 `/var/lib/campusflow` 持久目录。
2. 将 `deploy/campusflow.env.example` 复制到仓库外的 root-owned 环境文件并填入真实配置。
3. 运行 `python scripts/deployment_check.py`，确认数据路径不在源码目录。
4. 按 `docs/authentication_prototype.md` 配置原生 OIDC；Nginx 负责 HTTPS 与 WebSocket，
   Streamlit 只监听 127.0.0.1。若改用网关，必须清除浏览器身份 header 并只传受控 subject。
5. 启动 systemd 服务并运行 `python scripts/server_readiness.py --url http://127.0.0.1:8501`。
6. 在不使用真实用户数据的情况下验证服务器能否访问比赛 API；若学校要求校园网/VPN，
   需使用学校批准的服务器网络方案，不能在个人主机上长期挂非托管 VPN 凭据。
7. 建立定时备份和离线恢复演练，再邀请测试者。

本轮继续不加入 Dockerfile。当前单机测试版需要明确的持久目录、systemd 和反向代理，
venv 部署更直接；等认证和服务端数据库方案稳定后再决定容器化，避免维护两套未验证入口。

## 备份与恢复

```bash
python scripts/sqlite_profile_backup.py backup \
  --database /var/lib/campusflow/campusflow.sqlite3 \
  --output-dir /var/lib/campusflow/backups

python scripts/sqlite_profile_backup.py verify \
  --database /var/lib/campusflow/backups/campusflow-backup-....sqlite3

python scripts/sqlite_profile_backup.py restore-dry-run \
  --backup /var/lib/campusflow/backups/campusflow-backup-....sqlite3 \
  --target /var/lib/campusflow/campusflow.sqlite3
```

备份使用 SQLite online backup API，不直接复制写入中的数据库。`restore-dry-run` 只校验
来源和目标，永不替换文件。真实恢复必须先停止服务、再由操作者保留当前数据库并显式替换，
完成后运行 `verify`；仓库不提供自动覆盖生产数据的按钮。
