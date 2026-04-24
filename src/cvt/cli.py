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

from . import mineru
from . import paddle

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}

OUTPUT_FORMATS = {"md", "json", "zip", "docx", "pdf"}


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
    return base_dir / f"{input_path.stem}.{output_format}"


def _require_input(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise ValueError(f"不是文件: {path}")


def _run_pandoc(input_path: Path, output_path: Path, output_format: str) -> list[Path]:
    if shutil.which("pandoc") is None:
        raise RuntimeError("未找到 pandoc。请先安装 pandoc，或为 PDF/图片输入选择 Paddle/MinerU。")

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
    zip_path = output_path if output_format == "zip" else output_path.with_suffix(".mineru.zip")
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
    parser.add_argument("-d", "--output-dir", type=Path, help="未指定 --output 时的输出目录")
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
    paddle_group.add_argument("--paddle-token", help="Paddle API token；默认读取 PADDLE_TOKEN")
    paddle_group.add_argument("--paddle-api-url", help="Paddle API 地址；默认读取 PADDLE_API_URL 或内置地址")
    paddle_group.add_argument("--paddle-timeout", type=int, default=600, help="Paddle 请求超时秒数")
    paddle_group.add_argument("--split-pages", action="store_true", help="Paddle 输出 md 时同时保存分页 md")
    paddle_group.add_argument(
        "--no-assets",
        dest="download_assets",
        action="store_false",
        help="Paddle 输出 md 时不下载图片资源",
    )
    paddle_group.set_defaults(download_assets=True)

    mineru_group = parser.add_argument_group("MinerU")
    mineru_group.add_argument("--mineru-token", help="MinerU token；默认读取 MINERU_TOKEN")
    mineru_group.add_argument("--data-id", help="MinerU data_id；默认使用输入文件名主干")
    mineru_group.add_argument("--enable-formula", type=mineru.str2bool, default=True, help="是否开启公式识别")
    mineru_group.add_argument("--enable-table", type=mineru.str2bool, default=True, help="是否开启表格识别")
    mineru_group.add_argument("--language", default="ch", help="MinerU 文档语言，默认 ch")
    mineru_group.add_argument(
        "--model-version",
        default="pipeline",
        choices=["pipeline", "vlm", "MinerU-HTML"],
        help="MinerU 模型版本",
    )
    mineru_group.add_argument("--is-ocr", type=mineru.str2bool, default=False, help="MinerU 是否开启 OCR")
    mineru_group.add_argument("--interval", type=int, default=mineru.DEFAULT_POLL_INTERVAL, help="MinerU 轮询间隔秒数")
    mineru_group.add_argument("--timeout", type=int, default=mineru.DEFAULT_TIMEOUT_SECONDS, help="MinerU 最长等待秒数")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        written = convert(args)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    for path in written:
        print(f"[cvt] 输出: {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
