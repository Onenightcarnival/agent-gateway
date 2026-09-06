"""入口：agent-gateway --engine <name> [--port 6217] [--host localhost]"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn
from dotenv import load_dotenv

from .app import create_app
from .config import Settings
from .engines.registry import ENGINES, create_engine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent-gateway")
    parser.add_argument("--engine", choices=sorted(ENGINES), help="引擎标识；缺省取 AGENT_ENGINE")
    parser.add_argument("--port", type=int, help="服务端口；缺省取 GATEWAY_PORT 或 6217")
    parser.add_argument("--host", help="监听地址；缺省取 GATEWAY_HOST 或 localhost")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    try:
        settings = Settings.from_env(engine=args.engine, host=args.host, port=args.port)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(settings, create_engine(settings))
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
