"""封装 pandoc 与 pymupdf4llm 本地转换引擎。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def convert_with_pandoc(
    input_path: Path, output_path: Path, output_format: str
) -> list[Path]:
    """使用 pandoc 转换文档。

    Args:
        input_path: 本地输入文件路径。
        output_path: 目标输出文件路径。
        output_format: pandoc 输出格式。

    Returns:
        实际生成的文件或媒体目录列表。
    """
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
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"pandoc 转换失败: {message}")
    return [path for path in written if path.exists()]


def convert_with_pymupdf4llm(
    input_path: Path, output_path: Path, output_format: str
) -> list[Path]:
    """使用 pymupdf4llm 转换 PDF。

    Args:
        input_path: 本地 PDF 路径。
        output_path: 目标输出文件路径。
        output_format: 输出格式，仅支持 ``md`` 或 ``json``。

    Returns:
        实际生成的文件列表。
    """
    if output_format not in {"md", "json"}:
        raise ValueError("pymupdf4llm 仅支持输出 md/json。")
    try:
        import pymupdf4llm  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "当前环境缺少 pymupdf4llm。请重新安装 cvt，或配置 Paddle/MinerU token。"
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
