"""归一匹配器与营养表解析的测试。

重点是**假阳性防线**：热量工具里，把 A 算成 B 会静默污染数值，
比承认一个覆盖缺口糟糕得多。
"""

from __future__ import annotations

import unittest

from fixtures import (
    NUTRITION_ROWS,
    SUPPLEMENT_RULES,
    category_catalog,
    nutrition_payload,
    supplement_catalog,
    wrapped,
)

from mcd_order_calories.categories import CategoryCatalog
from mcd_order_calories.matching import AliasTable, MatchTier, ProductMatcher
from mcd_order_calories.nutrition import parse_nutrition
from mcd_order_calories.payload import PayloadError, extract_data


def build_matcher(**kwargs) -> ProductMatcher:
    entries, _ = parse_nutrition(nutrition_payload())
    return ProductMatcher(entries, kwargs.pop("aliases", AliasTable()), **kwargs)


class TestNutritionParser(unittest.TestCase):
    def test_parses_all_rows(self) -> None:
        """按归一化后的键去重——用 fixture 自身推导期望值，加行不用改测试。"""
        from mcd_order_calories.normalize import normalize

        expected = len({normalize(row.split(",")[0]) for row in NUTRITION_ROWS})
        entries, warnings = parse_nutrition(nutrition_payload())
        self.assertEqual(len(entries), expected)
        self.assertTrue(any("重复条目" in w for w in warnings))

    def test_reports_declared_mismatch(self) -> None:
        _, warnings = parse_nutrition(nutrition_payload(declared=160))
        self.assertTrue(any("表头声明 160" in w for w in warnings))

    def test_reads_kcal_column(self) -> None:
        entries, _ = parse_nutrition(nutrition_payload())
        by_name = {e.name: e for e in entries}
        self.assertEqual(by_name["中薯条"].kcal, 289.0)
        self.assertEqual(by_name["可乐中杯"].kcal, 147.0)

    def test_rejects_headerless_payload(self) -> None:
        """没有表头就不能猜列序，必须显式失败。"""
        with self.assertRaises(ValueError):
            parse_nutrition("猪柳麦满分,null,1288,308,16,16,24,781,213")


class TestPayloadExtraction(unittest.TestCase):
    def test_extracts_json_despite_brackets_in_description(self) -> None:
        """说明段里的 `data[].storeName` 含方括号，简单正则会被骗。"""
        text = wrapped('{"success":true,"code":200,"data":{"list":[{"k":1}]}}')
        self.assertEqual(extract_data(text), {"list": [{"k": 1}]})

    def test_raises_when_no_json(self) -> None:
        with self.assertRaises(PayloadError):
            extract_data("这里没有任何 JSON")


class TestMatchingTiers(unittest.TestCase):
    def test_mechanical_variants_hit_exact_tier(self) -> None:
        matcher = build_matcher()
        for name, expected in [
            ("纯牛奶(盒装)", "纯牛奶（盒装）"),
            ("麦辣鸡翅2块", "麦辣鸡翅-2块"),
        ]:
            with self.subTest(name=name):
                result = matcher.resolve(name)
                self.assertEqual(result.tier, MatchTier.EXACT)
                self.assertEqual(result.matched_name, expected)

    def test_semantic_variants_hit_guarded_tier(self) -> None:
        """语义变体走 guarded 层：唯一候选 + 通过硬约束。

        `可口可乐中杯` 这里尤其关键——无糖版本的相似度更高（0.732 > 0.633），
        能落到正确结果完全依赖硬约束把无糖候选淘汰掉。
        """
        matcher = build_matcher()
        for name, expected in [
            ("可口可乐中杯", "可乐中杯"),
            ("风味双层深海鳕鱼堡", "双层深海鳕鱼堡"),
        ]:
            with self.subTest(name=name):
                result = matcher.resolve(name)
                self.assertEqual(result.tier, MatchTier.GUARDED)
                self.assertEqual(result.matched_name, expected)

    def test_guarded_matching_reports_that_the_guard_did_the_work(self) -> None:
        matcher = build_matcher()
        result = matcher.resolve("可口可乐中杯")
        self.assertIn("唯一候选", result.reason)
        self.assertIn("硬约束淘汰", result.reason)

    def test_sugar_variant_is_never_matched_to_regular(self) -> None:
        """回归防线：这一条曾经会算错约 150 kcal。"""
        matcher = build_matcher()
        result = matcher.resolve("无糖可口可乐中杯")
        self.assertEqual(result.matched_name, "无糖可口可乐中杯")
        self.assertNotEqual(result.matched_name, "可乐中杯")

    def test_false_positive_regressions(self) -> None:
        """这两个是实测出来的假阳性，必须永远保持拒绝。

        0.30 门槛时它们会被错误采纳：
            双层脆鸡堡          → 双层吉士汉堡（不同商品）
            【美汁源】"多汁柠柠"  → 【美汁源】"黄金橙橙"（不同口味）
        """
        matcher = build_matcher()
        for name in ["双层脆鸡堡", "【美汁源】“多汁柠柠”"]:
            with self.subTest(name=name):
                result = matcher.resolve(name)
                self.assertFalse(result.tier.is_matched,
                                 f"{name} 被错误匹配为 {result.matched_name}")
                self.assertNotEqual(result.matched_name, "双层吉士汉堡")
                self.assertNotEqual(result.matched_name, "【美汁源】“黄金橙橙”")

    def test_genuine_gap_is_unmatched(self) -> None:
        matcher = build_matcher()
        result = matcher.resolve("世界波脆鸡排蛋堡")
        self.assertEqual(result.tier, MatchTier.UNMATCHED)
        self.assertIsNone(result.kcal)
        self.assertIn("没有相似条目", result.reason)

    def test_alias_table_wins_over_everything(self) -> None:
        """人工复核过的映射是最高信任级。"""
        matcher = build_matcher(aliases=AliasTable({"双层脆鸡堡": "双层深海鳕鱼堡"}))
        result = matcher.resolve("双层脆鸡堡")
        self.assertEqual(result.tier, MatchTier.ALIAS)
        self.assertEqual(result.matched_name, "双层深海鳕鱼堡")
        self.assertEqual(result.kcal, 485.0)

    def test_alias_pointing_nowhere_is_reported_not_silently_ignored(self) -> None:
        matcher = build_matcher(aliases=AliasTable({"某商品": "不存在的商品"}))
        result = matcher.resolve("某商品")
        self.assertEqual(result.tier, MatchTier.UNMATCHED)
        self.assertIn("别名表需要更新", result.reason)

    def test_suggest_explains_rejections(self) -> None:
        """候选必须带淘汰理由，否则人工无法判断该不该加别名。"""
        matcher = build_matcher()
        candidates = matcher.suggest("双层脆鸡堡")
        self.assertTrue(candidates)
        rejected = [c for c in candidates if c.rejected_because]
        self.assertTrue(rejected, "被拒绝的候选应当保留淘汰理由")


class TestSupplementCatalog(unittest.TestCase):
    """补充热量表：营养表没有、但推导出了近似值的单个商品。"""

    def test_supplement_hit_is_its_own_tier(self) -> None:
        matcher = build_matcher(supplements=supplement_catalog())
        result = matcher.resolve("双层脆鸡堡")
        self.assertEqual(result.tier, MatchTier.SUPPLEMENT)
        self.assertEqual(result.kcal, 520.0)
        self.assertIsNotNone(result.supplement)
        self.assertEqual((result.supplement.low, result.supplement.high), (490.0, 560.0))

    def test_supplement_counts_toward_coverage(self) -> None:
        """有值就是有值，不能因为是估算就假装这项不存在。"""
        matcher = build_matcher(supplements=supplement_catalog())
        result = matcher.resolve("双层脆鸡堡")
        self.assertTrue(result.is_assumed)
        self.assertFalse(result.condiment)

    def test_supplement_beats_category_default(self) -> None:
        """针对单个商品的推导，比整类默认值更精确，必须优先。"""
        from mcd_order_calories.matching import MatchTier as Tier  # noqa: N813

        catalog = CategoryCatalog.from_dict({
            "rules": [{"id": "broad", "label": "宽规则",
                       "match": {"type": "contains", "value": "鸡"},
                       "default_kcal": 999, "exclude_from_coverage": False}]
        })
        matcher = build_matcher(categories=catalog, supplements=supplement_catalog())
        result = matcher.resolve("双层脆鸡堡")
        self.assertEqual(result.tier, Tier.SUPPLEMENT)
        self.assertEqual(result.kcal, 520.0)

    def test_supplement_requires_derivation_range_and_source(self) -> None:
        """三条硬规矩：缺任何一条都必须报错，不允许没有依据的数值进表。"""
        for broken, why in [
            ({"双层脆鸡堡": {"kcal": 520, "range": [490, 560], "source": "x"}}, "推导过程"),
            ({"双层脆鸡堡": {"kcal": 520, "derivation": "x", "source": "y"}}, "误差区间"),
            ({"双层脆鸡堡": {"kcal": 520, "derivation": "x", "range": [490, 560]}}, "来源标注"),
        ]:
            with self.subTest(missing=why):
                from mcd_order_calories.supplements import SupplementCatalog

                with self.assertRaises(ValueError) as ctx:
                    SupplementCatalog.from_dict({"supplements": broken})
                self.assertIn(why, str(ctx.exception))

    def test_supplement_lookup_is_normalized(self) -> None:
        matcher = build_matcher(supplements=supplement_catalog())
        # 全角/半角、空格差异不应影响命中
        for name in ["双层脆鸡堡", "双层脆鸡堡 "]:
            with self.subTest(name=repr(name)):
                self.assertEqual(matcher.resolve(name).tier, MatchTier.SUPPLEMENT)

    def test_existing_report_renders_derivation(self) -> None:
        """输出里必须带上推导过程、区间和来源，否则无法复核。"""
        from mcd_order_calories.orders import compute_order

        order = {"orderId": "X", "createTime": "2026-08-01 12:00:00",
                 "storeName": "测试门店", "storeCode": "1", "realTotalAmount": "30",
                 "orderProductList": [{"productCode": "1", "productName": "双层脆鸡堡",
                                       "quantity": 1}]}
        rendered = compute_order(order, build_matcher(supplements=supplement_catalog())).render()
        self.assertIn("推导估算值", rendered)
        self.assertIn("490~560", rendered)
        self.assertIn("推导：", rendered)
        self.assertIn("来源：", rendered)


class TestCategoryMatchingModes(unittest.TestCase):
    """品类规则的匹配方式、排除项与正则校验。"""

    def test_regex_match(self) -> None:
        catalog = CategoryCatalog.from_dict({
            "rules": [{"id": "r", "label": "正则", "match": {"type": "regex", "value": "厚松饼.*堡$"},
                       "default_kcal": 390, "exclude_from_coverage": False}]
        })
        self.assertTrue(catalog.classify("巧克力味厚松饼猪柳堡"))
        self.assertFalse(catalog.classify("巧克力味厚松饼猪柳蛋套餐"))

    def test_invalid_regex_fails_at_load_not_silently(self) -> None:
        """正则写错必须在加载期报错，否则规则会静默失效、退化成'查不到'。"""
        with self.assertRaises(ValueError) as ctx:
            CategoryCatalog.from_dict({
                "rules": [{"id": "bad", "label": "坏正则",
                           "match": {"type": "regex", "value": "(未闭合"},
                           "default_kcal": 1, "exclude_from_coverage": False}]
            })
        self.assertIn("正则无效", str(ctx.exception))

    def test_exclude_match_beats_match(self) -> None:
        """排除项优先：命中排除子串即整条规则不适用。"""
        catalog = CategoryCatalog.from_dict({
            "rules": [{"id": "r", "label": "含松饼", "match": {"type": "contains", "value": "松饼"},
                       "exclude_match": ["套餐", "堡"],
                       "default_kcal": 132, "exclude_from_coverage": False}]
        })
        self.assertIsNotNone(catalog.classify("厚松饼"))
        self.assertIsNone(catalog.classify("巧克力味厚松饼猪柳蛋套餐"))
        self.assertIsNone(catalog.classify("枫糖风味厚松饼猪柳堡"))

    def test_unknown_match_type_raises(self) -> None:
        catalog = CategoryCatalog.from_dict({
            "rules": [{"id": "r", "label": "未知", "match": {"type": "nope", "value": "x"},
                       "default_kcal": 1, "exclude_from_coverage": False}]
        })
        with self.assertRaises(ValueError) as ctx:
            catalog.classify("任意")
        self.assertIn("未知的匹配方式", str(ctx.exception))


class TestStrippedTier(unittest.TestCase):
    """菜单名会在括号里加口味后缀，营养表用基础名。

    处理方式是**剥掉尾部括号后要求完全相等**，而不是放宽相似度门槛——
    后者会把对已知假阳性的安全边际压薄。
    """

    def test_trailing_flavour_suffix_resolves(self) -> None:
        matcher = build_matcher()
        result = matcher.resolve("那么大鸡排（椒盐风味）")
        self.assertEqual(result.tier, MatchTier.STRIPPED)
        self.assertEqual(result.matched_name, "那么大鸡排")
        self.assertEqual(result.kcal, 385.0)
        self.assertIn("剥掉尾部括号说明", result.reason)

    def test_half_width_brackets_also_work(self) -> None:
        matcher = build_matcher()
        self.assertEqual(matcher.resolve("那么大鸡排(椒盐风味)").matched_name, "那么大鸡排")

    def test_plain_name_is_still_exact_not_stripped(self) -> None:
        matcher = build_matcher()
        self.assertEqual(matcher.resolve("那么大鸡排").tier, MatchTier.EXACT)

    def test_guard_still_blocks_stripped_matches(self) -> None:
        """剥完虽然相等，但含糖标记冲突——必须拒绝，不能因为规则更强就跳过硬约束。"""
        matcher = build_matcher()
        result = matcher.resolve("可乐中杯（无糖）")
        self.assertNotEqual(result.tier, MatchTier.STRIPPED)
        self.assertIsNone(result.matched_name)

    def test_stripping_does_not_invent_a_match(self) -> None:
        """剥完不在营养表里就不该匹配——蘸酱鸡球不能靠剥括号变成某个商品。"""
        matcher = build_matcher()
        result = matcher.resolve("蘸酱鸡球（甜酸酱风味）")
        self.assertFalse(result.tier.is_matched)
        self.assertIsNone(result.kcal)


class TestCategoryRules(unittest.TestCase):
    def test_sauce_gets_category_tier_and_excluded_from_coverage(self) -> None:
        matcher = build_matcher(categories=category_catalog())
        result = matcher.resolve("日式鲜磨山葵酱")
        self.assertEqual(result.tier, MatchTier.CATEGORY)
        self.assertEqual(result.category_id, "sauce")
        self.assertTrue(result.condiment, "蘸酱应标记为不计入覆盖率分母")
        self.assertEqual(result.kcal, 0.0)

    def test_configured_category_value_is_used(self) -> None:
        matcher = build_matcher(categories=category_catalog(default_sauce_kcal=35))
        self.assertEqual(matcher.resolve("甜酸酱").kcal, 35.0)

    def test_sauce_is_not_fuzzy_matched_to_a_food(self) -> None:
        """蘸酱应走品类通道，不能被模糊匹配成某个汉堡。"""
        matcher = build_matcher(categories=category_catalog())
        result = matcher.resolve("日式鲜磨山葵酱")
        self.assertEqual(result.tier, MatchTier.CATEGORY)
        self.assertNotIn(result.matched_name, {"双层吉士汉堡", "双层深海鳕鱼堡"})

    def test_sauce_like_food_is_not_treated_as_condiment(self) -> None:
        """『蘸酱鸡球』是鸡球，不是酱——按缺口处理，热量不被丢弃。"""
        matcher = build_matcher(categories=category_catalog())
        result = matcher.resolve("蘸酱鸡球（甜酸酱风味）")
        self.assertFalse(result.condiment)
        self.assertFalse(result.tier.is_matched)
        self.assertIsNone(result.kcal)


class TestMinuteMaidCategory(unittest.TestCase):
    """【美汁源】饮料：按品类默认值归一，但**计入**覆盖率分母。"""

    def test_minute_maid_matches_by_prefix(self) -> None:
        matcher = build_matcher(categories=category_catalog())
        result = matcher.resolve("【美汁源】“多汁柠柠”")
        self.assertEqual(result.tier, MatchTier.CATEGORY)
        self.assertEqual(result.category_id, "minute_maid")
        self.assertEqual(result.kcal, 165.0)

    def test_minute_maid_counts_toward_coverage(self) -> None:
        """它是真实饮品，不能因为是估算就假装这项不存在。"""
        matcher = build_matcher(categories=category_catalog())
        result = matcher.resolve("【美汁源】“多汁柠柠”")
        self.assertFalse(result.condiment)
        self.assertTrue(result.is_assumed)

    def test_value_can_be_overridden_per_flavour(self) -> None:
        catalog = CategoryCatalog.from_dict({
            "rules": [
                {"id": "minute_maid", "label": "【美汁源】饮料",
                 "match": {"type": "prefix", "value": "【美汁源】"},
                 "default_kcal": 165, "exclude_from_coverage": False,
                 "kcal": {"【美汁源】“多汁柠柠”": 120}},
            ]
        })
        matcher = build_matcher(categories=catalog)
        self.assertEqual(matcher.resolve("【美汁源】“多汁柠柠”").kcal, 120.0)
        self.assertEqual(matcher.resolve("【美汁源】“某新口味”").kcal, 165.0)

    def test_unmatched_prefix_does_not_leak(self) -> None:
        """只有【美汁源】开头的才走该规则，普通饮料不受影响。"""
        matcher = build_matcher(categories=category_catalog())
        result = matcher.resolve("可口可乐中杯")
        self.assertEqual(result.tier, MatchTier.GUARDED)
        self.assertEqual(result.matched_name, "可乐中杯")


if __name__ == "__main__":
    unittest.main()
