from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from . import mineru, paddle
from .settings import (
    CONFIG_PATH,
    CvtSettings,
    ensure_config_file,
    load_settings,
    mask_secret,
    update_settings,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}

OUTPUT_FORMATS = {"md", "json", "zip", "docx", "pdf"}


def _find_dotenv(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        dotenv = directory / ".env"
        if dotenv.is_file():
            return dotenv
    return None


def _strip_inline_comment(value: str) -> str:
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
    for value in (cli_value, config_value, os.getenv(env_name)):
        if value and value.strip():
            return value
    return None


def _apply_settings(args: argparse.Namespace, settings: CvtSettings) -> None:
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


def _infer_output_format(output: Path | None, requested: str | None) -> str:
    if requested:
        return requested
    if output and output.suffix:
        suffix = output.suffix.lower().lstrip(".")
        if suffix in OUTPUT_FORMATS:
            return suffix
    return "md"


def _default_output_path(
    input_path: Path,
    *,
    output: Path | None,
    output_dir: Path | None,
    output_format: str,
) -> Path:
    if output is not None:
        return output
    base_dir = output_dir or Path.cwd()
    return base_dir / input_path.stem / f"{input_path.stem}.{output_format}"


def _require_input(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise ValueError(f"不是文件: {path}")


def _run_pandoc(input_path: Path, output_path: Path, output_format: str) -> list[Path]:
    if shutil.which("pandoc") is None:
        raise RuntimeError(
            "未找到 pandoc。请先安装 pandoc，或为 PDF/图片输入选择 Paddle/MinerU。"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["pandoc", str(input_path), "-o", str(output_path)]
    written = [output_path]

    if input_path.suffix.lower() in {".docx", ".doc"} and output_format == "md":
        media_dir = output_path.parent / f"{output_path.stem}_media"
        command.extend(["--to", "gfm", f"--extract-media={media_dir}"])
        written.append(media_dir)
    elif output_format == "json":
        command.extend(["--to", "json"])

    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"pandoc 转换失败: {stderr}")

    return [path for path in written if path.exists()]


def _convert_with_pymupdf4llm(
    input_path: Path,
    output_path: Path,
    output_format: str,
) -> list[Path]:
    if output_format not in {"md", "json"}:
        raise ValueError("pymupdf4llm 仅支持输出 md/json。")

    try:
        import pymupdf4llm  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "未安装 pymupdf4llm。请安装后重试，或配置 Paddle/MinerU token。"
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = pymupdf4llm.to_markdown(str(input_path))
    if output_format == "md":
        output_path.write_text(markdown, encoding="utf-8", newline="\n")
    else:
        output_path.write_text(
            json.dumps(
                {"source": str(input_path), "markdown": markdown},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
    return [output_path]


def _safe_extract_zip(zip_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    root = output_dir.resolve()

    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (output_dir / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(f"压缩包包含不安全路径: {member.filename}") from exc

            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"path": str(path), "text": path.read_text(encoding="utf-8")}


def _materialize_mineru_output(
    zip_path: Path,
    output_path: Path,
    output_format: str,
) -> list[Path]:
    if output_format == "zip":
        return [zip_path]

    extract_dir = output_path.parent / f"{output_path.stem}_mineru"
    _safe_extract_zip(zip_path, extract_dir)

    if output_format == "md":
        markdown_files = sorted(extract_dir.rglob("*.md"))
        if not markdown_files:
            raise RuntimeError(f"MinerU 结果中没有找到 Markdown 文件: {extract_dir}")

        parts: list[str] = []
        for markdown_file in markdown_files:
            if parts:
                parts.append("\n\n")
            parts.append(f"<!-- {markdown_file.relative_to(extract_dir)} -->\n\n")
            parts.append(markdown_file.read_text(encoding="utf-8").rstrip())

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
        return [output_path, extract_dir]

    if output_format == "json":
        json_files = sorted(extract_dir.rglob("*.json"))
        if not json_files:
            raise RuntimeError(f"MinerU 结果中没有找到 JSON 文件: {extract_dir}")

        payload = {
            "source_zip": str(zip_path),
            "extract_dir": str(extract_dir),
            "files": [
                {
                    "path": str(path.relative_to(extract_dir)),
                    "content": _read_json(path),
                }
                for path in json_files
            ],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return [output_path, extract_dir]

    raise ValueError(f"MinerU 输出格式仅支持 md/json/zip，不支持: {output_format}")


def _convert_with_mineru(
    args: argparse.Namespace,
    output_path: Path,
    output_format: str,
) -> list[Path]:
    zip_path = (
        output_path
        if output_format == "zip"
        else output_path.with_suffix(".mineru.zip")
    )
    downloaded_zip = mineru.convert_file(
        args.input,
        output_path=zip_path,
        token=args.mineru_token,
        data_id=args.data_id,
        enable_formula=args.enable_formula,
        enable_table=args.enable_table,
        language=args.language,
        model_version=args.model_version,
        is_ocr=args.is_ocr,
        interval=args.interval,
        timeout_seconds=args.timeout,
    )
    return _materialize_mineru_output(downloaded_zip, output_path, output_format)


def _convert_with_paddle(
    args: argparse.Namespace,
    output_path: Path,
    output_format: str,
) -> list[Path]:
    if output_format not in {"md", "json"}:
        raise ValueError("Paddle 输出格式仅支持 md/json。")
    return paddle.convert_document(
        args.input,
        output_path=output_path,
        output_format=output_format,
        token=args.paddle_token,
        api_url=args.paddle_api_url,
        timeout=args.paddle_timeout,
        split_pages=args.split_pages,
        download_assets=args.download_assets,
    )


def _candidate_engines(args: argparse.Namespace, output_format: str) -> list[str]:
    if args.engine != "auto":
        return [args.engine]

    suffix = args.input.suffix.lower()
    if suffix in {".docx", ".doc"} or suffix in MARKDOWN_EXTENSIONS:
        return ["pandoc"]

    if suffix == ".pdf" or suffix in IMAGE_EXTENSIONS:
        engines = ["paddle"]
        if args.fallback and (args.mineru_token or os.getenv("MINERU_TOKEN")):
            engines.append("mineru")
        if args.fallback and output_format in {"md", "json"}:
            engines.append("pymupdf4llm")
        return engines

    return ["pandoc"]


def convert(args: argparse.Namespace) -> list[Path]:
    _require_input(args.input)
    output_format = _infer_output_format(args.output, args.to)
    output_path = _default_output_path(
        args.input,
        output=args.output,
        output_dir=args.output_dir,
        output_format=output_format,
    )

    failures: list[str] = []
    for engine in _candidate_engines(args, output_format):
        try:
            if engine == "paddle":
                print("[cvt] 使用 Paddle OCR-VL")
                return _convert_with_paddle(args, output_path, output_format)
            if engine == "mineru":
                print("[cvt] 使用 MinerU")
                return _convert_with_mineru(args, output_path, output_format)
            if engine == "pandoc":
                print("[cvt] 使用 pandoc")
                return _run_pandoc(args.input, output_path, output_format)
            if engine == "pymupdf4llm":
                print("[cvt] 使用 pymupdf4llm")
                return _convert_with_pymupdf4llm(args.input, output_path, output_format)
            raise ValueError(f"未知引擎: {engine}")
        except Exception as exc:
            failures.append(f"{engine}: {exc}")
            if not args.fallback or args.engine != "auto":
                raise
            print(f"[cvt] {engine} 失败，尝试下一个引擎: {exc}", file=sys.stderr)

    raise RuntimeError("所有转换引擎均失败:\n" + "\n".join(failures))


def build_parser() -> argparse.ArgumentParser:
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
        "--split-pages", action="store_true", help="Paddle 输出 md 时同时保存分页 md"
    )
    paddle_group.add_argument(
        "--no-assets",
        dest="download_assets",
        action="store_false",
        help="Paddle 输出 md 时不下载图片资源",
    )
    paddle_group.set_defaults(download_assets=True)

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
    def add_config_file_argument(command_parser: argparse.ArgumentParser) -> None:
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
        written = convert(args)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    for path in written:
        print(f"[cvt] 输出: {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
