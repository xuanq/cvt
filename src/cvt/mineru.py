# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "requests>=2.31.0",
# ]
# ///

"""封装 MinerU 文件上传、任务轮询和结果压缩包下载流程。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

API_BASE = "https://mineru.net/api/v4"
DEFAULT_POLL_INTERVAL = 30
DEFAULT_TIMEOUT_SECONDS = 3600

DATA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def str2bool(value: str) -> bool:
    """解析命令行布尔字符串；输入文本，返回对应布尔值。"""
    value = value.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"无法解析布尔值: {value!r}。请使用 true/false、1/0、yes/no。"
    )


def build_headers(token: str) -> dict[str, str]:
    """构造 MinerU 请求头；输入 token，返回鉴权请求头。"""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def validate_file(path: Path) -> None:
    """校验本地输入文件；输入路径，无返回值。"""
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise ValueError(f"不是文件: {path}")


def validate_data_id(data_id: str) -> None:
    """校验 MinerU 业务 ID；输入 ID，无返回值。"""
    if not DATA_ID_PATTERN.fullmatch(data_id):
        raise ValueError(
            "data_id 不合法。仅允许大小写字母、数字、下划线(_)、短横线(-)、英文句号(.)，且长度不超过 128。"
        )


def guess_output_name_from_url(url: str) -> str:
    """从下载地址推断文件名；输入 URL，返回安全的默认文件名。"""
    parsed = urlparse(url)
    name = Path(parsed.path).name
    return name or "mineru_result.zip"


def apply_upload_url(
    token: str,
    file_path: Path,
    data_id: str,
    *,
    enable_formula: bool,
    enable_table: bool,
    language: str,
    model_version: str,
    is_ocr: bool,
) -> tuple[str, str]:
    """
    申请上传 URL，返回 (batch_id, upload_url)
    """
    url = f"{API_BASE}/file-urls/batch"
    headers = build_headers(token)

    payload = {
        "files": [
            {
                "name": file_path.name,
                "data_id": data_id,
                "is_ocr": is_ocr,
            }
        ],
        "enable_formula": enable_formula,
        "enable_table": enable_table,
        "language": language,
        "model_version": model_version,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()

    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"申请上传地址失败: {result}")

    data = result.get("data") or {}
    batch_id = data.get("batch_id")
    file_urls = data.get("file_urls") or []

    if not batch_id:
        raise RuntimeError(f"接口未返回 batch_id: {result}")
    if not file_urls:
        raise RuntimeError(f"接口未返回 file_urls: {result}")

    return batch_id, file_urls[0]


def upload_file(upload_url: str, file_path: Path) -> None:
    """
    PUT 上传文件到预签名 URL
    """
    with file_path.open("rb") as f:
        resp = requests.put(upload_url, data=f, timeout=600)

    if resp.status_code != 200:
        raise RuntimeError(
            f"上传失败: status={resp.status_code}, body={resp.text[:500]}"
        )


def fetch_batch_result(token: str, batch_id: str) -> dict[str, Any]:
    """
    查询批量解析结果
    """
    url = f"{API_BASE}/extract-results/batch/{batch_id}"
    headers = build_headers(token)

    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()

    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"查询结果失败: {result}")

    data = result.get("data") or {}
    extract_result = data.get("extract_result") or []
    if not extract_result:
        raise RuntimeError(f"返回结果里没有 extract_result: {result}")

    return extract_result[0]


def poll_until_done(
    token: str,
    batch_id: str,
    *,
    interval: int,
    timeout_seconds: int,
) -> str:
    """
    轮询直到完成，返回 full_zip_url
    """
    started_at = time.time()

    while True:
        item = fetch_batch_result(token, batch_id)
        state = item.get("state")
        err_msg = item.get("err_msg") or ""

        print(f"[状态] {state}")

        if state == "done":
            full_zip_url = item.get("full_zip_url")
            if not isinstance(full_zip_url, str) or not full_zip_url:
                raise RuntimeError(f"任务已完成，但未返回 full_zip_url: {item}")
            return full_zip_url

        if state in {"failed", "error"}:
            raise RuntimeError(f"解析失败: {err_msg or item}")

        progress_value = item.get("extract_progress")
        progress = progress_value if isinstance(progress_value, dict) else {}
        extracted_pages = progress.get("extracted_pages")
        total_pages = progress.get("total_pages")
        start_time = progress.get("start_time")

        if extracted_pages is not None and total_pages is not None:
            print(
                f"[进度] {extracted_pages}/{total_pages}"
                + (f" | start_time={start_time}" if start_time else "")
            )

        elapsed = time.time() - started_at
        if elapsed > timeout_seconds:
            raise TimeoutError(f"轮询超时，已等待 {timeout_seconds} 秒")

        print(f"[等待] {interval} 秒后继续查询...\n")
        time.sleep(interval)


def download_file(url: str, output_path: Path) -> None:
    """流式下载文件。

    Args:
        url: 远端下载地址。
        output_path: 本地目标路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with output_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def convert_file(
    file_path: Path,
    *,
    output_path: Path | None = None,
    token: str | None = None,
    data_id: str | None = None,
    enable_formula: bool = True,
    enable_table: bool = True,
    language: str = "ch",
    model_version: str = "pipeline",
    is_ocr: bool = False,
    interval: int = DEFAULT_POLL_INTERVAL,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """提交 MinerU 转换并下载结果压缩包。

    Args:
        file_path: 本地输入文件。
        output_path: 可选输出 zip 路径。
        token: MinerU token，为空时读取环境变量。
        data_id: 可选业务数据 ID。
        enable_formula: 是否识别公式。
        enable_table: 是否识别表格。
        language: 文档语言。
        model_version: MinerU 模型版本。
        is_ocr: 是否启用 OCR。
        interval: 轮询间隔秒数。
        timeout_seconds: 总超时秒数。

    Returns:
        下载完成的 zip 路径。
    """
    token = token or os.getenv("MINERU_TOKEN")
    if not token:
        raise ValueError(
            "未提供 MinerU token，请传入 --mineru-token 或设置 MINERU_TOKEN。"
        )

    validate_file(file_path)
    data_id = data_id if data_id is not None else file_path.stem
    validate_data_id(data_id)

    batch_id, upload_url = apply_upload_url(
        token=token,
        file_path=file_path,
        data_id=data_id,
        enable_formula=enable_formula,
        enable_table=enable_table,
        language=language,
        model_version=model_version,
        is_ocr=is_ocr,
    )
    print(f"[MinerU] batch_id={batch_id}")

    upload_file(upload_url, file_path)
    print("[MinerU] 上传成功")

    full_zip_url = poll_until_done(
        token=token,
        batch_id=batch_id,
        interval=interval,
        timeout_seconds=timeout_seconds,
    )

    if output_path is None:
        output_path = Path.cwd() / guess_output_name_from_url(full_zip_url)
    download_file(full_zip_url, output_path)
    return output_path


def _safe_extract_zip(zip_path: Path, output_dir: Path) -> None:
    """安全解压 MinerU 压缩包。

    Args:
        zip_path: 待解压的 zip 路径。
        output_dir: 解压目标目录。
    """
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
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def _read_json(path: Path) -> object:
    """读取 JSON 文件；格式异常时返回包含原始文本的对象。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"path": str(path), "text": path.read_text(encoding="utf-8")}


def _copy_image_dirs(extract_dir: Path, output_dir: Path) -> Path | None:
    """汇总 MinerU 图片目录；返回目标目录或 ``None``。"""
    image_dirs = sorted(
        path
        for path in extract_dir.rglob("images")
        if path.is_dir()
        and not any(
            parent.name == "images" for parent in path.relative_to(extract_dir).parents
        )
    )
    if not image_dirs:
        return None
    target_dir = output_dir / "images"
    for image_dir in image_dirs:
        shutil.copytree(image_dir, target_dir, dirs_exist_ok=True)
    return target_dir


def materialize_output(
    zip_path: Path, output_path: Path, output_format: str
) -> list[Path]:
    """将 MinerU 压缩包整理为目标输出格式。

    Args:
        zip_path: MinerU 下载的结果压缩包。
        output_path: 最终输出文件路径。
        output_format: 输出格式，仅支持 ``md``、``json`` 或 ``zip``。

    Returns:
        实际生成的文件和目录列表。
    """
    if output_format == "zip":
        return [zip_path]
    extract_dir = output_path.parent / f"{output_path.stem}_mineru"
    _safe_extract_zip(zip_path, extract_dir)

    if output_format == "md":
        markdown_files = sorted(extract_dir.rglob("*.md"))
        if not markdown_files:
            raise RuntimeError(f"MinerU 结果中没有找到 Markdown 文件: {extract_dir}")
        parts = [
            f"<!-- {path.relative_to(extract_dir)} -->\n\n"
            f"{path.read_text(encoding='utf-8').rstrip()}"
            for path in markdown_files
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n\n".join(parts).rstrip() + "\n", encoding="utf-8")
        written = [output_path]
        image_dir = _copy_image_dirs(extract_dir, output_path.parent)
        if image_dir is not None:
            written.append(image_dir)
        return [*written, extract_dir]

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


def parse_args() -> argparse.Namespace:
    """解析 MinerU 独立脚本参数；无输入，返回参数命名空间。"""
    parser = argparse.ArgumentParser(
        description="上传本地文件到 MinerU，轮询解析状态，并在完成后自动下载结果 zip。"
    )

    parser.add_argument(
        "file",
        type=Path,
        help="需要解析的本地文件路径",
    )
    parser.add_argument(
        "--token",
        help="MinerU Token；若不传则尝试读取环境变量 MINERU_TOKEN",
    )
    parser.add_argument(
        "--data-id",
        help="业务数据 ID；默认取文件名去除后缀，例如 HT06.pdf -> HT06",
    )
    parser.add_argument(
        "--enable-formula",
        type=str2bool,
        default=True,
        help="是否开启公式识别，默认 true",
    )
    parser.add_argument(
        "--enable-table",
        type=str2bool,
        default=True,
        help="是否开启表格识别，默认 true",
    )
    parser.add_argument(
        "--language",
        default="ch",
        help="文档语言，默认 ch",
    )
    parser.add_argument(
        "--model-version",
        default="vlm",
        choices=["pipeline", "vlm", "MinerU-HTML"],
        help="模型版本，默认 vlm",
    )
    parser.add_argument(
        "--is-ocr",
        type=str2bool,
        default=True,
        help="是否开启 OCR，默认 True",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"轮询间隔秒数，默认 {DEFAULT_POLL_INTERVAL}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"最长等待秒数，默认 {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="下载保存路径。默认保存到当前目录，文件名取自返回链接中的 zip 名称",
    )

    return parser.parse_args()


def main() -> int:
    """执行 MinerU 独立 CLI；无输入，返回进程退出码。"""
    args = parse_args()

    token = args.token or os.getenv("MINERU_TOKEN")
    if not token:
        print(
            "错误: 未提供 token。请通过 --token 传入，或先设置环境变量 MINERU_TOKEN。",
            file=sys.stderr,
        )
        return 2

    file_path: Path = args.file
    validate_file(file_path)

    data_id = args.data_id if args.data_id is not None else file_path.stem
    validate_data_id(data_id)

    print(f"[文件] {file_path}")
    print(f"[data_id] {data_id}")
    print(
        "[参数] "
        f"enable_formula={args.enable_formula}, "
        f"enable_table={args.enable_table}, "
        f"language={args.language}, "
        f"model_version={args.model_version}, "
        f"is_ocr={args.is_ocr}"
    )

    batch_id, upload_url = apply_upload_url(
        token=token,
        file_path=file_path,
        data_id=data_id,
        enable_formula=args.enable_formula,
        enable_table=args.enable_table,
        language=args.language,
        model_version=args.model_version,
        is_ocr=args.is_ocr,
    )
    print(f"[申请成功] batch_id={batch_id}")

    upload_file(upload_url, file_path)
    print("[上传成功]")

    full_zip_url = poll_until_done(
        token=token,
        batch_id=batch_id,
        interval=args.interval,
        timeout_seconds=args.timeout,
    )
    print(f"[完成] full_zip_url={full_zip_url}")

    output_path = (
        args.output
        if args.output is not None
        else Path.cwd() / guess_output_name_from_url(full_zip_url)
    )
    download_file(full_zip_url, output_path)
    print(f"[下载完成] {output_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
