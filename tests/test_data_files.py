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
                self.assertLessEqual(sup.low, sup.high, "区间上下界写反了")
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

    def test_list_tools_means_doctor_not_calories(self) -> None:
        """`--list-tools` 是自检诉求，不能被补成 `calories` 去拉订单。"""
        from mcd_order_calories.cli import build_parser, inject_default_command

        argv = inject_default_command(["--list-tools"], self.COMMANDS)
        self.assertEqual(argv, ["--list-tools", "doctor"])
        args = build_parser().parse_args(argv)
        self.assertTrue(args.list_tools)
        self.assertEqual(args.command, "doctor")

    def test_help_is_not_rewritten_into_subcommand_help(self) -> None:
        """`--help` 必须显示总览帮助，而不是被补成 `calories --help`。"""
        from mcd_order_calories.cli import inject_default_command

        for argv in (["--help"], ["-h"], ["--token", "t", "--help"]):
            with self.subTest(argv=argv):
                self.assertEqual(inject_default_command(argv, self.COMMANDS), argv)

    def test_global_options_accepted_after_subcommand(self) -> None:
        """`doctor --json`、`calories --url u` 这类写法必须能用。

        子命令里的全局选项默认值是 SUPPRESS，所以未显式传入时不会覆盖主 parser 的值。
        """
        from mcd_order_calories.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--url", "https://a", "doctor", "--json"])
        self.assertEqual(args.url, "https://a")
        self.assertTrue(args.json)
        self.assertEqual(args.command, "doctor")

        args = parser.parse_args(["doctor", "--url", "https://b"])
        self.assertEqual(args.url, "https://b")


class TestEnvFileLoading(unittest.TestCase):
    """多 Agent 安装后 Token 仍要能被读到，且不能覆盖用户已有的环境变量。"""

    def test_parse_env_text_supports_expected_subset(self) -> None:
        from mcd_order_calories.config import parse_env_text

        values = parse_env_text(
            "# 注释\n"
            "\n"
            "export MCD_MCP_TOKEN=abc123\n"
            "MCD_MCP_URL=https://example.test\n"
            'QUOTED="has space"\n'
            "SINGLE='single'\n"
            "TOKEN_WITH_HASH=tok#not-a-comment\n"
            "TRAILING_COMMENT=value # 这里的 # 前有空格，也按值处理\n"
            "EMPTY=\n"
            "BAD LINE WITHOUT EQUALS\n"
            "9INVALID=skipped\n"
        )
        self.assertEqual(values["MCD_MCP_TOKEN"], "abc123")
        self.assertEqual(values["MCD_MCP_URL"], "https://example.test")
        self.assertEqual(values["QUOTED"], "has space")
        self.assertEqual(values["SINGLE"], "single")
        self.assertEqual(values["TOKEN_WITH_HASH"], "tok#not-a-comment")
        self.assertEqual(values["EMPTY"], "")
        self.assertNotIn("9INVALID", values)
        self.assertEqual(len(values), 7)

    def test_candidate_env_files_order(self) -> None:
        """显式 $MCD_ENV_FILE → 当前目录 .env → Skill 根目录 .env。"""
        import os
        import tempfile
        from pathlib import Path

        from mcd_order_calories.config import ENV_FILE_VAR, candidate_env_files, install_root

        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "custom.env"
            saved = os.environ.get(ENV_FILE_VAR)
            os.environ[ENV_FILE_VAR] = str(explicit)
            try:
                candidates = candidate_env_files(Path(tmp))
            finally:
                if saved is None:
                    os.environ.pop(ENV_FILE_VAR, None)
                else:
                    os.environ[ENV_FILE_VAR] = saved

            self.assertEqual(candidates[0], explicit)
            self.assertEqual(candidates[1], Path(tmp) / ".env")
            root = install_root()
            if root is not None:
                self.assertIn(root / ".env", candidates)
            self.assertEqual(len(candidates), len(set(candidates)), "候选路径不应重复")

    def test_load_env_does_not_override_existing_values(self) -> None:
        import tempfile
        from pathlib import Path

        from mcd_order_calories.config import ENV_TOKEN, load_env

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(
                f"{ENV_TOKEN}=from-file\nOTHER_KEY=from-file\n", encoding="utf-8")
            environ = {ENV_TOKEN: "from-shell"}
            applied = load_env(cwd=Path(tmp), environ=environ)

            self.assertTrue(applied, "应至少应用了当前目录的 .env")
            self.assertEqual(environ[ENV_TOKEN], "from-shell", "已有环境变量不能被文件覆盖")
            self.assertEqual(environ["OTHER_KEY"], "from-file", "缺失的键应从文件补齐")

    def test_load_env_override_when_requested(self) -> None:
        import tempfile
        from pathlib import Path

        from mcd_order_calories.config import ENV_TOKEN, load_env

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(f"{ENV_TOKEN}=from-file\n", encoding="utf-8")
            environ = {ENV_TOKEN: "from-shell"}
            load_env(cwd=Path(tmp), override=True, environ=environ)
            self.assertEqual(environ[ENV_TOKEN], "from-file")

    def test_missing_env_file_is_not_an_error(self) -> None:
        import tempfile
        from pathlib import Path

        from mcd_order_calories.config import load_env, load_env_file

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_env_file(Path(tmp) / "nope.env"), {})
            self.assertEqual(load_env(cwd=Path(tmp), environ={}), [])

    def test_mask_secret_never_reveals_full_token(self) -> None:
        from mcd_order_calories.config import mask_secret

        token = "abcdefghijklmnop"
        masked = mask_secret(token)
        self.assertNotIn(token, masked)
        self.assertIn("abcd", masked)
        self.assertIn("mnop", masked)
        self.assertEqual(mask_secret(None), "(未设置)")
        self.assertEqual(mask_secret("short"), "*****")


class TestListToolsCommand(unittest.TestCase):
    """`--list-tools` / `doctor` 的 JSON 输出是各 Agent 的自检依据，必须字段稳定。"""

    def _run_with_fake_client(self, argv, tool_names):
        import io
        import unittest.mock as mock

        from mcd_order_calories import cli

        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        fake.server_info = {"serverInfo": {"name": "mcd-mcp", "version": "1.0"}}
        fake.tool_names.return_value = tool_names

        out = io.StringIO()
        with mock.patch.object(cli, "McpHttpClient", return_value=fake), \
                mock.patch("sys.stdout", out):
            code = cli.run(argv)
        return code, out.getvalue()

    def test_doctor_json_reports_ready_when_required_tools_present(self) -> None:
        import json as jsonlib

        secret = "secret-token-value"
        code, output = self._run_with_fake_client(
            ["--list-tools", "--json", "--token", secret],
            ["order-list", "list-nutrition-foods", "now-time-info"],
        )
        self.assertEqual(code, 0, output)
        payload = jsonlib.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["required_missing"], [])
        self.assertEqual(payload["tools"], 3)
        self.assertIn("now-time-info", payload["tool_names"])
        self.assertNotIn(secret, output, "JSON 输出里的 Token 必须脱敏")
        self.assertIn("secr", payload["token"])

    def test_doctor_json_flags_missing_required_tool(self) -> None:
        import json as jsonlib

        code, output = self._run_with_fake_client(
            ["doctor", "--json", "--token", "tok"], ["order-list"],
        )
        self.assertEqual(code, 1, "缺少必需工具时退出码应为 1")
        payload = jsonlib.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["required_missing"], ["list-nutrition-foods"])


if __name__ == "__main__":
    unittest.main()
