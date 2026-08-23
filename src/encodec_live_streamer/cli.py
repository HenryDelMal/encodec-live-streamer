from __future__ import annotations

import argparse
import json
import logging
import pathlib
import shutil
import subprocess
import sys

from . import __version__
from .config import Config
from .ecdc import parse_header
from .ffmpeg import command
from .service import LiveService


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="encodec-live")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("-v", "--verbose", action="count", default=0)
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the live encoder and publisher")
    serve.add_argument("--config", required=True)
    check = commands.add_parser("check", help="validate configuration and FFmpeg availability")
    check.add_argument("--config", required=True)
    inspect = commands.add_parser("inspect", help="print an ECDC segment header")
    inspect.add_argument("path")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "inspect":
            header = parse_header(pathlib.Path(args.path).read_bytes())
            print(json.dumps(header.__dict__, indent=2))
            return 0
        config = Config.from_toml(args.config)
        if args.command == "check":
            executable = shutil.which(config.ffmpeg)
            if executable is None:
                raise RuntimeError(f"FFmpeg executable not found: {config.ffmpeg}")
            completed = subprocess.run(
                [executable, "-version"], capture_output=True, text=True, check=True
            )
            print(completed.stdout.splitlines()[0])
            print("command:", " ".join(command(config)))
            if config.segment_is_hq_aligned:
                print("HQ segment alignment: OK")
            else:
                print(
                    "warning: segment_duration is not aligned to the HQ 0.99s stride; "
                    "prefer 1.98, 2.97, or 3.96 seconds",
                )
            print("configuration: OK")
            return 0
        LiveService(config).run()
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        logging.getLogger(__name__).error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
