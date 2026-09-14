# CampusFlow v1.0.0-rc1

天津大学校园时空规划：现在做什么、什么时候出发、下一段校园移动要多久。

## Windows 快速开始

1. 安装 **64 位 Python 3.12**，勾选 **Add python.exe to PATH**。
2. 下载 `CampusFlow-v1.0.0-rc1-windows.zip`，完整解压到可写文件夹。
3. 双击 **启动 CampusFlow.bat**。
4. 首次在浏览器的“连接 TJU 模型服务”填写服务地址、模型名称和 API Key，点击“保存配置”。
5. 输入今天的安排，开始使用。

第一次启动会创建独立环境并联网安装依赖。安装时保留启动窗口；以后版本匹配时跳过安装，重复双击会打开已有页面。

模型配置保存在 `%LOCALAPPDATA%\CampusFlow\model-service.json`，重启后继续使用。请填写你自己的 TJU 服务信息；发布包不附带服务账号或密钥。已有环境变量 / `.env` 配置继续兼容。

使用期间保留启动窗口，按 Ctrl+C 或关闭窗口结束服务。中文入口无法打开时，双击同目录 `start_campusflow.bat`。

包内含北洋园、卫津路完整本地路网，路线计算无需运行时高德 API。首次安装依赖需要网络，实际模型请求需要对应服务权限和网络条件。

[详细使用与故障处理](docs/local_quickstart.md) · [开发者配置模板](.env.example)
