# CampusFlow 多人隔离与部署准备

## 当前架构

CampusFlow 仍以 Streamlit 作为正式入口，以一个 SQLite 文件保存少量试用档案。
业务数据使用随机、不可读的内部 `user_id` 分区；规划器只看到这个作用域，不知道
学号。地图等只读资源可以跨会话共享，任务、进度、课程、估时、位置、设置、可靠
方案和 What-if 均不能跨档案共享。

页面支持两种模式：

- 临时使用：不要求学号，只保留当前浏览器会话，不读取任何学生个人档案。
- 个人档案：用户明确输入学号后，SQLite 将学号映射到内部 `user_id`，重开页面时
  再输入同一学号即可恢复该档案。

**学号唯一，但不是身份认证。** 当前方式只适合本地或可信小范围试用；知道他人
学号的人可能选择到相同档案，因此不能直接作为公开公网的登录方案，也没有加密哈希
可以把这一点变成认证。

## 数据库与升级

Schema v4 在原有 `profiles` 目录上增加 `external_identity_links`，用
`provider + provider_subject` 稳定映射内部 `user_id`。provider token 不保存，学号也不
参与认证。v3 升级只增表和版本号，不改已有任务、进度、课表或设置；v1/v2 的单用户
内容仍事务迁入 `legacy-local`，不会静默绑定真实学号。

SQLite 的 revision 比较仍按档案执行。保存失败、版本不兼容或损坏时保留原记录，
不会用空状态覆盖。SQLite 适合当前单进程、小规模试用；多进程横向扩容前应更换为
支持服务端并发事务的数据库，并保留当前 repository/user_id 边界。

## 数据目录

优先级：

1. `CAMPUSFLOW_DATA_DIR`
2. Windows `%LOCALAPPDATA%\CampusFlow`
3. macOS `~/Library/Application Support/CampusFlow`
4. Linux `$XDG_DATA_HOME/campusflow`，未设置时 `~/.local/share/campusflow`

服务器应显式设置 `CAMPUSFLOW_DATA_DIR` 到可写、可备份且不在源码目录内的持久卷。
真实数据库、WAL、日志、环境文件和离线截图均由 `.gitignore` 排除。

## 配置与启动

仓库实际使用：

- `TJU_LLM_API_KEY`
- `TJU_LLM_BASE_URL`
- `TJU_LLM_MODEL`
- `CAMPUSFLOW_DATA_DIR`（服务器强烈建议显式设置）

密钥只从环境或既有本地配置读取。普通错误页不展示凭据、Authorization header、
完整请求或学号。启动前可以离线执行：

```bash
python scripts/deployment_check.py
```

Linux 单进程试用启动：

```bash
export CAMPUSFLOW_DATA_DIR=/var/lib/campusflow
streamlit run src/p2_live_main.py --server.address 0.0.0.0 --server.port 8501
```

仓库同时提供不含真实配置的 `deploy/campusflow.service.example`、
`deploy/nginx-campusflow.conf.example` 与 `deploy/campusflow.env.example`。建议让
Streamlit 只监听回环地址，由反向代理终止 HTTPS、转发 WebSocket，并在应用之前接入
成熟的认证服务。当前认证原型、版本实验和最终测试版建议见
`docs/authentication_prototype.md`。就绪检查可运行：

```bash
python scripts/server_readiness.py --url http://127.0.0.1:8501
```

SQLite 备份必须使用在线备份接口，不要在服务写入时直接复制数据库文件：

```bash
python scripts/sqlite_profile_backup.py backup --database /var/lib/campusflow/campusflow.sqlite3 --output-dir /var/backups/campusflow
python scripts/sqlite_profile_backup.py verify --database /var/backups/campusflow/<backup>.sqlite3
python scripts/sqlite_profile_backup.py restore-dry-run --backup /var/backups/campusflow/<backup>.sqlite3 --target /var/lib/campusflow/campusflow.sqlite3
```

`restore-dry-run` 只校验并打印计划，不替换任何文件。真正恢复需要先停服务、保留当前
数据库，再由运维人员显式操作。

本轮不加入 Dockerfile：当前仍缺真实认证、TLS/反向代理和服务器数据库方案，此时固化
容器镜像容易让人误以为可以安全公网开放。单机测试服务器使用虚拟环境、受限系统用户、
持久目录和反向代理即可验证运行边界。

## 真正公网前必须补齐

1. 按 `docs/authentication_prototype.md` 迁移到具备稳定原生 OIDC 用户对象的运行时，
   注册测试身份提供方并把其 `sub` 映射到内部 `user_id`；不能信任浏览器提交的学号。
2. 配置 HTTPS、反向代理、访问控制、审计与备份/恢复策略；评估数据保留和删除机制。
3. 若使用多个进程或实例，将 SQLite 迁到合适的服务端数据库，增加跨进程并发测试。
4. 在测试服务器验证学校模型网络、超时、限流和敏感日志配置。本轮没有调用真实 API。

公开资料核实结果、当前 Streamlit 版本的认证限制和阶段 A/B 方案见
`docs/public_test_readiness.md`；精简的测试版数据说明见 `docs/test_data_notice.md`。
