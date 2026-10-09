"""`install.sh` 的冒烟测试。

当前**只适配 WorkBuddy**，其余渠道在脚本里是「明确报错」的占位。安装脚本一旦写坏，
用户看到的是「装完没反应」或「装到了不该装的地方」，所以这里锁住四件事：

1. 脚本**语法**有效（`bash -n`）——最便宜的回归防线；
2. WorkBuddy 能**完整装通**：复制 + `.env`(600) + 装完副本能读到 Token；
3. 未适配渠道**明确失败且零副作用**（不能静默装到别处）；
4. `--dry-run` 什么都不写——「先看看」不能变成「先装上」。

真实 Token 相关的检查只做「不回显、不落进仓库」；测试全程用临时目录与假 `HOME`，
不会碰到开发机上真实的 Agent 配置。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"

BASH = shutil.which("bash")
PYTHON = shutil.which("python3") or shutil.which("python")

# 只保留必要路径，避免测试环境里恰好存在的 Agent CLI 影响脚本分支
BASE_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    "LC_ALL": "en_US.UTF-8",
    "LANG": "en_US.UTF-8",
}

SKILL_DIR_NAME = "mcd-order-calories"

# 已适配 / 占位（尚未适配）的渠道
ADAPTED = ("workbuddy",)
PLACEHOLDER = ("kiro", "claude-code", "cursor", "vscode",
               "cherry-studio", "claude-desktop", "generic")


def run_install(*args: str, home: Path | None = None, cwd: Path | None = None):
    env = dict(BASE_ENV)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [BASH, str(INSTALL_SH), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@unittest.skipIf(BASH is None, "环境里没有 bash")
class TestInstallScript(unittest.TestCase):

    def setUp(self) -> None:
        if not INSTALL_SH.is_file():
            self.skipTest("install.sh 不存在")

    # ------------------------------------------------------------ 基础契约

    def test_script_exists_and_is_executable(self) -> None:
        self.assertTrue(INSTALL_SH.is_file())
        self.assertTrue(INSTALL_SH.stat().st_mode & 0o111, "install.sh 应可执行")

    def test_syntax_is_valid(self) -> None:
        result = subprocess.run([BASH, "-n", str(INSTALL_SH)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, f"bash -n 报错：{result.stderr}")

    def test_help_declares_workbuddy_adapted_and_others_placeholder(self) -> None:
        result = run_install("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workbuddy", result.stdout)
        self.assertIn("已适配", result.stdout)
        self.assertIn("尚未适配", result.stdout)
        for target in PLACEHOLDER:
            with self.subTest(target=target):
                self.assertIn(target, result.stdout)

    # -------------------------------------------------- 未适配渠道：零副作用

    def test_placeholder_targets_fail_loudly_without_side_effects(self) -> None:
        """占位渠道必须报错退出，且不在 HOME 下留下任何目录。"""
        for target in PLACEHOLDER:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                home.mkdir()
                result = run_install(target, "--token", "SOMETOKEN", home=home)

                self.assertEqual(result.returncode, 2, f"{target} 应以退出码 2 失败")
                self.assertIn("尚未适配", result.stderr)
                self.assertEqual(list(home.iterdir()), [],
                                 f"{target} 不应在 HOME 下创建任何文件")

    # ---------------------------------------------------- WorkBuddy：装通

    def test_default_target_installs_workbuddy_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            result = run_install("--token", "WBTOKEN0123456789", home=home)

            self.assertEqual(result.returncode, 0, result.stderr)
            skill_dir = home / ".workbuddy" / "skills" / SKILL_DIR_NAME
            self.assertTrue((skill_dir / "SKILL.md").is_file(), "必须带上 SKILL.md")
            self.assertTrue((skill_dir / "src" / "mcd_order_calories").is_dir())
            self.assertTrue((skill_dir / "INSTALL.md").is_file())

            env_file = skill_dir / ".env"
            self.assertTrue(env_file.is_file(), "提供 --token 时应生成 .env")
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600,
                             ".env 必须只有本人可读写")
            self.assertIn("WBTOKEN0123456789", env_file.read_text(encoding="utf-8"))

            # 复制时不得把仓库的 .env / 缓存一起带走
            self.assertFalse((skill_dir / ".git").exists(), "不应复制 .git")
            cache = list(skill_dir.rglob("__pycache__"))
            self.assertEqual(cache, [], "不应复制 __pycache__")

    def test_installed_copy_can_read_its_own_env(self) -> None:
        """装完必须「开箱可跑」：从任意工作目录都能读到技能目录里的 .env。"""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            run_install("--token", "INSTALLEDTOKEN123", home=home)
            skill_dir = home / ".workbuddy" / "skills" / SKILL_DIR_NAME

            env = dict(BASE_ENV)
            env["HOME"] = str(home)
            env["PYTHONPATH"] = str(skill_dir / "src")
            probe = (
                "from mcd_order_calories.config import load_env, mask_secret;"
                "import os;"
                "load_env();"
                "print(os.environ.get('MCD_MCP_TOKEN'));"
                "print(os.environ.get('MCD_MCP_URL'))"
            )
            result = subprocess.run([PYTHON, "-c", probe], cwd=str(home), env=env,
                                    capture_output=True, text=True, timeout=60)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("INSTALLEDTOKEN123", result.stdout)
            self.assertIn("https://mcp.mcd.cn", result.stdout)

    def test_custom_directory_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom-skills"
            result = run_install(str(target), "--token", "CUSTOMTOKEN42")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertEqual((target / ".env").stat().st_mode & 0o777, 0o600)

    def test_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "override"
            result = run_install("--dir", str(target), "--token", "DIRTOKEN1234")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "SKILL.md").is_file())

    def test_reinstall_removes_stale_files(self) -> None:
        """复装必须同步（rsync --delete），否则旧数据会留在技能目录里漂移。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"
            self.assertEqual(run_install(str(target)).returncode, 0)
            stale = target / "STALE.txt"
            stale.write_text("来自上一次安装", encoding="utf-8")

            self.assertEqual(run_install(str(target)).returncode, 0)
            self.assertFalse(stale.exists(), "复装后不应残留旧文件")

    def test_without_token_no_env_file_is_written(self) -> None:
        """不传 Token 就不该凭空生成 .env（避免写一个空凭据文件）。"""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            result = run_install(home=home)

            self.assertEqual(result.returncode, 0, result.stderr)
            skill_dir = home / ".workbuddy" / "skills" / SKILL_DIR_NAME
            self.assertFalse((skill_dir / ".env").exists())
            self.assertIn("界面", result.stdout, "应指引在 WorkBuddy 界面里配置连接器")

    def test_workbuddy_guidance_is_printed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            result = run_install(home=home)
            for hint in ("专家·技能·连接器", "配置MCP", "mcp-config/workbuddy.json"):
                with self.subTest(hint=hint):
                    self.assertIn(hint, result.stdout)

    # -------------------------------------------------------- 安全 / 无副作用

    def test_token_is_masked_in_output(self) -> None:
        secret = "SUPERSECRETTOKEN123456"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            result = run_install("--token", secret, "--dry-run", home=home)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)
        self.assertIn("SUPE…3456", result.stdout)

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            result = run_install("--token", "DRYRUNTOKEN", "--dry-run", home=home)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dry-run", result.stdout)
            self.assertEqual(list(home.iterdir()), [], "--dry-run 不应写 HOME 下的任何东西")

    def test_mcp_config_samples_are_placeholders_only(self) -> None:
        """样例只允许出现占位符——仓库里不能有任何真实凭据。"""
        samples = sorted((REPO_ROOT / "mcp-config").glob("*.json"))
        self.assertTrue(samples, "mcp-config/ 下应有 WorkBuddy 样例")
        sample = REPO_ROOT / "mcp-config" / "workbuddy.json"
        self.assertIn(sample, samples, "WorkBuddy 样例必须存在")

        text = sample.read_text(encoding="utf-8")
        self.assertIn("mcd-mcp", text)
        self.assertIn("mcp.mcd.cn", text)
        self.assertIn("${MCD_MCP_TOKEN}", text)
        for line in text.splitlines():
            if "Bearer" in line:
                self.assertIn("${MCD_MCP_TOKEN}", line)

    def test_unadapted_config_samples_are_not_shipped(self) -> None:
        """还没适配的平台不该留半成品样例，否则会被误当成可用配置。"""
        names = {p.name for p in (REPO_ROOT / "mcp-config").glob("*.json")}
        self.assertEqual(names, {"workbuddy.json"}, f"当前应只有 WorkBuddy 样例，实际：{names}")


if __name__ == "__main__":
    unittest.main()
