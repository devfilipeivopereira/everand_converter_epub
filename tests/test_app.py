from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from unittest import mock
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import everand_to_epub
from everand_app.adb_client import AdbClient, AdbError, parse_devices
from everand_app.catalog import CachedBook, filter_hidden_books, scan_library
from everand_to_epub import (
    BookRecord,
    Chapter,
    ConversionError,
    EpubRenderer,
    convert_book,
    decrypt_aes_ecb,
    generated_cover_svg,
    load_book_record,
    validate_epub,
    verify_entitlement,
)


class CoverFallbackTests(unittest.TestCase):
    def test_generated_cover_is_valid_svg(self) -> None:
        content = generated_cover_svg(
            "Um título & muito <especial>", "Autora & Companhia"
        )
        root = ET.fromstring(content)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertIn(b"Um t\xc3\xadtulo &amp; muito &lt;especial&gt;", content)

    def test_renderer_generates_cover_when_cache_has_no_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            renderer = object.__new__(EpubRenderer)
            renderer.chapters = [
                Chapter(
                    index=0,
                    title="Front Cover",
                    filename="chapter-001.xhtml",
                    source_dir=Path(temporary),
                    block_start=0,
                    block_end=0,
                    blocks=[],
                    body_type="cover",
                )
            ]
            renderer.key = bytes(16)
            renderer.images = {}
            renderer.image_map = {}
            renderer.cover_href = None
            renderer.cover_chapter_index = 0
            renderer.cover_source = "declared"
            renderer.record = BookRecord(
                title="Livro sem imagem",
                author="Autora",
                publisher="Editora",
                language="pt-BR",
                released_at=None,
                description="",
                unlocked=True,
                reader_type="epub",
                document_type="book",
            )

            renderer._load_images()

            self.assertEqual(renderer.cover_source, "generated")
            self.assertEqual(renderer.cover_href, "images/generated-cover.svg")
            ET.fromstring(renderer.images[renderer.cover_href])


class CatalogFilteringTests(unittest.TestCase):
    def test_hiding_book_only_filters_visible_catalog(self) -> None:
        books = [
            CachedBook("1", "Visível", "Autor", "", "pt", 10, True, "ok"),
            CachedBook("2", "Oculto", "Autor", "", "pt", 20, True, "ok"),
        ]
        visible = filter_hidden_books(books, {"2"})
        self.assertEqual([book.book_id for book in visible], ["1"])
        self.assertEqual([book.book_id for book in books], ["1", "2"])


class CatalogUiTests(unittest.TestCase):
    def test_exclude_and_restore_are_reversible(self) -> None:
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtCore import QSettings
            from PySide6.QtWidgets import QApplication, QMessageBox
            import everand_app.ui as ui
        except ImportError:
            self.skipTest("PySide6 não está disponível neste ambiente")

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = QSettings(
                str(root / "settings.ini"), QSettings.Format.IniFormat
            )
            with (
                mock.patch.object(ui, "QSettings", return_value=settings),
                mock.patch.object(
                    ui.QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
            ):
                window = ui.MainWindow(root)
                book = CachedBook(
                    "42", "Livro de teste", "Autora", "", "pt", 50, True, "ok"
                )
                window.all_books = [book]
                window._apply_hidden_filter()

                window.exclude_book_from_list("42")
                self.assertEqual(window.books, [])
                self.assertIn("42", window.hidden_book_ids)
                self.assertTrue(window.restore_button.isVisibleTo(window))

                window.restore_book("42")
                self.assertEqual([item.book_id for item in window.books], ["42"])
                self.assertNotIn("42", window.hidden_book_ids)
                window.close()
                app.processEvents()


class DeviceParsingTests(unittest.TestCase):
    def test_parses_emulator_and_ignores_daemon_lines(self) -> None:
        output = """* daemon started successfully
List of devices attached
emulator-5554 device product:ASUS_AI2205_A model:ASUS_AI2205_A transport_id:1

"""
        devices = parse_devices(output)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].serial, "emulator-5554")
        self.assertEqual(devices[0].model, "ASUS_AI2205_A")


class ArchiveSafetyTests(unittest.TestCase):
    def test_rejects_parent_traversal(self) -> None:
        client = object.__new__(AdbClient)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.tar"
            payload = b"unsafe"
            with tarfile.open(archive, "w") as bundle:
                member = tarfile.TarInfo("../outside.txt")
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
            with self.assertRaises(AdbError):
                client._safe_extract(archive, root / "output")
            self.assertFalse((root / "outside.txt").exists())


class NativeCryptoTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows CNG é específico do Windows")
    def test_windows_cng_fallback_matches_aes_ecb(self) -> None:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = bytes(range(16))
        payload = b"Everand EPUB Studio"
        padding = 16 - len(payload) % 16
        padded = payload + bytes([padding]) * padding
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        original = everand_to_epub.Cipher
        try:
            everand_to_epub.Cipher = None
            self.assertEqual(decrypt_aes_ecb(encrypted, key, Path("fixture.bin")), payload)
        finally:
            everand_to_epub.Cipher = original


class RealSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configured = os.environ.get("EVERAND_TEST_LIBRARY")
        cls.library = Path(configured) if configured else None

    def test_catalog_and_entitlement(self) -> None:
        if not self.library or not self.library.is_dir():
            self.skipTest("EVERAND_TEST_LIBRARY não foi configurado")
        books = scan_library(self.library)
        self.assertGreaterEqual(len(books), 1)
        self.assertTrue(any(book.eligible for book in books))

    def test_refuses_record_not_marked_as_unlocked(self) -> None:
        if not self.library or not self.library.is_dir():
            self.skipTest("EVERAND_TEST_LIBRARY não foi configurado")
        book = next(book for book in scan_library(self.library) if book.eligible)
        record = replace(load_book_record(self.library, book.book_id), unlocked=False)
        with self.assertRaises(ConversionError):
            verify_entitlement(self.library, book.book_id, record)

    def test_full_conversion_and_internal_validation(self) -> None:
        if not self.library or not self.library.is_dir():
            self.skipTest("EVERAND_TEST_LIBRARY não foi configurado")
        book = next(book for book in scan_library(self.library) if book.eligible)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "test.epub"
            report = convert_book(self.library, book.book_id, output)
            self.assertEqual(report["internal_validation"], "ok")
            self.assertGreater(report["chapters"], 0)
            self.assertGreater(report["images"], 0)
            self.assertEqual(validate_epub(output)["internal_validation"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
