# 发布到 GitHub 开源指引（AI漫剧）

> 2026-09 · 把项目以「公开快照」形式开源到 GitHub（your-github-username/aidrama-platform）。
> 本文沉淀「对接并上传 GitHub」的完整流程 + 本机已知坑与绕行方案，供后续复用。
> 复用脚本：scripts/publish-to-github.sh（脱敏→快照→单次提交→建仓→推送一条龙）。

## 一、目标与非目标

- 目标：把本地项目制作成一份可对外公开的快照上传 GitHub，包含：
  1. 一份中英双语、系统级的详细 README.md（一段话成片流水线/特性/架构/快速开始/模块表/技术栈/许可）。
  2. 一个开源 LICENSE（本仓库用 MIT）。
  3. 平台源码 + 定位清晰的文档。
- 非目标：不公开内部基础设施信息（内网 IP、服务器凭据、本机真实路径、API Key、实验/QC/运维脚本、生成媒体、依赖目录）。

### 公开前铁律
任何含内网 IP / 密码 / 本机绝对路径 / 真实 API Key 的内容，一律不能进公开仓库。**安全优先于发布速度。**

## 二、本机已知坑与绕行（本次实测）

| # | 坑 / 症状 | 根因 | 解法 |
|---|---|---|---|
| 1 | git 报 You have not agreed to the Xcode license agreements | 系统 /usr/bin/git（Xcode CLT 版）需已接受 Xcode 许可 | 用 Xcode.app 自带 git：/Applications/Xcode.app/Contents/Developer/usr/bin/git（2.54，可用）；或终端 sudo xcodebuild -license accept |
| 2 | brew 也被同一 Xcode 许可拦截 | Homebrew 需 Xcode CLT + 许可 | 同 #1（改用 Xcode git；或先接受许可） |
| 3 | python3 报 Xcode 许可错误（取 token 的解析脚本静默失败） | /usr/bin/python3 是 Xcode CLT 版 python | 解析 JSON 用 sed/grep；本环境手写 sed 解析 |
| 4 | gh repo create / REST /user/repos 返回 403 Resource not accessible by personal access token | fine-grained PAT（github_pat_…）没有「创建仓库」权限；已登录不等于能建仓 | 用 OAuth 设备流取带 repo,workflow 的 token，或给 token 加 Account→Administration:Write，或用 classic PAT(repo) |
| 5 | gh auth login 报 failed to move active token in keyring: exit status 161，token 没存下 | 沙箱禁写 macOS keyring 与 ~/.gitconfig，gh 默认用 keyring | 不依赖 gh 存储：用 curl 手动跑 OAuth 设备流，直接捕获 token 落盘到 /tmp |
| 6 | git push 报 unable to get credential storage lock ... Operation not permitted | 全局/系统 credential.helper=osxkeychain / gh / store 想写 keychain（沙箱拦） | 禁用凭据助手 + token 走 URL：git -c credential.helper= push https://x-access-token:TOKEN@github.com/... |
| 7 | git push 首次报 Failed to connect to github.com port 443 | git 被凭据助手拖累（curl 实际可达 github.com:443） | 见 #6：清掉本地凭据助手后 token URL 推送即可 |
| 8 | 扫描发现大量内网 IP / 路径 / 密码 | 开发期文档/脚本含内网信息 | 见「脱敏」；仅保留公开面 |
| 9 | .gitignore 的 models/ 误伤 backend/app/models，dashboard/ 被整目录忽略 | 根 .gitignore 过宽 | 快照里删除这两条忽略规则 |
| 10 | 直接推本地仓库会公开含内部信息的旧提交历史 | 历史即内容 | 用全新单次提交，不把本地历史带上公开仓库 |

> 小结：不要直接用本地仓库推公开；要构建「脱敏 + 单次干净提交 + 可建仓 token」的独立快照。

## 三、完整流程（人工版）

### 1) 准备可用的 git（绕坑 #1）

    GIT=/Applications/Xcode.app/Contents/Developer/usr/bin/git
    export PATH="/Applications/Xcode.app/Contents/Developer/usr/bin:/opt/homebrew/bin:$PATH"

### 2) 脱敏 + 复制公开快照（重点）

在干净目录 /tmp/aidrama-publish 从项目根复制公开面，并排除内网/运维/实验/大文件：
- 排除：.toolchain/ tmp/ logs/ out/ deliverables/ .evidence/、node_modules/ dist/ .venv/、backend/scripts/、backend/data/、dashboard/logs dashboard/data dashboard/.env*、所有 .env*。
- 复制：backend/app backend/alembic backend/tests backend/docs（+ pyproject/requirements/alembic.ini）、frontend/src（+ 前端配置）、dashboard（去掉数据/日志）、根 docker-compose.yml .gitignore .env.example AGENTS.md、设计文档、README.md LICENSE。
- 修正 .gitignore：删掉 models/ 与 dashboard/ 两条误伤（坑 #9）。
- 脱敏替换：10.0.0.1->10.0.0.1、10.0.0.2->10.0.0.2、10.0.0.x->10.0.0.x、/Users/yourname->~、<password>-><password>（可用 .scrubmap 每行 旧|新 扩展）。
- 扫描验证（必须清零）：

    for pat in "<password>" "192\.168\." "/Users/yourname" "-----BEGIN" "sk-[A-Za-z0-9]{20,}"; do
      echo "$pat -> $(grep -rIlE "$pat" . 2>/dev/null | grep -vE node_modules | wc -l)"
    done

### 3) 初始化干净仓库 + 单次提交（绕坑 #10）

    cd /tmp/aidrama-publish
    git init -q -b main
    git config user.name your-github-username
    git config user.email your-github-username@users.noreply.github.com
    git add -A && git commit -q -m "feat: AIDrama open-source AI short-drama video workbench"
    # 核对：git ls-files | wc -l 应为数百；git status 干净；无 .env/密钥

### 4) 拿一枚「可建仓」的 token（绕坑 #4/#5）

    CLIENT_ID=178c6fc778ccc68e1d6a   # GitHub CLI 公共 OAuth client_id
    curl -s -X POST https://github.com/login/device/code -H "Accept: application/json" -d "client_id=$CLIENT_ID&scope=repo workflow"
    # 取出的 user_code 由用户到 https://github.com/login/device 输入并 Authorize
    # 然后轮询 https://github.com/login/oauth/access_token 取 access_token

### 5) 创建仓库 + 推送（绕坑 #6/#7）

    curl -s -X POST https://api.github.com/user/repos \
      -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
      -d "{\"name\":\"aidrama-platform\",\"private\":false,\"description\":\"...\"}"
    git -c credential.helper= push -u "https://x-access-token:$TOKEN@github.com/your-github-username/aidrama-platform.git" main
    git remote set-url origin https://github.com/your-github-username/aidrama-platform.git   # 推完清干净 origin

### 6) 结束后安全
- 到 GitHub 撤销该设备流授权 / 删除 token（Settings → Developer settings → OAuth Apps → GitHub CLI → Revoke）。
- 本地 /tmp/ghcfg/token 可删（重启即失效）。

## 四、一键脚本

    scripts/publish-to-github.sh                 # 交互授权 → 自动脱敏/提交/建仓/推送
    GITHUB_TOKEN=xxx scripts/publish-to-github.sh  # 已有可建仓 token
    PUBLISH_PRIVATE=true scripts/publish-to-github.sh  # 推私有

脚本内嵌：找可用 git、脱敏复制、修 .gitignore、单次提交、设备流取 token、建仓、禁用凭据助手推送、恢复干净 origin。

## 五、本次开源产物（可复用为模板）
- 项目根 README.md：中英双语，系统级介绍 + 上游许可提示。
- 根 LICENSE（MIT）；README 明确 MiniMax H3 / ComfyUI 上游许可独立。
- scripts/publish-to-github.sh + 本指引。
- 公开仓库：https://github.com/your-github-username/aidrama-platform（main，单次提交 73ebf8e，536 文件）。

## 六、后续增量更新公开仓库
改完代码后重复「脱敏复制 → 单次提交 → 推送」即可（脚本一条龙）。注意：增量提交也只应包含公开面；若本地有新的内网内容，先脱敏或排除。

## 关联
- Obsidian 运维手册/（服务器/DGX/远程访问速查） · 踩坑知识库/（git/凭据相关，可补充本指引的坑 #1~#7）。
- 本仓库 docs/发布到GitHub开源指引.md
---

## 七、隐私与个人信息红线（重要）

> 原则：任何涉及隐私、个人身份、个人数据的内容，一律不得上传到 GitHub（包括私有仓库）。

### 必须清除/不得上传的数据类型
| 类别 | 示例 | 处理 |
|---|---|---|
| 真实姓名 / 私人用户名 | 机主真名、私人账号昵称 | 用泛化词替换或删除 |
| 个人机器名/主机名 | userMacBook-Pro | 替换为 generic（如 dev-host） |
| 手机号 | 1[3-9]xxxxxxxxx | 删除或不打码 |
| 身份证 / 护照 / 银行卡 | 15~19 位连续数字 | 删除或打码 |
| 私人邮箱 | 非 @users.noreply 的邮箱 | 删除或打码 |
| 地理位置 / 住址 / 家庭网络 | 私网 IP、内网拓扑、宽带账号 | 删除或泛化 |
| 会话/访问凭据 | token、cookie、API Key、OAuth secret、SSH 私钥 | 一律删除 |
| 私人文档/合同/账目 | 服务合同、数据统计、账本 | 不随平台源码上传 |
| 生成产物含真实人脸 | 真实人物照片/视频 | 不上传 |

### 隐私扫描（发布脚本已内置，命中即中止）
scripts/publish-to-github.sh 内置 privacy_scan()，在构建快照后 & 推送前各执行一次：
- 扫描：邮箱、手机号（1[3-9]xxxxxxxxx）、15~19 位长数字（身份证/银行卡/订单）、个人机器名/姓名（默认关键词 exampleuser / yourname）。
- 命中则 exit 2 中止并列出命中行，不会上传。
- 扩展：项目根新建 .privacy-blocklist（每行一个正则/关键词）追加要拦截的词。

### 发布前人工复查清单
1. 运行 grep -rInoE "1[3-9][0-9]{9}" 快照目录 等扫描，确认 0 命中。
2. 确认快照里没有 dist/、*.tsbuildinfo、*.map、node_modules、*.log、*.env、/Users/用户名、内网 192.168.x。
3. 确认 README/LICENSE 不存在真实姓名/邮箱/手机号。
4. 用单次干净提交，且不把本地仓库的旧历史（可能含内部信息）带上公开仓库。

### 一旦误传怎么办
- 若是内容改动，可 git commit --amend + 强制推送覆盖，或在仓库页面删除该文件（但 GitHub 会保留历史——所以务必发布前就扫描）。
- 更稳妥：重新构建脱敏快照、用新的单次提交推送；必要时删除并重建仓库（历史一并清掉）。
- 若真被公开了含隐私的数据，应立即撤销相关 token/凭据并在 GitHub 删除仓库，避免被爬取。
