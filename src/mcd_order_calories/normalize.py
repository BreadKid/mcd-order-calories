"""商品名归一化。

归一化的目标只有一个：让「订单里的名字」和「营养表里的名字」能对上。
难点在于两个数据源用了不同的命名习惯：

    订单:   可口可乐中杯              营养表: 可乐中杯
    订单:   麦辣鸡翅2块              营养表: 麦辣鸡翅-2块
    订单:   纯牛奶(盒装)             营养表: 纯牛奶（盒装）
    订单:   风味双层深海鳕鱼堡         营养表: 双层深海鳕鱼堡

但归一化**不能无脑抹掉一切差异**，因为有些差异是语义性的：

    可口可乐中杯  vs  无糖可口可乐中杯   热量差约 150 kcal
    大薯条       vs  中薯条
    冰燕麦奶铁中杯 vs 热燕麦奶铁中杯

所以本模块分成两件事：
1. :func:`normalize` —— 抹掉**装饰性**差异（营销前缀、括号、连字符、全半角）
2. :func:`guard_tokens` / :func:`guard_conflict` —— 提取并比对**不可归一化**的硬约束
   （规格、杯型、数量、含糖与否、冷热）。硬约束冲突的两个名字**永远不允许匹配**，
   哪怕字符串相似度极高。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 装饰性字符：去掉后不影响商品身份
_DECORATION = re.compile(r"[\s\-_—–·、,，.。!！/\\|]+")
_BRACKETS = re.compile(r"[（）()\[\]【】「」『』〈〉《》]")
_QUOTES = re.compile(r"[\"'“”‘’]")

# 刻意**不**剥离「风味」「可口」这类词。
#
# 试过把它们当营销词剥掉，结果是 `可口可乐中杯` 与 `可乐中杯` 归一化后完全相同，
# 直接走 exact 命中——看似省事，实则把风险藏了起来：
#
#   剥离前实测  similarity(可口可乐中杯, 无糖可口可乐中杯) = 0.732  ← 错答案更高
#               similarity(可口可乐中杯, 可乐中杯)        = 0.633
#
# 剥掉「可口」后两个都变成 `可乐中杯`，差异被抹平，硬约束也就无从发挥作用。
# 而「风味双层深海鳕鱼堡」这类变体，靠包含关系 + 唯一性 + 阈值同样能正确命中。
#
# 所以：归一化只做**机械性**差异（宽度、括号、标点、空格），
# 语义性差异一律交给 guard 与唯一性规则处理。

# ---------------------------------------------------------------------------
# 硬约束（guard）：这些差异是语义性的，绝不允许被归一化抹掉
# ---------------------------------------------------------------------------

# 杯型/规格。同一组内互斥，不同值视为不同商品。
_SIZE_GROUPS: dict[str, tuple[str, ...]] = {
    "cup": ("大杯", "中杯", "小杯"),
    "fries": ("大薯条", "中薯条", "小薯条", "迷你薯条"),
    "portion": ("大份", "中份", "小份"),
}

# 含糖与否：同组内互斥
_SUGAR_TOKENS = ("无糖", "0糖", "零糖", "零度", "无蔗糖")

# 冷热：同组内互斥
_TEMP_TOKENS = ("冰", "热")

# 数量：2块 / 4块 / 5块 / 2个 …
_COUNT = re.compile(r"(\d+)\s*(块|个|片|只|根|杯|份|件)")

# 口味/品类里的差异化词：出现即视为身份的一部分，不参与「抹除」
# 例如「黄金橙橙」与「多汁柠柠」是【美汁源】下的两个不同口味，不能互相映射。
# 本模块不试图判断口味是否等价——那必须靠人工别名表。这里只负责不让它们被抹平。


@dataclass
class GuardTokens:
    """一个商品名里提取出的硬约束。空字符串表示「未标注」。"""

    cup: str = ""
    fries: str = ""
    portion: str = ""
    sugar: str = ""          # 非空表示含「无糖」类标记
    temperature: str = ""    # 冰 / 热
    counts: tuple[str, ...] = field(default_factory=tuple)

    def conflicts_with(self, other: "GuardTokens") -> str | None:
        """返回冲突说明；无冲突返回 None。

        规则：只有双方**都标注了**同一维度的值、且值不同时，才算冲突。
        单方未标注不判冲突——否则「可乐中杯」和「可口可乐中杯」永远匹配不上。
        """
        for label, mine, theirs in (
            ("杯型", self.cup, other.cup),
            ("规格", self.fries or self.portion, other.fries or other.portion),
            ("冷热", self.temperature, other.temperature),
        ):
            if mine and theirs and mine != theirs:
                return f"{label}不一致：{mine} ≠ {theirs}"
        if (self.sugar == "") != (other.sugar == ""):
            if self.sugar or other.sugar:
                return f"含糖标记不一致：{self.sugar or '含糖'} ≠ {other.sugar or '含糖'}"
        if self.counts and other.counts and self.counts != other.counts:
            return f"数量不一致：{'/'.join(self.counts)} ≠ {'/'.join(other.counts)}"
        return None

    def describe(self) -> str:
        parts = []
        for label, value in (
            ("杯型", self.cup),
            ("规格", self.fries or self.portion),
            ("冷热", self.temperature),
            ("糖", self.sugar or ("含糖" if self.sugar == "" else "")),
        ):
            if value:
                parts.append(f"{label}={value}")
        if self.counts:
            parts.append("数量=" + "/".join(self.counts))
        return " ".join(parts) or "无硬约束"


def normalize(name: str) -> str:
    """把商品名压成用于比对的形式。

    只抹掉**机械性**差异（全半角、括号、连字符、空格、引号）。
    **不做任何语义推断**——同义词必须走别名表，语义变体靠 guard 与唯一性规则。
    """
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = _DECORATION.sub("", text)
    text = _BRACKETS.sub("", text)
    text = _QUOTES.sub("", text)
    return text.lower()


def guard_tokens(name: str) -> GuardTokens:
    """提取硬约束。注意：在归一化**之前**提取，否则会被抹掉。"""
    text = unicodedata.normalize("NFKC", str(name or ""))
    cup = next((c for c in _SIZE_GROUPS["cup"] if c in text), "")
    fries = next((f for f in _SIZE_GROUPS["fries"] if f in text), "")
    portion = "" if fries else next((p for p in _SIZE_GROUPS["portion"] if p in text), "")
    sugar = next((s for s in _SUGAR_TOKENS if s in text), "")
    temperature = next((t for t in _TEMP_TOKENS if t in text), "")
    counts = tuple(sorted({f"{n}{unit}" for n, unit in _COUNT.findall(text)}))
    return GuardTokens(cup=cup, fries=fries, portion=portion, sugar=sugar,
                       temperature=temperature, counts=counts)


def guard_conflict_pair(a: GuardTokens, b: GuardTokens) -> str | None:
    """两个商品名的硬约束是否冲突。冲突返回说明，否则 None。"""
    return a.conflicts_with(b)


# ---------------------------------------------------------------------------
# 蘸酱识别
# ---------------------------------------------------------------------------
#
# 蘸酱（甜酸酱、山葵酱、蒜蓉辣椒酱…）是随餐附赠的调味品，营养表不单独收录。
# 它们不该被算作「数据缺口」——那是数据源的固有边界，不是归一化的失败。
#
# 但**不能用「名字含酱」来判定**：麦当劳有「蘸酱鸡球（甜酸酱风味）」这种商品，
# 它是鸡球（约 200+ kcal），不是酱。按含酱判定会静默丢掉这部分热量。
#
# 判定规则：**去掉尾部标点后以「酱」结尾**。
#   日式鲜磨山葵酱            → 酱 ✅
#   蘸酱鸡球（甜酸酱风味）      → 结尾是「）」 ❌ 不是酱
#   甜酸酱                    → 酱 ✅

_TRAILING_PUNCT = re.compile(r"[\s、,，。.·\-—]+$")
_TRAILING_BRACKET = re.compile(r"\s*[（(【\[][^（()）【】\[\]]*[）)】\]]\s*$")


def strip_trailing_decorations(name: str) -> str:
    """反复剥掉**尾部的**括号补充说明与标点。

    ``日式鲜磨山葵酱（小）`` → ``日式鲜磨山葵酱``
    ``蘸酱鸡球（甜酸酱风味）`` → ``蘸酱鸡球``（中间的「酱」不会被误判）
    """
    text = unicodedata.normalize("NFKC", str(name or "")).strip()
    previous = None
    while previous != text:
        previous = text
        text = _TRAILING_BRACKET.sub("", text)
        text = _TRAILING_PUNCT.sub("", text)
    return text


def starts_with_token(name: str, token: str) -> bool:
    return unicodedata.normalize("NFKC", str(name or "")).strip().startswith(token)


def ends_with_token(name: str, token: str) -> bool:
    """剥掉尾部装饰后是否以 token 结尾（要求 token 之前还有内容）。"""
    text = strip_trailing_decorations(name)
    return text.endswith(token) and len(text) > len(token)


def contains_token(name: str, token: str) -> bool:
    return token in unicodedata.normalize("NFKC", str(name or ""))


def looks_like_sauce(name: str) -> bool:
    """商品名是否指蘸酱本身（而非「某酱风味的食品」）。

    这样既认得出规格后缀，又不会被前缀或中间的「酱」骗到：

        日式鲜磨山葵酱            → 是
        日式鲜磨山葵酱（小）       → 是（剥掉尾部括号后仍以酱结尾）
        蘸酱鸡球（甜酸酱风味）      → 否（剥掉括号后是「蘸酱鸡球」）
        蘸酱麦麦脆汁鸡            → 否（「酱」在中间）
    """
    return ends_with_token(name, "酱")


def bigrams(text: str) -> set[str]:
    """字符二元组，用于中文短语的相似度。"""
    text = normalize(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _lcs_length(a: str, b: str) -> int:
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0]
        for j, y in enumerate(b, start=1):
            current.append(previous[j - 1] + 1 if x == y else max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    """0..1 的相似度。仅用于**提出候选**，绝不单独用于决定映射。"""
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return 0.0
    left_grams, right_grams = bigrams(left), bigrams(right)
    jaccard = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    lcs = _lcs_length(left, right) / max(len(left), len(right))
    return round(0.5 * jaccard + 0.5 * lcs, 4)
