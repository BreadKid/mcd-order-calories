"""品类规则 —— 给「营养表未收录、但同品类内热量可近似共享」的商品定默认值。

营养表只收录 158~160 条商品，而麦当劳在售与历史商品远多于此。有些缺口是
**结构性**的：同一品类的商品热量接近，与其逐条人工补，不如按品类给一个默认值。

两条已落地的规则：

============  ============================  ==============  ==================
品类          匹配                          默认热量         是否计入覆盖率
============  ============================  ==============  ==================
蘸酱          （剥尾部装饰后）以「酱」结尾      0 kcal          **否**（随餐附赠调味品）
【美汁源】饮料  以「【美汁源】」开头             165 kcal        **是**（真实饮品）
============  ============================  ==============  ==================

两条规则的覆盖率语义**刻意不同**：
- 蘸酱是附赠调味品，营养表不收是数据源的固有边界，算进分母会让覆盖率失真；
- 【美汁源】是真实饮品，用了默认值就该计入分母——**不能因为是估算就假装这项不存在**。

所有取值都属于**估算**而非营养表官方数据，输出里必须显式披露。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .normalize import (
    contains_token,
    ends_with_token,
    looks_like_sauce,
    normalize,
    starts_with_token,
)
from .nutrition import NutritionEntry

_REGEX_CACHE: dict[str, re.Pattern[str]] = {}


def _regex_matches(name: str, pattern: str) -> bool:
    """按正则匹配。模式在加载时就编译校验，写错会在加载期直接报错而非静默失效。"""
    compiled = _REGEX_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern)
        _REGEX_CACHE[pattern] = compiled
    return bool(compiled.search(name))


# 匹配器注册表：配置文件里写字符串，代码里映射到谓词。
_PREDICATES = {
    "prefix": starts_with_token,
    "suffix": ends_with_token,
    "contains": contains_token,
    "regex": _regex_matches,
    "sauce": lambda name, _value: looks_like_sauce(name),
}


@dataclass(frozen=True)
class CategoryRule:
    """一条品类规则。"""

    id: str
    label: str
    default_kcal: float
    match_type: str
    match_value: str = ""
    exclude_from_coverage: bool = False
    overrides: Mapping[str, float] = field(default_factory=dict)
    source: str = ""
    exclude_match: tuple[str, ...] = ()

    def matches(self, name: str) -> bool:
        predicate = _PREDICATES.get(self.match_type)
        if predicate is None:
            raise ValueError(f"品类规则 {self.id!r} 使用了未知的匹配方式：{self.match_type!r}")
        # 排除项优先：命中任一排除子串即整条规则不适用
        if any(token in str(name) for token in self.exclude_match):
            return False
        return bool(predicate(name, self.match_value))

    def kcal_for(self, name: str) -> float:
        return float(self.overrides.get(normalize(name), self.default_kcal))

    def entry_for(self, name: str) -> NutritionEntry:
        """构造虚拟营养条目，``raw`` 标注其估算属性，便于追溯。"""
        kcal = self.kcal_for(name)
        return NutritionEntry(
            name=name,
            kcal=kcal,
            energy_kj=round(kcal * 4.184, 1),
            protein=None, fat=None, carbohydrate=None, sodium=None, calcium=None,
            raw=f"category:{self.id}:{kcal:g}",
        )


class CategoryCatalog:
    """按顺序应用品类规则，先匹配到的先胜。"""

    def __init__(self, rules: list[CategoryRule] | None = None, *, source: str = "") -> None:
        self.rules = list(rules or [])
        self.source = source

    def __len__(self) -> int:
        return len(self.rules)

    def classify(self, name: str) -> tuple[CategoryRule, float] | None:
        for rule in self.rules:
            if rule.matches(name):
                return rule, rule.kcal_for(name)
        return None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str = "") -> "CategoryCatalog":
        raw_rules = data.get("rules")
        if not isinstance(raw_rules, list):
            raise ValueError("品类规则表需要顶层 `rules` 数组")
        rules: list[CategoryRule] = []
        for index, raw in enumerate(raw_rules):
            if not isinstance(raw, Mapping):
                raise ValueError(f"rules[{index}] 不是对象")
            match = raw.get("match")
            if not isinstance(match, Mapping):
                raise ValueError(f"rules[{index}] 缺少 match 定义")
            overrides = raw.get("kcal")
            match_type = str(match.get("type") or "")
            match_value = str(match.get("value") or "")
            if match_type == "regex":
                try:
                    re.compile(match_value)
                except re.error as exc:
                    raise ValueError(
                        f"rules[{index}] 的正则无效：{match_value!r}（{exc}）"
                    ) from exc
            rules.append(
                CategoryRule(
                    id=str(raw.get("id") or f"rule-{index}"),
                    label=str(raw.get("label") or raw.get("id") or f"规则{index}"),
                    default_kcal=float(raw.get("default_kcal") or 0),
                    match_type=match_type,
                    match_value=match_value,
                    exclude_from_coverage=bool(raw.get("exclude_from_coverage", False)),
                    exclude_match=tuple(str(t) for t in (raw.get("exclude_match") or [])),
                    overrides={normalize(k): float(v)
                               for k, v in (overrides or {}).items()}
                    if isinstance(overrides, Mapping) else {},
                    source=str(raw.get("source") or ""),
                )
            )
        return cls(rules, source=source)
