"""验证 MinerU 结果整理和压缩包安全检查。"""

import zipfile
from pathlib import Path

import pytest

from cvt import mineru


def test_materialize_output_merges_markdown_and_images(tmp_path: Path) -> None:
    """Markdown 结果应合并正文并汇总图片目录。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("document/one.md", "第一页")
        archive.writestr("document/two.md", "第二页")
        archive.writestr("document/images/chart.png", b"image")

    output_path = tmp_path / "result.md"
    written = mineru.materialize_output(zip_path, output_path, "md")

    content = output_path.read_text(encoding="utf-8")
    assert "第一页" in content
    assert "第二页" in content
    assert (tmp_path / "images/chart.png").read_bytes() == b"image"
    assert output_path in written
    assert tmp_path / "result_mineru" in written


def test_materialize_output_rejects_unsafe_zip_path(tmp_path: Path) -> None:
    """包含路径穿越成员的压缩包应被拒绝。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    zip_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../escaped.md", "危险内容")

    with pytest.raises(RuntimeError, match="不安全路径"):
        mineru.materialize_output(zip_path, tmp_path / "result.md", "md")

    assert not (tmp_path / "escaped.md").exists()
