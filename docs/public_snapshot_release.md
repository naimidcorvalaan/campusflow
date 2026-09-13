# 从开发仓库生成首次公开快照

开发仓库保留完整历史。公开 GitHub 仓库从最终、干净的开发 HEAD 导出文件，再独立建立初始提交；不继承开发仓库的父提交、分支、remote 或 `.git`。
建议公开仓库名：**campusflow**。这份 GitHub 展示仓库与学校比赛要求的提交仓库分别管理，不替代比赛交付要求。

## 1. 导出已提交文件

以下命令供维护者在开发仓库根目录的 PowerShell 中执行。每次使用新目录，不覆盖已有公开仓库，不使用普通文件夹整目录复制。

```powershell
$ErrorActionPreference = 'Stop'
$devRepo = (git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw '请先进入开发仓库根目录' }
if (git -C $devRepo status --porcelain) { throw '开发工作树尚未干净，先完成审查和提交' }
$releaseHead = (git -C $devRepo rev-parse HEAD).Trim()
$bundleDir = Join-Path $devRepo ('artifacts/public-release/' + $releaseHead)
$archiveZip = Join-Path $bundleDir 'campusflow-public.zip'

# 若本轮已生成同一 HEAD 的 ZIP，可直接使用，不重复覆盖。
if (-not (Test-Path -LiteralPath $archiveZip)) {
    New-Item -ItemType Directory -Path $bundleDir -Force | Out-Null
    git -C $devRepo archive --format=zip --output=$archiveZip $releaseHead
    if ($LASTEXITCODE -ne 0) { throw 'git archive 失败' }
}

# 独立公开仓库放在开发仓库的同级目录。
$publicRepo = Join-Path (Split-Path $devRepo -Parent) 'campusflow-public'
if (Test-Path -LiteralPath $publicRepo) { throw '公开目录已存在，请另选全新目录，不要覆盖' }
Expand-Archive -LiteralPath $archiveZip -DestinationPath $publicRepo
if (Test-Path -LiteralPath (Join-Path $publicRepo '.git')) { throw '导出目录不应包含 .git' }
Get-ChildItem -LiteralPath $publicRepo -Force
```

`git archive` 只导出指定提交的文件树，不读取工作树中被忽略的 `.env`、上传材料、数据库、虚拟环境或验收截图。
不要用 PowerShell 的 `>` 重定向二进制 archive 输出；上面的 `--output` 直接写 ZIP。

本轮生成的审查包位于 `artifacts/public-release/<开发 HEAD>/`，整个目录被忽略。其中 `campusflow/` 是已解压但**未 git init** 的核验副本；ZIP 和 `manifest.json` 放在它旁边。
不要在这个位于开发仓库内部、尚未初始化的副本里运行 `git add`；Git 可能向上找到开发仓库。先按上面的步骤解压到同级新目录，再初始化。

## 2. 检查并建立独立初始提交

预期目录包括 `.streamlit/`、`data/`、`deploy/`、`docs/`、`prototype/`、`scripts/`、`src/`、`tests/`，以及根目录 README、依赖、启动器、设计文档和忽略规则。
`docs/assets/readme/` 是正式展示图片；`docs/assets/design-history/` 是少量明确标注的历史提案。原型保留源文件，不包含生成截图目录。

```powershell
$forbidden = @('.git', '.env', '.env.dev', '.streamlit/secrets.toml',
    '.venv', '.campusflow', '.preview-data', 'artifacts', 'screenshots',
    'prototype/visual-direction/screenshots', 'uploads', 'user_uploads')
foreach ($item in $forbidden) {
    if (Test-Path -LiteralPath (Join-Path $publicRepo $item)) {
        throw "不应发布的路径：$item"
    }
}
$privateFiles = Get-ChildItem -LiteralPath $publicRepo -Recurse -File -Force |
    Where-Object { $_.Name -match '\.(sqlite3?|db)(-wal|-shm|-journal)?$|\.log$' }
if ($privateFiles) { throw '发现数据库或日志，请先核对' }

git -C $publicRepo init -b main
if ($LASTEXITCODE -ne 0) { throw '独立仓库初始化失败' }
if ((git -C $publicRepo rev-parse --show-toplevel).Trim() -ne ($publicRepo -replace '\\','/')) {
    throw 'Git 根目录不是公开目录，停止操作'
}
git -C $publicRepo add --all
if ($LASTEXITCODE -ne 0) { throw '暂存失败' }
git -C $publicRepo diff --cached --check
if ($LASTEXITCODE -ne 0) { throw '暂存内容检查失败' }
git -C $publicRepo diff --cached --stat
git -C $publicRepo ls-files
```

核对暂存列表、README 的本地相对图片和配置示例；真实凭据只能放进 Cloud Secrets。只有确认文件范围后再执行：

```powershell
git -C $publicRepo commit -m "发布：CampusFlow 首次公开快照"
if ($LASTEXITCODE -ne 0) { throw '提交失败' }
git -C $publicRepo rev-list --count HEAD  # 首次应为 1
git -C $publicRepo status --short        # 应为空
git -C $publicRepo remote               # 此时应为空
```

初始提交会使用维护者本机 Git 作者配置；需要隐藏邮箱时，先按 GitHub 账户设置选用自己的 noreply 邮箱，不在步骤中代填个人身份。

## 3. 在 GitHub 建仓并首次推送

在自己的 GitHub 账户创建 Public 空仓库 `campusflow`，不要勾选生成 README、`.gitignore` 或许可证文件，以免产生另一份初始历史。
许可证如需添加，由作者选择并审查后单独处理。以下仅为后续命令，本轮不执行远程操作。

```powershell
$githubOwner = 'YOUR_GITHUB_NAME' # 替换为自己的账户或组织
if ($githubOwner -eq 'YOUR_GITHUB_NAME') { throw '请先填写 GitHub 账户名' }
git -C $publicRepo remote add origin "https://github.com/$githubOwner/campusflow.git"
if ($LASTEXITCODE -ne 0) { throw '添加 remote 失败，请检查当前 remote，不要盲目覆盖' }
git -C $publicRepo remote -v
git -C $publicRepo push -u origin main
```

只从 `$publicRepo` 推送，不给开发仓库添加这个公开 remote。登录交给 Git Credential Manager / 浏览器完成，不把 token 拼进 URL。
GitHub 的空仓库导入流程参见[官方说明](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)。

## 4. 连接 Streamlit Community Cloud

首次 push 完成、GitHub README 图片正常显示后，在 Community Cloud 创建应用，填写：

| 设置 | 值 |
| --- | --- |
| Repository | `YOUR_GITHUB_NAME/campusflow` |
| Branch | `main` |
| Main file path | `src/p2_live_main.py` |
| Advanced settings → Python version | **3.12**，显式选择 |
| Advanced settings → Secrets | 首次启动先不填真实模型配置；确认受限访问后再填写下方配置 |

公开仓库部署的应用默认公开，不能把“不分享 URL”当作访问限制。首次按以下顺序操作：

1. 先只填写 `TZ = "Asia/Shanghai"` 与 `CAMPUSFLOW_DATA_DIR = "/tmp/campusflow"`，不填三个 TJU 模型键。页面可启动，模型配置保持缺失。
2. 在应用 Settings → Sharing → Who can view this app 中选择 **Only specific people can view this app**，仅添加受信任试用者。
3. 用未获授权的浏览器会话确认访问受限，再在应用 Secrets 中填写下方完整配置。平台当前只允许一个 private app；若账户额度或权限不允许设置，不填写真实密钥，先解决应用访问范围。

权限设置与限制参见[Streamlit 官方共享说明](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)。这不会把 CampusFlow 的档案选择入口变成逐用户身份认证；首轮仍只面向可信试用者。

```toml
TJU_LLM_BASE_URL = "https://your-service.invalid/v1"
TJU_LLM_MODEL = "your-model-id"
TJU_LLM_API_KEY = "replace-with-your-service-key"
TZ = "Asia/Shanghai"
CAMPUSFLOW_DATA_DIR = "/tmp/campusflow"
```

保留顶层键，不加 `[tju]` 分组；模板见 [`deploy/streamlit-community.secrets.toml.example`](../deploy/streamlit-community.secrets.toml.example)。
三个真实模型值来自当前 TJU 服务配置，填写在平台 Secrets 中，不修改仓库内示例。界面入口以[Streamlit 官方部署说明](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)为准。

源码公开和应用访问权限分别管理，不将本地档案选择入口当成统一身份认证。
需要维护者在真实部署后验证 Cloud 服务器到 TJU 的网络和权限；手机上的校园 VPN 不会改变服务器网络。
SQLite 暂按实例内临时数据使用，接受重启或重新部署后档案可能丢失；本轮不迁移数据库。

## 后续开发与再次发布

继续在原开发仓库开发。后续公开更新也从审查后的提交导出文件，再在已有公开仓库中审查增删并做正常提交；不要复制开发 `.git`，不要 merge 开发分支历史。
不把“第一次快照”的 `git init` 和初始提交流程当成每次更新都需重建历史的步骤。
