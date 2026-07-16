#!/usr/bin/env python3
"""Rebuild a standards-compliant EPUB from a locally authorized Everand cache.

The converter is intentionally local-only. It requires the entitlement databases
copied with the cache and refuses books that are not marked as unlocked,
downloadable, and fully accessible.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import posixpath
import re
import sqlite3
import sys
import urllib.parse
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover - exercised by the signed portable build
    Cipher = None
    algorithms = None
    modes = None


EPUB_NS = "http://www.idpf.org/2007/ops"
XHTML_NS = "http://www.w3.org/1999/xhtml"
XML_NS = "http://www.w3.org/XML/1998/namespace"


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class BookRecord:
    title: str
    author: str
    publisher: str
    language: str
    released_at: int | None
    description: str
    unlocked: bool
    reader_type: str
    document_type: str


@dataclass
class Chapter:
    index: int
    title: str
    filename: str
    source_dir: Path
    block_start: int
    block_end: int
    blocks: list[dict[str, Any]]
    body_type: str


def xml_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return "".join(
        ch
        for ch in text
        if ch in "\t\n\r"
        or "\u0020" <= ch <= "\ud7ff"
        or "\ue000" <= ch <= "\ufffd"
        or "\U00010000" <= ch <= "\U0010ffff"
    )


def text_escape(value: Any) -> str:
    return html.escape(xml_text(value), quote=False)


def attr_escape(value: Any) -> str:
    return html.escape(xml_text(value), quote=True)


def format_number(value: Any) -> str:
    number = float(value)
    rendered = f"{number:.6f}".rstrip("0").rstrip(".")
    return rendered or "0"


def wrap_cover_text(value: Any, max_chars: int, max_lines: int) -> list[str]:
    """Wrap untrusted metadata into a small, deterministic set of SVG text lines."""
    words = xml_text(value).strip().split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        last = " ".join(lines[max_lines - 1 :])
        if len(last) > max_chars:
            last = last[: max(1, max_chars - 1)].rstrip() + "…"
        lines = lines[: max_lines - 1] + [last]
    return lines


def generated_cover_svg(title: str, author: str) -> bytes:
    """Create a standards-compliant fallback cover when the cache has no cover image."""
    title_lines = wrap_cover_text(title or "Untitled", 26, 7) or ["Untitled"]
    author_lines = wrap_cover_text(author or "Autor não informado", 38, 2)
    title_font_size = 150 if len(title_lines) <= 4 else 124
    title_step = int(title_font_size * 1.22)
    title_y = 650
    title_tspans = "".join(
        f'<tspan x="150" y="{title_y + index * title_step}">{text_escape(line)}</tspan>'
        for index, line in enumerate(title_lines)
    )
    author_y = 2140
    author_tspans = "".join(
        f'<tspan x="150" y="{author_y + index * 86}">{text_escape(line)}</tspan>'
        for index, line in enumerate(author_lines)
    )
    document = f'''<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="2560" viewBox="0 0 1600 2560" role="img" aria-labelledby="cover-title cover-description">
  <title id="cover-title">{text_escape(title or "Untitled")}</title>
  <desc id="cover-description">Capa tipográfica de contingência para {text_escape(title or "Untitled")}</desc>
  <rect width="1600" height="2560" fill="#111827" />
  <rect x="0" y="0" width="46" height="2560" fill="#f59e0b" />
  <circle cx="1450" cy="190" r="330" fill="#1f2937" />
  <circle cx="1450" cy="190" r="210" fill="#f59e0b" opacity="0.94" />
  <rect x="150" y="330" width="230" height="18" rx="9" fill="#f59e0b" />
  <text x="150" y="445" fill="#f59e0b" font-family="Segoe UI, Arial, sans-serif" font-size="54" font-weight="700" letter-spacing="9">EPUB</text>
  <text fill="#ffffff" font-family="Georgia, Times New Roman, serif" font-size="{title_font_size}" font-weight="700">{title_tspans}</text>
  <rect x="150" y="2010" width="1300" height="3" fill="#f59e0b" opacity="0.8" />
  <text fill="#f3f4f6" font-family="Segoe UI, Arial, sans-serif" font-size="62" font-weight="500">{author_tspans}</text>
  <text x="150" y="2390" fill="#9ca3af" font-family="Segoe UI, Arial, sans-serif" font-size="34" letter-spacing="4">EVERAND EPUB STUDIO</text>
</svg>
'''
    return document.encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sqlite_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ConversionError(f"Banco de dados ausente: {path}")
    uri = f"file:{urllib.parse.quote(path.as_posix(), safe='/:')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_book_record(library_root: Path, book_id: str) -> BookRecord:
    with sqlite_ro(library_root / "scribdData") as connection:
        row = connection.execute(
            """
            SELECT title, author_name, uploadedBy, language, released_at,
                   full_description, unlocked, reader_type, document_type
              FROM documents
             WHERE CAST(remoteId AS TEXT)=?
             LIMIT 1
            """,
            (book_id,),
        ).fetchone()
    if row is None:
        raise ConversionError(f"Livro {book_id} não foi encontrado em scribdData.")
    return BookRecord(
        title=xml_text(row["title"]),
        author=xml_text(row["author_name"]),
        publisher=xml_text(row["uploadedBy"]),
        language=xml_text(row["language"] or "en"),
        released_at=row["released_at"],
        description=xml_text(row["full_description"]),
        unlocked=bool(row["unlocked"]),
        reader_type=xml_text(row["reader_type"]),
        document_type=xml_text(row["document_type"]),
    )


def verify_entitlement(library_root: Path, book_id: str, record: BookRecord) -> None:
    if not record.unlocked:
        raise ConversionError(f"O livro {book_id} não está marcado como desbloqueado no cache local.")
    if record.reader_type.lower() != "epub" or record.document_type.lower() != "book":
        raise ConversionError(
            f"O item {book_id} não é um ebook EPUB ({record.document_type}/{record.reader_type})."
        )
    with sqlite_ro(library_root / "Scribd.db") as connection:
        row = connection.execute(
            """
            SELECT has_full_access, can_download
              FROM DocumentEntitlements
             WHERE doc_id=?
             LIMIT 1
            """,
            (int(book_id),),
        ).fetchone()
    if row is None or not (bool(row["has_full_access"]) and bool(row["can_download"])):
        raise ConversionError(
            f"O cache não confirma acesso integral e permissão de download para o livro {book_id}."
        )


def load_real_key(library_root: Path, book_id: str) -> bytes:
    keyfile = library_root / "FILENAME_KEYS.xml"
    if not keyfile.is_file():
        raise ConversionError(f"Arquivo de chaves ausente: {keyfile}")
    root = ET.parse(keyfile).getroot()
    encoded = None
    for element in root.findall("string"):
        if element.attrib.get("name") == f"KEY_{book_id}":
            encoded = (element.text or "").strip()
            break
    if not encoded or not re.fullmatch(r"[0-9a-fA-F]+", encoded) or len(encoded) % 2:
        raise ConversionError(f"Chave inválida ou ausente para o livro {book_id}.")
    source = bytes.fromhex(encoded)
    transformed = bytearray(len(source))
    previous = 31
    for index, value in enumerate(source):
        transformed[index] = value ^ previous
        previous = transformed[index]
    key = bytes(transformed)
    if len(key) not in (16, 24, 32):
        raise ConversionError(f"Tamanho de chave AES inválido: {len(key)} bytes.")
    return key


def _windows_aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt through Windows CNG so the portable build needs no unsigned extension."""
    if sys.platform != "win32":
        raise ConversionError("O mecanismo AES nativo só está disponível no Windows.")
    import ctypes
    from ctypes import wintypes

    bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)
    byte_pointer = ctypes.POINTER(ctypes.c_ubyte)
    bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.ULONG,
    ]
    bcrypt.BCryptOpenAlgorithmProvider.restype = wintypes.LONG
    bcrypt.BCryptSetProperty.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        byte_pointer,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    bcrypt.BCryptSetProperty.restype = wintypes.LONG
    bcrypt.BCryptGetProperty.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        byte_pointer,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.ULONG,
    ]
    bcrypt.BCryptGetProperty.restype = wintypes.LONG
    bcrypt.BCryptGenerateSymmetricKey.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        byte_pointer,
        wintypes.ULONG,
        byte_pointer,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    bcrypt.BCryptGenerateSymmetricKey.restype = wintypes.LONG
    bcrypt.BCryptDecrypt.argtypes = [
        ctypes.c_void_p,
        byte_pointer,
        wintypes.ULONG,
        ctypes.c_void_p,
        byte_pointer,
        wintypes.ULONG,
        byte_pointer,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.ULONG,
    ]
    bcrypt.BCryptDecrypt.restype = wintypes.LONG
    bcrypt.BCryptDestroyKey.argtypes = [ctypes.c_void_p]
    bcrypt.BCryptDestroyKey.restype = wintypes.LONG
    bcrypt.BCryptCloseAlgorithmProvider.argtypes = [ctypes.c_void_p, wintypes.ULONG]
    bcrypt.BCryptCloseAlgorithmProvider.restype = wintypes.LONG

    def check(status: int, operation: str) -> None:
        if status < 0:
            code = ctypes.c_ulong(status).value
            raise ConversionError(f"Falha no AES do Windows durante {operation} (0x{code:08X}).")

    algorithm = ctypes.c_void_p()
    symmetric_key = ctypes.c_void_p()
    check(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(algorithm), "AES", None, 0), "abertura")
    try:
        mode = ("ChainingModeECB\0").encode("utf-16-le")
        mode_buffer = (ctypes.c_ubyte * len(mode)).from_buffer_copy(mode)
        check(
            bcrypt.BCryptSetProperty(
                algorithm, "ChainingMode", mode_buffer, len(mode), 0
            ),
            "configuração ECB",
        )
        object_length = wintypes.ULONG()
        received = wintypes.ULONG()
        object_length_buffer = ctypes.cast(ctypes.byref(object_length), byte_pointer)
        check(
            bcrypt.BCryptGetProperty(
                algorithm,
                "ObjectLength",
                object_length_buffer,
                ctypes.sizeof(object_length),
                ctypes.byref(received),
                0,
            ),
            "consulta da chave",
        )
        key_object = (ctypes.c_ubyte * object_length.value)()
        key_buffer = (ctypes.c_ubyte * len(key)).from_buffer_copy(key)
        check(
            bcrypt.BCryptGenerateSymmetricKey(
                algorithm,
                ctypes.byref(symmetric_key),
                key_object,
                object_length.value,
                key_buffer,
                len(key),
                0,
            ),
            "criação da chave",
        )
        source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        output_buffer = (ctypes.c_ubyte * len(data))()
        written = wintypes.ULONG()
        check(
            bcrypt.BCryptDecrypt(
                symmetric_key,
                source_buffer,
                len(data),
                None,
                None,
                0,
                output_buffer,
                len(output_buffer),
                ctypes.byref(written),
                0,
            ),
            "decifragem",
        )
        return bytes(output_buffer[: written.value])
    finally:
        if symmetric_key:
            bcrypt.BCryptDestroyKey(symmetric_key)
        bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)


def decrypt_aes_ecb(data: bytes, key: bytes, source: Path) -> bytes:
    if not data or len(data) % 16:
        raise ConversionError(f"Arquivo cifrado inválido: {source}")
    if Cipher is not None:
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        plain = decryptor.update(data) + decryptor.finalize()
    else:
        plain = _windows_aes_ecb_decrypt(data, key)
    padding = plain[-1]
    if not (1 <= padding <= 16 and plain[-padding:] == bytes([padding]) * padding):
        raise ConversionError(f"Padding AES inválido após decifrar: {source}")
    return plain[:-padding]


def decrypt_json(path: Path, key: bytes) -> Any:
    plain = decrypt_aes_ecb(path.read_bytes(), key, path)
    try:
        return json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversionError(f"Conteúdo JSON inválido após decifrar: {path}") from exc


def isbn13_is_valid(value: str) -> bool:
    if not re.fullmatch(r"\d{13}", value):
        return False
    total = sum(int(ch) * (1 if index % 2 == 0 else 3) for index, ch in enumerate(value[:12]))
    return (10 - total % 10) % 10 == int(value[-1])


def infer_isbn(tags: dict[str, Any]) -> str | None:
    candidates: dict[str, int] = defaultdict(int)
    for name in tags:
        for candidate in re.findall(r"(?<!\d)(97[89]\d{10})(?!\d)", name):
            if isbn13_is_valid(candidate):
                candidates[candidate] += 1
    return max(candidates, key=candidates.get) if candidates else None


def normalize_language(language: str) -> str:
    value = language.strip().lower().replace("_", "-")
    mapping = {
        "english": "en",
        "portuguese": "pt",
        "brazilian portuguese": "pt-BR",
        "spanish": "es",
        "french": "fr",
        "german": "de",
        "italian": "it",
    }
    return mapping.get(value, value or "en")


def body_type_for(title: str, index: int) -> str:
    upper = title.upper()
    if index == 0 or "COVER" in upper:
        return "cover"
    if "TITLE PAGE" in upper:
        return "titlepage"
    if "COPYRIGHT" in upper:
        return "copyright-page"
    if upper == "CONTENTS":
        return "toc"
    if upper in {"NOTES", "ENDNOTES"}:
        return "endnotes"
    if "FURTHER READING" in upper or "BIBLIOGRAPH" in upper:
        return "bibliography"
    if upper.startswith("PART "):
        return "part"
    return "chapter"


class EpubRenderer:
    def __init__(
        self,
        book_root: Path,
        library_root: Path,
        book_id: str,
        key: bytes,
        record: BookRecord,
    ) -> None:
        self.book_root = book_root
        self.library_root = library_root
        self.book_id = book_id
        self.key = key
        self.record = record
        self.book_metadata = read_json(book_root / "book_metadata.json")
        self.toc = read_json(book_root / "toc.json")
        self.tags = read_json(book_root / "tags.json").get("tags", {})
        self.metadatas = read_json(book_root / "metadata.json")
        self.styles = read_json(book_root / "styles.json")
        self.page_refs = read_json(book_root / "page_refs.json")
        self.chapters: list[Chapter] = []
        self.images: dict[str, bytes] = {}
        self.image_map: dict[tuple[int, str], str] = {}
        self.cover_href: str | None = None
        self.cover_chapter_index = 0
        self.cover_source = "declared"
        self.position_ids: dict[tuple[int, int], str] = {}
        self.tag_targets: dict[str, tuple[int, int]] = {}
        self.page_targets: dict[int, tuple[int, int]] = {}
        self.pages_at_position: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.chapter_for_global_block: dict[int, int] = {}
        self.emitted_positions: set[tuple[int, int]] = set()
        self.files: dict[str, bytes] = {}
        self._load_chapters()
        self._load_images()
        self._build_targets()

    def _load_chapters(self) -> None:
        expected_start = 0
        for index, item in enumerate(self.toc):
            source_dir = self.book_root / PurePosixPath(item["filepath"])
            contents = decrypt_json(source_dir / "contents.json", self.key)
            if not isinstance(contents, dict) or not isinstance(contents.get("blocks"), list):
                raise ConversionError(f"Estrutura de capítulo inválida: {source_dir}")
            block_start = int(item.get("block_start", expected_start))
            block_end = int(item.get("block_end", block_start + len(contents["blocks"])))
            if block_start != expected_start or block_end - block_start != len(contents["blocks"]):
                raise ConversionError(
                    f"Faixa de blocos inconsistente no capítulo {index + 1}: "
                    f"{block_start}:{block_end} para {len(contents['blocks'])} blocos."
                )
            chapter = Chapter(
                index=index,
                title=xml_text(item.get("title") or contents.get("title") or f"Chapter {index + 1}"),
                filename=f"chapter-{index + 1:03d}.xhtml",
                source_dir=source_dir,
                block_start=block_start,
                block_end=block_end,
                blocks=contents["blocks"],
                body_type=body_type_for(xml_text(item.get("title")), index),
            )
            self.chapters.append(chapter)
            for block_index in range(block_start, block_end):
                self.chapter_for_global_block[block_index] = index
            expected_start = block_end
        if len(self.page_refs) != len(self.chapters):
            raise ConversionError("page_refs.json não corresponde ao número de capítulos.")

    def _load_images(self) -> None:
        for chapter in self.chapters:
            for path in sorted((chapter.source_dir / "images").glob("*")):
                if not path.is_file():
                    continue
                content = decrypt_aes_ecb(path.read_bytes(), self.key, path)
                if content.startswith(b"\xff\xd8\xff"):
                    extension = ".jpg"
                elif content.startswith(b"\x89PNG\r\n\x1a\n"):
                    extension = ".png"
                else:
                    raise ConversionError(f"Imagem decifrada com formato desconhecido: {path}")
                digest = hashlib.sha256(content).hexdigest()[:12]
                href = f"images/ch{chapter.index + 1:03d}-{digest}{extension}"
                source_ref = f"images/{path.name}"
                self.image_map[(chapter.index, source_ref)] = href
                self.images[href] = content

        cover_candidates: list[tuple[str, int]] = []
        for chapter in self.chapters:
            for block in chapter.blocks:
                for image in self._walk_images(block):
                    source_ref = xml_text(image.get("src"))
                    mapped = self.image_map.get((chapter.index, source_ref))
                    if not mapped:
                        raise ConversionError(
                            f"Imagem referenciada e ausente: capítulo {chapter.index + 1}, {source_ref}"
                        )
                    if chapter.body_type == "cover":
                        cover_candidates.append((mapped, chapter.index))
                    normalized_alt = re.sub(
                        r"\s+", " ", xml_text(image.get("alt")).strip().casefold()
                    )
                    if normalized_alt in {"cover", "front cover", "book cover", "capa"} and self.cover_href is None:
                        self.cover_href = mapped
                        self.cover_chapter_index = chapter.index
        if self.cover_href is None:
            if cover_candidates:
                self.cover_href, self.cover_chapter_index = cover_candidates[0]
                self.cover_source = "inferred"
            else:
                self.cover_href = "images/generated-cover.svg"
                self.cover_chapter_index = 0
                self.cover_source = "generated"
                self.images[self.cover_href] = generated_cover_svg(
                    self.record.title, self.record.author
                )

    def _walk_images(self, value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            if value.get("type") == "image" and value.get("src"):
                yield value
            for child in value.values():
                yield from self._walk_images(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_images(child)

    def _build_targets(self) -> None:
        for name, target in self.tags.items():
            block_index = int(target["block_idx"])
            word_index = int(target["word_idx"])
            if block_index not in self.chapter_for_global_block:
                raise ConversionError(f"Âncora fora do livro: {name}")
            position = (block_index, word_index)
            self.tag_targets[name] = position
            self.position_ids.setdefault(position, f"loc-{block_index}-{word_index}")

        for chapter_index, references in enumerate(self.page_refs):
            chapter = self.chapters[chapter_index]
            page_start = int(self.toc[chapter_index].get("reference_page_start", 0))
            for local_page_index, reference in enumerate(references):
                referenced_chapter, local_block, word_index = map(int, reference)
                if referenced_chapter != chapter_index:
                    raise ConversionError(
                        f"Referência de página cruza capítulos: {reference} em {chapter_index}."
                    )
                page_number = page_start + local_page_index + 1
                position = (chapter.block_start + local_block, word_index)
                self.page_targets[page_number] = position
                self.pages_at_position[position].append(page_number)

    def anchors_at(self, block_index: int, word_index: int) -> str:
        position = (block_index, word_index)
        if position in self.emitted_positions:
            return ""
        result: list[str] = []
        location_id = self.position_ids.get(position)
        if location_id:
            result.append(f'<span id="{location_id}" class="anchor"></span>')
        for page in self.pages_at_position.get(position, []):
            result.append(
                f'<span id="page-{page}" epub:type="pagebreak" role="doc-pagebreak" '
                f'class="pagebreak-anchor" title="{page}"></span>'
            )
        if result:
            self.emitted_positions.add(position)
        return "".join(result)

    def resolve_href(self, href: str) -> str:
        href = xml_text(href).strip()
        if not href:
            return ""
        if href.startswith("www."):
            return "https://" + href
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
            return href
        target = self.tag_targets.get(href) or self.tag_targets.get(href.split("#", 1)[0])
        if target is None:
            raise ConversionError(f"Link interno sem destino: {href}")
        block_index, word_index = target
        chapter_index = self.chapter_for_global_block[block_index]
        return f"{self.chapters[chapter_index].filename}#{self.position_ids[target]}"

    def render_inline_image(self, image: dict[str, Any], chapter_index: int) -> str:
        source_ref = xml_text(image.get("src"))
        mapped = self.image_map.get((chapter_index, source_ref))
        if not mapped:
            raise ConversionError(f"Imagem sem mapeamento: capítulo {chapter_index + 1}, {source_ref}")
        attributes = [f'src="../{attr_escape(mapped)}"']
        alternative = xml_text(image.get("alt", "")).strip()
        if mapped == self.cover_href and alternative.casefold() in {"", "image", "imagem"}:
            alternative = f"Capa de {self.record.title}"
        attributes.append(f'alt="{attr_escape(alternative)}"')
        for dimension in ("width", "height"):
            value = image.get(dimension)
            if isinstance(value, (int, float)) and value > 0:
                attributes.append(f'{dimension}="{int(value)}"')
        classes = ["book-image"]
        if mapped == self.cover_href:
            classes.append("cover-image")
        attributes.append(f'class="{" ".join(classes)}"')
        return "<img " + " ".join(attributes) + " />"

    def _word_text(self, word: dict[str, Any]) -> str:
        break_map = word.get("break_map")
        if isinstance(break_map, dict) and break_map.get("text") is not None:
            return xml_text(break_map.get("text"))
        return xml_text(word.get("text", ""))

    def render_word(self, word: dict[str, Any], chapter_index: int) -> str:
        word_type = word.get("type")
        if word_type == "image":
            return self.render_inline_image(word, chapter_index)
        if word_type == "composite" or (not word.get("text") and isinstance(word.get("words"), list)):
            return "".join(self.render_word(child, chapter_index) for child in word.get("words", []))

        content = text_escape(self._word_text(word))
        classes: list[str] = []
        style_name = xml_text(word.get("style"))
        if style_name in {"style1", "style2", "style3"}:
            classes.append(style_name)
        font_name = xml_text(word.get("font"))
        if font_name in {"font4", "font5", "font7"}:
            classes.append("smallcaps")
        if font_name in {"font6", "font8"}:
            classes.append("bold-italic-font")

        metadata = self.metadatas.get(xml_text(word.get("metadata")), {})
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("underline"):
            classes.append("underline")
        if metadata.get("overline"):
            classes.append("overline")

        inline_style = ""
        color = xml_text(metadata.get("color"))
        if color and re.fullmatch(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]+", color):
            inline_style = f' style="color:{attr_escape(color)}"'
        class_attr = f' class="{" ".join(dict.fromkeys(classes))}"' if classes else ""
        tag = "sup" if metadata.get("superscript") else "span"
        if tag == "span" and not class_attr and not inline_style:
            wrapped = content
        else:
            wrapped = f"<{tag}{class_attr}{inline_style}>{content}</{tag}>"

        href = xml_text(metadata.get("href"))
        if href:
            resolved = self.resolve_href(href)
            external = bool(metadata.get("external_link")) or re.match(
                r"^(?:https?://|mailto:)", resolved, re.IGNORECASE
            )
            extra = ' rel="noopener noreferrer"' if external else ""
            wrapped = f'<a href="{attr_escape(resolved)}"{extra}>{wrapped}</a>'
        return wrapped

    def render_words(
        self,
        words: list[dict[str, Any]],
        chapter_index: int,
        block_index: int,
        start_index: int = 0,
        add_position_anchors: bool = True,
    ) -> tuple[str, int]:
        output: list[str] = []
        cursor = start_index
        for local_index, word in enumerate(words):
            if local_index:
                output.append(" ")
            if add_position_anchors:
                output.append(self.anchors_at(block_index, cursor))
            output.append(self.render_word(word, chapter_index))
            cursor += 1
        if add_position_anchors:
            output.append(self.anchors_at(block_index, cursor))
        return "".join(output), cursor

    def paragraph_style(self, block: dict[str, Any]) -> str:
        declarations: list[str] = []
        align = xml_text(block.get("align"))
        if align in {"left", "right", "center", "justify"}:
            declarations.append(f"text-align:{align}")
        mappings = (
            ("block_indent", "margin-left"),
            ("right_indent", "margin-right"),
            ("text_indent", "text-indent"),
        )
        for source_name, css_name in mappings:
            value = block.get(source_name)
            if isinstance(value, (int, float)) and value:
                declarations.append(f"{css_name}:{format_number(value)}em")
        return ";".join(declarations)

    def render_text_block(
        self,
        block: dict[str, Any],
        chapter: Chapter,
        block_index: int,
        force_h1: bool,
    ) -> str:
        words = block.get("words", [])
        content, _ = self.render_words(words, chapter.index, block_index)
        if block.get("size") == "headline":
            if force_h1:
                element = "h1"
            else:
                size_class = int(block.get("size_class") or 3)
                element = f"h{max(2, min(5, 6 - size_class))}"
        else:
            element = "p"
        classes = ["text-block"]
        if block.get("size") == "headline":
            classes.append(f"headline-{int(block.get('size_class') or 3)}")
        style = self.paragraph_style(block)
        style_attr = f' style="{attr_escape(style)}"' if style else ""
        return f'<{element} class="{" ".join(classes)}"{style_attr}>{content}</{element}>'

    def render_table(
        self, chapter: Chapter, rows: list[tuple[int, dict[str, Any]]]
    ) -> str:
        column_count = max(len(row.get("cells", [])) for _, row in rows)
        result = [
            '<div class="table-wrap"><table><colgroup>',
            "".join(f'<col style="width:{100 / column_count:.4f}%" />' for _ in range(column_count)),
            "</colgroup><tbody>",
        ]
        for block_index, row in rows:
            cells = row.get("cells", [])
            result.append("<tr>")
            word_cursor = 0
            for cell_index, cell in enumerate(cells):
                raw_style = xml_text(cell.get("style"))
                safe_parts = []
                for declaration in raw_style.split(";"):
                    declaration = declaration.strip()
                    if not declaration or declaration.startswith("-scribd-"):
                        continue
                    if re.fullmatch(
                        r"(?:vertical-align|padding(?:-(?:left|right|top|bottom))?|text-align)\s*:\s*[-#%().,\w\s]+",
                        declaration,
                        re.IGNORECASE,
                    ):
                        safe_parts.append(declaration)
                style_attr = f' style="{attr_escape(";".join(safe_parts))}"' if safe_parts else ""
                result.append(f"<td{style_attr}>")
                if cell_index == 0:
                    result.append(self.anchors_at(block_index, 0))
                for node in cell.get("nodes", []):
                    if node.get("type") == "text":
                        rendered, word_cursor = self.render_words(
                            node.get("words", []),
                            chapter.index,
                            block_index,
                            start_index=word_cursor,
                            add_position_anchors=cell_index != 0 or word_cursor != 0,
                        )
                        result.append(f"<p>{rendered}</p>")
                    elif node.get("type") == "image":
                        result.append(self.render_inline_image(node, chapter.index))
                result.append("</td>")
            result.append("</tr>")
        result.append("</tbody></table></div>")
        return "".join(result)

    def render_standalone_block(
        self, block: dict[str, Any], chapter: Chapter, block_index: int
    ) -> str:
        block_type = block.get("type")
        anchors = self.anchors_at(block_index, 0)
        if block_type == "spacer":
            size = max(0.0, min(12.0, float(block.get("size") or 0)))
            return f'{anchors}<div class="spacer" style="height:{format_number(size)}em"></div>'
        if block_type == "image":
            image = self.render_inline_image(block, chapter.index)
            classes = "figure center" if block.get("center") else "figure"
            return f'{anchors}<div class="{classes}">{image}</div>'
        if block_type == "page_break":
            return f'{anchors}<div class="forced-page-break" aria-hidden="true"></div>'
        if block_type == "border":
            color = xml_text(block.get("color") or "currentColor")
            if not re.fullmatch(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]+", color):
                color = "currentColor"
            width = max(0.5, min(100.0, float(block.get("width") or 100)))
            thickness = "3px" if float(block.get("width") or 0) >= 0.18 else "1px"
            return (
                f'{anchors}<div class="border" style="border-top:{thickness} solid '
                f'{attr_escape(color)};width:{format_number(width)}em"></div>'
            )
        if block_type == "hr":
            return f"{anchors}<hr />"
        if block_type == "raw":
            return f'{anchors}<div class="raw">{text_escape(block.get("text", ""))}</div>'
        return anchors

    def render_chapter(self, chapter: Chapter) -> bytes:
        headline_indexes = [
            index
            for index, block in enumerate(chapter.blocks)
            if block.get("type") == "text" and block.get("size") == "headline"
        ]
        first_headline = headline_indexes[0] if headline_indexes else None
        body: list[str] = []
        if self.cover_source == "generated" and chapter.index == self.cover_chapter_index:
            body.append(
                '<section class="generated-cover-page" epub:type="cover">'
                f'<img src="../{attr_escape(self.cover_href)}" class="book-image cover-image" '
                f'alt="{attr_escape(f"Capa de {self.record.title}")}" />'
                "</section>"
            )
        if first_headline is None:
            body.append(f'<h1 class="visually-hidden">{text_escape(chapter.title)}</h1>')
        cursor = 0
        while cursor < len(chapter.blocks):
            block = chapter.blocks[cursor]
            global_block = chapter.block_start + cursor
            if block.get("type") == "row":
                rows: list[tuple[int, dict[str, Any]]] = []
                while cursor < len(chapter.blocks) and chapter.blocks[cursor].get("type") == "row":
                    rows.append((chapter.block_start + cursor, chapter.blocks[cursor]))
                    cursor += 1
                body.append(self.render_table(chapter, rows))
                continue
            if block.get("type") == "text":
                body.append(
                    self.render_text_block(
                        block, chapter, global_block, force_h1=(cursor == first_headline)
                    )
                )
            else:
                body.append(self.render_standalone_block(block, chapter, global_block))
            cursor += 1

        language = normalize_language(self.record.language)
        document = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" xml:lang="{attr_escape(language)}" lang="{attr_escape(language)}">
<head>
  <meta charset="utf-8" />
  <title>{text_escape(chapter.title)}</title>
  <link rel="stylesheet" type="text/css" href="../styles/book.css" />
</head>
<body epub:type="{attr_escape(chapter.body_type)}">
{''.join(body)}
</body>
</html>
'''
        return document.encode("utf-8")

    def nav_tree(self) -> str:
        result = ["<ol>"]
        part_open = False
        for chapter in self.chapters:
            title = chapter.title
            is_part = title.upper().startswith("PART ")
            is_numbered_chapter = bool(re.match(r"^\d+\.\s", title))
            if is_part:
                if part_open:
                    result.append("</ol></li>")
                result.append(
                    f'<li><a href="text/{chapter.filename}">{text_escape(title)}</a><ol>'
                )
                part_open = True
                continue
            if part_open and not is_numbered_chapter:
                result.append("</ol></li>")
                part_open = False
            result.append(
                f'<li><a href="text/{chapter.filename}">{text_escape(title)}</a></li>'
            )
        if part_open:
            result.append("</ol></li>")
        result.append("</ol>")
        return "".join(result)

    def build_nav(self) -> bytes:
        language = normalize_language(self.record.language)
        page_items = []
        for page in sorted(self.page_targets):
            block_index, _ = self.page_targets[page]
            chapter = self.chapters[self.chapter_for_global_block[block_index]]
            page_items.append(
                f'<li><a href="text/{chapter.filename}#page-{page}">{page}</a></li>'
            )
        toc_chapter = next(
            (chapter for chapter in self.chapters if chapter.body_type == "toc"),
            self.chapters[0],
        )
        landmarks = [
            (
                "cover",
                f"text/{self.chapters[self.cover_chapter_index].filename}",
                "Cover",
            ),
            ("toc", f"text/{toc_chapter.filename}", "Table of Contents"),
        ]
        first_body = next((chapter for chapter in self.chapters if chapter.body_type == "chapter"), None)
        if first_body:
            landmarks.append(("bodymatter", f"text/{first_body.filename}", "Start of Content"))
        landmark_items = "".join(
            f'<li><a epub:type="{kind}" href="{href}">{text_escape(label)}</a></li>'
            for kind, href, label in landmarks
        )
        document = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" xml:lang="{attr_escape(language)}" lang="{attr_escape(language)}">
<head><meta charset="utf-8" /><title>Navigation</title><link rel="stylesheet" type="text/css" href="styles/book.css" /></head>
<body>
<nav epub:type="toc" id="toc"><h1>Contents</h1>{self.nav_tree()}</nav>
<nav epub:type="page-list" id="page-list" hidden="hidden"><h2>Pages</h2><ol>{''.join(page_items)}</ol></nav>
<nav epub:type="landmarks" id="landmarks" hidden="hidden"><h2>Landmarks</h2><ol>{landmark_items}</ol></nav>
</body>
</html>
'''
        return document.encode("utf-8")

    def build_ncx(self, identifier: str) -> bytes:
        nav_points = []
        for play_order, chapter in enumerate(self.chapters, start=1):
            nav_points.append(
                f'''<navPoint id="nav-{play_order}" playOrder="{play_order}">
  <navLabel><text>{text_escape(chapter.title)}</text></navLabel>
  <content src="text/{chapter.filename}" />
</navPoint>'''
            )
        document = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head>
  <meta name="dtb:uid" content="{attr_escape(identifier)}" />
  <meta name="dtb:depth" content="1" />
  <meta name="dtb:totalPageCount" content="0" />
  <meta name="dtb:maxPageNumber" content="0" />
</head>
<docTitle><text>{text_escape(self.record.title)}</text></docTitle>
<navMap>{''.join(nav_points)}</navMap>
</ncx>
'''
        return document.encode("utf-8")

    def font_files(self) -> dict[str, bytes]:
        font_root = self.book_root.parent / "fonts" / "default_20250526"
        mapping = {
            "fonts/NotoSerif-Regular.ttf": "noto-serif--.ttf",
            "fonts/NotoSerif-Italic.ttf": "noto-serif-i-.ttf",
            "fonts/NotoSerif-Bold.ttf": "noto-serif-b-.ttf",
            "fonts/NotoSerif-BoldItalic.ttf": "noto-serif-bi-.ttf",
        }
        result = {}
        for href, filename in mapping.items():
            path = font_root / filename
            if not path.is_file():
                raise ConversionError(f"Fonte esperada ausente: {path}")
            result[href] = path.read_bytes()
        return result

    def build_css(self) -> bytes:
        return b'''@charset "UTF-8";
@font-face{font-family:"Book Serif";font-style:normal;font-weight:400;src:url("../fonts/NotoSerif-Regular.ttf") format("truetype")}
@font-face{font-family:"Book Serif";font-style:italic;font-weight:400;src:url("../fonts/NotoSerif-Italic.ttf") format("truetype")}
@font-face{font-family:"Book Serif";font-style:normal;font-weight:700;src:url("../fonts/NotoSerif-Bold.ttf") format("truetype")}
@font-face{font-family:"Book Serif";font-style:italic;font-weight:700;src:url("../fonts/NotoSerif-BoldItalic.ttf") format("truetype")}
html{font-family:"Book Serif",serif;line-height:1.45;text-rendering:optimizeLegibility}
body{margin:5%;orphans:2;widows:2}
p{margin:.45em 0}
h1,h2,h3,h4,h5{font-weight:700;line-height:1.2;margin:1.2em 0 .55em;page-break-after:avoid;break-after:avoid}
h1{font-size:1.65em}.headline-4{font-size:1.45em}.headline-3{font-size:1.28em}.headline-2{font-size:1.12em}
.style1{font-style:italic}.style2{font-weight:700}.style3,.bold-italic-font{font-style:italic;font-weight:700}
.smallcaps{font-variant:small-caps}.underline{text-decoration:underline}.overline{text-decoration:overline}
sup{font-size:.75em;line-height:0;vertical-align:super}
a{color:inherit;text-decoration:underline;text-decoration-thickness:.06em;text-underline-offset:.12em}
.figure{text-align:left;margin:1em auto;page-break-inside:avoid;break-inside:avoid}.figure.center{text-align:center}
.book-image{display:inline-block;max-width:100%;height:auto}.cover-image{display:block;max-height:90vh;width:auto;margin:0 auto}
.generated-cover-page{margin:0;padding:0;text-align:center;break-after:page;page-break-after:always}
.spacer{display:block}.forced-page-break{break-before:page;page-break-before:always;height:0}
.border{height:0;margin:.6em auto}.anchor,.pagebreak-anchor{display:inline;height:0;width:0}
hr{border:0;border-top:1px solid currentColor;margin:1em auto;width:35%}
.table-wrap{overflow-x:auto;margin:1em 0;page-break-inside:auto}.table-wrap table{border-collapse:collapse;border-spacing:0;width:100%;table-layout:fixed}
td{vertical-align:top}td p{margin:.2em 0}
.visually-hidden{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
nav ol{padding-left:1.5em}nav li{margin:.35em 0}
@media (max-width:30em){body{margin:4%}.text-block[style*="margin-left:9em"]{margin-left:2em!important}.text-block[style*="margin-right:9em"]{margin-right:2em!important}}
'''

    def primary_identifier(self) -> tuple[str, str | None]:
        isbn = infer_isbn(self.tags)
        if isbn:
            return f"urn:isbn:{isbn}", isbn
        return f"urn:everand:{self.book_id}", None

    def build_opf(self, identifier: str, isbn: str | None, font_hrefs: list[str]) -> bytes:
        language = normalize_language(self.record.language)
        modified = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        date_value = ""
        if self.record.released_at:
            try:
                date_value = dt.datetime.fromtimestamp(
                    int(self.record.released_at), tz=dt.timezone.utc
                ).date().isoformat()
            except (OverflowError, OSError, ValueError):
                date_value = ""

        metadata = [
            f'<dc:identifier id="pub-id">{text_escape(identifier)}</dc:identifier>',
            f'<dc:identifier id="everand-id">urn:everand:{text_escape(self.book_id)}</dc:identifier>',
            f'<dc:title id="title">{text_escape(self.record.title)}</dc:title>',
            '<meta refines="#title" property="title-type">main</meta>',
            f'<dc:creator id="creator">{text_escape(self.record.author)}</dc:creator>',
            '<meta refines="#creator" property="role" scheme="marc:relators">aut</meta>',
            '<meta refines="#creator" property="file-as">' + text_escape(self.record.author) + "</meta>",
            f'<dc:language>{text_escape(language)}</dc:language>',
            f'<meta property="dcterms:modified">{modified}</meta>',
            '<meta property="rendition:layout">reflowable</meta>',
            '<meta property="rendition:orientation">auto</meta>',
            '<meta property="rendition:spread">auto</meta>',
            '<meta name="cover" content="cover-image" />',
        ]
        if isbn:
            metadata.append(f'<meta refines="#pub-id" property="identifier-type">15</meta>')
        if self.record.publisher:
            metadata.append(f'<dc:publisher>{text_escape(self.record.publisher)}</dc:publisher>')
        if date_value:
            metadata.append(f'<dc:date>{date_value}</dc:date>')
        if self.record.description:
            metadata.append(f'<dc:description>{text_escape(self.record.description)}</dc:description>')

        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />',
            '<item id="css" href="styles/book.css" media-type="text/css" />',
        ]
        for chapter in self.chapters:
            manifest.append(
                f'<item id="chapter-{chapter.index + 1:03d}" href="text/{chapter.filename}" '
                f'media-type="application/xhtml+xml" />'
            )
        for index, href in enumerate(sorted(self.images), start=1):
            if href.endswith(".png"):
                media_type = "image/png"
            elif href.endswith(".svg"):
                media_type = "image/svg+xml"
            else:
                media_type = "image/jpeg"
            properties = ' properties="cover-image"' if href == self.cover_href else ""
            manifest.append(
                f'<item id="image-{index:03d}" href="{attr_escape(href)}" '
                f'media-type="{media_type}"{properties} />'
            )
        for index, href in enumerate(sorted(font_hrefs), start=1):
            manifest.append(
                f'<item id="font-{index:02d}" href="{attr_escape(href)}" media-type="font/ttf" />'
            )
        spine = "".join(
            f'<itemref idref="chapter-{chapter.index + 1:03d}" />' for chapter in self.chapters
        )
        document = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="pub-id" prefix="rendition: http://www.idpf.org/vocab/rendition/#">
<metadata>{''.join(metadata)}</metadata>
<manifest>{''.join(manifest)}</manifest>
<spine toc="ncx" page-progression-direction="ltr">{spine}</spine>
<guide>
  <reference type="cover" title="Cover" href="text/{self.chapters[self.cover_chapter_index].filename}" />
  <reference type="toc" title="Table of Contents" href="nav.xhtml" />
</guide>
</package>
'''
        return document.encode("utf-8")

    def build(self) -> tuple[dict[str, bytes], dict[str, Any]]:
        identifier, isbn = self.primary_identifier()
        self.emitted_positions.clear()
        for chapter in self.chapters:
            self.files[f"OEBPS/text/{chapter.filename}"] = self.render_chapter(chapter)
        self.files.update({f"OEBPS/{href}": content for href, content in self.images.items()})
        fonts = self.font_files()
        self.files.update({f"OEBPS/{href}": content for href, content in fonts.items()})
        self.files["OEBPS/styles/book.css"] = self.build_css()
        self.files["OEBPS/nav.xhtml"] = self.build_nav()
        self.files["OEBPS/toc.ncx"] = self.build_ncx(identifier)
        self.files["OEBPS/package.opf"] = self.build_opf(identifier, isbn, list(fonts))
        self.files["META-INF/container.xml"] = b'''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml" /></rootfiles>
</container>
'''
        report = {
            "book_id": self.book_id,
            "title": self.record.title,
            "chapters": len(self.chapters),
            "blocks": sum(len(chapter.blocks) for chapter in self.chapters),
            "images": len(self.images),
            "fonts": len(fonts),
            "internal_links": sum(
                1
                for value in self.metadatas.values()
                if isinstance(value, dict) and value.get("href") and not value.get("external_link")
            ),
            "anchors": len(self.position_ids),
            "pages": len(self.page_targets),
            "identifier": identifier,
            "cover_source": self.cover_source,
        }
        return self.files, report


def zip_write(output: Path, files: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        mimetype = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        mimetype.compress_type = zipfile.ZIP_STORED
        mimetype.external_attr = 0o100644 << 16
        archive.writestr(mimetype, b"application/epub+zip")
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name], compresslevel=9)


def validate_epub(output: Path) -> dict[str, Any]:
    errors: list[str] = []
    with zipfile.ZipFile(output) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        if not infos or infos[0].filename != "mimetype":
            errors.append("mimetype não é a primeira entrada do ZIP")
        elif infos[0].compress_type != zipfile.ZIP_STORED:
            errors.append("mimetype está comprimido")
        if archive.read("mimetype") != b"application/epub+zip":
            errors.append("mimetype inválido")

        xml_names = [
            name
            for name in names
            if name.endswith((".xml", ".opf", ".xhtml", ".ncx"))
        ]
        roots: dict[str, ET.Element] = {}
        ids: dict[str, set[str]] = {}
        for name in xml_names:
            try:
                root = ET.fromstring(archive.read(name))
                roots[name] = root
                ids[name] = {
                    element.attrib["id"] for element in root.iter() if "id" in element.attrib
                }
            except ET.ParseError as exc:
                errors.append(f"XML inválido em {name}: {exc}")

        for name, root in roots.items():
            if not name.endswith(".xhtml"):
                continue
            base = PurePosixPath(name).parent
            for element in root.iter():
                for attribute in ("href", "src"):
                    value = element.attrib.get(attribute)
                    if not value or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
                        continue
                    path_part, _, fragment = value.partition("#")
                    target = str((base / path_part).as_posix()) if path_part else name
                    target = posixpath.normpath(target)
                    if target not in names:
                        errors.append(f"Referência ausente em {name}: {value}")
                    elif fragment and fragment not in ids.get(target, set()):
                        errors.append(f"Fragmento ausente em {name}: {value}")

        opf_name = "OEBPS/package.opf"
        opf_root = roots.get(opf_name)
        if opf_root is not None:
            opf_ns = {"opf": "http://www.idpf.org/2007/opf"}
            manifest_ids = {
                item.attrib.get("id"): item.attrib.get("href")
                for item in opf_root.findall(".//opf:manifest/opf:item", opf_ns)
            }
            for item_id, href in manifest_ids.items():
                if not href or f"OEBPS/{href}" not in names:
                    errors.append(f"Item do manifesto ausente: {item_id} -> {href}")
            for itemref in opf_root.findall(".//opf:spine/opf:itemref", opf_ns):
                if itemref.attrib.get("idref") not in manifest_ids:
                    errors.append(f"Spine aponta para id inexistente: {itemref.attrib.get('idref')}")

    if errors:
        raise ConversionError("Validação interna falhou:\n- " + "\n- ".join(errors[:50]))
    return {
        "zip_entries": len(infos),
        "xml_documents": len(xml_names),
        "internal_validation": "ok",
    }


def default_output_name(title: str) -> str:
    clean = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", title).strip(" .")
    return (clean[:180] or "book") + ".epub"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconstrói um EPUB 3 fiel a partir do cache local autorizado do Everand."
    )
    parser.add_argument(
        "--library-root",
        type=Path,
        default=Path.home() / "AppData/Roaming/.EpuborEverand/library",
        help="Pasta library que contém app_document_cache e os bancos locais.",
    )
    parser.add_argument("--book-id", help="ID numérico do livro. Detectado se houver apenas um.")
    parser.add_argument("--output", type=Path, help="Arquivo EPUB de saída.")
    return parser.parse_args(argv)


def discover_book_id(cache_root: Path) -> str:
    candidates = sorted(
        path.name
        for path in cache_root.iterdir()
        if path.is_dir() and path.name.isdigit() and (path / "toc.json").is_file()
    )
    if len(candidates) != 1:
        raise ConversionError(
            "Informe --book-id; foram encontrados: " + (", ".join(candidates) or "nenhum")
        )
    return candidates[0]


def convert_book(library_root: Path, book_id: str, output: Path) -> dict[str, Any]:
    """Convert one locally entitled Everand cache item and return its audit report."""
    library_root = library_root.resolve()
    output = output.resolve()
    if not book_id.isdigit():
        raise ConversionError("O ID do livro deve conter apenas dígitos.")
    cache_root = library_root / "app_document_cache"
    book_root = cache_root / book_id
    if not book_root.is_dir():
        raise ConversionError(f"Livro não encontrado no cache: {book_root}")

    record = load_book_record(library_root, book_id)
    verify_entitlement(library_root, book_id, record)
    key = load_real_key(library_root, book_id)
    renderer = EpubRenderer(book_root, library_root, book_id, key, record)
    files, report = renderer.build()
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        zip_write(temporary, files)
        validation = validate_epub(temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    report.update(validation)
    report["output"] = str(output)
    report["bytes"] = output.stat().st_size
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    library_root = args.library_root.resolve()
    cache_root = library_root / "app_document_cache"
    if not cache_root.is_dir():
        raise ConversionError(f"Cache não encontrado: {cache_root}")
    book_id = args.book_id or discover_book_id(cache_root)
    if not book_id.isdigit():
        raise ConversionError("--book-id deve conter apenas dígitos.")
    book_root = cache_root / book_id
    if not book_root.is_dir():
        raise ConversionError(f"Livro não encontrado no cache: {book_root}")

    record = load_book_record(library_root, book_id)
    output = (args.output or (Path.cwd() / default_output_name(record.title))).resolve()
    report = convert_book(library_root, book_id, output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConversionError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        raise SystemExit(1)
