from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import everand_to_epub
from everand_app.adb_client import AdbClient, AdbError, parse_devices
from everand_app.catalog import scan_library
from everand_to_epub import (
    ConversionError,
    convert_book,
    decrypt_aes_ecb,
    load_book_record,
    validate_epub,
    verify_entitlement,
)


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
