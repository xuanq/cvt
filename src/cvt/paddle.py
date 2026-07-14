"""调用 PaddleOCR-VL 异步任务服务并保存 Markdown、JSON 和图片资源。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"
DEFAULT_POLL_INTERVAL_SECONDS = 5

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
    """PaddleOCR-VL 的可选解析参数。

    Attributes:
        use_chart_recognition: 是否识别图表，默认关闭。
        markdown_ignore_labels: 生成 Markdown 时忽略的版面标签。
    """

    markdown_ignore_labels: list[str] = field(
        default_factory=lambda: DEFAULT_MARKDOWN_IGNORE_LABELS.copy()
    )
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    use_layout_detection: bool = True
    use_chart_recognition: bool = False
    use_seal_recognition: bool = True
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
        """转换为 Paddle API 所需字段。

        Returns:
            使用 API 驼峰命名的可选参数字典。
        """
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


def submit_job(
    file_path: str | Path,
    *,
    token: str,
    api_url: str = JOB_URL,
    options: PaddleOptions | None = None,
    timeout: int = 60,
) -> str:
    """提交 Paddle OCR 任务。

    Args:
        file_path: 本地文件路径或 HTTP(S) 文件地址。
        token: Paddle API token。
        api_url: Jobs API 地址。
        options: OCR 解析参数。
        timeout: 单次提交请求超时秒数。

    Returns:
        服务端任务 ID。
    """
    source = str(file_path)
    headers = {"Authorization": f"bearer {token}"}
    optional_payload = (options or PaddleOptions()).to_payload()

    if source.startswith(("http://", "https://")):
        response = requests.post(
            api_url,
            json={
                "fileUrl": source,
                "model": MODEL,
                "optionalPayload": optional_payload,
            },
            headers={**headers, "Content-Type": "application/json"},
            timeout=timeout,
        )
    else:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"文件不存在: {source_path}")
        data = {"model": MODEL, "optionalPayload": json.dumps(optional_payload)}
        with source_path.open("rb") as source_file:
            response = requests.post(
                api_url,
                headers=headers,
                data=data,
                files={"file": source_file},
                timeout=timeout,
            )

    response.raise_for_status()
    return str(response.json()["data"]["jobId"])


def wait_for_result(
    job_id: str,
    *,
    token: str,
    api_url: str = JOB_URL,
    interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: int = 600,
) -> str:
    """轮询任务直到完成。

    Args:
        job_id: 服务端任务 ID。
        token: Paddle API token。
        api_url: Jobs API 地址。
        interval: 两次查询之间的等待秒数。
        timeout_seconds: 总轮询超时秒数。

    Returns:
        JSONL 结果下载地址。
    """
    headers = {"Authorization": f"bearer {token}"}
    started_at = time.monotonic()
    while True:
        response = requests.get(f"{api_url}/{job_id}", headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()["data"]
        state = data["state"]
        if state == "done":
            return str(data["resultUrl"]["jsonUrl"])
        if state == "failed":
            raise RuntimeError(
                f"Paddle OCR 任务失败: {data.get('errorMsg', '未知原因')}"
            )
        if state not in {"pending", "running"}:
            raise RuntimeError(f"Paddle OCR 任务返回未知状态: {state}")
        if time.monotonic() - started_at > timeout_seconds:
            raise TimeoutError(f"Paddle OCR 轮询超时，已等待 {timeout_seconds} 秒")
        time.sleep(interval)


def download_result(jsonl_url: str, *, timeout: int = 600) -> list[dict[str, Any]]:
    """下载并解析 Paddle JSONL 结果。

    Args:
        jsonl_url: JSONL 结果下载地址。
        timeout: 下载请求超时秒数。

    Returns:
        JSONL 中每行 ``result`` 对象组成的列表。
    """
    response = requests.get(jsonl_url, timeout=timeout)
    response.raise_for_status()
    return [
        json.loads(line)["result"]
        for line in response.text.splitlines()
        if line.strip()
    ]


def _iter_pages(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展开所有任务结果中的分页数据。

    Args:
        results: Paddle JSONL 的结果对象列表。

    Returns:
        按原始顺序排列的版面解析页列表。
    """
    return [
        page
        for result in results
        for page in (result.get("layoutParsingResults") or [])
    ]


def merge_markdown(pages: list[dict[str, Any]]) -> str:
    """合并分页 Markdown，并添加页码注释。

    Args:
        pages: Paddle 返回的版面解析页列表。

    Returns:
        合并后的 Markdown 文本。
    """
    parts = [
        f"<!-- page {index} -->\n\n{((page.get('markdown') or {}).get('text') or '').rstrip()}"
        for index, page in enumerate(pages, start=1)
    ]
    return "\n\n".join(parts).rstrip() + "\n"


def _safe_output_path(base_dir: Path, relative_path: str) -> Path:
    """把远端相对路径限制在指定输出目录内。"""
    normalized = Path(relative_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        normalized = Path(normalized.name)
    candidate = base_dir / normalized
    try:
        candidate.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return base_dir / normalized.name
    return candidate


def _download_url(url: str, output_path: Path, *, timeout: int) -> None:
    """下载单个远端资源到本地路径。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def write_outputs(
    results: list[dict[str, Any]],
    *,
    output_path: Path,
    output_format: str = "md",
    merge_pages: bool = True,
    keep_layout_images: bool = False,
    download_assets: bool = True,
    timeout: int = 600,
) -> list[Path]:
    """将 Paddle 结果写入磁盘。

    Args:
        results: Paddle JSONL 的结果对象列表。
        output_path: 合并 Markdown 或 JSON 的输出路径。
        output_format: 输出格式，仅支持 ``md`` 或 ``json``。
        merge_pages: 是否把分页 Markdown 合并为一个文件。
        keep_layout_images: 是否保留 ``layout_det_res_x.jpg`` 等版面图。
        download_assets: 是否下载 Markdown 引用的图片。
        timeout: 单个图片下载超时秒数。

    Returns:
        实际写入的文件及资源目录列表。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        output_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return [output_path]
    if output_format != "md":
        raise ValueError(f"Paddle 输出格式仅支持 md/json，不支持: {output_format}")

    pages = _iter_pages(results)
    written: list[Path] = []
    if merge_pages:
        output_path.write_text(merge_markdown(pages), encoding="utf-8", newline="\n")
        written.append(output_path)
    else:
        page_dir = output_path.parent / f"{output_path.stem}_pages"
        page_dir.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(pages, start=1):
            page_path = page_dir / f"page_{index}.md"
            text = ((page.get("markdown") or {}).get("text") or "").rstrip() + "\n"
            page_path.write_text(text, encoding="utf-8", newline="\n")
        written.append(page_dir)

    if download_assets:
        image_dirs: set[Path] = set()
        for page in pages:
            for relative_path, image_url in (
                (page.get("markdown") or {}).get("images") or {}
            ).items():
                image_path = _safe_output_path(output_path.parent, str(relative_path))
                _download_url(str(image_url), image_path, timeout=timeout)
                image_dirs.add(image_path.parent)
        written.extend(sorted(image_dirs))

    if keep_layout_images:
        layout_dir = output_path.parent / "layout"
        for page_index, page in enumerate(pages):
            for image_name, image_url in (page.get("outputImages") or {}).items():
                image_path = layout_dir / f"{image_name}_{page_index}.jpg"
                _download_url(str(image_url), image_path, timeout=timeout)
        if layout_dir.exists():
            written.append(layout_dir)
    return written


def convert_document(
    file_path: str | Path,
    *,
    output_path: Path,
    output_format: str = "md",
    token: str | None = None,
    api_url: str | None = None,
    options: PaddleOptions | None = None,
    timeout: int = 600,
    interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    merge_pages: bool = True,
    keep_layout_images: bool = False,
    download_assets: bool = True,
) -> list[Path]:
    """调用 PaddleOCR-VL 并保存转换结果。

    Args:
        file_path: 本地文件路径或 HTTP(S) 文件地址。
        output_path: 输出 Markdown 或 JSON 路径。
        output_format: 输出格式，仅支持 ``md`` 或 ``json``。
        token: Paddle token；为空时读取 ``PADDLE_TOKEN``。
        api_url: Jobs API 地址；为空时读取 ``PADDLE_API_URL``。
        options: Paddle 解析参数。
        timeout: 提交、轮询总时长和下载的超时秒数。
        interval: 任务轮询间隔秒数。
        merge_pages: 是否合并原始分页 Markdown，默认合并。
        keep_layout_images: 是否保留版面检测图，默认不保留。
        download_assets: 是否下载 Markdown 引用的图片。

    Returns:
        实际写入的文件及资源目录列表。
    """
    resolved_token = token or os.getenv("PADDLE_TOKEN")
    if not resolved_token:
        raise ValueError("未提供 Paddle token，请传入 token 或设置 PADDLE_TOKEN。")
    resolved_url = api_url or os.getenv("PADDLE_API_URL") or JOB_URL
    job_id = submit_job(
        file_path,
        token=resolved_token,
        api_url=resolved_url,
        options=options,
        timeout=min(timeout, 60),
    )
    jsonl_url = wait_for_result(
        job_id,
        token=resolved_token,
        api_url=resolved_url,
        interval=interval,
        timeout_seconds=timeout,
    )
    results = download_result(jsonl_url, timeout=timeout)
    return write_outputs(
        results,
        output_path=output_path,
        output_format=output_format,
        merge_pages=merge_pages,
        keep_layout_images=keep_layout_images,
        download_assets=download_assets,
        timeout=timeout,
    )
