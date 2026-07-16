from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from everand_to_epub import (
    ConversionError,
    convert_book,
    default_output_name,
    load_book_record,
    verify_entitlement,
)


@dataclass(frozen=True)
class CachedBook:
    book_id: str
    title: str
    author: str
    publisher: str
    language: str
    size_bytes: int
    eligible: bool
    status: str

    @property
    def size_label(self) -> str:
        value = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{value:.1f} GB"


def folder_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def scan_library(library_root: Path) -> list[CachedBook]:
    cache_root = library_root / "app_document_cache"
    if not cache_root.is_dir():
        return []
    books: list[CachedBook] = []
    for book_root in sorted(cache_root.iterdir(), key=lambda item: item.name):
        if not book_root.is_dir() or not book_root.name.isdigit() or not (book_root / "toc.json").is_file():
            continue
        book_id = book_root.name
        eligible = False
        status = "Pronto para reconstruir"
        try:
            record = load_book_record(library_root, book_id)
            verify_entitlement(library_root, book_id, record)
            eligible = True
        except ConversionError as exc:
            try:
                record = load_book_record(library_root, book_id)
            except ConversionError:
                continue
            status = str(exc)
        books.append(
            CachedBook(
                book_id=book_id,
                title=record.title or f"Livro {book_id}",
                author=record.author or "Autor não informado",
                publisher=record.publisher,
                language=record.language,
                size_bytes=folder_size(book_root),
                eligible=eligible,
                status=status,
            )
        )
    return books


def filter_hidden_books(
    books: list[CachedBook], hidden_book_ids: set[str]
) -> list[CachedBook]:
    """Return the visible catalog without modifying the collected snapshot."""
    return [book for book in books if book.book_id not in hidden_book_ids]


def latest_snapshot(snapshots_root: Path) -> Path | None:
    if not snapshots_root.is_dir():
        return None
    candidates = [
        path
        for path in snapshots_root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and (path / "snapshot.json").is_file()
    ]
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def convert_selected(
    library_root: Path,
    books: list[CachedBook],
    output_dir: Path,
    progress: Callable[[int, str], None] | None = None,
) -> list[dict]:
    emit = progress or (lambda _percent, _message: None)
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict] = []
    eligible = [book for book in books if book.eligible]
    if not eligible:
        raise ConversionError("Nenhum livro elegível foi selecionado.")
    for index, book in enumerate(eligible):
        emit(int(index * 100 / len(eligible)), f"Reconstruindo “{book.title}”…")
        destination = output_dir / default_output_name(book.title)
        report = convert_book(library_root, book.book_id, destination)
        reports.append(report)
        emit(int((index + 1) * 100 / len(eligible)), f"Validado: {destination.name}")
    return reports


def snapshot_description(snapshot: Path) -> str:
    metadata_file = snapshot / "snapshot.json"
    if not metadata_file.is_file():
        return snapshot.name
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        return f"{metadata.get('created_at', snapshot.name)} • {metadata.get('device', 'LDPlayer')}"
    except (OSError, ValueError):
        return snapshot.name
