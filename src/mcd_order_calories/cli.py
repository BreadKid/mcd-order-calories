"""订单卡路里一览 —— 命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import resources
from pathlib import Path
from typing import Any, Sequence

from .categories import CategoryCatalog
from .config import (
    DEFAULT_URL,
    ENV_TOKEN,
    ENV_URL,
    load_env,
    mask_secret,
)
from .matching import AliasTable, ProductMatcher
from .supplements import SupplementCatalog
from .mcp_client import McpError, McpHttpClient
from .nutrition import parse_nutrition
from .orders import STATUS_LABELS, GapLedger, OrderStatusPolicy, compute_orders
from .payload import PayloadError, extract_data

REQUIRED_TOOLS = ("order-list", "list-nutrition-foods")

TOKEN_HELP = """\
缺少 MCP Token：无法读取麦当劳订单。

配置方式（任选其一）：
  1. 复制并填写 .env（推荐）：
       cp .env.example .env      # 然后把 MCD_MCP_TOKEN 换成你的真实 Token
  2. 直接设置环境变量：
       export MCD_MCP_TOKEN="你的 Token"
  3. 命令行传入：
       mcd-calories --token "你的 Token"

Token 申请：https://github.com/M-China/mcd-mcp-server
说明：Token 只从环境变量 / .env / 命令行读取，本项目不会写入或上传任何凭据。"""


def _data_text(name: str) -> str:
    return resources.files("mcd_order_calories").joinpath("data", name).read_text(encoding="utf-8")


def load_alias_table() -> AliasTable:
    data = json.loads(_data_text("aliases.json"))
    return AliasTable({k: v for k, v in data.items()
                       if not str(k).startswith("_") and isinstance(v, str)})


def load_gap_ledger() -> GapLedger:
    return GapLedger.from_dict(json.loads(_data_text("known_gaps.json")),
                               source="known_gaps.json")


def load_category_catalog() -> CategoryCatalog:
    return CategoryCatalog.from_dict(json.loads(_data_text("categories.json")),
                                     source="categories.json")


def load_status_policy() -> OrderStatusPolicy:
    return OrderStatusPolicy.from_dict(json.loads(_data_text("order_status.json")),
                                       source="order_status.json")


def load_supplement_catalog() -> SupplementCatalog:
    return SupplementCatalog.from_dict(json.loads(_data_text("supplements.json")),
                                       source="supplements.json")


def _add_global_options(parser: argparse.ArgumentParser,
                        *, suppress: bool = False) -> None:
    """注册全局选项。

    ``suppress=True`` 用于子命令副本：这样 ``--url`` 写在子命令后面也能生效，
    且未显式传入时（``SUPPRESS``）不会把主 parser 已解析出的值覆盖成默认值。
    """
    default = argparse.SUPPRESS if suppress else None

    def value_default(fallback):
        return argparse.SUPPRESS if suppress else fallback

    parser.add_argument("--url", default=value_default(os.environ.get(ENV_URL, DEFAULT_URL)),
                        help=f"MCP 服务地址（默认 ${ENV_URL} 或 {DEFAULT_URL}）")
    parser.add_argument("--token", default=value_default(os.environ.get(ENV_TOKEN)),
                        help=f"麦当劳 MCP Token（默认读取 ${ENV_TOKEN}）")
    parser.add_argument("--timeout", type=float, default=value_default(60.0))
    parser.add_argument("--json", action="store_true", default=default,
                        help="以 JSON 输出")
    parser.add_argument("--list-tools", dest="list_tools", action="store_true",
                        default=default,
                        help="连接 MCP 并列出可用工具（等价于 doctor，便于各 Agent 自检）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcd-calories",
        description="订单卡路里一览 —— 把麦当劳历史订单折算成卡路里，并如实给出数据覆盖率",
    )
    _add_global_options(parser)

    # 不带子命令时默认执行 calories——`mcd-calories` 直接给结果，少敲一次
    sub = parser.add_subparsers(dest="command")

    subcommands = (
        sub.add_parser("doctor", help="检查连通性与所需工具（同 --list-tools）"),
        sub.add_parser("calories", help="输出历史订单的卡路里一览"),
        sub.add_parser("explain", help="解析单个商品名，展示判定理由与全部候选"),
        sub.add_parser("gaps", help="列出未匹配商品，区分「已知缺口」与「新出现」"),
    )
    for command in subcommands:
        _add_global_options(command, suppress=True)

    orders = subcommands[1]
    orders.add_argument("--detail", action="store_true", help="展开每单的商品明细")
    orders.add_argument("--limit", type=int, default=0, help="只显示最近 N 单")
    subcommands[2].add_argument("name", help="商品名，例如「可口可乐中杯」")

    return parser


DEFAULT_COMMAND = "calories"

# 全局选项（定义在主 parser 上）。argparse 要求它们出现在子命令**之前**，
# 所以补默认子命令时不能简单地把命令插到最前面。
_GLOBAL_OPTIONS_WITH_VALUE = ("--url", "--token", "--timeout")
_GLOBAL_OPTIONS_BOOL = ("--json", "--list-tools")


def inject_default_command(argv: Sequence[str], commands: Sequence[str],
                           default: str = DEFAULT_COMMAND) -> list[str]:
    """未指定子命令时补上默认命令，且保持全局选项在子命令之前。

    ``[]``                     → ``["calories"]``
    ``["--json"]``             → ``["--json", "calories"]``
    ``["--detail"]``           → ``["calories", "--detail"]``
    ``["--url", "u"]``         → ``["--url", "u", "calories"]``
    ``["--list-tools"]``       → ``["--list-tools", "doctor"]``
    ``["doctor"]``             → 原样返回
    """
    argv = list(argv)
    if any(token in commands for token in argv):
        return argv
    # `-h/--help` 是「看这个程序怎么用」，不该被补成 `calories --help`
    if "-h" in argv or "--help" in argv:
        return argv
    # `--list-tools` 本身就是一个完整诉求（自检），不该再补默认命令去拉订单
    if "--list-tools" in argv:
        return argv + ["doctor"]
    head: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _GLOBAL_OPTIONS_WITH_VALUE:
            head.extend(argv[index:index + 2])
            index += 2
            continue
        if token in _GLOBAL_OPTIONS_BOOL:
            head.append(token)
            index += 1
            continue
        break
    return head + [default] + argv[index:]


def _subcommands(parser: argparse.ArgumentParser) -> list[str]:
    for action in parser._actions:  # noqa: SLF001 - argparse 未提供公开查询方式
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return list(action.choices)
    return []


def run(argv: Sequence[str] | None = None) -> int:
    # 先加载 .env 再解析参数：这样 `--token` 的默认值也能吃到文件里的配置，
    # 装到各种 Agent 工具里「clone 下来就能跑」，不用手工 export。
    env_files = load_env()
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(inject_default_command(raw, _subcommands(parser)))
    if args.list_tools:
        args.command = "doctor"
    if not args.token:
        print(TOKEN_HELP, file=sys.stderr)
        return 2
    try:
        with McpHttpClient(args.url, token=args.token, timeout=args.timeout) as client:
            available = set(client.tool_names())
            missing = [t for t in REQUIRED_TOOLS if t not in available]
            if args.command == "doctor":
                return _doctor(args, client, available, missing, env_files)
            if missing:
                print(f"错误：服务端缺少必需工具 {missing}", file=sys.stderr)
                return 1

            nutrition, warnings = parse_nutrition(extract_data(client.call_tool("list-nutrition-foods").text))
            orders_raw = extract_data(client.call_tool("order-list").text) or {}
            matcher = ProductMatcher(nutrition, load_alias_table(),
                                     categories=load_category_catalog(),
                                     supplements=load_supplement_catalog())
            results = compute_orders(orders_raw.get("list") or [], matcher,
                                     load_status_policy())

            if args.command == "calories":
                return _calories(args, results, matcher, nutrition, warnings)
            if args.command == "explain":
                return _explain(args, matcher)
            if args.command == "gaps":
                return _gaps(args, results)
    except (McpError, PayloadError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


def _doctor(args, client: McpHttpClient, available: set[str], missing: list[str],
            env_files: Sequence[Path] = ()) -> int:
    info = client.server_info
    tools = sorted(available)
    if args.json:
        print(json.dumps({"server": info, "url": args.url, "tools": len(available),
                          "tool_names": tools,
                          "token": mask_secret(args.token),
                          "env_files": [str(p) for p in env_files],
                          "required_missing": missing, "ok": not missing},
                         ensure_ascii=False, indent=2))
    else:
        print(f"服务地址：{args.url}")
        print(f"服务端：{info}")
        print(f"Token：{mask_secret(args.token)}")
        if env_files:
            print(f"配置文件：{'、'.join(str(p) for p in env_files)}")
        else:
            print("配置文件：未使用 .env（Token 来自环境变量或命令行）")
        print(f"工具总数：{len(available)}")
        print(f"  可用：{'、'.join(tools) if tools else '（无）'}")
        print(f"所需工具：{'全部就绪' if not missing else '缺少 ' + ', '.join(missing)}")
        print(f"结论：{'可用于折算' if not missing else '不可用'}")
    return 0 if not missing else 1


def _calories(args, results, matcher, nutrition, warnings) -> int:
    if args.limit:
        results = results[: args.limit]

    # 只有「已消费」订单计入热量合计。
    # order-list 会返回**未支付**订单，直接计入会把还没吃到的东西算成已摄入热量。
    consumed = [o for o in results if o.is_consumed]
    excluded = [o for o in results if not o.is_consumed]

    complete = [o for o in consumed if o.is_complete]
    known = sum(o.known_kcal for o in consumed)
    known_units = sum(o.known_units for o in consumed)
    total_units = sum(len(o.substantive_units) for o in consumed)
    units_all = [u for o in consumed for u in o.units]
    supplement_units = [u for u in units_all if u.match.supplement is not None]
    category_units = [u for u in units_all
                      if u.match.is_assumed and u.match.supplement is None]
    supplement_kcal = round(sum(u.kcal or 0 for u in supplement_units), 1)
    category_kcal = round(sum(u.kcal or 0 for u in category_units), 1)
    ledger = load_gap_ledger()
    triage = ledger.triage(results)

    excluded_groups: dict[str, list] = {}
    for order in excluded:
        excluded_groups.setdefault(order.status_class, []).append(order)

    if args.json:
        print(json.dumps({
            "summary": {
                "orders_total": len(results),
                "consumed_orders": len(consumed),
                "excluded_orders": len(excluded),
                "excluded_by_status": {
                    STATUS_LABELS.get(k, k): len(v) for k, v in excluded_groups.items()
                },
                "complete_orders": len(complete),
                "known_kcal": round(known, 1),
                "covered_units": known_units,
                "substantive_units": total_units,
                "coverage": round(known_units / total_units, 4) if total_units else 0.0,
                "category_default_units": len(category_units),
                "category_default_kcal": category_kcal,
                "supplement_units": len(supplement_units),
                "supplement_kcal": supplement_kcal,
                "scope_note": "known_kcal 只合计「已消费」订单；待支付/已取消/进行中/状态未知一律排除并单列。",
                "assumed_category_note": "部分商品营养表未收录，按 categories.json 的品类默认值估算；蘸酱类不计入覆盖率分母，【美汁源】等实质品类计入。",
                "estimated_note": "另有商品取自 supplements.json 的推导估算值，附区间与推导过程。",
            },
            "orders": [
                {
                    "order_id": o.order_id, "create_time": o.create_time,
                    "store": o.store_name, "store_code": o.store_code, "paid": o.paid,
                    "status": o.order_status, "status_class": o.status_class,
                    "counted_in_total": o.is_consumed,
                    "kcal": o.known_kcal, "is_complete": o.is_complete,
                    "covered": o.known_units, "total": len(o.substantive_units),
                    "assumed_kcal": o.assumed_kcal, "assumed_units": len(o.assumed_units),
                    "condiment_units": len(o.condiment_units),
                    "supplements_used": [
                        {"name": u.match.supplement.name,
                         "kcal": u.match.supplement.kcal,
                         "range": [u.match.supplement.low, u.match.supplement.high],
                         "confidence": u.match.supplement.confidence,
                         "derivation": u.match.supplement.derivation,
                         "source": u.match.supplement.source}
                        for u in o.units if u.match.supplement is not None
                    ],
                    "items": [
                        {"name": u.unit.name, "quantity": u.unit.quantity,
                         "kcal": u.kcal, "tier": u.match.tier.value,
                         "matched": u.match.matched_name, "reason": u.match.reason}
                        for u in o.units
                    ],
                }
                for o in results
            ],
            "gaps": triage,
            "nutrition_warnings": warnings,
        }, ensure_ascii=False, indent=2))
        return 0

    print("订单卡路里一览")
    print(f"营养表 {len(nutrition)} 条 · 别名表 {len(matcher.aliases)} 条 · 已判定缺口 {len(ledger)} 条")
    for warning in warnings:
        print(f"  ⚠️ 数据源：{warning}")

    def render_group(title: str, orders: list, *, counted: bool, note: str = "") -> None:
        if not orders:
            return
        print("=" * 78)
        tag = "计入合计" if counted else "**不计入合计**"
        print(f"【{title}】{tag}" + (f"  —— {note}" if note else ""))
        for order in orders:
            if args.detail:
                print(order.render())
                print("-" * 78)
            else:
                label = f"{order.known_kcal:.0f}" if order.is_complete else f"≥{order.known_kcal:.0f}"
                flag = "  " if order.is_complete else "⚠️"
                print(f"{order.create_time[:16]:<18}{label:>7} kcal  "
                      f"{order.known_units}/{len(order.substantive_units):<5}{flag} {order.store_name}")

    render_group("已消费", consumed, counted=True)
    for status_class in ("pending", "in_progress", "cancelled", "other"):
        group = excluded_groups.get(status_class) or []
        if not group:
            continue
        note = {
            "pending": "未支付，还没吃到",
            "in_progress": "尚未完成",
            "cancelled": "已取消或退款",
            "other": "状态未识别，保守排除",
        }.get(status_class, "")
        render_group(STATUS_LABELS.get(status_class, status_class), group,
                     counted=False, note=note)

    print("=" * 78)
    print(f"已消费 {len(consumed)} 单 · 完整覆盖 {len(complete)}/{len(consumed)} 单 · "
          f"覆盖商品 {known_units}/{total_units} 项 · 已覆盖部分合计 {known:.0f} kcal")
    if excluded:
        parts = []
        for status_class, group in excluded_groups.items():
            subtotal = round(sum(o.known_kcal for o in group), 1)
            # 完全覆盖就不该带下界前缀——否则会把一个完整数值说成不完整
            prefix = "" if all(o.is_complete for o in group) else "≥"
            parts.append(f"{STATUS_LABELS.get(status_class, status_class)} "
                         f"{len(group)} 单（{prefix}{subtotal:.0f} kcal）")
        print(f"未计入合计：{'、'.join(parts)}")
        print("    以上订单未纳入热量合计——待支付/已取消不算已摄入，状态未知则保守排除。")
    if category_units:
        print(f"另有 {len(category_units)} 项按**品类默认值**计 {category_kcal:.0f} kcal"
              f"（估算，非营养表官方数据；规则见 categories.json）")
    if supplement_units:
        detail = "、".join(
            f"{u.unit.name} {u.match.supplement.range_text}"
            for u in supplement_units if u.match.supplement
        )
        print(f"另有 {len(supplement_units)} 项取自**推导估算值**，合计 {supplement_kcal:.0f} kcal"
              f"（非营养表官方数据；见 supplements.json）")
        print(f"    {detail}")
    if len(complete) < len(consumed):
        print("⚠️ 存在未覆盖商品，带 ≥ 的数值是**下界**，不是该单总热量。用 --detail 查看缺了什么。")
    if triage["new"]:
        print(f"\n⚠️ 出现 {len(triage['new'])} 个未判定商品（不在缺口台账中）："
              f"{'、'.join(triage['new'])}")
        print("   请用 `mcd-calories explain <名称>` 判断是「可别名映射」还是「营养表未收录」，")
        print("   然后分别登记到 aliases.json / categories.json / supplements.json / known_gaps.json。")
    return 0


def _explain(args, matcher: ProductMatcher) -> int:
    name = args.name
    result = matcher.resolve(name)
    if args.json:
        print(json.dumps({
            "name": name, "tier": result.tier.value, "matched": result.matched_name,
            "kcal": result.kcal, "reason": result.reason,
            "candidates": [{"name": c.entry.name, "score": c.score,
                            "rejected_because": c.rejected_because}
                           for c in matcher.suggest(name, limit=8)],
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"查询：{name}")
    print(f"判定：{result.tier.value}")
    if result.tier.is_matched:
        print(f"结果：{result.matched_name}  {result.kcal} kcal")
    print(f"理由：{result.reason}")
    candidates = matcher.suggest(name, limit=8)
    if candidates:
        print("\n候选（相似度仅供人工参考，不作为自动决策依据）：")
        for c in candidates:
            mark = "✗" if c.rejected_because else "✓"
            note = f"  ← {c.rejected_because}" if c.rejected_because else ""
            print(f"  {mark} {c.entry.name:<24} {c.score:.3f}{note}")
    if not result.tier.is_matched:
        print("\n下一步：")
        print("  · 若上述候选中确有等价商品 → 加入 aliases.json")
        print("  · 若都不等价（营养表确实未收录）→ 加入 known_gaps.json 建档")
    return 0


def _gaps(args, results) -> int:
    ledger = load_gap_ledger()
    triage = ledger.triage(results)
    if args.json:
        print(json.dumps(triage, ensure_ascii=False, indent=2))
        return 0
    print(f"已知缺口 {len(triage['known_gap'])} 个（已判定营养表未收录）：")
    for name in triage["known_gap"]:
        print(f"  · {name}\n      {ledger.known(name)}")
    print(f"\n新出现 {len(triage['new'])} 个：")
    for name in triage["new"]:
        print(f"  · {name}")
    if not triage["new"]:
        print("  （无）")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
