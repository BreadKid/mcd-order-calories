"""跨 Agent 配置解析 —— 零依赖的 ``.env`` 读取。

本 Skill 会被安装到不同 Agent 工具的 skills 目录（Claude Code、Kiro、Cursor…），
有时还以软链接方式被引用，工作目录不固定。为了「装上就能用」，这里按固定顺序
查找配置文件，并把解析结果以最小侵入的方式补齐到 ``os.environ``：

1. ``$MCD_ENV_FILE`` 显式指定的文件（最高优先级）；
2. 当前工作目录的 ``.env``（项目内直接运行时命中）；
3. Skill 根目录的 ``.env``（``install.sh`` 会往这里写）。

已有环境变量永远优先于文件内容——谁在更外层设置，谁说了算。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_FILE_VAR = "MCD_ENV_FILE"
ENV_TOKEN = "MCD_MCP_TOKEN"
ENV_URL = "MCD_MCP_URL"
DEFAULT_URL = "https://mcp.mcd.cn"

# 合法键名；同时用于剥掉 `export KEY=...` 写法
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
EXPORT_PREFIX = "export "

SENSITIVE_KEYS = frozenset({ENV_TOKEN})


def install_root() -> Path | None:
    """返回 Skill 根目录（即 ``pyproject.toml`` 所在目录），推断不出则返回 ``None``。

    安装到 site-packages 后 ``__file__`` 位于 ``.../site-packages/mcd_order_calories/config.py``，
    此时向上找 ``pyproject.toml`` 会失败——这是预期行为，这类安装应由环境变量提供 Token。
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def candidate_env_files(cwd: Path | None = None) -> list[Path]:
    """按优先级列出待查找的 ``.env`` 路径（去重，保持顺序）。"""
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        key = path.expanduser()
        if key not in seen:
            seen.add(key)
            found.append(key)

    explicit = os.environ.get(ENV_FILE_VAR)
    if explicit:
        add(Path(explicit))
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()
    add(cwd_path / ".env")
    root = install_root()
    if root is not None:
        add(root / ".env")
    return found


def _clean_value(raw: str) -> str:
    """去掉首尾空白与成对引号；不做变量插值，避免意外的二次展开。"""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def parse_env_text(text: str) -> dict[str, str]:
    """解析 ``.env`` 文本。

    规则与常见 dotenv 实现保持一致的最小集合：

    - 忽略空行与 ``#`` 开头的注释行；
    - 支持 ``KEY=VALUE`` 与 ``export KEY=VALUE``；
    - ``#`` 前无空白才会被当作注释，因此 Token 里含 ``#`` 不会被截断；
    - 值两端的成对引号会被剥掉；**不做变量插值**。
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(EXPORT_PREFIX):
            stripped = stripped[len(EXPORT_PREFIX):].lstrip()
        if "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            continue
        values[key] = _clean_value(raw)
    return values


def load_env_file(path: Path) -> dict[str, str]:
    """读取单个文件；文件不存在或不可读时返回空字典（不抛异常）。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return parse_env_text(text)


def load_env(*, cwd: Path | None = None, override: bool = False,
             environ: dict[str, str] | None = None) -> list[Path]:
    """把找到的 ``.env`` 内容补进 ``os.environ``，返回实际生效的文件列表。

    默认 ``override=False``：已存在的环境变量不会被文件覆盖。
    """
    target = os.environ if environ is None else environ
    applied: list[Path] = []
    for path in candidate_env_files(cwd):
        values = load_env_file(path)
        if not values:
            continue
        for key, value in values.items():
            if override or not target.get(key):
                target[key] = value
        applied.append(path)
    return applied


def mask_secret(value: str | None) -> str:
    """脱敏显示 Token——日志/诊断输出里只允许出现这个形式。"""
    if not value:
        return "(未设置)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}（长度 {len(value)}）"


def resolved_settings(environ: dict[str, str] | None = None) -> dict[str, str]:
    """读取最终生效的地址与 Token（供诊断输出使用）。"""
    source = os.environ if environ is None else environ
    return {
        "url": source.get(ENV_URL) or DEFAULT_URL,
        "token": source.get(ENV_TOKEN) or "",
    }


__all__ = [
    "ENV_FILE_VAR", "ENV_TOKEN", "ENV_URL", "DEFAULT_URL", "SENSITIVE_KEYS",
    "candidate_env_files", "install_root", "load_env", "load_env_file",
    "mask_secret", "parse_env_text", "resolved_settings",
]
