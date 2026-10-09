# mcp-config —— 各平台 MCP 配置样例

**当前只提供 WorkBuddy 的样例**；其他平台留待后续适配时逐个补齐。

| 文件 | 平台 | 状态 |
|---|---|---|
| [`workbuddy.json`](./workbuddy.json) | WorkBuddy（自定义连接器） | ✅ 已适配 |
| *(待补)* | Kiro / Claude Code / Cursor / VSCode / Cherry Studio / Claude Desktop | ⏳ 占位 |

所有样例**只含 `${MCD_MCP_TOKEN}` 占位符**，不含任何真实凭据。

## WorkBuddy 使用步骤

1. 左侧【专家·技能·连接器】→【连接器】→【自定义连接器】→【配置MCP】；
2. 粘贴 `workbuddy.json` 的内容，把 `${MCD_MCP_TOKEN}` 换成你的真实 Token；
3. 保存并**启用**该连接器；
4. 用自然语言触发，例如「算算我最近在麦当劳吃了多少卡路里」。

> 界面里配置的连接器只供 WorkBuddy 调用 MCP 工具；
> 要让**命令行**也能直接跑，请在技能目录放一份 `.env`（`bash install.sh workbuddy --token "..."` 会自动生成）。
