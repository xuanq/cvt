from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path.home() / ".cvt"
CONFIG_PATH = CONFIG_DIR / "config.toml"


class PaddleSettings(BaseModel):
    token: str | None = None
    api_url: str | None = None


class MineruSettings(BaseModel):
    token: str | None = None


class CvtSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    paddle: PaddleSettings = Field(default_factory=PaddleSettings)
    mineru: MineruSettings = Field(default_factory=MineruSettings)


def default_config_text() -> str:
    return """[paddle]
token = ""
api_url = ""

[mineru]
token = ""
"""


def ensure_config_file(path: Path = CONFIG_PATH, *, force: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if force or not path.exists():
        path.write_text(default_config_text(), encoding="utf-8", newline="\n")
    return path


def load_settings(path: Path = CONFIG_PATH, *, create: bool = True) -> CvtSettings:
    if create:
        ensure_config_file(path)
    if not path.exists():
        return CvtSettings()

    with path.open("rb") as file:
        data = tomllib.load(file)
    return CvtSettings.model_validate(data)


def _toml_string(value: str | None) -> str:
    if not value:
        return '""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_settings(settings: CvtSettings, path: Path = CONFIG_PATH) -> Path:
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
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
