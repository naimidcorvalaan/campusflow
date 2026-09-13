# CampusFlow 真实认证接线原型

查询与实验日期：2026-09-06。本文区分公开能力、已经运行的离线原型和仍需外部配置的
真实登录；CampusFlow 尚未公网部署，也没有使用真实天津大学账号。

## 结论与测试版推荐

**主方案：先把测试服务器运行时迁移到 Python 3.9+、Streamlit 1.45.1 或更新的已回归
补丁版本，再使用 Streamlit 原生 OIDC。** 1.42.0 是 `st.login()`/`st.logout()` 的功能
起点，但 `st.user` 到 1.45.0 才成为 GA；所以 1.45.x 是本项目可接受的最低稳定能力线，
不是要求永远停留在旧补丁。外部 IdP 完成身份验证，CampusFlow 只取 OIDC `sub`，再由
schema v4 映射到内部 `user_id`。

备选方案是 **oauth2-proxy + Nginx 认证网关**。它适合已有统一网关运维能力的环境，
但当前 Streamlit 1.31.1 没有公开请求头 API；若走此方案，也应至少迁移到具备公开
`st.context.headers` 的版本，并确保代理清除浏览器同名 header、后端只监听 loopback、
应用接收的 user-id claim 确实是稳定 `sub`。不能把私有 WebSocket API当生产接口。

没有把 Cloudflare Access 选为主备方案：它能提供签名 JWT，但源站仍须验证签名、issuer
和 audience，并处理公钥轮换；对 5–10 人测试会增加供应商配置与验证代码。它仍是未来
可评估的成熟网关，不应只信任一个可伪造的 email/header。

## 官方能力依据

- Streamlit 2025 release notes：1.42.0 引入 `st.login()`/`st.logout()`，1.45.0 将
  `st.user` 标为 GA：<https://docs.streamlit.io/develop/quick-reference/release-notes/2025>
- Streamlit 认证文档：OIDC 需要 callback、client secret、cookie secret；认证不等于业务
  授权：<https://docs.streamlit.io/develop/concepts/connections/authentication>
- `st.context.headers/cookies` 公开接口在 1.37 能力线上可用：
  <https://docs.streamlit.io/1.37.0/develop/api-reference/caching-and-state/st.context>
- OIDC Core：稳定用户键应使用 issuer/provider 与 `sub` 组合，不能使用 email 或显示名：
  <https://openid.net/specs/openid-connect-core-1_0-final.html>
- oauth2-proxy 的 `set_xauthrequest` 会产生 `X-Auth-Request-*`，且 token/header 转发需显式
  控制：<https://oauth2-proxy.github.io/oauth2-proxy/7.7.x/configuration/overview/>
- Cloudflare Access 要求源站校验 `Cf-Access-Jwt-Assertion`，不能只看 header 是否存在：
  <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/>

## 实际最小实验

当前正式环境是 Python 3.8.10 + Streamlit 1.31.1：不存在 `st.context`、`st.login`、
`st.logout` 或 `st.user`。仓库的 `scripts/streamlit_131_header_probe.py` 与本地受控代理证明，
代理替换的标记可以随 WebSocket 到达 Streamlit；但唯一可读路径是 Streamlit 自己标注为
unsupported/private 的 `_get_websocket_headers()`。这个探针只输出布尔结果，正式代码没有
导入它。

独立系统临时环境没有改 `requirements.txt`：

- Python 3.12 + Streamlit 1.42.2：原生登录 API 存在，131 个关键回归通过；其公开用户
  对象仍处于 `user_info` 阶段。
- Python 3.12 + Streamlit 1.45.1：`st.context`、`st.login`、`st.logout`、`st.user` 均存在，
  同一 131 个关键回归通过；实际离线页面可以启动。
- 1.42.2 和 1.45.1 的包元数据均要求 Python `>=3.9, !=3.9.7`，所以现有 Python 3.8.10
  是明确迁移项。
- 1.45.1 页面实验发现设置抽屉旧 CSS selector 不再匹配新版容器名，遮罩会拦截保存按钮；
  selector 已改为同时兼容 1.31/1.45，并在两个版本的真实浏览器路径复测。

`scripts/offline_auth_preview.py` 使用完全离线 fake provider，服务端闭包生成已验证断言；
URL/query/header 不参与选择身份。两个独立浏览器 context 已跑通：Alice 保存独立档案，
Bob 看不到 Alice，Alice 再次进入恢复原档案，退出后清除用户作用域。该演示不注册账号、
不访问网络，也不是密码系统。

## 代码边界

1. 认证 adapter 先验证 provider 会话，再生成 `VerifiedExternalIdentity(provider, subject)`。
2. `external_identity_links(provider, provider_subject, internal user_id)` 保持稳定映射；首次
   认证创建随机内部 ID，再次认证恢复同一 ID。
3. `AuthenticationContext` 只带认证状态、内部 ID、provider 和不透明 link ID；不带 token、
   学号或 provider subject。规划、估时和课表仍只依赖 `ProfileIdentity.user_id`。
4. `streamlit_oidc_identity()` 只接受服务端 `st.user` 的已登录状态与 `sub`，忽略 token。
5. 学号可以认证后作为可选资料关联，但不改变映射，也不决定认证结果，不进入模型上下文。
6. 退出或身份切换先保存当前可靠档案，再清理任务、位置、估时草稿、未采用 What-if 与
   设置草稿，最后发布完整目标档案；失败不会混合两个用户的事实。

若未来 TjuSSO 提供合适协议，只需将新 provider subject 显式关联到现有内部 ID；业务表
不需要把主键改成学号，也不保存 provider token。

## 距离 5 名同学拿到 URL 还缺什么

1. 选择一个同学可访问的成熟 OIDC IdP，注册测试应用、5 人 allowlist 与 callback；本轮
   没有代用户注册。
2. 在独立部署分支把运行时迁移到 Python 3.9+ 和回归过的 Streamlit 1.45+ 补丁版本，
   加 `Authlib>=1.3.2`，并用 `st.user` 接上现有 loader。
3. 把 cookie/client secret 放到服务器受限 secrets 文件，配置 HTTPS/Nginx，Streamlit 只
   监听 loopback；浏览器永远看不到 TJU 模型 Key。
4. 在学校允许的网络里确认测试服务器能访问 TJU 模型端点、限流和超时。本轮没有探测。
5. 使用现有 SQLite 单进程、持久卷与在线备份方案，完成一次恢复演练。
6. 用 2–5 个非敏感测试账号做登录、退出、跨浏览器隔离与数据删除验收，再发测试 URL。

原生 OIDC 的外部配置形状见 `deploy/streamlit-oidc.secrets.toml.example`。它只含占位符，
当前 1.31.1 正式入口不会读取或启用该原型。
