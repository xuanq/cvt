"""提供可供其他 Python 项目直接调用的统一文档转换 API。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import local, mineru, paddle

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
OUTPUT_FORMATS = {"md", "json", "zip", "docx", "pdf"}


def _infer_output_format(output_path: Path | None, requested: str | None) -> str:
    """推断输出格式；输入输出路径和显式格式，返回格式名称。"""
    if requested:
        return requested
    if output_path and output_path.suffix:
        suffix = output_path.suffix.lower().lstrip(".")
        if suffix in OUTPUT_FORMATS:
            return suffix
    return "md"


def _default_output_path(
    input_path: Path,
    output_path: Path | None,
    output_dir: Path | None,
    output_format: str,
) -> Path:
    """计算默认输出路径；返回明确的目标文件路径。"""
    if output_path is not None:
        return output_path
    base_dir = output_dir or Path.cwd()
    return base_dir / input_path.stem / f"{input_path.stem}.{output_format}"


def _candidate_engines(
    input_path: Path,
    *,
    engine: str,
    fallback: bool,
    output_format: str,
    mineru_token: str | None,
) -> list[str]:
    """排列候选转换引擎；返回按尝试顺序排列的名称列表。"""
    if engine != "auto":
        return [engine]
    suffix = input_path.suffix.lower()
    if suffix in {".docx", ".doc"} or suffix in MARKDOWN_EXTENSIONS:
        return ["pandoc"]
    if suffix == ".pdf" or suffix in IMAGE_EXTENSIONS:
        engines = ["paddle"]
        if fallback and (mineru_token or os.getenv("MINERU_TOKEN")):
            engines.append("mineru")
        if fallback and output_format in {"md", "json"}:
            engines.append("pymupdf4llm")
        return engines
    return ["pandoc"]


def convert_document(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    output_format: str | None = None,
    engine: str = "auto",
    fallback: bool = True,
    paddle_token: str | None = None,
    paddle_api_url: str | None = None,
    paddle_timeout: int = 600,
    paddle_interval: int = paddle.DEFAULT_POLL_INTERVAL_SECONDS,
    merge_pages: bool = True,
    keep_layout_images: bool = False,
    parse_chart: bool = False,
    paddle_options: paddle.PaddleOptions | None = None,
    download_assets: bool = True,
    mineru_token: str | None = None,
    data_id: str | None = None,
    enable_formula: bool = True,
    enable_table: bool = True,
    language: str = "ch",
    model_version: str = "vlm",
    is_ocr: bool = True,
    mineru_interval: int = mineru.DEFAULT_POLL_INTERVAL,
    mineru_timeout: int = mineru.DEFAULT_TIMEOUT_SECONDS,
) -> list[Path]:
    """使用指定引擎转换一个文档。

    Args:
        input_path: 输入文件路径。
        output_path: 明确的输出文件路径；为空时按输入文件名生成。
        output_dir: 自动生成输出路径时使用的根目录。
        output_format: 输出格式；为空时从输出后缀推断，默认 ``md``。
        engine: ``auto``、``paddle``、``mineru``、``pandoc`` 或
            ``pymupdf4llm``。
        fallback: ``auto`` 模式下首选引擎失败后是否尝试后备引擎。
        paddle_token: Paddle API token。
        paddle_api_url: Paddle Jobs API 地址。
        paddle_timeout: Paddle 任务总超时秒数。
        paddle_interval: Paddle 轮询间隔秒数。
        merge_pages: 是否合并 Paddle 分页 Markdown。
        keep_layout_images: 是否保留 Paddle 版面检测图。
        parse_chart: 是否启用 Paddle 图表解析。
        paddle_options: 完整 Paddle 参数；传入时优先于 ``parse_chart``。
        download_assets: 是否下载 Paddle Markdown 引用的图片。
        mineru_token: MinerU API token。
        data_id: MinerU 业务数据 ID。
        enable_formula: MinerU 是否识别公式。
        enable_table: MinerU 是否识别表格。
        language: MinerU 文档语言。
        model_version: MinerU 模型版本。
        is_ocr: MinerU 是否启用 OCR。
        mineru_interval: MinerU 轮询间隔秒数。
        mineru_timeout: MinerU 任务总超时秒数。

    Returns:
        实际生成的文件或资源目录路径列表。
    """
    # ************************************************************
    # API 负责路径推断、引擎选择和回退；各引擎模块负责具体转换和结果整理。
    # ************************************************************
    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"文件不存在: {source}")
    if not source.is_file():
        raise ValueError(f"不是文件: {source}")
    requested_output = Path(output_path) if output_path is not None else None
    requested_dir = Path(output_dir) if output_dir is not None else None
    resolved_format = _infer_output_format(requested_output, output_format)
    resolved_output = _default_output_path(
        source, requested_output, requested_dir, resolved_format
    )

    failures: list[str] = []
    candidates = _candidate_engines(
        source,
        engine=engine,
        fallback=fallback,
        output_format=resolved_format,
        mineru_token=mineru_token,
    )
    for candidate in candidates:
        try:
            if candidate == "paddle":
                print("[cvt] 使用 Paddle OCR-VL")
                return paddle.convert_document(
                    source,
                    output_path=resolved_output,
                    output_format=resolved_format,
                    token=paddle_token,
                    api_url=paddle_api_url,
                    options=paddle_options
                    or paddle.PaddleOptions(use_chart_recognition=parse_chart),
                    timeout=paddle_timeout,
                    interval=paddle_interval,
                    merge_pages=merge_pages,
                    keep_layout_images=keep_layout_images,
                    download_assets=download_assets,
                )
            if candidate == "mineru":
                print("[cvt] 使用 MinerU")
                zip_path = (
                    resolved_output
                    if resolved_format == "zip"
                    else resolved_output.with_suffix(".mineru.zip")
                )
                downloaded_zip = mineru.convert_file(
                    source,
                    output_path=zip_path,
                    token=mineru_token,
                    data_id=data_id,
                    enable_formula=enable_formula,
                    enable_table=enable_table,
                    language=language,
                    model_version=model_version,
                    is_ocr=is_ocr,
                    interval=mineru_interval,
                    timeout_seconds=mineru_timeout,
                )
                return mineru.materialize_output(
                    downloaded_zip, resolved_output, resolved_format
                )
            if candidate == "pandoc":
                print("[cvt] 使用 pandoc")
                return local.convert_with_pandoc(
                    source, resolved_output, resolved_format
                )
            if candidate == "pymupdf4llm":
                print("[cvt] 使用 pymupdf4llm")
                return local.convert_with_pymupdf4llm(
                    source, resolved_output, resolved_format
                )
            raise ValueError(f"未知引擎: {candidate}")
        except Exception as exc:
            failures.append(f"{candidate}: {exc}")
            if not fallback or engine != "auto":
                raise
            print(f"[cvt] {candidate} 失败，尝试下一个引擎: {exc}", file=sys.stderr)

    raise RuntimeError("所有转换引擎均失败:\n" + "\n".join(failures))
