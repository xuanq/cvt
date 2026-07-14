"""管理 cvt 的用户级 TOML 配置及敏感信息展示。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path.home() / ".cvt"
CONFIG_PATH = CONFIG_DIR / "config.toml"


class PaddleSettings(BaseModel):
    """保存 Paddle token 和可选 API 地址。"""

    token: str | None = None
    api_url: str | None = None


class MineruSettings(BaseModel):
    """保存 MinerU token。"""

    token: str | None = None


class CvtSettings(BaseSettings):
    """聚合 cvt 支持的各转换引擎配置。"""

    model_config = SettingsConfigDict(extra="ignore")

    paddle: PaddleSettings = Field(default_factory=PaddleSettings)
    mineru: MineruSettings = Field(default_factory=MineruSettings)


def default_config_text() -> str:
    """生成默认配置文本。

    Returns:
        不包含真实 token 的 TOML 文本。
    """
    return """[paddle]
token = ""
api_url = ""

[mineru]
token = ""
"""


def ensure_config_file(path: Path = CONFIG_PATH, *, force: bool = False) -> Path:
    """确保配置文件存在。

    Args:
        path: 配置文件路径。
        force: 是否覆盖现有文件。

    Returns:
        已确认存在的配置文件路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if force or not path.exists():
        path.write_text(default_config_text(), encoding="utf-8", newline="\n")
    return path


def load_settings(path: Path = CONFIG_PATH, *, create: bool = True) -> CvtSettings:
    """读取并校验配置。

    Args:
        path: 配置文件路径。
        create: 文件不存在时是否创建默认配置。

    Returns:
        校验后的 cvt 配置对象。
    """
    if create:
        ensure_config_file(path)
    if not path.exists():
        return CvtSettings()

    with path.open("rb") as file:
        data = tomllib.load(file)
    return CvtSettings.model_validate(data)


def _toml_string(value: str | None) -> str:
    """将可选字符串编码为 TOML 字符串字面量。"""
    if not value:
        return '""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_settings(settings: CvtSettings, path: Path = CONFIG_PATH) -> Path:
    """保存配置。

    Args:
        settings: 待保存的配置对象。
        path: 目标配置路径。

    Returns:
        已写入的配置路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        [
            "[paddle]",
            f"token = {_toml_string(settings.paddle.token)}",
            f"api_url = {_toml_string(settings.paddle.api_url)}",
            "",
            "[mineru]",
            f"token = {_toml_string(settings.mineru.token)}",
            "",
        ]
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def update_settings(
    *,
    path: Path = CONFIG_PATH,
    paddle_token: str | None = None,
    paddle_api_url: str | None = None,
    mineru_token: str | None = None,
) -> CvtSettings:
    """更新明确传入的配置项并保存。

    Args:
        path: 配置文件路径。
        paddle_token: 新 Paddle token，``None`` 表示不修改。
        paddle_api_url: 新 Paddle API 地址，``None`` 表示不修改。
        mineru_token: 新 MinerU token，``None`` 表示不修改。

    Returns:
        更新后的配置对象。
    """
    settings = load_settings(path)
    if paddle_token is not None:
        settings.paddle.token = paddle_token
    if paddle_api_url is not None:
        settings.paddle.api_url = paddle_api_url
    if mineru_token is not None:
        settings.mineru.token = mineru_token
    save_settings(settings, path)
    return settings


def mask_secret(value: str | None) -> str:
    """对 token 进行脱敏展示；输入可选 token，返回脱敏字符串。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
