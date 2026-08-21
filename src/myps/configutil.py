from __future__ import annotations

import os
import re
import tomllib
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


def _default_config_path() -> Path:
    if os.name == "nt" and (local_app_data := os.environ.get("LOCALAPPDATA")):
        return Path(local_app_data) / "myps" / "config.toml"
    return Path("~/.config/myps/config.toml").expanduser()


DEFAULT_CONFIG_PATH = _default_config_path()
_SAMPLE_CONFIG_TEMPLATE = Path(__file__).with_name("config.sample.toml")
_FALLBACK_SAMPLE_CONTENT = """# Example myps configuration\n# Define regex patterns to skip/keep processes.\n[regexSkipPatterns]\nsystem = '^/System/'\nusr_sbin = '^/usr/sbin/'\nusr_libexec = '^/usr/libexec/'\napplications = '^/Applications/'\nlibrary = '^/Library/'\nhttpd = '/httpd$'\nphp_fpm = '/php-fpm$'\n\n[regexKeepPatterns]\niterm = '/Applications/iTerm2?.app/'\nterminal = '/Applications/Utilities/Terminal.app/'\nxcode = '[Xx][Cc]ode'\n"""
_CONFIG_SKIP_KEY = "regexSkipPatterns"
_CONFIG_KEEP_KEY = "regexKeepPatterns"


@dataclass
class MypsConfig:
    skip_patterns: list[str]
    keep_patterns: list[str]

    @property
    def skip_re(self) -> re.Pattern[str] | None:
        return re.compile("|".join(self.skip_patterns)) if self.skip_patterns else None

    @property
    def keep_re(self) -> re.Pattern[str] | None:
        return re.compile("|".join(self.keep_patterns)) if self.keep_patterns else None


def resolve_config_path(path: str | Path | None) -> Path:
    if path is None:
        return DEFAULT_CONFIG_PATH
    if isinstance(path, str):
        return Path(path).expanduser()
    return Path(path).expanduser()


def write_sample_config(target: str | Path, *, overwrite: bool = False) -> Path:
    resolved = resolve_config_path(target)
    if resolved.exists() and not overwrite:
        raise FileExistsError(f"Config file already exists at {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if _SAMPLE_CONFIG_TEMPLATE.exists():
        content = _SAMPLE_CONFIG_TEMPLATE.read_text(encoding="utf-8")
    else:
        content = _FALLBACK_SAMPLE_CONTENT
    resolved.write_text(content, encoding="utf-8")
    return resolved


def _normalize_patterns(data: object, *, key: str, source: Path) -> list[str]:
    if data is None:
        return []
    values: Sequence[object]
    if isinstance(data, Mapping):
        values = list(data.values())
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        values = typing.cast(Sequence[object], data)
    else:
        raise ValueError(
            f"Config key '{key}' in {source} must be a list of strings or table"
        )

    patterns: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError(
                f"Config key '{key}' in {source} must contain strings (got {type(item).__name__})"
            )
        patterns.append(item)
    return patterns


def load_config(path: str | Path | None) -> MypsConfig:
    resolved = resolve_config_path(path)
    if not resolved.exists():
        return MypsConfig(skip_patterns=[], keep_patterns=[])
    try:
        with resolved.open("rb") as f:
            config_data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Failed to read config file {resolved}: {exc}") from exc

    skip_patterns = _normalize_patterns(
        config_data.get(_CONFIG_SKIP_KEY), key=_CONFIG_SKIP_KEY, source=resolved
    )
    keep_patterns = _normalize_patterns(
        config_data.get(_CONFIG_KEEP_KEY), key=_CONFIG_KEEP_KEY, source=resolved
    )

    return MypsConfig(skip_patterns=skip_patterns, keep_patterns=keep_patterns)
