"""订单展开、覆盖率与缺口台账的测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixtures import ORDERS, category_catalog, combo, nutrition_payload, single

from mcd_order_calories.matching import AliasTable, ProductMatcher
from mcd_order_calories.nutrition import parse_nutrition
from mcd_order_calories.orders import GapLedger, compute_order, compute_orders, expand_order


def build_matcher() -> ProductMatcher:
    entries, _ = parse_nutrition(nutrition_payload())
    return ProductMatcher(entries, AliasTable(), categories=category_catalog())


class TestOrderExpansion(unittest.TestCase):
    def test_combo_is_expanded_into_components(self) -> None:
        """套餐容器本身不计入，只计成分——否则会拿容器名去匹配营养表而失败。"""
        order = ORDERS[0]
        units = expand_order(order)
        self.assertEqual([u.name for u in units], ["双层吉士汉堡", "小杯玉米杯"])
        self.assertTrue(all(u.from_combo for u in units))
        self.assertNotIn("人气经典随心配", [u.name for u in units])

    def test_single_item_stays_as_is(self) -> None:
        units = expand_order(ORDERS[1])
        self.assertEqual([u.name for u in units], ["世界波脆鸡排蛋堡", "中薯条"])
        self.assertFalse(any(u.from_combo for u in units))

    def test_quantity_is_preserved(self) -> None:
        order = ORDERS[0] | {"orderProductList": [single("999", "中薯条", quantity=3)]}
        units = expand_order(order)
        self.assertEqual(units[0].quantity, 3)


class TestCalorieComputation(unittest.TestCase):
    def test_complete_order_is_marked_complete(self) -> None:
        result = compute_order(ORDERS[0], build_matcher())
        self.assertTrue(result.is_complete)
        self.assertEqual(result.coverage, 1.0)
        # 双层吉士汉堡 429 + 小杯玉米杯 53
        self.assertEqual(result.known_kcal, 482.0)

    def test_partial_order_exposes_a_lower_bound(self) -> None:
        """覆盖率不足时数值是下界，必须能看出来。"""
        result = compute_order(ORDERS[1], build_matcher())
        self.assertFalse(result.is_complete)
        self.assertEqual(result.coverage, 0.5)
        self.assertEqual(result.known_kcal, 289.0)      # 只有中薯条
        self.assertEqual([u.unit.name for u in result.missing], ["世界波脆鸡排蛋堡"])
        self.assertIn("≥", result.render())
        self.assertIn("请勿据此做热量控制", result.render())

    def test_quantity_multiplies_calories(self) -> None:
        order = ORDERS[0] | {"orderProductList": [single("999", "中薯条", quantity=2)]}
        self.assertEqual(compute_order(order, build_matcher()).known_kcal, 578.0)


class TestSauceCoverageSemantics(unittest.TestCase):
    def test_condiment_is_excluded_from_coverage_denominator(self) -> None:
        """蘸酱是随餐附赠调味品，营养表不收；把它算进分母会让覆盖率失真。"""
        result = compute_order(ORDERS[2], build_matcher())
        self.assertEqual(len(result.units), 2)
        self.assertEqual(len(result.condiment_units), 1)
        self.assertEqual(len(result.substantive_units), 1)
        self.assertTrue(result.is_complete)          # 实质商品全命中
        self.assertEqual(result.coverage, 1.0)

    def test_assumption_is_disclosed(self) -> None:
        result = compute_order(ORDERS[2], build_matcher())
        rendered = result.render()
        self.assertIn("蘸酱", rendered)
        self.assertIn("非营养表数据", rendered)

    def test_sauce_like_food_still_counts_against_coverage(self) -> None:
        """『蘸酱鸡球』不是酱，必须拉低覆盖率，不能被蘸酱规则掩盖。"""
        result = compute_order(ORDERS[3], build_matcher())
        self.assertEqual(len(result.condiment_units), 0)
        self.assertFalse(result.is_complete)
        self.assertEqual(result.coverage, 0.0)
        self.assertEqual(result.known_units, 0)
        self.assertIn("蘸酱鸡球", result.render())


class TestGapLedger(unittest.TestCase):
    def test_triage_separates_known_gaps_from_new_ones(self) -> None:
        matcher = build_matcher()
        results = compute_orders(ORDERS, matcher)
        ledger = GapLedger({"世界波脆鸡排蛋堡": "赛事限定"})
        triage = ledger.triage(results)
        self.assertIn("世界波脆鸡排蛋堡", triage["known_gap"])
        self.assertIn("蘸酱鸡球（甜酸酱风味）", triage["new"])
        self.assertNotIn("日式鲜磨山葵酱", triage["known_gap"] + triage["new"])

    def test_round_trip_through_disk(self) -> None:
        ledger = GapLedger({"甲": "理由甲", "乙": "理由乙"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gaps.json"
            ledger.save(path)
            reloaded = GapLedger.load(path)
        self.assertEqual(len(reloaded), 2)
        self.assertEqual(reloaded.known("甲"), "理由甲")

    def test_comment_keys_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gaps.json"
            path.write_text(json.dumps({"_说明": "注释", "甲": "理由"}), encoding="utf-8")
            ledger = GapLedger.load(path)
        self.assertEqual(len(ledger), 1)

    def test_v2_schema_with_canonical_aliases(self) -> None:
        """同一产品的多种写法必须归到同一个规范名，不重复计数。"""
        ledger = GapLedger.from_dict({
            "aliases": {"订单里的写法": "规范名"},
            "gaps": {"规范名": "营养表未收录"},
        })
        self.assertEqual(ledger.canonical("订单里的写法"), "规范名")
        self.assertEqual(ledger.known("订单里的写法"), "营养表未收录")
        self.assertEqual(len(ledger), 1, "别名不应重复计入缺口条数")

    def test_v1_flat_schema_still_works(self) -> None:
        """向后兼容：旧的扁平格式仍然可用。"""
        ledger = GapLedger.from_dict({"_注释": "x", "某商品": "理由"})
        self.assertEqual(ledger.known("某商品"), "理由")
        self.assertEqual(len(ledger), 1)

    def test_canonical_alias_deduplicates_triage(self) -> None:
        """两种写法指同一产品时，triage 只应报出规范名一次。"""
        matcher = build_matcher()
        orders = [
            ORDERS[3] | {"orderProductList": [single("1", "蘸酱鸡球（甜酸酱风味）")]},
            ORDERS[3] | {"orderProductList": [single("2", "蘸酱脆皮鸡球")]},
        ]
        results = compute_orders(orders, matcher)
        ledger = GapLedger.from_dict({
            "aliases": {"蘸酱鸡球（甜酸酱风味）": "蘸酱脆皮鸡球"},
            "gaps": {"蘸酱脆皮鸡球": "营养表未收录"},
        })
        triage = ledger.triage(results)
        self.assertEqual(triage["known_gap"], ["蘸酱脆皮鸡球"])
        self.assertEqual(triage["new"], [])


if __name__ == "__main__":
    unittest.main()
