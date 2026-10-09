"""归一化与硬约束的测试。

这里的每一条都对应一个**实测踩过的真实坑**，不是设想出来的边界。
"""

from __future__ import annotations

import unittest

from mcd_order_calories.normalize import (
    guard_conflict_pair,
    guard_tokens,
    looks_like_sauce,
    normalize,
    similarity,
)


class TestNormalize(unittest.TestCase):
    def test_strips_mechanical_differences(self) -> None:
        """归一化只负责**机械性**差异：宽度、括号、连字符、空格、引号。"""
        pairs = [
            ("纯牛奶(盒装)", "纯牛奶（盒装）"),            # 半角/全角括号
            ("麦辣鸡翅2块", "麦辣鸡翅-2块"),              # 连字符
            ("中 薯条", "中薯条"),                     # 空格
            ('美汁源“黄金橙橙”', '美汁源"黄金橙橙"'),      # 引号
        ]
        for left, right in pairs:
            with self.subTest(pair=(left, right)):
                self.assertEqual(normalize(left), normalize(right))

    def test_semantic_variants_are_not_mechanically_equal(self) -> None:
        """语义变体**刻意**不被归一化抹平——留给硬约束与唯一性规则处理。

        试过把「可口」「风味」当营销词剥掉：那样这两组会直接相等、走 exact 命中，
        看似省事，实则让硬约束失去用武之地（见下一个测试）。
        """
        for left, right in [("可口可乐中杯", "可乐中杯"),
                            ("风味双层深海鳕鱼堡", "双层深海鳕鱼堡")]:
            with self.subTest(pair=(left, right)):
                self.assertNotEqual(normalize(left), normalize(right))

    def test_does_not_merge_sugar_variants(self) -> None:
        """含糖与无糖是两个商品，归一化不得抹平。"""
        self.assertNotEqual(normalize("可口可乐中杯"), normalize("无糖可口可乐中杯"))

    def test_does_not_merge_size_variants(self) -> None:
        self.assertNotEqual(normalize("大薯条"), normalize("中薯条"))
        self.assertNotEqual(normalize("可乐大杯"), normalize("可乐中杯"))

    def test_similarity_ranks_the_wrong_answer_first(self) -> None:
        """记录一个反直觉的实测事实：**错答案的相似度更高**。

            similarity(可口可乐中杯, 无糖可口可乐中杯) = 0.732   ← 错，且更高
            similarity(可口可乐中杯, 可乐中杯)        = 0.633

        含糖与无糖只有两字之差，字符串上反而更"像"。这就是为什么本项目的
        自动采纳**绝不能**依赖相似度排序，而必须靠硬约束把无糖候选淘汰掉。
        """
        wrong = similarity("可口可乐中杯", "无糖可口可乐中杯")
        right = similarity("可口可乐中杯", "可乐中杯")
        self.assertGreater(wrong, right)
        self.assertAlmostEqual(wrong, 0.732, places=3)
        self.assertAlmostEqual(right, 0.633, places=3)


class TestGuardTokens(unittest.TestCase):
    def test_detects_cup_size(self) -> None:
        self.assertEqual(guard_tokens("可乐大杯").cup, "大杯")
        self.assertEqual(guard_tokens("可乐小杯").cup, "小杯")
        self.assertEqual(guard_tokens("可乐中杯").cup, "中杯")

    def test_detects_sugar_marker(self) -> None:
        self.assertEqual(guard_tokens("无糖可口可乐中杯").sugar, "无糖")
        self.assertEqual(guard_tokens("可口可乐中杯").sugar, "")

    def test_detects_temperature(self) -> None:
        self.assertEqual(guard_tokens("冰燕麦奶铁中杯").temperature, "冰")
        self.assertEqual(guard_tokens("热燕麦奶铁中杯").temperature, "热")

    def test_detects_count(self) -> None:
        self.assertEqual(guard_tokens("麦辣鸡翅-2块").counts, ("2块",))
        self.assertEqual(guard_tokens("麦乐鸡5块").counts, ("5块",))

    def test_no_conflict_when_one_side_is_unmarked(self) -> None:
        """单方未标注不算冲突，否则「可乐中杯」与「可口可乐中杯」永远匹配不上。"""
        self.assertIsNone(guard_conflict_pair(guard_tokens("可口可乐中杯"), guard_tokens("可乐中杯")))

    def test_conflict_on_sugar(self) -> None:
        conflict = guard_conflict_pair(guard_tokens("可口可乐中杯"),
                                      guard_tokens("无糖可口可乐中杯"))
        self.assertIsNotNone(conflict)
        self.assertIn("含糖", conflict)

    def test_conflict_on_cup_size(self) -> None:
        self.assertIsNotNone(guard_conflict_pair(guard_tokens("大薯条"), guard_tokens("中薯条")))

    def test_conflict_on_temperature(self) -> None:
        self.assertIsNotNone(
            guard_conflict_pair(guard_tokens("冰燕麦奶铁中杯"), guard_tokens("热燕麦奶铁中杯"))
        )

    def test_conflict_on_count(self) -> None:
        self.assertIsNotNone(guard_conflict_pair(guard_tokens("麦乐鸡4块"), guard_tokens("麦乐鸡5块")))


class TestSauceDetection(unittest.TestCase):
    """蘸酱识别：结尾为「酱」才算，否则会误伤「蘸酱鸡球」这类实质商品。"""

    def test_real_sauces(self) -> None:
        for name in ["日式鲜磨山葵酱", "甜酸酱", "蒜蓉辣椒酱",
                     "日式鲜磨山葵酱（小）", "日式鲜磨山葵酱(大)", "日式鲜磨山葵酱【小份】"]:
            with self.subTest(name=name):
                self.assertTrue(looks_like_sauce(name))

    def test_foods_that_merely_mention_sauce(self) -> None:
        """这些名字里有「酱」，但都是实质商品，绝不能被当成蘸酱丢掉热量。"""
        for name in ["蘸酱鸡球（甜酸酱风味）", "蘸酱麦麦脆汁鸡", "麦辣鸡腿汉堡", "双层脆鸡堡"]:
            with self.subTest(name=name):
                self.assertFalse(looks_like_sauce(name))

    def test_degenerate_inputs(self) -> None:
        for name in ["", "酱", "   "]:
            with self.subTest(name=repr(name)):
                self.assertFalse(looks_like_sauce(name))


if __name__ == "__main__":
    unittest.main()
