"""订单展开与卡路里折算。

一条订单的组成有两层：

    orderProductList[]                订单行（可能是套餐容器，也可能是单品）
      └ comboItemList[]               套餐内的实际成分 ← 真正吃进去的东西

所以折算前必须**展开套餐**：`人气经典随心配` 是个容器，真实消费是
`双层吉士汉堡 + 小杯玉米杯`。直接拿容器名去匹配营养表必然失败
（实测 10 条订单里识别出 11 个容器名）。

覆盖率是硬性输出。实测某单只覆盖 1/4 项时算出 87 kcal，而真实热量在 800 kcal
以上——**只给数字不给出覆盖率会严重误导**。因此：
:class:`OrderCalories` 永远同时提供 :attr:`known_kcal` 与 :attr:`coverage`，
渲染时覆盖率不足 100% 一律标注为下界。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .matching import MatchResult, MatchTier, ProductMatcher
from .normalize import normalize


@dataclass(frozen=True)
class OrderUnit:
    """一件实际入口的商品（套餐已展开）。"""

    name: str
    product_code: str
    quantity: int
    from_combo: bool = False

    @property
    def key(self) -> tuple[str, bool]:
        return (self.name, self.from_combo)


@dataclass
class UnitResult:
    unit: OrderUnit
    match: MatchResult

    @property
    def kcal(self) -> float | None:
        if self.match.kcal is None:
            return None
        return round(self.match.kcal * self.unit.quantity, 1)


@dataclass
class OrderCalories:
    order_id: str
    create_time: str
    store_name: str
    store_code: str
    paid: str
    units: list[UnitResult] = field(default_factory=list)
    order_status: str = ""

    # ---------------------------------------------------------------- #

    @property
    def total_units(self) -> int:
        return len(self.units)

    @property
    def condiment_units(self) -> list[UnitResult]:
        """随餐附赠的调味品（蘸酱）。不计入覆盖率分母。"""
        return [u for u in self.units if u.match.condiment]

    @property
    def assumed_units(self) -> list[UnitResult]:
        """取值为品类默认值的项（蘸酱、【美汁源】饮料…），属估算而非营养表数据。"""
        return [u for u in self.units if u.match.is_assumed]

    @property
    def substantive_units(self) -> list[UnitResult]:
        """非蘸酱项——覆盖率只按这些计算。

        蘸酱是随餐附赠的调味品，营养表不单独收录。把它算进分母会让覆盖率失真：
        那是数据源的固有边界，不是归一化失败。
        """
        return [u for u in self.units if not u.match.condiment]

    @property
    def known_units(self) -> int:
        return sum(1 for u in self.substantive_units if u.kcal is not None)

    @property
    def coverage(self) -> float:
        total = len(self.substantive_units)
        return self.known_units / total if total else 0.0

    @property
    def is_complete(self) -> bool:
        return len(self.substantive_units) > 0 and self.known_units == len(self.substantive_units)

    @property
    def known_kcal(self) -> float:
        """已覆盖部分的热量合计（含蘸酱）。覆盖率不足时这是**下界**，不是总量。"""
        return round(sum(u.kcal for u in self.units if u.kcal is not None), 1)

    @property
    def assumed_kcal(self) -> float:
        """来自品类默认值的热量合计。"""
        return round(sum(u.kcal for u in self.assumed_units if u.kcal is not None), 1)

    @property
    def missing(self) -> list[UnitResult]:
        return [u for u in self.substantive_units if u.kcal is None]

    # ---------------------------------------------------------------- #

    def render(self, *, show_matched: bool = True) -> str:
        lines = [
            f"{self.create_time}  ·  {self.store_name}  ·  实付 ¥{self.paid}"
        ]
        if self.is_complete:
            lines.append(f"  🔥 {self.known_kcal:.0f} kcal")
        else:
            pct = self.coverage * 100
            note = "，随餐调味品不计入分母" if self.condiment_units else ""
            lines.append(
                f"  🔥 ≥{self.known_kcal:.0f} kcal  ⚠️ 仅覆盖 {self.known_units}/"
                f"{len(self.substantive_units)} 项（{pct:.0f}%{note}）"
            )
        if show_matched:
            for unit in self.units:
                qty = f"×{unit.unit.quantity}" if unit.unit.quantity > 1 else ""
                if unit.match.supplement is not None:
                    sup = unit.match.supplement
                    lines.append(f"     · {unit.unit.name}{qty}  {unit.kcal:.0f} kcal"
                                 f"  [supplement·估算 {sup.range_text}，{sup.confidence} 置信度]")
                elif unit.match.is_assumed:
                    lines.append(f"     · {unit.unit.name}{qty}  {unit.kcal:.0f} kcal"
                                 f"  [category:{unit.match.category_id}·估算]")
                elif unit.kcal is None:
                    lines.append(f"     ❔ {unit.unit.name}{qty}  — 无营养数据")
                else:
                    lines.append(f"     · {unit.unit.name}{qty}  {unit.kcal:.0f} kcal"
                                 f"  [{unit.match.tier.value}]")
        for unit in self.units:
            if unit.match.supplement is not None:
                sup = unit.match.supplement
                lines.append(f"     ℹ️ 「{sup.name}」为推导估算值 {sup.range_text} kcal"
                             f"（{sup.confidence} 置信度，非营养表数据）")
                lines.append(f"        推导：{sup.derivation}")
                lines.append(f"        来源：{sup.source}")

        category_units = [u for u in self.assumed_units if u.match.category_id]
        if category_units:
            by_rule: dict[str, list[UnitResult]] = {}
            for unit in category_units:
                by_rule.setdefault(unit.match.category_label or unit.match.category_id, []).append(unit)
            for label, units in by_rule.items():
                subtotal = round(sum(u.kcal for u in units if u.kcal is not None), 1)
                lines.append(f"     ℹ️ {label} {len(units)} 项合计 {subtotal:.0f} kcal"
                             f"（品类默认值·估算，非营养表数据；可在 categories.json 配置）")
        if not self.is_complete:
            names = "、".join(dict.fromkeys(u.unit.name for u in self.missing))
            lines.append(f"     ⚠️ 缺少：{names}")
            lines.append("     ⚠️ 实际热量显著高于此值，请勿据此做热量控制")
        return "\n".join(lines)


def expand_order(order: Mapping) -> list[OrderUnit]:
    """把一条订单展开成实际入口的商品清单。"""
    units: list[OrderUnit] = []
    for product in order.get("orderProductList") or []:
        combo = product.get("comboItemList") or []
        if combo:
            # 套餐容器本身不计入，只计其成分
            for item in combo:
                units.append(
                    OrderUnit(
                        name=str(item.get("name", "")),
                        product_code=str(item.get("productCode", "")),
                        quantity=int(item.get("quantity", 1) or 1),
                        from_combo=True,
                    )
                )
        else:
            units.append(
                OrderUnit(
                    name=str(product.get("productName", "")),
                    product_code=str(product.get("productCode", "")),
                    quantity=int(product.get("quantity", 1) or 1),
                )
            )
    return units


def compute_order(order: Mapping, matcher: ProductMatcher) -> OrderCalories:
    """折算一条订单的卡路里。"""
    result = OrderCalories(
        order_id=str(order.get("orderId", "")),
        create_time=str(order.get("createTime", "")),
        store_name=str(order.get("storeName", "")),
        store_code=str(order.get("storeCode", "")),
        paid=str(order.get("realTotalAmount", "")),
        order_status=str(order.get("orderStatus", "")),
    )
    # 名称去重缓存，避免同一商品重复匹配
    cache: dict[str, MatchResult] = {}
    for unit in expand_order(order):
        if unit.name not in cache:
            cache[unit.name] = matcher.resolve(unit.name)
        result.units.append(UnitResult(unit=unit, match=cache[unit.name]))
    return result


def compute_orders(orders: Iterable[Mapping], matcher: ProductMatcher) -> list[OrderCalories]:
    return [compute_order(order, matcher) for order in orders]


# --------------------------------------------------------------------- #
# 缺口台账：把「未匹配」从每次都要重新判断，变成一次判断、长期记录
# --------------------------------------------------------------------- #


class GapLedger:
    """记录已人工判定过、且确认营养表没有收录的商品名。

    同一个产品在订单里可能有多个写法（例如 `蘸酱鸡球（甜酸酱风味）` 与
    `蘸酱脆皮鸡球` 是同一件商品），因此台账支持一层**规范名别名**：
    判定记在规范名上，各种写法通过 ``aliases`` 指过来，避免同一产品被反复
    当成新问题上报。

    JSON 支持两种形态：

    .. code-block:: json

        // v2（推荐）
        {"aliases": {"订单里的写法": "规范名"},
         "gaps":    {"规范名": "为什么判定为缺口"}}

        // v1（扁平，等价于只有 gaps）
        {"规范名": "为什么判定为缺口"}
    """

    def __init__(
        self,
        gaps: Mapping[str, str] | None = None,
        aliases: Mapping[str, str] | None = None,
        *,
        source: str = "",
    ) -> None:
        self.entries: dict[str, str] = dict(gaps or {})
        self.aliases: dict[str, str] = {normalize(k): v for k, v in (aliases or {}).items()}
        self.source = source

    def canonical(self, name: str) -> str:
        """把一个写法归到规范名。"""
        return self.aliases.get(normalize(name), name)

    def known(self, name: str) -> str | None:
        return self.entries.get(self.canonical(name))

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str = "") -> "GapLedger":
        """按 v2（``gaps``/``aliases`` 两段）或 v1（扁平）解析。"""
        if not isinstance(data, Mapping):
            raise ValueError("缺口台账必须是 JSON 对象")
        if "gaps" in data or "aliases" in data:
            gaps = {k: v for k, v in (data.get("gaps") or {}).items()
                    if not str(k).startswith("_")}
            aliases = {k: v for k, v in (data.get("aliases") or {}).items()
                       if not str(k).startswith("_") and isinstance(v, str)}
            return cls(gaps, aliases, source=source)
        return cls({k: v for k, v in data.items() if not str(k).startswith("_")},
                   source=source)

    @classmethod
    def load(cls, path: str | Path) -> "GapLedger":
        file = Path(path)
        if not file.exists():
            return cls({}, {}, source=str(path))
        return cls.from_dict(json.loads(file.read_text(encoding="utf-8")), source=str(path))

    def save(self, path: str | Path) -> None:
        payload = {
            "aliases": {k: v for k, v in sorted(self.aliases.items())},
            "gaps": {k: v for k, v in sorted(self.entries.items())},
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def triage(self, results: Sequence[OrderCalories]) -> dict[str, list[str]]:
        """把未匹配商品分成「台账已记录」和「新出现」两类。

        ``known_gap`` 里给出的是**规范名**，同一产品的多种写法不会重复上报。
        """
        reported: dict[str, list[str]] = {"known_gap": [], "new": []}
        seen: set[str] = set()
        for order in results:
            for unit in order.missing:
                canonical = self.canonical(unit.unit.name)
                if canonical in seen:
                    continue
                seen.add(canonical)
                reported["known_gap" if self.known(unit.unit.name) else "new"].append(canonical)
        return reported
