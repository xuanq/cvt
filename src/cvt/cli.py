"""解析统一命令行参数、加载配置并调用公共转换 API。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import api, mineru, paddle
from .settings import (
    CONFIG_PATH,
    CvtSettings,
    ensure_config_file,
    load_settings,
    mask_secret,
    update_settings,
)

OUTPUT_FORMATS = {"md", "json", "zip", "docx", "pdf"}


def _find_dotenv(start: Path) -> Path | None:
    """从起始路径向上查找 ``.env``；返回首个匹配路径或 ``None``。"""
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        dotenv = directory / ".env"
        if dotenv.is_file():
            return dotenv
    return None


def _strip_inline_comment(value: str) -> str:
    """移除未被引号包围的行内注释；输入原值，返回清理后的值。"""
    quote: str | None = None
    escaped = False

    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return value[:index].rstrip()

    return value.strip()


def _load_dotenv(path: Path) -> None:
    """把 dotenv 中尚未设置的变量加载到环境；输入文件路径，无返回值。"""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue

        value = _strip_inline_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _resolve_setting(
    cli_value: str | None,
    config_value: str | None,
    env_name: str,
) -> str | None:
    """按 CLI、配置、环境变量顺序解析设置值。"""
    for value in (cli_value, config_value, os.getenv(env_name)):
        if value and value.strip():
            return value
    return None


def _apply_settings(args: argparse.Namespace, settings: CvtSettings) -> None:
    """将配置和环境变量补充到 CLI 参数；输入参数与配置，无返回值。"""
    args.paddle_token = _resolve_setting(
        args.paddle_token,
        settings.paddle.token,
        "PADDLE_TOKEN",
    )
    args.paddle_api_url = _resolve_setting(
        args.paddle_api_url,
        settings.paddle.api_url,
        "PADDLE_API_URL",
    )
    args.mineru_token = _resolve_setting(
        args.mineru_token,
        settings.mineru.token,
        "MINERU_TOKEN",
    )


def build_parser() -> argparse.ArgumentParser:
    """构建主命令解析器；无输入，返回解析器。"""
    parser = argparse.ArgumentParser(
        prog="cvt",
        description="将 PDF、图片、DOCX、Markdown 转为适合大模型读取的 md/json，或通过 pandoc 导出 docx/pdf。",
    )
    parser.add_argument("input", type=Path, help="输入文件路径")
    parser.add_argument("-o", "--output", type=Path, help="输出文件路径")
    parser.add_argument(
        "-d", "--output-dir", type=Path, help="未指定 --output 时的输出目录"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="加载指定 .env 文件；默认从当前目录向上查找 .env",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=CONFIG_PATH,
        help=f"配置文件路径，默认 {CONFIG_PATH}",
    )
    parser.add_argument(
        "--to",
        choices=sorted(OUTPUT_FORMATS),
        help="输出格式；默认从 --output 后缀推断，否则为 md",
    )
    parser.add_argument(
        "--engine",
        choices=["auto", "paddle", "mineru", "pandoc", "pymupdf4llm"],
        default="auto",
        help="转换引擎，默认 auto",
    )
    parser.add_argument(
        "--no-fallback",
        dest="fallback",
        action="store_false",
        help="auto 模式下禁用后备引擎",
    )
    parser.set_defaults(fallback=True)

    paddle_group = parser.add_argument_group("Paddle")
    paddle_group.add_argument(
        "--paddle-token", help="Paddle API token；默认读取 PADDLE_TOKEN"
    )
    paddle_group.add_argument(
        "--paddle-api-url", help="Paddle API 地址；默认读取 PADDLE_API_URL 或内置地址"
    )
    paddle_group.add_argument(
        "--paddle-timeout", type=int, default=600, help="Paddle 请求超时秒数"
    )
    paddle_group.add_argument(
        "--no-merge-pages",
        dest="merge_pages",
        action="store_false",
        help="不合并 Paddle 原始分页 Markdown，只保存分页文件",
    )
    paddle_group.add_argument(
        "--keep-layout-images",
        action="store_true",
        help="保留 Paddle 返回的 layout_det_res_x.jpg 等版面检测图",
    )
    paddle_group.add_argument(
        "--parse-chart",
        action="store_true",
        help="开启 Paddle 图表解析，默认关闭",
    )
    paddle_group.add_argument(
        "--paddle-interval",
        type=int,
        default=paddle.DEFAULT_POLL_INTERVAL_SECONDS,
        help="Paddle 任务轮询间隔秒数",
    )
    paddle_group.add_argument(
        "--no-assets",
        dest="download_assets",
        action="store_false",
        help="Paddle 输出 md 时不下载图片资源",
    )
    paddle_group.set_defaults(download_assets=True, merge_pages=True)

    mineru_group = parser.add_argument_group("MinerU")
    mineru_group.add_argument(
        "--mineru-token", help="MinerU token；默认读取 MINERU_TOKEN"
    )
    mineru_group.add_argument(
        "--data-id", help="MinerU data_id；默认使用输入文件名主干"
    )
    mineru_group.add_argument(
        "--enable-formula", type=mineru.str2bool, default=True, help="是否开启公式识别"
    )
    mineru_group.add_argument(
        "--enable-table", type=mineru.str2bool, default=True, help="是否开启表格识别"
    )
    mineru_group.add_argument(
        "--language", default="ch", help="MinerU 文档语言，默认 ch"
    )
    mineru_group.add_argument(
        "--model-version",
        default="vlm",
        choices=["pipeline", "vlm", "MinerU-HTML"],
        help="MinerU 模型版本",
    )
    mineru_group.add_argument(
        "--is-ocr", type=mineru.str2bool, default=True, help="MinerU 是否开启 OCR"
    )
    mineru_group.add_argument(
        "--interval",
        type=int,
        default=mineru.DEFAULT_POLL_INTERVAL,
        help="MinerU 轮询间隔秒数",
    )
    mineru_group.add_argument(
        "--timeout",
        type=int,
        default=mineru.DEFAULT_TIMEOUT_SECONDS,
        help="MinerU 最长等待秒数",
    )

    return parser


def build_config_parser() -> argparse.ArgumentParser:
    """构建配置子命令解析器；无输入，返回解析器。"""

    def add_config_file_argument(command_parser: argparse.ArgumentParser) -> None:
        """为子命令补充隐藏的配置路径参数；无返回值。"""
        command_parser.add_argument(
            "--config-file",
            type=Path,
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )

    parser = argparse.ArgumentParser(
        prog="cvt config",
        description="管理 cvt 配置文件。",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=CONFIG_PATH,
        help=f"配置文件路径，默认 {CONFIG_PATH}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="创建默认配置文件")
    add_config_file_argument(init_parser)
    init_parser.add_argument("--force", action="store_true", help="覆盖已有配置文件")

    set_parser = subparsers.add_parser("set", help="写入 token 或 API 配置")
    add_config_file_argument(set_parser)
    set_parser.add_argument("--paddle-token", help="保存 Paddle token")
    set_parser.add_argument("--paddle-api-url", help="保存 Paddle API 地址")
    set_parser.add_argument("--mineru-token", help="保存 MinerU token")

    show_parser = subparsers.add_parser("show", help="显示当前配置")
    add_config_file_argument(show_parser)
    show_parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="显示完整 token，默认会脱敏",
    )

    path_parser = subparsers.add_parser("path", help="显示配置文件路径")
    add_config_file_argument(path_parser)
    return parser


def handle_config_command(argv: list[str]) -> int:
    """执行配置子命令；输入参数列表，返回进程退出码。"""
    parser = build_config_parser()
    args = parser.parse_args(argv)

    if args.command == "path":
        print(args.config_file.expanduser().resolve())
        return 0

    config_file: Path = args.config_file.expanduser()

    if args.command == "init":
        path = ensure_config_file(config_file, force=args.force)
        print(f"[cvt] 配置文件: {path.resolve()}")
        return 0

    if args.command == "set":
        if not any([args.paddle_token, args.paddle_api_url, args.mineru_token]):
            parser.error("config set 至少需要一个配置项")
        update_settings(
            path=config_file,
            paddle_token=args.paddle_token,
            paddle_api_url=args.paddle_api_url,
            mineru_token=args.mineru_token,
        )
        print(f"[cvt] 已更新配置: {config_file.resolve()}")
        return 0

    if args.command == "show":
        settings = load_settings(config_file)
        paddle_token = settings.paddle.token
        mineru_token = settings.mineru.token
        if not args.show_secrets:
            paddle_token = mask_secret(paddle_token)
            mineru_token = mask_secret(mineru_token)

        print(f"config_file = {config_file.resolve()}")
        print("[paddle]")
        print(f"token = {paddle_token!r}")
        print(f"api_url = {(settings.paddle.api_url or '')!r}")
        print("[mineru]")
        print(f"token = {mineru_token!r}")
        return 0

    parser.error(f"未知 config 命令: {args.command}")
    return 2


def main(argv: list[str] | None = None) -> int:
    """执行 cvt CLI；输入可选参数列表，返回进程退出码。"""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["config"]:
        try:
            return handle_config_command(argv[1:])
        except Exception as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1

    parser = build_parser()
    args = parser.parse_args(argv)
    dotenv = args.env_file or _find_dotenv(Path.cwd())
    if dotenv is not None:
        _load_dotenv(dotenv)

    try:
        settings = load_settings(args.config_file.expanduser())
        _apply_settings(args, settings)
        written = api.convert_document(
            args.input,
            output_path=args.output,
            output_dir=args.output_dir,
            output_format=args.to,
            engine=args.engine,
            fallback=args.fallback,
            paddle_token=args.paddle_token,
            paddle_api_url=args.paddle_api_url,
            paddle_timeout=args.paddle_timeout,
            paddle_interval=args.paddle_interval,
            merge_pages=args.merge_pages,
            keep_layout_images=args.keep_layout_images,
            parse_chart=args.parse_chart,
            download_assets=args.download_assets,
            mineru_token=args.mineru_token,
            data_id=args.data_id,
            enable_formula=args.enable_formula,
            enable_table=args.enable_table,
            language=args.language,
            model_version=args.model_version,
            is_ocr=args.is_ocr,
            mineru_interval=args.interval,
            mineru_timeout=args.timeout,
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    for path in written:
        print(f"[cvt] 输出: {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
