from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

API_URL = "https://a3zff7f2kew9g8c3.aistudio-app.com/layout-parsing"

PDF_FILE_TYPE = 0
IMAGE_FILE_TYPE = 1

DEFAULT_MARKDOWN_IGNORE_LABELS = [
    "header",
    "header_image",
    "footer",
    "footer_image",
    "number",
    "footnote",
    "aside_text",
]


@dataclass(slots=True)
class PaddleOptions:
    markdown_ignore_labels: list[str] = field(
        default_factory=lambda: DEFAULT_MARKDOWN_IGNORE_LABELS.copy()
    )
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    use_layout_detection: bool = True
    use_chart_recognition: bool = True
    use_seal_recognition: bool = False
    use_ocr_for_image_block: bool = False
    merge_tables: bool = True
    relevel_titles: bool = True
    layout_shape_mode: str = "auto"
    prompt_label: str = "ocr"
    repetition_penalty: int = 1
    temperature: int = 0
    top_p: int = 1
    min_pixels: int = 147384
    max_pixels: int = 2822400
    layout_nms: bool = True
    restructure_pages: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "markdownIgnoreLabels": self.markdown_ignore_labels,
            "useDocOrientationClassify": self.use_doc_orientation_classify,
            "useDocUnwarping": self.use_doc_unwarping,
            "useLayoutDetection": self.use_layout_detection,
            "useChartRecognition": self.use_chart_recognition,
            "useSealRecognition": self.use_seal_recognition,
            "useOcrForImageBlock": self.use_ocr_for_image_block,
            "mergeTables": self.merge_tables,
            "relevelTitles": self.relevel_titles,
            "layoutShapeMode": self.layout_shape_mode,
            "promptLabel": self.prompt_label,
            "repetitionPenalty": self.repetition_penalty,
            "temperature": self.temperature,
            "topP": self.top_p,
            "minPixels": self.min_pixels,
            "maxPixels": self.max_pixels,
            "layoutNms": self.layout_nms,
            "restructurePages": self.restructure_pages,
        }


def infer_file_type(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return PDF_FILE_TYPE
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        return IMAGE_FILE_TYPE
    raise ValueError(f"Paddle layout-parsing 暂不支持该文件类型: {path.suffix}")


def parse_document(
    file_path: Path,
    *,
    token: str | None = None,
    api_url: str | None = None,
    file_type: int | None = None,
    options: PaddleOptions | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    token = token or os.getenv("PADDLE_TOKEN")
    if not token:
        raise ValueError("未提供 Paddle token，请传入 --paddle-token 或设置 PADDLE_TOKEN。")

    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"不是文件: {file_path}")

    with file_path.open("rb") as file:
        file_data = base64.b64encode(file.read()).decode("ascii")

    payload = {
        "file": file_data,
        "fileType": infer_file_type(file_path) if file_type is None else file_type,
        **(options or PaddleOptions()).to_payload(),
    }
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        api_url or os.getenv("PADDLE_API_URL") or API_URL,
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if "result" not in body:
        raise RuntimeError(f"Paddle API 未返回 result: {body}")
    return body["result"]


def merge_markdown(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for index, page in enumerate(result.get("layoutParsingResults") or [], start=1):
        markdown = (page.get("markdown") or {}).get("text") or ""
        if parts:
            parts.append("\n\n")
        parts.append(f"<!-- page {index} -->\n\n")
        parts.append(markdown.rstrip())
    return "\n".join(parts).rstrip() + "\n"


def _safe_output_path(base_dir: Path, relative_path: str) -> Path:
    normalized = Path(relative_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        normalized = Path(normalized.name)

    output_path = base_dir / normalized
    try:
        output_path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        output_path = base_dir / normalized.name
    return output_path


def _download_url(url: str, output_path: Path, *, timeout: int = 600) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def write_outputs(
    result: dict[str, Any],
    *,
    output_path: Path,
    output_format: str,
    assets_dir: Path | None = None,
    split_pages: bool = False,
    download_assets: bool = True,
) -> list[Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if output_format == "json":
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return [output_path]

    if output_format != "md":
        raise ValueError(f"Paddle 输出格式仅支持 md/json，不支持: {output_format}")

    output_path.write_text(merge_markdown(result), encoding="utf-8", newline="\n")
    written.append(output_path)

    pages = result.get("layoutParsingResults") or []
    if split_pages:
        page_dir = output_path.parent / f"{output_path.stem}_pages"
        page_dir.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(pages, start=1):
            text = ((page.get("markdown") or {}).get("text") or "").rstrip() + "\n"
            page_path = page_dir / f"page_{index}.md"
            page_path.write_text(text, encoding="utf-8", newline="\n")
            written.append(page_path)

    if download_assets:
        asset_root = assets_dir or output_path.parent
        image_dirs: set[Path] = set()
        layout_dir = asset_root / "layout"

        for page_index, page in enumerate(pages, start=1):
            markdown = page.get("markdown") or {}
            for img_path, img_url in (markdown.get("images") or {}).items():
                saved_path = _safe_output_path(asset_root, str(img_path))
                _download_url(str(img_url), saved_path)
                image_dirs.add(saved_path.parent)
            for img_name, img_url in (page.get("outputImages") or {}).items():
                name = f"{img_name}_{page_index}.jpg"
                _download_url(str(img_url), layout_dir / name)

        for directory in sorted(image_dirs):
            if directory.exists():
                written.append(directory)
        if layout_dir.exists():
            written.append(layout_dir)

    return written


def convert_document(
    file_path: Path,
    *,
    output_path: Path,
    output_format: str = "md",
    token: str | None = None,
    api_url: str | None = None,
    options: PaddleOptions | None = None,
    timeout: int = 600,
    split_pages: bool = False,
    download_assets: bool = True,
) -> list[Path]:
    result = parse_document(
        file_path,
        token=token,
        api_url=api_url,
        options=options,
        timeout=timeout,
    )
    return write_outputs(
        result,
        output_path=output_path,
        output_format=output_format,
        split_pages=split_pages,
        download_assets=download_assets,
    )
