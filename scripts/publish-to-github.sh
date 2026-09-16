#!/usr/bin/env bash
# =============================================================================
# publish-to-github.sh —— AI漫剧 GitHub 一键发布（含脱敏与干净历史）
# 用途：把本地项目制作成"可开源"快照，创建 GitHub 仓库并推送。
#  - 剔除内网IP/密码/本机路径/API Key/实验脚本/大文件
#  - 用一次干净的单次提交（不公开含内部信息的旧历史）
#  - 自动绕开：Xcode协议卡git、fine-grained PAT不能建仓、gh不能写keyring、~/.gitconfig不能写
# 详见 docs/发布到GitHub开源指引.md
# 用法:
#   scripts/publish-to-github.sh
#   GITHUB_TOKEN=xxx scripts/publish-to-github.sh
#   PUBLISH_PRIVATE=true scripts/publish-to-github.sh
# =============================================================================
set -euo pipefail

OWNER="${GITHUB_OWNER:-your-github-username}"
REPO_NAME="${GITHUB_REPO:-aidrama-platform}"
VISIBILITY="${PUBLISH_PRIVATE:-false}"
BRANCH="main"
COMMIT_MSG="${GITHUB_COMMIT_MSG:-feat: AIDrama open-source AI short-drama video workbench}"
DESCRIPTION="${GITHUB_DESC:-AI short-drama video production workbench - one-liner to episode}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/aidrama-publish"

# ---- 找一个可用的 git（绕开 Xcode 协议坑）----
if [ -x "/Applications/Xcode.app/Contents/Developer/usr/bin/git" ]; then
  GIT="/Applications/Xcode.app/Contents/Developer/usr/bin/git"
elif command -v git >/dev/null 2>&1; then GIT="$(command -v git)"; else
  echo "错误：找不到可用 git，请先运行: sudo xcodebuild -license accept" >&2; exit 1; fi
export PATH="/Applications/Xcode.app/Contents/Developer/usr/bin:/opt/homebrew/bin:$PATH"

# ---- 隐私扫描：命中任一条即中止，避免把个人信息上传 GitHub ----
privacy_scan() {
  echo ">> [隐私扫描] 检查 邮箱/手机号/身份证/银行卡/真实人名…"
  local hits=0 f
  grep -rInoE "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}" "$WORK" 2>/dev/null \
    | grep -viE "noreply|@example|test|fixture|localhost|@users|\.(png|jpg|json|ts|tsx|py|js|css|sh|txt|md)$" > /tmp/aidr_pv_email || true
  grep -rInoE "1[3-9][0-9]{9}" "$WORK" 2>/dev/null | grep -viE "node_modules|/dist/|/.git/" > /tmp/aidr_pv_phone || true
  grep -rInoE "[0-9]{15,19}" "$WORK" 2>/dev/null | grep -viE "node_modules|/dist/|/.git/|0000000000|alembic|package|lock" > /tmp/aidr_pv_id || true
  : > /tmp/aidr_pv_name
  # 真实人名/敏感标识只从 gitignored 的 .privacy-blocklist 读取（占位示例词不在此列）
  if [ -f "$PROJECT_ROOT/.privacy-blocklist" ]; then
    while IFS= read -r kw; do [ -n "$kw" ] && grep -rInoE "$kw" "$WORK" 2>/dev/null | grep -viE "/.git/" >> /tmp/aidr_pv_name || true; done < "$PROJECT_ROOT/.privacy-blocklist"
  fi
  # .privacy-allow 白名单：放行文档/脚本里的占位示例（占位邮箱、示例用户名等，非真实个人信息）
  if [ -f "$PROJECT_ROOT/.privacy-allow" ]; then
    for f in /tmp/aidr_pv_email /tmp/aidr_pv_phone /tmp/aidr_pv_id /tmp/aidr_pv_name; do
      [ -s "$f" ] || continue
      while IFS= read -r allowkw; do
        [ -n "$allowkw" ] && { grep -vF "$allowkw" "$f" > "$f.tmp" 2>/dev/null || true; mv "$f.tmp" "$f" 2>/dev/null || true; }
      done < "$PROJECT_ROOT/.privacy-allow"
    done
  fi
  for f in /tmp/aidr_pv_email /tmp/aidr_pv_phone /tmp/aidr_pv_id /tmp/aidr_pv_name; do
    if [ -s "$f" ]; then hits=$((hits+1)); echo ">> [隐私告警] $(basename "$f") 命中："; cat "$f" | head -20; fi
  done
  if [ "$hits" -gt 0 ]; then echo "错误：检测到疑似个人信息，已中止上传。请脱敏（或加入 .privacy-allow 白名单）。" >&2; exit 2; fi
  echo ">> [隐私扫描] 通过（无邮箱/手机/身份证/姓名命中）"
}

scrub_and_copy() {
  echo ">> 准备公开快照：$WORK"; rm -rf "$WORK"; mkdir -p "$WORK/backend" "$WORK/frontend"
  EX="--exclude=.git --exclude=.env --exclude=.env.local --exclude=.venv --exclude=node_modules --exclude=dist --exclude=out --exclude=tmp --exclude=logs --exclude=.toolchain --exclude=deliverables --exclude=.evidence --exclude=__pycache__ --exclude=.pytest_cache --exclude=.pnpm-store --exclude=*.tsbuildinfo --exclude=*.map --exclude=coverage --exclude=*.log"
  rsync -a $EX --exclude=scripts "$PROJECT_ROOT/backend/app" "$WORK/backend/"
  rsync -a $EX "$PROJECT_ROOT/backend/alembic" "$WORK/backend/"
  rsync -a $EX "$PROJECT_ROOT/backend/tests" "$WORK/backend/"
  rsync -a $EX "$PROJECT_ROOT/backend/docs" "$WORK/backend/"
  cp "$PROJECT_ROOT/backend"/pyproject.toml requirements.txt alembic.ini "$WORK/backend/" 2>/dev/null || true
  rsync -a $EX "$PROJECT_ROOT/frontend/src" "$WORK/frontend/"
  cp "$PROJECT_ROOT/frontend"/package.json tsconfig.json tsconfig.node.json vite.config.ts vite.config.js index.html postcss.config.js tailwind.config.js pnpm-workspace.yaml pnpm-lock.yaml "$WORK/frontend/" 2>/dev/null || true
  rsync -a $EX --exclude=data --exclude=logs --exclude=.env "$PROJECT_ROOT/dashboard" "$WORK/"
  cp "$PROJECT_ROOT"/docker-compose.yml .gitignore .env.example AGENTS.md LICENSE "$WORK/" 2>/dev/null || true
  cp "$PROJECT_ROOT"/README.md "$WORK/" 2>/dev/null || true
  mkdir -p "$WORK/docs" "$WORK/scripts"
  cp "$PROJECT_ROOT/docs/发布到GitHub开源指引.md" "$PROJECT_ROOT/docs/视频子系统收敛设计方案.md" "$WORK/docs/" 2>/dev/null || true
  cp "$PROJECT_ROOT/scripts/publish-to-github.sh" "$WORK/scripts/" 2>/dev/null || true
  sed -i '' '/^models\/$/d' "$WORK/.gitignore" 2>/dev/null || true
  sed -i '' '/^dashboard\/$/d' "$WORK/.gitignore" 2>/dev/null || true
  find "$WORK" -type f \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.md" -o -name "*.yml" -o -name "*.yaml" -o -name "*.json" -o -name "*.js" -o -name "*.sh" -o -name "*.ini" -o -name "*.txt" \) -print0 | xargs -0 sed -i ''     -e 's/192\.168\.3\.178/10.0.0.2/g' -e 's/192\.168\.3\.177/10.0.0.1/g' -e 's/192\.168\.50/10.0.0/g'     -e "s|${HOME}|~|g" -e 's/<password>/<password>/g' 2>/dev/null || true
  echo ">> 快照完成，文件数：$(find "$WORK" -type f | wc -l | tr -d ' ')"
  privacy_scan
}

init_repo() {
  echo ">> 初始化干净仓库并做单次提交（不公开旧历史）"; cd "$WORK"
  "$GIT" init -q -b "$BRANCH"; "$GIT" config user.name "$OWNER"; "$GIT" config user.email "$OWNER@users.noreply.github.com"
  "$GIT" add -A; "$GIT" commit -q -m "$COMMIT_MSG"
  "$GIT" remote remove origin 2>/dev/null || true; "$GIT" remote add origin "https://github.com/$OWNER/$REPO_NAME.git"
  echo ">> 提交：$("$GIT" rev-parse --short HEAD)  文件数：$("$GIT" ls-files | wc -l | tr -d ' ')"
}

ensure_token() {
  CLIENT_ID="178c6fc778ccc68e1d6a"
  if [ -n "${GITHUB_TOKEN:-}" ]; then TOKEN="$GITHUB_TOKEN"; echo ">> 使用外部传入的 GITHUB_TOKEN"; return; fi
  if [ -f "$WORK/../ghcfg/token" ] && [ -s "$WORK/../ghcfg/token" ]; then TOKEN="$(cat "$WORK/../ghcfg/token")"; echo ">> 复用已有 token"; return; fi
  echo ">> 申请 GitHub 设备授权码…"
  local resp uc uri dcode
  resp=$(curl -s -X POST https://github.com/login/device/code -H "Accept: application/json" -d "client_id=$CLIENT_ID&scope=repo workflow")
  uc=$(echo "$resp" | sed -n 's/.*"user_code":"\([^"]*\)".*/\1/p')
  uri=$(echo "$resp" | sed -n 's/.*"verification_uri":"\([^"]*\)".*/\1/p')
  dcode=$(echo "$resp" | sed -n 's/.*"device_code":"\([^"]*\)".*/\1/p')
  echo ">> 请到 $uri 输入授权码：${uc}  然后回车继续"; read -r _
  echo ">> 等待授权…"
  for _ in $(seq 1 100); do
    local r tok
    r=$(curl -s -X POST https://github.com/login/oauth/access_token -H "Accept: application/json" -d "client_id=$CLIENT_ID&device_code=$dcode&grant_type=urn:ietf:params:oauth:grant-type:device_code")
    tok=$(echo "$r" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
    if [ -n "$tok" ]; then TOKEN="$tok"; mkdir -p "$WORK/../ghcfg"; echo "$tok" > "$WORK/../ghcfg/token"; echo ">> 已取得 token"; return; fi
    sleep 6
  done
  echo "错误：授权超时" >&2; exit 3
}

create_and_push() {
  echo ">> 创建 GitHub 仓库（${VISIBILITY}）"
  curl -s -X POST https://api.github.com/user/repos -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" -d "{\"name\":\"$REPO_NAME\",\"private\":$VISIBILITY,\"description\":\"$DESCRIPTION\"}" | grep -q '"full_name"' && echo ">> 仓库创建/已存在：$OWNER/$REPO_NAME" || true
  echo ">> 推送（禁用凭据助手，token 走 URL；推后恢复干净 origin）"; cd "$WORK"
  "$GIT" config --local --unset credential.helper 2>/dev/null || true
  GIT_TERMINAL_PROMPT=0 "$GIT" -c credential.helper= push -u "https://x-access-token:${TOKEN}@github.com/$OWNER/$REPO_NAME.git" "$BRANCH"
  "$GIT" remote set-url origin "https://github.com/$OWNER/$REPO_NAME.git"
  echo ">> 完成：https://github.com/$OWNER/$REPO_NAME"
}

main() { scrub_and_copy; init_repo; privacy_scan; ensure_token; create_and_push; echo ">> 推送成功，提交：$("$GIT" -C "$WORK" rev-parse --short HEAD)"; echo ">> 提示：token 已临时写入 $WORK/../ghcfg/token（重启失效），建议到 GitHub 撤销该授权。"; }
main "$@"
