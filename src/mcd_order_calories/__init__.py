"""订单卡路里一览 —— 把麦当劳历史订单折算成卡路里。

核心难点不是算术，而是**商品归一**：订单与营养表只能按商品名对齐。
本包提供分级、可审计、宁可不匹配也不匹配错的归一匹配器。
"""

from .categories import CategoryCatalog, CategoryRule
from .matching import AliasTable, Candidate, MatchResult, MatchTier, ProductMatcher
from .normalize import GuardTokens, guard_tokens, normalize, similarity
from .nutrition import NutritionEntry, parse_nutrition
from .supplements import Supplement, SupplementCatalog
from .payload import PayloadError, extract_data, extract_payload

__version__ = "0.1.0"

__all__ = [
    "AliasTable",
    "CategoryCatalog",
    "CategoryRule",
    "Candidate",
    "GuardTokens",
    "MatchResult",
    "MatchTier",
    "NutritionEntry",
    "PayloadError",
    "ProductMatcher",
    "Supplement",
    "SupplementCatalog",
    "extract_data",
    "extract_payload",
    "guard_tokens",
    "normalize",
    "parse_nutrition",
    "similarity",
    "__version__",
]
