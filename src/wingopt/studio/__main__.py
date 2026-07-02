"""Studio server entrypoint: ``python -m wingopt.studio --config <toml>``."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from wingopt.config import load_config
from wingopt.studio.server import REPO_ROOT, create_app


def build_parser() -> argparse.ArgumentParser:
    """Build the studio CLI argument parser."""
    parser = argparse.ArgumentParser(description="WingGen Studio local web server")
    parser.add_argument("--config", type=Path, default=Path("configs/default_wing.toml"))
    parser.add_argument("--host", type=str, default=None, help="Override studio.host")
    parser.add_argument("--port", type=int, default=None, help="Override studio.port")
    return parser


def main() -> int:
    """Parse arguments, build the app, and serve it with uvicorn."""
    args = build_parser().parse_args()
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    config = load_config(config_path)
    host = args.host if args.host is not None else config.studio.host
    port = args.port if args.port is not None else config.studio.port
    app = create_app(config_path=config_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
