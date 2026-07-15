"""Automatic launcher used by the trusted, signed Python runtime distribution."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


data_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Everand EPUB Studio"
logs = data_root / "logs"
logs.mkdir(parents=True, exist_ok=True)
log_path = logs / "last-run.log"
status_path = logs / "last-run.json"
log_file = log_path.open("w", encoding="utf-8", buffering=1)
sys.stdout = log_file
sys.stderr = log_file
application_arguments = sys.argv[1:]


def finish(code: int, error: str | None = None) -> None:
    payload = {
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "arguments": application_arguments,
        "exit_code": code,
        "error": error,
        "log": str(log_path),
    }
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log_file.flush()
    log_file.close()
    os._exit(code)


try:
    from everand_app.main import main

    forwarded = os.environ.get("EVERAND_EPUB_STUDIO_ARGS_JSON", "")
    application_arguments = json.loads(forwarded) if forwarded else sys.argv[1:]
    if not isinstance(application_arguments, list) or not all(
        isinstance(item, str) for item in application_arguments
    ):
        raise ValueError("Argumentos internos inválidos.")
    exit_code = int(main(application_arguments) or 0)
except BaseException as exc:
    detail = "".join(traceback.format_exception(exc))
    print(detail, flush=True)
    if not os.environ.get("EVERAND_EPUB_STUDIO_ARGS_JSON"):
        ctypes.windll.user32.MessageBoxW(
            0,
            f"O aplicativo não pôde iniciar.\n\n{exc}\n\nDetalhes: {log_path}",
            "Everand EPUB Studio",
            0x10,
        )
    finish(1, str(exc))
else:
    finish(exit_code)
