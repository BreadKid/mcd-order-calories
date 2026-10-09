#!/usr/bin/env bash
#
# install.sh —— 把「订单卡路里一览」安装为 Agent Skill
#
# 当前**只适配 WorkBuddy**：其余平台先在 TARGETS 表里占位（明确报「尚未适配」），
# 后续按同一张表补实现即可。
#
# 本 Skill 遵循 Anthropic Agent Skills 开放规范（SKILL.md），且**零第三方依赖**
# （纯 Python 标准库），因此没有 npm install / pip install 这一步：复制过去就能跑。
#
# 用法：
#   bash install.sh [target] [选项]
#
#   target：workbuddy（默认，已适配）| kiro | claude-code | cursor | vscode
#           | cherry-studio | claude-desktop | generic（占位，尚未适配）
#           也可以直接给一个绝对路径作为自定义目录
#
# 选项：
#   --token <token>   生成 <技能目录>/.env（权限 600）供 CLI 直接运行；
#                     等价于环境变量 MCD_MCP_TOKEN
#   --dir <path>      覆盖技能落盘目录（默认 ~/.workbuddy/skills/mcd-order-calories）
#   --dry-run         只打印将要做什么，不写任何文件
#   -h, --help        显示本帮助
#
# 示例：
#   bash install.sh                                  # 装到 WorkBuddy
#   bash install.sh workbuddy --token "$MCD_MCP_TOKEN"
#   bash install.sh --dir /tmp/try --dry-run          # 先看看会做什么
#   bash install.sh kiro                              # 会明确提示「尚未适配」
#
set -euo pipefail

SKILL_NAME="mcd-order-calories"
MCP_NAME="mcd-mcp"
MCP_URL_DEFAULT="https://mcp.mcd.cn"
MCP_CONFIG_SAMPLE="mcp-config/workbuddy.json"

# 渠道适配状态（后续实现时按这张表逐个补齐，并同步 INSTALL.md / README.md）：
#   ✅ 已适配： workbuddy
#   ⏳ 占位  ： kiro / claude-code / cursor / vscode / cherry-studio
#              / claude-desktop / generic
ADAPTED_TARGETS="workbuddy"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET=""
DIR_OVERRIDE=""
TOKEN_VALUE="${MCD_MCP_TOKEN:-}"
DRY_RUN=0

# ---------------------------------------------------------------- 帮助 / 参数
usage() {
  cat <<'EOF'
用法：bash install.sh [target] [选项]

target：
  workbuddy        已适配（默认）
  kiro | claude-code | cursor | vscode | cherry-studio | claude-desktop | generic
                   占位，尚未适配（会明确报错并退出，不会误装）
  绝对路径         作为自定义技能目录

选项：
  --token <token>   生成 <技能目录>/.env（权限 600）供 CLI 直接运行
  --dir <path>      覆盖技能落盘目录（默认 ~/.workbuddy/skills/mcd-order-calories）
  --dry-run         只打印将要做什么，不写任何文件
  -h, --help        显示本帮助

示例：
  bash install.sh
  bash install.sh workbuddy --token "$MCD_MCP_TOKEN"
  bash install.sh --dir /tmp/try --dry-run
  bash install.sh kiro

本项目零第三方依赖，安装后无需 pip / npm 安装即可运行。
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)   usage; exit 0 ;;
    --token)     TOKEN_VALUE="${2:?--token 需要一个值}"; shift 2 ;;
    --token=*)   TOKEN_VALUE="${1#--token=}"; shift ;;
    --dir)       DIR_OVERRIDE="${2:?--dir 需要一个值}"; shift 2 ;;
    --dir=*)     DIR_OVERRIDE="${1#--dir=}"; shift ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -*)          echo "未知选项：$1（用 --help 查看用法）" >&2; exit 2 ;;
    *)           TARGET="$1"; shift ;;
  esac
done

# ------------------------------------------------------------------ 渠道解析
# 目标写法归一：claude-code / claude_code / claudecode 都认
normalize_target() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr '-' '_'
}

HOME_DIR="${HOME:-${USERPROFILE:-$PWD}}"

if [ -n "${DIR_OVERRIDE}" ]; then
  TARGET="workbuddy"                       # --dir 只在已适配渠道上有意义
  case "${DIR_OVERRIDE}" in
    /*) SKILL_DIR="${DIR_OVERRIDE}" ;;
    *)  SKILL_DIR="${SRC_DIR}/${DIR_OVERRIDE}" ;;
  esac
elif [ -z "${TARGET}" ]; then
  TARGET="workbuddy"
  SKILL_DIR="${HOME_DIR}/.workbuddy/skills/${SKILL_NAME}"
elif case "${TARGET}" in /*|./*|../*|~*) true ;; *) false ;; esac; then
  # 自定义目录：显式给路径就按「已适配」处理（至少 Skill 本体与 .env 是对的）
  SKILL_DIR="${TARGET}"
  TARGET="custom:${TARGET}"
else
  TARGET_RAW="${TARGET}"
  TARGET="$(normalize_target "${TARGET}")"
  SKILL_DIR="${HOME_DIR}/.workbuddy/skills/${SKILL_NAME}"
fi

case "${TARGET}" in
  custom:*)
    # 自定义目录：复制 + .env 可用；MCP 连接需在目标 Agent 里自行配置
    SKILL_DIR="${TARGET#custom:}"
    SKILL_DIR="${SKILL_DIR/#\~/${HOME_DIR}}"
    TARGET="workbuddy"          # 安装动作与 workbuddy 一致，只是目录不同
    ;;
  workbuddy*)   TARGET="workbuddy" ;;
esac

if [ "${TARGET}" != "workbuddy" ]; then
  echo "⚠️  target「${TARGET_RAW:-$TARGET}」尚未适配。" >&2
  echo "   当前只完成 WorkBuddy 渠道（skills 目录：~/.workbuddy/skills/${SKILL_NAME}/）。" >&2
  echo "   其他渠道的适配留待后续实现；现在可以用：" >&2
  echo "     bash install.sh workbuddy                 # 装到 WorkBuddy" >&2
  echo "     bash install.sh --dir /任意/目录           # 装到自定义目录" >&2
  echo "     cp -R . /任意/Agent 的 skills 目录/        # 手工复制（读 SKILL.md 的工具即可用）" >&2
  exit 2
fi

mask_token() {
  local value="$1"
  if [ -z "${value}" ]; then printf '(未提供，未生成 .env)'
  elif [ "${#value}" -le 8 ]; then printf '********'
  else printf '%s…%s' "${value:0:4}" "${value: -4}"; fi
}

echo "🍔 安装 ${SKILL_NAME} 到 [WorkBuddy]"
echo "   源目录  : ${SRC_DIR}"
echo "   技能目录: ${SKILL_DIR}"
echo "   Token   : $(mask_token "${TOKEN_VALUE}")"
[ "${DRY_RUN}" = "1" ] && echo "   ⚠️  dry-run：不会写入任何文件"

# ------------------------------------------------------------------ 1. 复制
copy_skill() {
  mkdir -p "$(dirname "${SKILL_DIR}")"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.env' --exclude '.env.*' \
      --exclude '__pycache__' --exclude '*.pyc' \
      --exclude '.git' --exclude '.venv' --exclude 'venv' \
      --exclude '.pytest_cache' --exclude '*.egg-info' \
      --exclude '.DS_Store' \
      "${SRC_DIR}/" "${SKILL_DIR}/"
  else
    mkdir -p "${SKILL_DIR}"
    cp -R "${SRC_DIR}/." "${SKILL_DIR}/"
    rm -rf "${SKILL_DIR}/.env" "${SKILL_DIR}/.git" "${SKILL_DIR}/.venv" \
           "${SKILL_DIR}/.pytest_cache"
    find "${SKILL_DIR}" \( -name '__pycache__' -o -name '.DS_Store' \) -prune \
      -exec rm -rf {} + 2>/dev/null || true
  fi
}

if [ "${DRY_RUN}" = "1" ]; then
  echo "ℹ️  [dry-run] 跳过复制（正式执行时会同步到 ${SKILL_DIR}）"
else
  copy_skill
  echo "✅ 已复制到技能目录（已排除 .env / 缓存，避免 Token 被一并带走）"
fi

# --------------------------------------------------------- 2. 生成 .env
write_env_file() {
  local token="$1"
  MCD_ENV_PATH="${SKILL_DIR}/.env" MCD_ENV_TOKEN="${token}" MCD_ENV_URL="${MCP_URL_DEFAULT}" \
  python3 - <<'PY'
import os

path = os.environ["MCD_ENV_PATH"]
lines = [
    "# 由 install.sh 生成；已被 .gitignore 忽略，请勿提交到公开仓库",
    f'MCD_MCP_TOKEN={os.environ["MCD_ENV_TOKEN"]}',
    f'MCD_MCP_URL={os.environ["MCD_ENV_URL"]}',
    "",
]
with open(path, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines))
os.chmod(path, 0o600)
print(f"✅ 已生成 {path}（权限 600，供 CLI 直接运行）")
PY
}

if [ "${DRY_RUN}" = "1" ]; then
  if [ -n "${TOKEN_VALUE}" ]; then
    echo "ℹ️  [dry-run] 跳过 .env 生成（目标：${SKILL_DIR}/.env）"
  else
    echo "ℹ️  [dry-run] 未提供 Token，不会生成 .env"
  fi
elif [ -n "${TOKEN_VALUE}" ]; then
  write_env_file "${TOKEN_VALUE}"
else
  echo "ℹ️  未提供 --token：未生成 .env。"
  echo "     - 想用命令行：bash install.sh workbuddy --token \"你的Token\""
  echo "     - 只想用 WorkBuddy 界面调用工具：按下一步在界面里配置 ${MCP_NAME} 即可"
fi

# ------------------------------------------------- 3. WorkBuddy 界面配置指引
echo ""
echo "🔌 下一步：在 WorkBuddy 里配置 MCP 连接"
echo "   左侧【专家·技能·连接器】→【连接器】→【自定义连接器】→【配置MCP】→ 粘贴配置 → 保存 → 启用"
echo "   配置样例（含占位符，把 \${MCD_MCP_TOKEN} 换成真实 Token）："
echo "     ${SKILL_DIR}/${MCP_CONFIG_SAMPLE}"
if [ -n "${TOKEN_VALUE}" ] && [ "${DRY_RUN}" != "1" ]; then
  echo "   已生成的 ${SKILL_DIR}/.env 只对命令行生效，界面里仍需填一次 Token。"
fi

echo ""
echo "🎉 安装完成！"
echo "   自检  : cd ${SKILL_DIR} && PYTHONPATH=src python3 -m mcd_order_calories.cli --list-tools"
echo "   使用  : cd ${SKILL_DIR} && PYTHONPATH=src python3 -m mcd_order_calories.cli --detail"
echo "   详细步骤与常见问题见：${SKILL_DIR}/INSTALL.md"
