"""出厂数据文件的冒烟测试。

这些 JSON 是人工维护的，最容易出现的不是代码 bug 而是**数据写坏**
（漏字段、JSON 语法错、区间写成单值…）。所以直接加载仓库里真实的那几份文件，
而不是用测试替身。
"""

from __future__ import annotations

import unittest

from mcd_order_calories.categories import CategoryCatalog
from mcd_order_calories.matching import AliasTable
from mcd_order_calories.orders import GapLedger
from mcd_order_calories.supplements import SupplementCatalog
from mcd_order_calories.cli import (
    load_alias_table,
    load_category_catalog,
    load_gap_ledger,
    load_supplement_catalog,
)


class TestShippedDataLoads(unittest.TestCase):
    def test_all_catalogs_load(self) -> None:
        self.assertIsInstance(load_alias_table(), AliasTable)
        self.assertIsInstance(load_category_catalog(), CategoryCatalog)
        self.assertIsInstance(load_supplement_catalog(), SupplementCatalog)
        self.assertIsInstance(load_gap_ledger(), GapLedger)

    def test_category_rules_are_wellformed(self) -> None:
        catalog = load_category_catalog()
        self.assertTrue(len(catalog) >= 2, "至少应有蘸酱与【美汁源】两条规则")
        for rule in catalog.rules:
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.id)
                self.assertTrue(rule.label)
                self.assertTrue(rule.match_type, "缺少 match.type")
                self.assertTrue(rule.source, "缺少 source 说明")
                # 匹配方式必须是注册过的谓词
                rule.matches("测试商品名")

    def test_sauce_rule_is_condiment_and_minute_maid_is_not(self) -> None:
        """两条规则的覆盖率语义必须相反——这是刻意设计，不能被改坏。"""
        catalog = load_category_catalog()
        sauce = next(r for r in catalog.rules if r.id == "sauce")
        maid = next(r for r in catalog.rules if r.id == "minute_maid")
        self.assertTrue(sauce.exclude_from_coverage)
        self.assertFalse(maid.exclude_from_coverage)

    def test_supplements_satisfy_three_hard_rules(self) -> None:
        """推导、区间、来源三项缺一不可——加载本身就会校验。"""
        catalog = load_supplement_catalog()
        self.assertTrue(len(catalog) >= 1)
        for key, sup in catalog.entries.items():
            with self.subTest(name=sup.name):
                self.assertTrue(sup.derivation, "缺少推导过程")
                self.assertTrue(sup.source, "缺少来源标注")
                self.assertIsNotNone(sup.low)
                self.assertIsNotNone(sup.high)
                self.assertLess(sup.low, sup.high, "区间上下界写反了")
                self.assertGreaterEqual(sup.kcal, sup.low)
                self.assertLessEqual(sup.kcal, sup.high)

    def test_coconut_burger_treats_coconut_as_sauce_not_marinade(self) -> None:
        """回归：曾把「椰香」当作可能增脂的腌料，导致区间上界偏大。"""
        catalog = load_supplement_catalog()
        sup = catalog.lookup("东南亚风味椰香鸡扒堡")
        self.assertIsNotNone(sup)
        assert sup is not None
        self.assertLessEqual(sup.high, 395, "确认椰香是酱之后，区间上界应收紧")
        self.assertIn("酱", sup.derivation)
        self.assertNotIn("腌料", sup.source)

    def test_hotcake_rule_covers_flavours_but_not_combos(self) -> None:
        """所有松饼堡按同一推导（风味不改变热量），但套餐形态结构不同，必须排除。"""
        catalog = load_category_catalog()
        rule = next((r for r in catalog.rules if r.id == "hotcake_burger"), None)
        self.assertIsNotNone(rule, "缺少 hotcake_burger 规则")
        assert rule is not None
        self.assertEqual(rule.default_kcal, 390)
        self.assertTrue(rule.matches("枫糖风味厚松饼猪柳堡"))
        self.assertTrue(rule.matches("巧克力味厚松饼猪柳堡"))
        self.assertFalse(rule.matches("巧克力味厚松饼猪柳蛋套餐"))
        self.assertFalse(rule.matches("厚松饼"))

    def test_hotcake_is_recorded_as_a_per_piece_benchmark(self) -> None:
        """132 是『每片』基准值，必须记在公共基准值里，而不是当成某个商品的整份热量。"""
        import json

        from mcd_order_calories.cli import _data_text

        bench = json.loads(_data_text("supplements.json"))["_公共基准值"]
        self.assertEqual(bench["厚松饼"], 132)
        self.assertIn("按片计", bench["_厚松饼推导"])

    def test_status_policy_loads_and_is_conservative(self) -> None:
        """出厂状态策略必须可加载，且未识别状态一定不计入合计。"""
        from mcd_order_calories.cli import load_status_policy

        policy = load_status_policy()
        self.assertEqual(policy.classify("订单已完成"), "consumed")
        self.assertEqual(policy.classify("待支付"), "pending")
        self.assertEqual(policy.classify("从没见过的状态"), "other")

    def test_gap_ledger_entries_all_have_reasons(self) -> None:
        ledger = load_gap_ledger()
        for name, reason in ledger.entries.items():
            with self.subTest(name=name):
                self.assertTrue(reason and len(reason) > 10, "缺口必须有可读的判定理由")


class TestCliArgumentHandling(unittest.TestCase):
    """CLI 必须容忍省略子命令——`mcd-calories`、`mcd-calories --json` 都应出结果。

    这是一个真实踩过的坑：最初子命令设为 required，README 里写的 `mcd-calories`
    直接运行会报错；改成默认子命令后，又因为 argparse 要求全局选项在子命令之前，
    `--json` 单独用仍然失败。
    """

    COMMANDS = ["calories", "doctor", "explain", "gaps"]

    def test_empty_argv_gets_default_command(self) -> None:
        from mcd_order_calories.cli import inject_default_command

        self.assertEqual(inject_default_command([], self.COMMANDS), ["calories"])

    def test_global_bool_option_stays_before_command(self) -> None:
        from mcd_order_calories.cli import inject_default_command

        self.assertEqual(inject_default_command(["--json"], self.COMMANDS), ["--json", "calories"])

    def test_global_valued_option_stays_before_command(self) -> None:
        from mcd_order_calories.cli import inject_default_command

        self.assertEqual(inject_default_command(["--url", "u", "--token", "t"], self.COMMANDS),
                         ["--url", "u", "--token", "t", "calories"])

    def test_subcommand_specific_option_goes_after_command(self) -> None:
        from mcd_order_calories.cli import inject_default_command

        self.assertEqual(inject_default_command(["--detail"], self.COMMANDS),
                         ["calories", "--detail"])
        self.assertEqual(inject_default_command(["--url", "u", "--detail"], self.COMMANDS),
                         ["--url", "u", "calories", "--detail"])

    def test_explicit_command_is_untouched(self) -> None:
        from mcd_order_calories.cli import inject_default_command

        for argv in (["doctor"], ["gaps"], ["--json", "doctor"], ["explain", "麦香鸡"]):
            with self.subTest(argv=argv):
                self.assertEqual(inject_default_command(argv, self.COMMANDS), argv)

    def test_parser_accepts_resulting_argv(self) -> None:
        """注入后的 argv 必须真的能被 parser 解析（不只是字符串拼接正确）。"""
        from mcd_order_calories.cli import build_parser, inject_default_command

        parser = build_parser()
        for argv in ([], ["--json"], ["--detail"], ["--limit", "3"]):
            with self.subTest(argv=argv):
                args = parser.parse_args(inject_default_command(argv, self.COMMANDS))
                self.assertEqual(args.command, "calories")


if __name__ == "__main__":
    unittest.main()
