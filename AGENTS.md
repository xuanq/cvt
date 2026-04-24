# Repository Guidelines

## Project Structure & Module Organization

- `src/cvt/cli.py`: unified command-line interface and engine selection.
- `src/cvt/paddle.py`: Paddle OCR-VL API wrapper and output materialization.
- `src/cvt/mineru.py`: MinerU API workflow and legacy single-script CLI.
- `src/cvt/__init__.py`: package entry point used by `cvt = "cvt:main"`.
- `README.md`: user-facing installation and usage examples.
- `pyproject.toml` and `uv.lock`: package metadata and locked dependencies.

There is no `tests/` directory yet. Add tests under `tests/` for behavior that can be verified without live third-party APIs.

## Build, Test, and Development Commands

- `uv run cvt --help`: run the local CLI entry point.
- `uv run python -m compileall src`: syntax-check all source modules.
- `uv build`: build distributable package artifacts.
- `uv tool install .`: install the CLI locally as a uv tool.

For service-backed conversion, set tokens first:

```bash
export PADDLE_TOKEN="..."
export MINERU_TOKEN="..."
cvt paper.pdf -o paper.md
```

## Coding Style & Naming Conventions

Use Python 3.13-compatible code and keep modules import-safe: no network calls or file conversion at import time. Prefer typed signatures, `Path` objects, and small service-specific helpers.

Follow existing naming patterns:

- Functions and variables: `snake_case`.
- Constants: `UPPER_SNAKE_CASE`.
- CLI options: long, descriptive kebab-case such as `--paddle-token`.

Keep comments sparse and useful. Avoid reimplementing conversion logic already provided by Paddle, MinerU, pandoc, or pymupdf4llm.

## Testing Guidelines

No test framework is configured yet. When adding tests, prefer `pytest` and name files `tests/test_*.py`. Mock Paddle and MinerU HTTP calls; do not require real API tokens.

At minimum, verify CLI argument behavior, output path inference, engine selection, and safe archive extraction. Continue using `uv run python -m compileall src` as a quick baseline check.

## Commit & Pull Request Guidelines

This repository has no commit history, so there is no local convention yet. Use concise, imperative commit messages:

- `Add unified conversion CLI`
- `Wrap Paddle layout parsing API`
- `Document MinerU fallback behavior`

Pull requests should include a summary, verification commands, and any API or environment variables needed for manual testing. For CLI behavior changes, include before/after command examples.

## Security & Configuration Tips

Never commit real `PADDLE_TOKEN` or `MINERU_TOKEN` values. Keep secrets in environment variables or a local `.env` file. Treat downloaded archives and remote image URLs as untrusted; preserve the safe extraction checks in `cli.py`.
