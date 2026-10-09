---
name: mcd-order-calories
description: 把麦当劳历史订单折算成卡路里。当用户想知道「我在麦当劳吃过多少热量」「这几单热量多少」「我常点的东西热量多高」，或需要把订单商品与营养表对齐时使用。依赖麦当劳中国 MCP 的 order-list 与 list-nutrition-foods。只读，不涉及下单。
license: MIT
metadata:
  version: "1.0"
  runtime: python>=3.9
  dependencies: none
  mcp: mcd-mcp (https://mcp.mcd.cn)
  adapted-for: workbuddy
---

# 订单卡路里一览

> 通用 Agent Skill，遵循 Anthropic Agent Skills 开放规范（`SKILL.md`），
> 任何能读 `SKILL.md` 的 Agent 工具都可加载。
> **当前只完成 WorkBuddy 渠道的适配**；其他平台在 `install.sh` 与 `INSTALL.md`
> 里是明确标注的占位（执行会报「尚未适配」并不写任何文件）。
> 安装步骤见 [`INSTALL.md`](./INSTALL.md)，MCP 配置样例见 [`mcp-config/`](./mcp-config)。

把麦当劳**历史订单**折算成卡路里，并**如实披露数据覆盖率**。

## 何时使用

在以下任一情况下使用本 Skill：

- 用户问「我最近在麦当劳吃了多少卡路里」「这几单热量多高」；
- 用户想按订单、按门店、按时间段统计热量摄入；
- 用户想知道某个常点商品的热量；
- 用户需要把订单里的商品名与营养表对齐（归一化）。

不适用：**点餐推荐、省钱方案、下单**等场景——那些请使用麦当劳 MCP 的对应 Tool。本项目**全程只读**。

## 前置条件

- 环境变量 `MCD_MCP_TOKEN` 已设置（在 https://github.com/M-China/mcd-mcp-server 申请）；
  也支持从 `.env` 读取（查找顺序：`$MCD_ENV_FILE` → 当前目录 `.env` → Skill 根目录 `.env`），
  或由 `--token` 传入。已存在的环境变量优先级最高。
- 运行环境可访问 `https://mcp.mcd.cn`。

## 使用方式

### 方式一：命令行（推荐，无需 MCP 宿主）

```bash
mcd-calories --list-tools    # 先确认连接与所需工具（含脱敏 Token、生效的 .env 路径）
mcd-calories                 # 订单卡路里一览
mcd-calories --detail        # 展开每单商品明细与估算推导
mcd-calories --json          # 结构化输出
mcd-calories gaps            # 未匹配商品，区分「已知缺口」与「新出现」
mcd-calories explain <商品名>  # 单商品判定理由与全部候选
```

若未安装，用 `PYTHONPATH=src python3 -m mcd_order_calories.cli <子命令>`。
未配置 Token 时程序**不会编造数据**，而是打印配置引导并以退出码 2 结束——照做即可。

### 方式二：作为 Python 库嵌入

```python
from mcd_order_calories.mcp_client import McpHttpClient
from mcd_order_calories.payload import extract_data
from mcd_order_calories.nutrition import parse_nutrition
from mcd_order_calories.matching import ProductMatcher
from mcd_order_calories.orders import compute_orders
from mcd_order_calories.cli import (
    load_alias_table, load_category_catalog, load_supplement_catalog,
)

with McpHttpClient("https://mcp.mcd.cn", token=token) as client:
    nutrition, _ = parse_nutrition(extract_data(client.call_tool("list-nutrition-foods").text))
    orders = extract_data(client.call_tool("order-list").text)["list"]

matcher = ProductMatcher(nutrition, load_alias_table(),
                         categories=load_category_catalog(),
                         supplements=load_supplement_catalog())
for result in compute_orders(orders, matcher):
    print(result.render())
```

### 方式三：在支持 MCP 的宿主中使用（当前已适配 WorkBuddy）

把 [`mcp-config/workbuddy.json`](./mcp-config/workbuddy.json) 的内容粘贴进
WorkBuddy 的自定义连接器（【专家·技能·连接器】→【连接器】→【自定义连接器】→【配置MCP】），
替换 `${MCD_MCP_TOKEN}`，保存并启用，即可直接调用 `order-list` 与 `list-nutrition-foods`；
折算逻辑通过上面的 CLI 或库调用完成。其他平台的样例尚未提供（占位）。

## 安装到 Agent 工具

本 Skill 是「一个含 `SKILL.md` 的目录」，且**零第三方依赖**，复制过去即可运行。
**当前只适配 WorkBuddy**：

```bash
bash install.sh                          # 装到 ~/.workbuddy/skills/mcd-order-calories/
bash install.sh --token "$MCD_MCP_TOKEN" # 顺带生成 .env，命令行也能直接跑
bash install.sh --dir /任意/目录          # 自定义目录（未适配平台的临时方案）
bash install.sh kiro                     # 未适配渠道：明确报错并退出（不会误装）
```

`install.sh` 会同步技能目录（`rsync --delete`，**排除 `.env` 与缓存**）、
按需生成权限 `600` 的 `.env`、并打印 WorkBuddy 界面配置指引。
完整平台状态表、`--dry-run` 用法与常见问题见 [`INSTALL.md`](./INSTALL.md)。

## 调用链路

```
tools/list → list-nutrition-foods（158 条商品 → 热量）
           → order-list（历史订单）
           → 展开 comboItemList（套餐 → 真实成分）
           → 多级级联匹配（alias → exact → supplement → category → stripped → guarded）
           → 汇总 + 覆盖率 + 估算披露
```

单次运行**仅 2 次 MCP 调用**，无循环。

## 输出约定（调用方必须遵守）

1. **覆盖率必须一并输出。** 覆盖率不足 100% 时，数值是**下界**，必须标注 `≥` 与缺少项，并提示「请勿据此做热量控制」。
   - 实测某单只覆盖 1/4 时算出 87 kcal，而真实值在 800 kcal 以上。**只给数字是危险的。**
2. **估算值必须披露。** 带 `[supplement·估算]` 或 `[category·估算]` 标记的项，必须一并给出**区间、置信度与推导过程**，并说明「非营养表官方数据」。
3. **不得自行推测热量。** 所有数值必须来自营养表、别名表、品类规则或补充热量表；`unmatched` 项应如实列为缺口，**不要估算填充**。
4. **不得声称官方数据。** 估算值一律标注为估算。

## 维护约定（当出现未匹配商品时）

新增未匹配商品时，按以下顺序判断并登记，**不要随手加进别名表**：

| 情况 | 登记到 | 要求 |
|---|---|---|
| 与营养表某条目**语义等价**（仅命名不同） | `data/aliases.json` | 附判断依据 |
| 属于某个**品类**，同类热量可共享 | `data/categories.json` | 附来源说明 |
| 能**推导**出近似值 | `data/supplements.json` | **必须附推导过程 + 误差区间 + 非官方来源**（缺一即加载报错） |
| 确认**推导不出来** | `data/known_gaps.json` | 说明为什么 |

**硬性禁止**：同系列不同口味的不同单品、相似但不同的商品，不得声明等价。热量工具里的错配会**静默污染数值**，比承认一个缺口糟糕得多。

## 安全约束

- **Token 不落盘（除显式配置）**：只从环境变量、`.env` 或命令行参数读取；输出到日志时脱敏为 `abcd…wxyz`。
  `.env` 已被 `.gitignore` 忽略，`install.sh` 也只在目标目录生成权限 `600` 的 `.env`，且复制 Skill 时排除它。
- **全程只读**：只调用 `order-list` 与 `list-nutrition-foods`（以及诊断用的 `query-meals` / `now-time-info`），**不调用任何写操作**。
- **不输出个人敏感信息**：订单里的门店、金额、商品可以展示，但账号标识等信息不应写入交付物。
