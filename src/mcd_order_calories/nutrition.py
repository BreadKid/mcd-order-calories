"""营养表解析。

`list-nutrition-foods` 返回的 `data` 是一个**自定义分隔格式的字符串**，
既不是 JSON 也不是标准 CSV：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213
      中薯条,null,1210,289,4,12,38,165,18
      ...

实测注意点：
- 声明 `[160]` 但去重后只有 **158** 条，`纯牛奶（盒装）` 重复出现，需要去重。
- 表里**只有商品名，没有 productCode**，所以与订单只能按名字匹配。
- 部分商品名与本模块的归一化结果冲突（全角括号、连字符），交由 normalize 处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import normalize

_HEADER = re.compile(r"^\[(\d+)\]\{([^}]*)\}\s*:?\s*$")

# energyKcal 在表头里的列序（0-based）
FIELDS = (
    "productName",
    "nutritionDescription",
    "energyKj",
    "energyKcal",
    "protein",
    "fat",
    "carbohydrate",
    "sodium",
    "calcium",
)
_KCAL_INDEX = FIELDS.index("energyKcal")


@dataclass(frozen=True)
class NutritionEntry:
    name: str
    kcal: float | None
    energy_kj: float | None
    protein: float | None
    fat: float | None
    carbohydrate: float | None
    sodium: float | None
    calcium: float | None
    raw: str

    @property
    def key(self) -> str:
        return normalize(self.name)


def _number(text: str) -> float | None:
    text = text.strip()
    if not text or text.lower() in {"null", "none", "-", ""}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_nutrition(payload: str) -> tuple[list[NutritionEntry], list[str]]:
    """解析营养表字符串。

    返回 ``(条目列表, 警告列表)``。警告用于暴露数据源自身的质量问题
    （行数不符、重复条目、字段不足），而不是静默吞掉。
    """
    warnings: list[str] = []
    lines = [line for line in str(payload or "").splitlines() if line.strip()]
    if not lines:
        return [], ["营养表为空"]

    declared: int | None = None
    header, body = lines[0], lines[1:]
    match = _HEADER.match(header.strip())
    if match:
        declared = int(match.group(1))
    else:
        # 没有表头时不要猜列序，宁可显式报错
        raise ValueError(f"营养表缺少表头，无法确定列序：{header.strip()[:80]!r}")

    entries: list[NutritionEntry] = []
    malformed = 0
    for line in body:
        parts = line.strip().split(",")
        if len(parts) < len(FIELDS):
            malformed += 1
            continue
        # 商品名本身可能含逗号：从右侧固定取 8 列，剩余全部归名称
        head = parts[: len(parts) - (len(FIELDS) - 1)]
        tail = parts[len(parts) - (len(FIELDS) - 1):]
        name = ",".join(head).strip()
        values = [_number(v) for v in tail]
        entries.append(
            NutritionEntry(
                name=name,
                kcal=values[_KCAL_INDEX - 1],
                energy_kj=values[0],
                protein=values[1],
                fat=values[2],
                carbohydrate=values[3],
                sodium=values[4],
                calcium=values[5],
                raw=line.strip(),
            )
        )

    if malformed:
        warnings.append(f"{malformed} 行字段不足，已跳过")

    # 去重：同名保留首个，并报告
    seen: dict[str, NutritionEntry] = {}
    duplicates: list[str] = []
    for entry in entries:
        if entry.key in seen:
            duplicates.append(entry.name)
            continue
        seen[entry.key] = entry
    if duplicates:
        warnings.append("营养表存在重复条目：" + "、".join(sorted(set(duplicates))))

    if declared is not None and declared != len(entries) + malformed:
        warnings.append(f"表头声明 {declared} 条，实际解析 {len(entries)} 条（另有 {malformed} 行异常）")

    return list(seen.values()), warnings
