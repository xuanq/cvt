# cvt

`cvt` 是一个文档转换 CLI，对 Paddle OCR-VL、MinerU、pandoc、pymupdf4llm 等成熟工具做统一封装，目标是把 PDF、图片、DOCX 等文档转换为适合大模型读取的 Markdown 或 JSON。

## 安装

```bash
uv tool install .
```

## 配置

安装后首次运行 CLI 会创建用户配置文件 `~/.cvt/config.toml`。推荐通过 CLI 写入 token：

```bash
cvt config set --paddle-token "your-paddle-token"
cvt config set --mineru-token "your-mineru-token"
cvt config show
```

配置文件格式：

```toml
[paddle]
token = "your-paddle-token"
api_url = ""

[mineru]
token = "your-mineru-token"
```

Token 优先级为：命令行参数 > `~/.cvt/config.toml` > 环境变量。命令行参数和环境变量也支持：

```bash
export PADDLE_TOKEN="your-paddle-token"
export MINERU_TOKEN="your-mineru-token"
cvt input.pdf --paddle-token "$PADDLE_TOKEN"
cvt input.pdf --engine mineru --mineru-token "$MINERU_TOKEN"
```

开发时仍可使用 `.env`，CLI 会默认从当前目录向上查找 `.env`，也可以用 `--env-file` 指定路径。

## 使用

默认输出 Markdown，PDF/图片优先使用 Paddle OCR-VL：

```bash
cvt paper.pdf
```

不指定 `--output` 时，结果会写入同名目录，例如 `paper/paper.md`、`paper/imgs/`、`paper/layout/`。

输出 Paddle 原始 JSON：

```bash
cvt paper.pdf --to json -o paper.json
```

使用 MinerU，并把返回 zip 中的 Markdown 汇总为一个文件：

```bash
cvt paper.pdf --engine mineru -o paper.md
```

只下载 MinerU 结果 zip：

```bash
cvt paper.pdf --engine mineru --to zip -o paper.zip
```

DOCX 通过 pandoc 转 Markdown：

```bash
cvt report.docx -o report.md
```

Markdown 通过 pandoc 转 DOCX 或 PDF：

```bash
cvt report.md -o report.docx
cvt report.md -o report.pdf
```

## 引擎选择

- `auto`: 默认模式。PDF/图片优先 Paddle，失败后可回退 MinerU 或 pymupdf4llm；DOCX/Markdown 使用 pandoc。
- `paddle`: 调用 Paddle layout-parsing API，支持 `md/json`。
- `mineru`: 调用 MinerU API，支持下载 `zip`，并可从 zip 中汇总 `md/json`。
- `pandoc`: 调用本机 pandoc，适合 DOCX/Markdown 互转。
- `pymupdf4llm`: 默认随 `cvt` 安装的本地 PDF 转 `md/json` 后备方案。

禁用自动回退：

```bash
cvt paper.pdf --no-fallback -o paper.md
```
