"""补充热量表 —— 为营养表未收录、但能**推导出近似值**的商品建档。

与另外两张表的分工：

======================  ==========================================================
表                      用途
======================  ==========================================================
``aliases.json``        订单名 → 营养表**已有条目**（等价映射，值来自营养表）
``categories.json``     整个**品类**共用一个默认值（蘸酱、【美汁源】饮料…）
``supplements.json``    单个商品的**推导估算值**（营养表没有，但能算出来）
``known_gaps.json``     确认算不出来，如实披露为缺口
======================  ==========================================================

三条硬规矩，缺一不可：

1. **必须附推导过程**（``derivation``）——让任何人能复核这个数是怎么来的。
2. **必须给区间**（``range``）——推导必然有误差，只给一个点值会假装精确。
3. **必须标注非官方**（``source``）——它是估算，不能冒充营养表数据。

因此本表的条目在输出里永远带 ``[supplement·估算]`` 标记和区间，且**计入覆盖率
分母**（有值就是有值，不能因为是估算就假装这项不存在）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .normalize import normalize
from .nutrition import NutritionEntry


@dataclass(frozen=True)
class Supplement:
    """一个商品的推导估算值。"""

    name: str
    kcal: float
    low: float | None = None
    high: float | None = None
    confidence: str = "medium"
    derivation: str = ""
    source: str = ""
    basis: str = "derived"   # "derived"（本项目推导） | "provided"（外部给定的点值）

    @property
    def range_text(self) -> str:
        if self.low is None or self.high is None or self.low == self.high:
            return f"{self.kcal:g}"
        return f"{self.kcal:g}（{self.low:g}~{self.high:g}）"

    @property
    def is_provided(self) -> bool:
        """值由外部给定、本项目无法独立验证。

        与 derived 的区别不只是来源——它意味着**区间可能无法刻画真实误差**，
        输出里必须说清这一点，不能让读者以为它和推导值同等可信。
        """
        return self.basis == "provided"

    @property
    def basis_text(self) -> str:
        return "外部给定值，未经本项目推导" if self.is_provided else "本项目推导"

    def entry(self) -> NutritionEntry:
        return NutritionEntry(
            name=self.name,
            kcal=self.kcal,
            energy_kj=round(self.kcal * 4.184, 1),
            protein=None, fat=None, carbohydrate=None, sodium=None, calcium=None,
            raw=f"supplement:{self.confidence}:{self.kcal:g}",
        )


class SupplementCatalog:
    """按归一化商品名索引的补充热量表。"""

    def __init__(self, entries: Mapping[str, Supplement] | None = None, *, source: str = "") -> None:
        self.entries: dict[str, Supplement] = dict(entries or {})
        self.source = source

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, name: str) -> Supplement | None:
        return self.entries.get(normalize(name))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str = "") -> "SupplementCatalog":
        raw_entries = data.get("supplements")
        if raw_entries is None:
            # 兼容：顶层直接就是 商品名 → 条目 的写法
            raw_entries = {k: v for k, v in data.items() if not str(k).startswith("_")}
        if not isinstance(raw_entries, Mapping):
            raise ValueError("补充热量表需要 `supplements` 对象")

        entries: dict[str, Supplement] = {}
        for name, raw in raw_entries.items():
            if str(name).startswith("_"):
                continue
            if not isinstance(raw, Mapping):
                raise ValueError(f"补充条目 {name!r} 不是对象")
            if raw.get("kcal") is None:
                raise ValueError(f"补充条目 {name!r} 缺少 kcal")
            derivation = str(raw.get("derivation") or "").strip()
            source_text = str(raw.get("source") or "").strip()
            if not derivation:
                raise ValueError(f"补充条目 {name!r} 缺少推导过程（derivation）——本表不允许无依据的数值")
            if not source_text:
                raise ValueError(f"补充条目 {name!r} 缺少来源标注（source）")
            bounds = raw.get("range") or []
            low = high = None
            if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                low, high = float(bounds[0]), float(bounds[1])
            if low is None or high is None:
                raise ValueError(f"补充条目 {name!r} 缺少误差区间（range）——只给点值会假装精确")
            if low > high:
                raise ValueError(f"补充条目 {name!r} 的区间上下界写反了：{low} > {high}")
            if not (low <= float(raw["kcal"]) <= high):
                raise ValueError(
                    f"补充条目 {name!r} 的 kcal 不在区间内：{raw['kcal']} ∉ [{low}, {high}]"
                )
            basis = str(raw.get("basis") or "derived")
            if basis not in ("derived", "provided"):
                raise ValueError(f"补充条目 {name!r} 的 basis 只能是 derived 或 provided")
            entries[normalize(str(name))] = Supplement(
                name=str(name),
                kcal=float(raw["kcal"]),
                low=low,
                high=high,
                confidence=str(raw.get("confidence") or "medium"),
                derivation=derivation,
                source=source_text,
                basis=basis,
            )
        return cls(entries, source=source)
