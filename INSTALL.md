# 安装与配置指南

「订单卡路里一览」遵循 [Anthropic Agent Skills 开放规范](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)（`SKILL.md`），
底层能力通过麦当劳官方 MCP（`mcd-mcp`）获取**只读**数据。

> **当前只完成 WorkBuddy 渠道的适配**；其他平台的适配留待后续实现，
> 在 `install.sh` 与下表里都是**明确标注的占位**（不会静默装到别处）。
> 任何能读 `SKILL.md` 的 Agent 工具，都可以先用下面的「手工安装」路径使用。

安装分两步：**① 装 Skill 本体** + **② 配置 mcd-mcp 连接**。

> 本项目**零第三方依赖**（纯 Python 标准库），所以没有 `npm install` / `pip install` 这一步：
> 复制过去就能跑，`python3` 版本 ≥ 3.9 即可。

---

## 平台适配状态

| 平台 | 技能目录 | 状态 |
|---|---|---|
| **WorkBuddy** | `~/.workbuddy/skills/mcd-order-calories/` | ✅ **已适配**（`bash install.sh`） |
| Kiro | `~/.kiro/skills/…` | ⏳ 占位，尚未适配 |
| Claude Code | `~/.claude/skills/…` | ⏳ 占位，尚未适配 |
| Claude Desktop | Settings → Capabilities → Skills | ⏳ 占位，尚未适配 |
| Cursor | `~/.cursor/skills/…` | ⏳ 占位，尚未适配 |
| VSCode | `~/.vscode/skills/…` | ⏳ 占位，尚未适配 |
| Cherry Studio | 连接器形式使用 | ⏳ 占位，尚未适配 |
| 其他 Agent | 任意目录 | ⏳ 占位（可用 `--dir` / 手工复制替代） |

> 占位渠道现在执行 `bash install.sh kiro` 会打印「尚未适配」并以退出码 `2` 结束——
> 这是有意为之：宁可明确失败，也不要把 Skill 装到一个没验证过的位置。

---

## 前置准备

1. 环境：Python >= 3.9（`python3 --version` 能跑就行）。
2. 申请麦当劳 MCP Token：访问 <https://github.com/M-China/mcd-mcp-server> 按其说明申请。
3. 克隆仓库：
   ```bash
   git clone <你的仓库地址>
   cd mcd-order-calories
   ```
4. 自检（确认能连上 MCP，并看到可用工具）：
   ```bash
   PYTHONPATH=src python3 -m mcd_order_calories.cli --list-tools
   ```
   输出里应能看到 `order-list` 与 `list-nutrition-foods`，以及 `结论：可用于折算`。

> 🔐 Token 只从环境变量 / `.env` / 命令行参数读取，诊断输出里形如 `abcd…wxyz`。
> 仓库内不含任何真实凭据，配置样例只有 `${MCD_MCP_TOKEN}` 占位符。

---

## ① 安装 Skill 本体（WorkBuddy）

### 一键安装

```bash
bash install.sh                                # 装到 ~/.workbuddy/skills/mcd-order-calories/
bash install.sh --token "$MCD_MCP_TOKEN"       # 顺带生成 .env，命令行也能直接用
bash install.sh --dir /tmp/try --dry-run       # 先看会做什么，不写任何文件
```

`install.sh` 做三件事：

1. **同步技能目录**（`rsync --delete`，幂等复装；排除 `.env` / `__pycache__` / `.git`，避免 Token 被一并带走）；
2. **按需生成 `.env`**（仅在传 `--token` 时；权限 `600`）——只影响命令行，界面里仍需单独填 Token；
3. **打印 WorkBuddy 界面配置指引**（见下一步）。

不传 `--token` 也可以安装：Skill 本体与 `SKILL.md` 会就位，Token 全部在界面里填。

### 手工安装（任何平台）

```bash
cp -R mcd-order-calories ~/.workbuddy/skills/          # WorkBuddy
cp -R mcd-order-calories <任意 Agent 的 skills 目录>/    # 其他平台（未适配，仅供参考）
```

Agent Skill 的本质就是「一个含 `SKILL.md` 的目录」。装好后，Agent 读 `SKILL.md`
判断「何时使用」；真正的取数与折算由本项目的 Python CLI 完成
（`PYTHONPATH=src python3 -m mcd_order_calories.cli`）。

---

## ② 配置 mcd-mcp 连接（WorkBuddy）

WorkBuddy 用**自定义连接器**接 MCP，配置在界面里完成：

1. 左侧【专家·技能·连接器】→【连接器】→【自定义连接器】→【配置MCP】；
2. 粘贴 [`mcp-config/workbuddy.json`](./mcp-config/workbuddy.json) 的内容，
   把 `${MCD_MCP_TOKEN}` 换成你的真实 Token；
3. 保存 → **启用**该连接器；
4. 重启/重载 WorkBuddy 后即可用自然语言触发。

配置内容（`mcp-config/workbuddy.json`，脱敏样例）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

> 界面连接器供 **WorkBuddy 调用 MCP 工具**；技能目录里的 `.env` 供**命令行**直接运行。
> 两者独立：只想在对话里用，只配第 ② 步；想跑 `mcd-calories`，再加 `.env`。

### 其他平台的 MCP 配置（尚未适配，暂留白）

Kiro / Cursor / VSCode 用 `type: streamable-http`，Cherry Studio / Trae 用 `type: streamablehttp`，
Claude Desktop 需要 `mcp-remote` 做 stdio 桥接，Claude Code 用
`claude mcp add --transport http`。这些样例**尚未提供**，等对应渠道适配时一并补进 `mcp-config/`。

---

## 验证可用

配置完成后，用自然语言触发：

- "算算我最近在麦当劳吃了多少卡路里"
- "我这几单麦乐鸡加起来多少热量？"
- "有没有算不出来的商品？"
- "「那么大鸡排（椒盐风味）」在营养表里对应哪一条？"

或直接命令行验证（不依赖任何 Agent 工具）：

```bash
PYTHONPATH=src python3 -m mcd_order_calories.cli --list-tools   # 连通性自检
PYTHONPATH=src python3 -m mcd_order_calories.cli --detail       # 订单卡路里一览
```

安装为命令行工具后可以更短：

```bash
python3 -m pip install -e .
mcd-calories --detail
```

---

## 常见问题

- **报「缺少 MCP Token」**：按提示三选一——写好 `.env`、`export MCD_MCP_TOKEN=...`、或 `--token` 传入。
  自检时用 `--list-tools --json` 可以看到 `token`（已脱敏）与**生效的 `.env` 路径**。
- **`.env` 放在哪都能生效吗**：查找顺序是 `$MCD_ENV_FILE` → 当前目录 `.env` → 技能根目录 `.env`，
  已存在的环境变量优先级最高（不会被文件覆盖）。
- **`bash install.sh kiro` 报「尚未适配」**：当前只完成 WorkBuddy。请用
  `bash install.sh`（WorkBuddy）或 `bash install.sh --dir /任意/目录`。
- **界面里能用了，但命令行报缺 Token**：界面连接器与命令行是两套凭据入口，
  再跑一次 `bash install.sh --token "..."` 生成技能目录下的 `.env` 即可。
- **429 限流**：MCP 有每分钟请求上限，本 Skill 单次运行**只发 2 次业务调用**，正常使用不会触发。
- **没有 Token 能不能验证**：可以。测试完全离线、零依赖：
  `PYTHONPATH=src python3 -m unittest discover -s tests -t tests`（133 项）。
