"""补充热量表的一致性测试。

这些不是「某个值等于多少」的硬编码断言，而是**条目之间的关系**。
关系比数值更稳定：即使将来换了基准值，关系仍然必须成立。
"""

from __future__ import annotations

import unittest

from mcd_order_calories.cli import load_alias_table, load_category_catalog, load_supplement_catalog
from mcd_order_calories.matching import ProductMatcher
from mcd_order_calories.nutrition import parse_nutrition
from mcd_order_calories.cli import _data_text
import json


def build_matcher() -> ProductMatcher:
    entries, _ = parse_nutrition(_raw_nutrition())
    return ProductMatcher(entries, load_alias_table(),
                          categories=load_category_catalog(),
                          supplements=load_supplement_catalog())


def _raw_nutrition() -> str:
    """用营养表 fixture 构造，避免测试联网。"""
    from fixtures import nutrition_payload

    return nutrition_payload()


def benchmarks() -> dict:
    return json.loads(_data_text("supplements.json"))["_公共基准值"]


class TestSingleDoubleRelationship(unittest.TestCase):
    """单层与双层必须相差「恰好一层」，这是类比的全部意义所在。"""

    def setUp(self) -> None:
        self.supplements = load_supplement_catalog()
        self.single = self.supplements.lookup("Hold不住鸡排堡（椒盐风味）")
        self.double = self.supplements.lookup("双层脆鸡堡")

    def test_both_entries_exist(self) -> None:
        self.assertIsNotNone(self.single, "缺少单层鸡排堡条目")
        self.assertIsNotNone(self.double, "缺少双层脆鸡堡条目")

    def test_difference_equals_one_patty(self) -> None:
        assert self.single and self.double
        patty = benchmarks()["麦香鸡层鸡排"]
        self.assertAlmostEqual(self.double.kcal - self.single.kcal, patty, delta=1)

    def test_single_is_consistent_with_mcchicken(self) -> None:
        """麦香鸡结构与本堡相同（面包+鸡排+酱），两者应互相佐证。"""
        assert self.single
        mcchicken = 369
        self.assertLess(abs(self.single.kcal - mcchicken), 15,
                        "单层鸡排堡与麦香鸡偏差过大，说明某一条推导有问题")


class TestProvidedValues(unittest.TestCase):
    """外部给定值与本项目推导值必须可区分。

    外部给定值的区间可能**无法刻画真实误差**，输出里必须说清，
    不能让读者以为它和推导值同等可信。
    """

    def test_cheese_stick_is_marked_as_provided(self) -> None:
        sup = load_supplement_catalog().lookup("马苏里拉拉丝芝士条")
        self.assertIsNotNone(sup)
        assert sup is not None
        self.assertTrue(sup.is_provided)
        self.assertEqual(sup.basis, "provided")
        self.assertEqual(sup.kcal, 1200)
        self.assertIn("外部给定", sup.basis_text)

    def test_provided_value_keeps_the_doubt_on_record(self) -> None:
        """价格密度冲突必须留在条目里，不能因为按原值录入就删掉留痕。"""
        sup = load_supplement_catalog().lookup("马苏里拉拉丝芝士条")
        assert sup is not None
        self.assertIn("未经本项目独立验证", sup.source)

    def test_derived_entries_are_not_marked_provided(self) -> None:
        for name in ["双层脆鸡堡", "Hold不住鸡排堡（椒盐风味）",
                     "泰式炭烤风味猪猪堡", "东南亚风味椰香鸡扒堡", "世界波脆鸡排蛋堡"]:
            with self.subTest(name=name):
                sup = load_supplement_catalog().lookup(name)
                assert sup is not None
                self.assertFalse(sup.is_provided, f"{name} 应为本项目推导值")
                self.assertIn("本项目推导", sup.basis_text)

    def test_point_value_renders_without_a_fake_range(self) -> None:
        """low == high 时不应渲染成 “1200（1200~1200）”。"""
        sup = load_supplement_catalog().lookup("马苏里拉拉丝芝士条")
        assert sup is not None
        self.assertEqual(sup.range_text, "1200")

    def test_out_of_range_value_is_rejected(self) -> None:
        from mcd_order_calories.supplements import SupplementCatalog

        with self.assertRaises(ValueError) as ctx:
            SupplementCatalog.from_dict({"supplements": {"X": {
                "kcal": 999, "range": [100, 200],
                "derivation": "d", "source": "s"}}})
        self.assertIn("不在区间内", str(ctx.exception))

    def test_bad_basis_is_rejected(self) -> None:
        from mcd_order_calories.supplements import SupplementCatalog

        with self.assertRaises(ValueError) as ctx:
            SupplementCatalog.from_dict({"supplements": {"X": {
                "kcal": 150, "range": [100, 200], "basis": "胡编",
                "derivation": "d", "source": "s"}}})
        self.assertIn("basis", str(ctx.exception))


class TestEntryHygiene(unittest.TestCase):
    """每条补充值必须自洽，且引用得到公共基准值。"""

    def test_all_entries_internally_consistent(self) -> None:
        for key, sup in load_supplement_catalog().entries.items():
            with self.subTest(name=sup.name):
                self.assertLessEqual(sup.low, sup.kcal)
                self.assertLessEqual(sup.kcal, sup.high)
                self.assertIn(sup.confidence, ("high", "medium", "low"))
                self.assertTrue(sup.derivation)
                self.assertTrue(sup.source)

    def test_benchmarks_are_positive_and_documented(self) -> None:
        bench = benchmarks()
        values = {k: v for k, v in bench.items()
                  if not k.startswith("_") and isinstance(v, (int, float))}
        self.assertGreaterEqual(len(values), 8, "公共基准值数量异常")
        for name, value in values.items():
            with self.subTest(benchmark=name):
                self.assertGreater(value, 0)
                self.assertIn(f"_{name}推导", bench, f"基准值 {name} 缺少推导说明")

    def test_benchmark_self_checks_are_recorded(self) -> None:
        checks = benchmarks().get("_自检") or []
        self.assertGreaterEqual(len(checks), 3, "应至少记录三组自检，用于保证基准值自洽")


if __name__ == "__main__":
    unittest.main()
