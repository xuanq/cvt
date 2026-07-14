"""验证 Paddle 参数映射及结果文件落盘行为。"""

from pathlib import Path

from pytest import MonkeyPatch

from cvt import paddle


def _sample_results() -> list[dict[str, object]]:
    """构造两页 Paddle 返回结果。

    Returns:
        包含 Markdown 图片和版面图的最小结果数据。
    """
    return [
        {
            "layoutParsingResults": [
                {
                    "markdown": {
                        "text": "第一页",
                        "images": {"images/chart.png": "https://example/chart"},
                    },
                    "outputImages": {"layout_det_res": "https://example/layout-1"},
                },
                {
                    "markdown": {"text": "第二页", "images": {}},
                    "outputImages": {"layout_det_res": "https://example/layout-2"},
                },
            ]
        }
    ]


def test_paddle_options_disable_chart_by_default() -> None:
    """默认 payload 应关闭图表识别；无输入且无返回值。"""
    assert paddle.PaddleOptions().to_payload()["useChartRecognition"] is False
    assert (
        paddle.PaddleOptions(use_chart_recognition=True).to_payload()[
            "useChartRecognition"
        ]
        is True
    )


def test_write_outputs_merges_pages_and_skips_layout_by_default(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """默认合并分页并不下载版面图。

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest 提供的替换工具。
    """
    downloaded: list[Path] = []

    def fake_download(url: str, output_path: Path, *, timeout: int) -> None:
        """记录下载目标并写入占位内容。"""
        del url, timeout
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"image")
        downloaded.append(output_path)

    monkeypatch.setattr(paddle, "_download_url", fake_download)
    output_path = tmp_path / "document.md"
    written = paddle.write_outputs(_sample_results(), output_path=output_path)

    assert "第一页" in output_path.read_text(encoding="utf-8")
    assert "第二页" in output_path.read_text(encoding="utf-8")
    assert tmp_path / "images" in written
    assert downloaded == [tmp_path / "images/chart.png"]
    assert not (tmp_path / "layout").exists()


def test_write_outputs_can_keep_pages_and_layout_images(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """关闭合并并保留版面图时应生成分页目录和 layout 目录。

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest 提供的替换工具。
    """

    def fake_download(url: str, output_path: Path, *, timeout: int) -> None:
        """写入图片占位内容。"""
        del url, timeout
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"image")

    monkeypatch.setattr(paddle, "_download_url", fake_download)
    output_path = tmp_path / "document.md"
    written = paddle.write_outputs(
        _sample_results(),
        output_path=output_path,
        merge_pages=False,
        keep_layout_images=True,
    )

    assert not output_path.exists()
    assert (tmp_path / "document_pages/page_1.md").read_text() == "第一页\n"
    assert (tmp_path / "document_pages/page_2.md").read_text() == "第二页\n"
    assert (tmp_path / "layout/layout_det_res_0.jpg").exists()
    assert (tmp_path / "layout/layout_det_res_1.jpg").exists()
    assert tmp_path / "document_pages" in written
    assert tmp_path / "layout" in written


def test_cli_exposes_new_paddle_options() -> None:
    """CLI 应提供三个新开关且默认值符合约定；无输入且无返回值。"""
    from cvt.cli import build_parser

    defaults = build_parser().parse_args(["input.pdf"])
    enabled = build_parser().parse_args(
        ["input.pdf", "--no-merge-pages", "--keep-layout-images", "--parse-chart"]
    )

    assert defaults.merge_pages is True
    assert defaults.keep_layout_images is False
    assert defaults.parse_chart is False
    assert enabled.merge_pages is False
    assert enabled.keep_layout_images is True
    assert enabled.parse_chart is True
