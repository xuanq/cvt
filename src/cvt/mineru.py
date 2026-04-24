# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "requests>=2.31.0",
# ]
# ///

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

API_BASE = "https://mineru.net/api/v4"
DEFAULT_POLL_INTERVAL = 30
DEFAULT_TIMEOUT_SECONDS = 3600

DATA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def str2bool(value: str) -> bool:
    value = value.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"无法解析布尔值: {value!r}。请使用 true/false、1/0、yes/no。"
    )


def build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def validate_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise ValueError(f"不是文件: {path}")


def validate_data_id(data_id: str) -> None:
    if not DATA_ID_PATTERN.fullmatch(data_id):
        raise ValueError(
            "data_id 不合法。仅允许大小写字母、数字、下划线(_)、短横线(-)、英文句号(.)，且长度不超过 128。"
        )


def guess_output_name_from_url(url: str) -> str:
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


def fetch_batch_result(token: str, batch_id: str) -> dict:
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
            if not full_zip_url:
                raise RuntimeError(f"任务已完成，但未返回 full_zip_url: {item}")
            return full_zip_url

        if state in {"failed", "error"}:
            raise RuntimeError(f"解析失败: {err_msg or item}")

        progress = item.get("extract_progress") or {}
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
    token = token or os.getenv("MINERU_TOKEN")
    if not token:
        raise ValueError("未提供 MinerU token，请传入 --mineru-token 或设置 MINERU_TOKEN。")

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


def parse_args() -> argparse.Namespace:
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
        default="pipeline",
        choices=["pipeline", "vlm", "MinerU-HTML"],
        help="模型版本，默认 pipeline",
    )
    parser.add_argument(
        "--is-ocr",
        type=str2bool,
        default=False,
        help="是否开启 OCR，默认 false",
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
