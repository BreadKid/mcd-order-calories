"""商品归一匹配器 —— 本项目的核心。

订单与营养表只能按**名字**对齐（营养表没有 productCode），而两个数据源的命名
习惯不同，所以需要一个分级、可审计、且**宁可不匹配也不匹配错**的解析器。

分级顺序（先到先得）：

===  ============  ==========================================================
层级   名称          采纳条件
===  ============  ==========================================================
1     alias         人工复核过的别名表命中。最高信任级别。
2     exact         归一化后完全相等。
3     guarded       候选经硬约束过滤后**只剩一个**（唯一性即证据）。
4     ambiguous     过滤后仍剩多个候选。**不自动决定**，交人工，列候选。
5     unmatched     无候选。属于营养表覆盖缺口，如实计入未覆盖。
===  ============  ==========================================================

三条不可动摇的规则：

1. **硬约束优先于相似度。** 含糖标记、杯型、规格、数量、冷热冲突的两个名字，
   即使字符串相似度极高也直接淘汰。实测「可口可乐中杯」与「无糖可口可乐中杯」
   相似度 0.732（比正确答案「可乐中杯」的 0.633 还高），只靠相似度必然算错
   约 150 kcal。
2. **唯一性才自动采纳。** 过滤后多于一个候选一律判 ambiguous，不排序取第一。
3. **每个决定都可追溯。** :class:`MatchResult` 记录层级、理由和被淘汰的候选，
   便于随营养表更新而回归验证。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .categories import CategoryCatalog
from .supplements import Supplement, SupplementCatalog
from .normalize import (
    guard_conflict_pair,
    guard_tokens,
    normalize,
    similarity,
    strip_trailing_decorations,
)
from .nutrition import NutritionEntry

# 相似度只用于「提出候选」，不用于「决定结果」
MIN_CANDIDATE_SIMILARITY = 0.30

# 自动采纳（guarded 层）所需的相似度下限。
#
# 这个阈值是**用真实订单数据标定**出来的，不是拍的。在 0.30 时实测到两个假阳性：
#
#   【美汁源】“多汁柠柠”  → 【美汁源】“黄金橙橙”   相似度 0.314  （不同口味）
#   双层脆鸡堡           → 双层吉士汉堡          相似度 0.312  （不同商品）
#
# 两者都「唯一且通过硬约束」，但都是错的。硬约束只能枚举已知维度（含糖/杯型/规格/
# 数量/冷热），无法覆盖所有语义差异。而热量工具里的假阳性会**静默污染数值**，
# 比承认一个缺口糟糕得多。
#
# 0.60 之上实测无假阳性：真正的合法变体（可口可乐中杯、风味双层深海鳕鱼堡、
# 纯牛奶(盒装)、麦辣鸡翅2块）都能通过「归一化后相等」直接命中，根本不需要走
# guarded 层。因此抬高门槛**不损失任何正确匹配**。
MIN_AUTO_SIMILARITY = 0.60


class MatchTier(str, Enum):
    ALIAS = "alias"
    EXACT = "exact"
    CATEGORY = "category"
    SUPPLEMENT = "supplement"
    STRIPPED = "stripped"
    GUARDED = "guarded"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"

    @property
    def is_matched(self) -> bool:
        return self in (MatchTier.ALIAS, MatchTier.EXACT, MatchTier.CATEGORY,
                        MatchTier.SUPPLEMENT, MatchTier.STRIPPED, MatchTier.GUARDED)

    @property
    def is_category(self) -> bool:
        """是否由品类规则给出的默认值（估算，非营养表数据）。"""
        return self is MatchTier.CATEGORY


@dataclass
class Candidate:
    entry: NutritionEntry
    score: float
    rejected_because: str = ""

    def describe(self) -> str:
        note = f"  ✗ {self.rejected_because}" if self.rejected_because else ""
        return f"{self.entry.name}（相似度 {self.score:.3f}）{note}"


@dataclass
class MatchResult:
    source_name: str
    tier: MatchTier
    entry: NutritionEntry | None = None
    reason: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    category_id: str = ""
    category_label: str = ""
    condiment: bool = False
    supplement: Supplement | None = None

    @property
    def kcal(self) -> float | None:
        return self.entry.kcal if self.entry else None

    @property
    def matched_name(self) -> str | None:
        return self.entry.name if self.entry else None

    @property
    def is_assumed(self) -> bool:
        """值是否来自估算（品类默认值 / 推导补充值）而非营养表。"""
        return self.tier in (MatchTier.CATEGORY, MatchTier.SUPPLEMENT)

    def describe(self) -> str:
        if self.tier.is_matched:
            tag = self.tier.value
            if self.category_id:
                tag = f"{tag}:{self.category_id}"
            if self.supplement is not None:
                tag = f"{tag}·估算 {self.supplement.range_text}"
            return f"[{tag}] {self.source_name} → {self.matched_name}（{self.reason}）"
        if self.tier is MatchTier.AMBIGUOUS:
            listing = "；".join(c.describe() for c in self.candidates[:3])
            return f"[ambiguous] {self.source_name} 有多个候选，待人工确认：{listing}"
        return f"[unmatched] {self.source_name} 在营养表中没有可对应条目"


class AliasTable:
    """人工复核过的 商品名 → 营养表商品名 映射。

    这是唯一允许表达「语义等价」的地方（例如 `可口可乐中杯` = `可乐中杯`）。
    自动流程只能*建议*新条目，不能写回——写回必须有人确认。
    """

    def __init__(self, mapping: Mapping[str, str] | None = None, *, source: str = "") -> None:
        self._mapping: dict[str, str] = {}
        self.source = source
        for key, value in (mapping or {}).items():
            self.add(key, value)

    def add(self, source_name: str, target_name: str) -> None:
        self._mapping[normalize(source_name)] = target_name

    def lookup(self, name: str) -> str | None:
        return self._mapping.get(normalize(name))

    def __len__(self) -> int:
        return len(self._mapping)

    def __contains__(self, name: object) -> bool:
        return normalize(str(name)) in self._mapping

    def items(self) -> Iterable[tuple[str, str]]:
        return self._mapping.items()

    @classmethod
    def load(cls, path: str | Path) -> "AliasTable":
        """从 JSON 读取，格式 ``{"源商品名": "营养表商品名"}``。

        以 ``_`` 开头的键视为注释，会被忽略。
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"别名表必须是 JSON 对象：{path}")
        entries = {k: v for k, v in data.items()
                   if not str(k).startswith("_") and isinstance(v, str)}
        return cls(entries, source=str(path))

    @classmethod
    def from_dict(cls, mapping: Mapping[str, str]) -> "AliasTable":
        return cls(mapping)


class ProductMatcher:
    """把订单里的商品名解析到营养表条目。"""

    def __init__(
        self,
        entries: Sequence[NutritionEntry],
        aliases: AliasTable | None = None,
        *,
        categories: CategoryCatalog | None = None,
        supplements: SupplementCatalog | None = None,
        min_similarity: float = MIN_CANDIDATE_SIMILARITY,
        min_auto_similarity: float = MIN_AUTO_SIMILARITY,
    ) -> None:
        self.entries = list(entries)
        self.aliases = aliases or AliasTable()
        self.categories = categories or CategoryCatalog()
        self.supplements = supplements or SupplementCatalog()
        self.min_similarity = min_similarity
        self.min_auto_similarity = min_auto_similarity
        self._by_key: dict[str, NutritionEntry] = {}
        self.key_collisions: dict[str, list[str]] = {}
        for entry in self.entries:
            if entry.key in self._by_key:
                self.key_collisions.setdefault(entry.key, [
                    self._by_key[entry.key].name
                ]).append(entry.name)
                continue
            self._by_key[entry.key] = entry

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def resolve(self, name: str) -> MatchResult:
        """按分级规则解析一个商品名。"""
        if not str(name).strip():
            return MatchResult(name, MatchTier.UNMATCHED, reason="空名称")

        # 1) 人工别名表
        aliased = self.aliases.lookup(name)
        if aliased:
            entry = self._by_key.get(normalize(aliased))
            if entry is not None:
                return MatchResult(name, MatchTier.ALIAS, entry,
                                   reason=f"别名表：{name} → {entry.name}")
            return MatchResult(
                name, MatchTier.UNMATCHED,
                reason=f"别名表指向 {aliased!r}，但营养表中不存在该条目（别名表需要更新）",
            )

        # 2) 归一化后完全相等
        exact = self._by_key.get(normalize(name))
        if exact is not None:
            return MatchResult(name, MatchTier.EXACT, exact, reason="归一化后完全一致")

        # 3) 补充热量表：营养表未收录、但已推导出近似值的**单个商品**。
        #    放在品类规则之前——针对单个商品的推导比整类默认值更精确。
        supplement = self.supplements.lookup(name)
        if supplement is not None:
            return MatchResult(
                name, MatchTier.SUPPLEMENT, supplement.entry(),
                reason=(f"命中补充热量表，估算 {supplement.range_text} kcal"
                        f"（{supplement.confidence} 置信度，非营养表数据）"),
                supplement=supplement,
            )

        # 4) 品类规则：营养表未收录、但同品类内热量可近似共享的商品
        #    （蘸酱、【美汁源】饮料…）。
        #    放在相似度匹配**之前**，避免这类商品被模糊匹配成某个食品
        #    （例如「日式鲜磨山葵酱」不该匹配到任何汉堡）。
        classified = self.categories.classify(name)
        if classified is not None:
            rule, _kcal = classified
            entry = rule.entry_for(name)
            note = "随餐附赠调味品，不计入覆盖率分母" if rule.exclude_from_coverage \
                else "视为实质商品，计入覆盖率分母"
            return MatchResult(
                name, MatchTier.CATEGORY, entry,
                reason=(f"命中品类规则「{rule.label}」，取值 {entry.kcal:g} kcal"
                        f"（估算，非营养表数据；{note}）"),
                category_id=rule.id,
                category_label=rule.label,
                condiment=rule.exclude_from_coverage,
            )

        # 4) 剥掉尾部括号说明后与营养表**完全相等**。
        #
        #    菜单名常带口味后缀，营养表用基础名：
        #        那么大鸡排（椒盐风味）  ← 菜单     那么大鸡排  ← 营养表
        #
        #    这里刻意要求「剥完必须完全相等」，而不是放宽相似度门槛。
        #    实测安全阈值区间是 (0.312, 0.633]，本条目的相似度 0.50 虽在区间内，
        #    但把门槛降到 0.50 会把对已知假阳性（0.312）的安全边际从 0.288 压到 0.188。
        #    用「剥完完全相等」这个更强的前提，既覆盖了这类后缀，又不动门槛。
        #
        #    仍需过一遍硬约束：`可乐中杯（无糖）` 剥完虽等于 `可乐中杯`，
        #    但含糖标记冲突，必须拒绝。
        stripped = strip_trailing_decorations(name)
        if normalize(stripped) != normalize(name):
            entry = self._by_key.get(normalize(stripped))
            if entry is not None and guard_conflict_pair(
                guard_tokens(name), guard_tokens(entry.name)
            ) is None:
                return MatchResult(
                    name, MatchTier.STRIPPED, entry,
                    reason=f"剥掉尾部括号说明「{name[len(stripped):]}」后与营养表条目一致",
                )

        # 5) 收集候选 → 硬约束过滤 → 唯一才采纳
        candidates = self._candidates(name)
        survived = [c for c in candidates if not c.rejected_because]
        rejected = [c for c in candidates if c.rejected_because]

        if len(survived) == 1:
            only = survived[0]
            detail = f"唯一候选，相似度 {only.score:.3f}"
            if rejected:
                detail += f"；另有 {len(rejected)} 个候选被硬约束淘汰"
            return MatchResult(name, MatchTier.GUARDED, only.entry,
                               reason=detail, candidates=candidates)

        if len(survived) > 1:
            return MatchResult(
                name, MatchTier.AMBIGUOUS,
                reason=f"{len(survived)} 个候选通过硬约束，无法唯一确定",
                candidates=survived + rejected,
            )

        if rejected:
            conflicts = [c for c in rejected if c.rejected_because.startswith(("杯型", "规格", "冷热", "数量", "含糖"))]
            if conflicts:
                detail = f"{len(conflicts)} 个候选因硬约束冲突被淘汰"
            else:
                detail = f"{len(rejected)} 个候选相似度均低于自动采纳门槛，需人工确认"
            return MatchResult(name, MatchTier.UNMATCHED, reason=detail, candidates=rejected)
        return MatchResult(name, MatchTier.UNMATCHED, reason="营养表中没有相似条目")

    def suggest(self, name: str, limit: int = 5) -> list[Candidate]:
        """给出候选清单（含被淘汰的原因），用于人工扩充别名表。"""
        return self._candidates(name)[:limit]

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _candidates(self, name: str) -> list[Candidate]:
        target = normalize(name)
        target_guard = guard_tokens(name)
        scored: list[Candidate] = []
        for entry in self.entries:
            key = entry.key
            if not key:
                continue
            # 包含关系或相似度达标才进入候选池
            related = key in target or target in key
            score = similarity(name, entry.name)
            if not related and score < self.min_similarity:
                continue

            # 淘汰原因按「硬约束优先于相似度」的顺序判定
            reason = guard_conflict_pair(target_guard, guard_tokens(entry.name)) or ""
            if not reason and score < self.min_auto_similarity and key != target:
                reason = (f"相似度 {score:.3f} 低于自动采纳门槛 "
                          f"{self.min_auto_similarity:.2f}，需人工确认")
            scored.append(Candidate(entry=entry, score=score, rejected_because=reason))

        # 未被淘汰的按相似度降序；被淘汰的排在后面，仅作解释用
        scored.sort(key=lambda c: (c.rejected_because != "", -c.score, c.entry.name))
        return scored
