from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from everand_to_epub import ConversionError

from .adb_client import AdbClient, AdbError
from .catalog import convert_selected, latest_snapshot, scan_library
from .version import APP_NAME, APP_VERSION


def default_data_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    return local / "Everand EPUB Studio"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--serial", help="Serial ADB específico, se houver mais de um emulador.")
    parser.add_argument("--self-test", action="store_true", help="Verifica ADB, root e Everand sem coletar.")
    parser.add_argument("--collect-only", action="store_true", help="Coleta um snapshot e encerra.")
    parser.add_argument("--convert-all", action="store_true", help="Converte todos os ebooks elegíveis do último snapshot.")
    parser.add_argument("--ui-smoke-test", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=Path.home() / "Documents" / "EPUBs Everand")
    return parser.parse_args(argv)


def cli_progress(percent: int, message: str) -> None:
    print(f"[{percent:3d}%] {message}", flush=True)


def self_test(args: argparse.Namespace) -> int:
    client = AdbClient()
    devices = client.list_devices()
    selected = client.ensure_device(args.serial)
    snapshot = latest_snapshot(args.data_root / "snapshots")
    result = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "adb": str(client.adb_path),
        "devices": [device.__dict__ for device in devices],
        "selected": selected.__dict__,
        "latest_snapshot": str(snapshot) if snapshot else None,
        "status": "ok",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def collect_only(args: argparse.Namespace) -> int:
    snapshot = AdbClient().collect_snapshot(
        args.data_root / "snapshots", serial=args.serial, progress=cli_progress
    )
    print(json.dumps({"snapshot": str(snapshot), "status": "ok"}, ensure_ascii=False, indent=2))
    return 0


def convert_all(args: argparse.Namespace) -> int:
    snapshot = latest_snapshot(args.data_root / "snapshots")
    if not snapshot:
        raise ConversionError("Nenhum snapshot local. Execute primeiro com --collect-only.")
    books = [book for book in scan_library(snapshot) if book.eligible]
    reports = convert_selected(snapshot, books, args.output, cli_progress)
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.ui_smoke_test:
        from .ui import smoke_test_ui

        return smoke_test_ui(args.data_root.resolve(), args.ui_smoke_test.resolve())
    if args.self_test:
        return self_test(args)
    if args.collect_only:
        return collect_only(args)
    if args.convert_all:
        return convert_all(args)
    from .ui import run_gui

    return run_gui(args.data_root.resolve())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdbError, ConversionError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        raise SystemExit(1)
