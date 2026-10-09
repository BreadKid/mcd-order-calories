# MCP 集成说明

本文件说明**订单卡路里一览**实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。按大赛要求，这里写的是项目**真实发起**的调用，而非设计构想。

---

## 一、使用的 MCP Server

| 项目 | 值 |
|---|---|
| Server 名称 | `mcd-mcp`（麦当劳中国 MCP Server） |
| 类型 | `streamablehttp` |
| 地址 | `https://mcp.mcd.cn` |
| 鉴权 | HTTP Header `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 协议版本 | `2025-06-18` |
| 客户端实现 | 本项目自带 `src/mcd_order_calories/mcp_client.py`（仅 Python 标准库，无第三方依赖） |
| Token 申请 | https://github.com/M-China/mcd-mcp-server |

客户端实现的 MCP 方法：`initialize` → `notifications/initialized` → `tools/list` → `tools/call`，并透传服务端下发的 `Mcp-Session-Id`、退出时尝试 `DELETE` 释放会话。

> ⚠️ Token 为个人申请，仅通过环境变量、本地 `.env` 或宿主连接器传入，**不写入本仓库任何文件**。
> 仓库内的 [`mcp-config.example.json`](./mcp-config.example.json) 与
> [`mcp-config/workbuddy.json`](./mcp-config/workbuddy.json) **只含环境变量占位符**。
> WorkBuddy 的接入方式见 [`INSTALL.md`](./INSTALL.md)。

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

---

## 二、使用的 Tool 清单

### 2.1 核心工具（每次运行必调）

| Tool | 用途 | 在本项目中的角色 |
|---|---|---|
| `order-list` | 查询历史订单（非商城订单） | **唯一能知道你实际吃了什么的数据源**。返回订单商品、套餐成分 `comboItemList`、门店、`realTotalAmount` 与 `orderStatus`。⚠️ **无分页参数，只返回最近 10 条**（实测：新订单会把最老一条挤出窗口） |
| `list-nutrition-foods` | 获取餐品营养成分 | **唯一有热量的数据源**。返回 158 条商品名 → `energyKcal` |

**这两个工具的关系就是本项目的全部难点**：订单按 `productCode` + 商品名组织，营养表**只有商品名、没有 code**，所以只能按名字对齐。

### 2.2 其他工具（本项目的实际调用不含它们）

| Tool | 用途 | 本项目是否调用 |
|---|---|---|
| `query-meals` | 查门店当前菜单（可用于按 productCode 反查官方商品名，但实测命中率仅 35%——菜单只反映**当前在售**，季节品与已下架商品查不到） | ❌ 不调用 |
| `query-meal-detail` | 查餐品套餐组成 | ❌ 不调用 |
| `now-time-info` | 服务端当前时间 | ❌ 不调用 |

> **实际调用面只有两个只读查询接口**：`order-list` 与 `list-nutrition-foods`。
> `--list-tools` 自检时只做「工具清单里有没有这两个」的检查，不会因为缺少其他工具而失败。
> 未来若要用 `query-meals` 做交叉验证，会在本节把「实测命中率」一并升级为正式数据。

> **本项目不调用任何写操作**：不涉及 `create-order` / `mall-create-order` / `party-order-create` / `cancel-order` / `auto-bind-coupons` / `draw-lottery` / `delivery-create-address`。全程只读。

---

## 三、调用流程

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#DA291C', 'primaryTextColor': '#fff', 'secondaryColor': '#FFC72C', 'tertiaryColor': '#27251F', 'lineColor': '#27251F'}}}%%
flowchart TB
    A["读取 MCD_MCP_TOKEN<br/>缺失则退出码 2，不发请求"] --> B["initialize<br/>+ notifications/initialized"]
    B --> C["tools/list<br/>确认 order-list 与 list-nutrition-foods 存在"]
    C --> D["list-nutrition-foods<br/>解析 158 条商品 → 热量"]
    D --> E["order-list<br/>取出历史订单"]
    E --> F["展开套餐<br/>用 comboItemList 替换套餐容器名"]
    F --> G["多级级联匹配<br/>alias → exact → supplement → category → stripped → guarded"]
    G --> H["汇总每单热量<br/>并计算覆盖率"]
    H --> I["渲染<br/>覆盖率不足时标注为下界"]
    style A fill:#FFC72C,color:#27251F
    style C fill:#FFC72C,color:#27251F
    style D fill:#FFC72C,color:#27251F
    style E fill:#FFC72C,color:#27251F
    style F fill:#DA291C,color:#fff
    style G fill:#DA291C,color:#fff
    style H fill:#FFC72C,color:#27251F
    style I fill:#27251F,color:#fff
```

### 多级匹配的详细决策流

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#DA291C', 'primaryTextColor': '#fff', 'secondaryColor': '#FFC72C', 'tertiaryColor': '#27251F', 'lineColor': '#27251F'}}}%%
flowchart TB
    N["商品名"] --> A{"① 别名表命中？"}
    A -->|是| A1["alias<br/>人工复核，最高信任"]
    A -->|否| B{"② 归一化后完全相等？"}
    B -->|是| B1["exact<br/>抹掉全半角/括号/连字符/空格"]
    B -->|否| C{"③ 补充热量表命中？"}
    C -->|是| C1["supplement<br/>人工推导，附区间与来源"]
    C -->|否| D{"④ 命中品类规则？"}
    D -->|是| D1["category<br/>蘸酱/【美汁源】/厚松饼堡"]
    D -->|否| E{"⑤ 剥尾部括号后完全相等？"}
    E -->|是| E1["stripped<br/>且须通过硬约束"]
    E -->|否| F["收集候选<br/>包含关系 或 相似度 ≥ 0.30"]
    F --> G["硬约束过滤<br/>含糖/杯型/规格/数量/冷热"]
    G --> H{"通过硬约束的候选数"}
    H -->|恰好 1 且相似度 ≥ 0.60| I["guarded"]
    H -->|多于 1| J["ambiguous<br/>交人工，不猜"]
    H -->|0| K["unmatched<br/>如实计入未覆盖"]
    style A1 fill:#27251F,color:#fff
    style B1 fill:#27251F,color:#fff
    style C1 fill:#FFC72C,color:#27251F
    style D1 fill:#FFC72C,color:#27251F
    style E1 fill:#27251F,color:#fff
    style I fill:#27251F,color:#fff
    style J fill:#DA291C,color:#fff
    style K fill:#DA291C,color:#fff
```

---

## 四、工程细节

### 4.1 踩过的两个真实坑

**① 服务端返回的不是裸 JSON，而是「字段说明 + JSON」混排：**

```
## Response Structure
- **门店名称**: data[].storeName      ← 注意这里的 data[]
## Original Response
{"success":true,...,"data":[...]}
```

说明段里的 `data[]` **含方括号**，会让 `\[.*\]` 这类简单正则抢先匹配、解析出空结果（本项目第一版就中了）。稳健做法是**从最后一个 `{"success"` 起**截取。

**② 营养表是自定义分隔格式**，既不是 JSON 也不是标准 CSV：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
```

表头声明 160 条，实际去重后 158 条（`小杯玉米杯`、`纯牛奶（盒装）` 重复）。本项目会**如实报告数据源自身的质量问题**，而不是静默吞掉。

### 4.2 未支付订单必须排除

`order-list` 会返回**未支付**订单——实测账号里就有 1 条「待支付」。若直接计入热量合计，会把**还没吃到的东西算成已摄入热量**。

因此本项目把 `orderStatus` 归类（规则见 `data/order_status.json`）：

| 分类 | 是否计入合计 | 说明 |
|---|---|---|
| `consumed`（订单已完成） | **是** | 明确代表已吃到 |
| `pending`（待支付） | 否 | 单独展示，标注「未支付，还没吃到」 |
| `in_progress`（备餐中/待取餐…） | 否 | 尚未完成 |
| `cancelled`（已取消/退款） | 否 | 未发生消费 |
| `other`（未识别的状态） | 否 | **保守排除并披露** |

兜底原则：**宁可少算并说明，也不要多算不说**。未列出的状态一律归入 `other` 且不计入合计。

待支付订单的卡路里仍然会算出来并单独展示——**它本身是有用信息**（"你有个待支付订单，约 436 kcal"），只是不能混进"已摄入"。

### 4.3 错误处理

| 情况 | 行为 |
|---|---|
| 无 Token | 退出码 `2`，提示申请地址，**不发网络请求** |
| Token 无效 / 过期 | 退出码 `1`，翻译为 `HTTP 401 ... check MCD_MCP_TOKEN is set and valid`，并附服务端原文 |
| 网络不可达 / 超时 | 退出码 `1`，报告目标 URL 与底层原因 |
| 服务端返回 JSON-RPC error | 抛出 `McpError`，携带 `code` 与 `message` |
| 返回文本里没有 JSON | 抛出 `PayloadError`，打印前 120 字符便于定位 |
| 补充表缺少推导/区间/来源 | **加载期直接报错**，不允许无依据的数值进表 |
| 品类规则正则写错 | **加载期直接报错**，避免规则静默失效退化成「查不到」 |

### 4.4 安全与脱敏

- Token 只从环境变量、`.env` 或 `--token` 读取；诊断输出中脱敏为 `abcd…wxyz`，**不进日志、不进交付物**。
- 各平台 MCP 配置样例（`mcp-config/`）只含 `${MCD_MCP_TOKEN}` 占位符。
- 仓库内不含真实 Token、密钥、账号凭证、手机号、邮箱或他人个人信息。
- 仓库内的运行示例**已隐去账号标识**，只保留商品与热量结构。
- **全程只读**：不调用任何下单 / 领券 / 抽奖 / 改地址等写接口。
- 仅通过官方 MCP 接口访问数据，未抓取、未内嵌任何非公开数据。

### 4.5 Agent 适配（当前只做 WorkBuddy）

本项目的 MCP 能力有两种消费方式，彼此独立：

| 路径 | 谁在调用 | Token 从哪来 |
|---|---|---|
| **宿主直连 MCP** | Agent 工具自己——当前已适配 **WorkBuddy** 自定义连接器，按 `mcp-config/workbuddy.json` 调用 `order-list` 与 `list-nutrition-foods` | 连接器配置里的 `Authorization: Bearer ${MCD_MCP_TOKEN}` |
| **CLI / 库调用** | 本项目自带的零依赖 Streamable HTTP 客户端 | `$MCD_MCP_TOKEN` → 当前目录 `.env` → 技能根目录 `.env`（查找顺序见 `src/mcd_order_calories/config.py`），或 `--token` |

适配要点：

1. **`SKILL.md` 是唯一入口描述**（Anthropic Agent Skills 规范），负责「何时使用 / 何时不使用 / 输出约定」；
2. **`install.sh` 负责落盘位置**，默认 `~/.workbuddy/skills/mcd-order-calories/`，也可用 `--dir` 指定任意目录；
3. **未适配渠道是显式占位**：`bash install.sh kiro` 会打印「尚未适配」并以退出码 `2` 结束、
   **不写任何文件**——宁可明确失败，也不要把 Skill 装到没验证过的位置；
4. **`.env` 查找不依赖工作目录**，因为 Skill 常被复制到别处；已存在的环境变量优先级最高；
5. **界面连接器与命令行是两套凭据入口**：前者供 WorkBuddy 调用 MCP 工具，后者靠技能目录的 `.env`。

> 后续补齐其他渠道时，需同步更新 `install.sh`、`mcp-config/`、`INSTALL.md` 与本节的表格。

### 4.6 兼容性

仅依赖 Python 标准库（`json` / `urllib` / `socket` / `re` / `unicodedata` / `dataclasses`），Python 3.9+ 直接运行，无需安装 MCP SDK。测试用标准库 `unittest`，`clone` 下来零安装即可复现全部 133 项。

---

## 五、业务价值

### 5.1 对用户

1. **补上 App 缺失的一环**——麦当劳 App 只显示订单，不显示热量。本项目把「你实际吃进去什么」变成看得懂的数字。
2. **套餐自动展开**——「人气经典随心配」这类容器名无法直接匹配营养表，本项目用 `comboItemList` 展开成真实成分再说。
3. **不骗人**——算不出来的部分**如实披露**，而不是拿一个偏低的下界冒充总量。实测某单只覆盖 1/4 时算出 87 kcal，而真实值在 800 kcal 以上；只给数字不给出覆盖率是危险的。
4. **每个数都能追溯**——估算值附区间、置信度与推导过程。

### 5.2 对麦当劳

1. **激活低频 Tool**——`list-nutrition-foods` 在点餐类作品里几乎只被当作"热量排序"的辅助数据，本项目把它与 `order-list` 真正**联接**起来，形成新的数据价值。
2. **不产生额外负载**——单次运行仅 2 次 MCP 调用（`list-nutrition-foods` + `order-list`），无循环、无爬虫式流量。
3. **全站最克制的一类作品**——只读、不下单、不改账号状态，零副作用。

### 5.3 作为 MCP 工程范式参考

项目沉淀了四个可复用实践：

1. **混合数据源对齐**——当两个接口无法用 ID 关联、只能靠名称匹配时，如何用「归一化 + 硬约束 + 唯一性 + 阈值标定」建立可审计的匹配层；
2. **假阳性优先防御**——用真实数据标定阈值，并记录已知假阳性作为回归防线；
3. **覆盖率披露原则**——宁可给出带区间的下界，也不给一个看起来精确的错数；
4. **估算值可审计**——强制要求推导过程 + 误差区间 + 非官方来源标注，缺一即加载报错。

---

## 六、如何自行验证

```bash
cp .env.example .env                    # 0) 填入真实 MCD_MCP_TOKEN

mcd-calories --list-tools               # 1) 连通性与所需工具（含脱敏 Token 与生效的 .env）
mcd-calories                            # 2) 订单卡路里一览
mcd-calories --detail                   # 3) 展开每单明细与推导
mcd-calories explain 双层脆鸡堡           # 4) 单商品判定理由与候选
mcd-calories gaps                       # 5) 未匹配商品（已知缺口 / 新出现）

PYTHONPATH=src python3 -m unittest discover -s tests -t tests   # 6) 133 项测试
bash install.sh --dir /tmp/wb --dry-run  # 7) WorkBuddy 安装演练（不写任何文件）
bash install.sh kiro                    # 8) 未适配渠道：应报「尚未适配」并退出码 2
```

未安装 CLI 时，把 `mcd-calories` 换成 `PYTHONPATH=src python3 -m mcd_order_calories.cli`。

**本项目不产生任何写操作**，全部命令都是只读的。
