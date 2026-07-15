from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from everand_to_epub import ConversionError

from .adb_client import AdbClient, AdbError
from .catalog import CachedBook, convert_selected, latest_snapshot, scan_library, snapshot_description
from .version import APP_NAME, APP_VERSION


class JobThread(QThread):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Callable[[Callable[[int, str], None]], object]) -> None:
        super().__init__()
        self.job = job

    def run(self) -> None:
        try:
            result = self.job(lambda percent, message: self.progress.emit(percent, message))
        except (AdbError, ConversionError, OSError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Falha inesperada:\n" + traceback.format_exc(limit=8))
        else:
            self.succeeded.emit(result)


class BookCard(QFrame):
    def __init__(self, book: CachedBook) -> None:
        super().__init__()
        self.book = book
        self.setObjectName("bookCard")
        self.setProperty("eligible", book.eligible)
        self.setMinimumHeight(92)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(book.eligible)
        self.checkbox.setEnabled(book.eligible)
        self.checkbox.setAccessibleName(f"Selecionar {book.title}")
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignTop)

        icon = QLabel("E")
        icon.setObjectName("bookIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(50, 62)
        layout.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(4)
        title = QLabel(book.title)
        title.setObjectName("bookTitle")
        title.setWordWrap(True)
        author = QLabel(book.author)
        author.setObjectName("bookAuthor")
        status = QLabel(
            f"{'Acesso integral confirmado' if book.eligible else 'Não elegível'}  •  {book.size_label}  •  ID {book.book_id}"
        )
        status.setObjectName("bookStatus" if book.eligible else "bookWarning")
        status.setToolTip(book.status)
        text.addWidget(title)
        text.addWidget(author)
        text.addWidget(status)
        layout.addLayout(text, 1)

    def matches(self, query: str) -> bool:
        haystack = f"{self.book.title} {self.book.author} {self.book.book_id}".casefold()
        return query.casefold() in haystack


def app_icon() -> QIcon:
    canvas = QPixmap(128, 128)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#f59e0b"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(10, 10, 108, 108, 24, 24)
    painter.setPen(QColor("#111827"))
    font = QFont("Segoe UI", 58, QFont.Weight.Black)
    painter.setFont(font)
    painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, "E")
    painter.end()
    return QIcon(canvas)


class MainWindow(QMainWindow):
    def __init__(self, data_root: Path) -> None:
        super().__init__()
        self.data_root = data_root
        self.snapshots_root = data_root / "snapshots"
        self.settings = QSettings("Everand EPUB Studio", "Everand EPUB Studio")
        self.library_root: Path | None = None
        self.books: list[CachedBook] = []
        self.cards: list[BookCard] = []
        self.active_job: JobThread | None = None

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(app_icon())
        self.resize(1180, 760)
        self.setMinimumSize(900, 620)
        self._build_ui()
        self._apply_style()
        self._load_initial_library()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 18, 28, 18)
        brand = QLabel("EVERAND  /  EPUB STUDIO")
        brand.setObjectName("brand")
        header_layout.addWidget(brand)
        header_layout.addStretch()
        self.device_status = QLabel("LDPlayer: aguardando")
        self.device_status.setObjectName("deviceStatus")
        header_layout.addWidget(self.device_status)
        outer.addWidget(header)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        outer.addLayout(content, 1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(315)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 24, 22, 22)
        side.setSpacing(12)
        library_label = QLabel("SUA BIBLIOTECA")
        library_label.setObjectName("sectionLabel")
        side.addWidget(library_label)
        self.collect_button = QPushButton("Atualizar do LDPlayer")
        self.collect_button.setObjectName("primaryButton")
        self.collect_button.clicked.connect(self.collect_from_ldplayer)
        side.addWidget(self.collect_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por título ou autor")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_books)
        side.addWidget(self.search)
        self.library_summary = QLabel("Nenhum snapshot carregado")
        self.library_summary.setObjectName("muted")
        self.library_summary.setWordWrap(True)
        side.addWidget(self.library_summary)
        side.addSpacerItem(QSpacerItem(10, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        notice = QLabel(
            "Uso local autorizado\n\nA reconstrução só é liberada quando o cache confirma acesso integral e permissão de download."
        )
        notice.setObjectName("notice")
        notice.setWordWrap(True)
        side.addWidget(notice)
        content.addWidget(sidebar)

        main = QFrame()
        main.setObjectName("mainPanel")
        main.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(30, 26, 30, 24)
        main_layout.setSpacing(14)

        output_title = QLabel("Pasta de saída")
        output_title.setObjectName("sectionLabelDark")
        main_layout.addWidget(output_title)
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        documents = Path.home() / "Documents" / "EPUBs Everand"
        self.output_edit.setText(str(self.settings.value("output_dir", str(documents))))
        self.output_edit.editingFinished.connect(self._save_output)
        output_row.addWidget(self.output_edit, 1)
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self.choose_output)
        output_row.addWidget(browse)
        open_folder = QPushButton("Abrir pasta")
        open_folder.clicked.connect(self.open_output)
        output_row.addWidget(open_folder)
        main_layout.addLayout(output_row)

        books_header = QHBoxLayout()
        heading = QLabel("Livros baixados no Everand")
        heading.setObjectName("heading")
        books_header.addWidget(heading)
        books_header.addStretch()
        self.count_label = QLabel("0 livros")
        self.count_label.setObjectName("mutedDark")
        books_header.addWidget(self.count_label)
        main_layout.addLayout(books_header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.cards_layout = QVBoxLayout(self.scroll_content)
        self.cards_layout.setContentsMargins(2, 2, 8, 2)
        self.cards_layout.setSpacing(10)
        self.empty_label = QLabel(
            "Abra o Everand no LDPlayer, baixe um livro para leitura offline e clique em “Atualizar do LDPlayer”."
        )
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.cards_layout.addWidget(self.empty_label, 1)
        self.scroll.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        main_layout.addWidget(self.progress)
        self.activity = QTextEdit()
        self.activity.setObjectName("activity")
        self.activity.setReadOnly(True)
        self.activity.setMaximumHeight(82)
        self.activity.setPlaceholderText("O andamento aparecerá aqui.")
        main_layout.addWidget(self.activity)

        action_row = QHBoxLayout()
        self.select_all = QCheckBox("Selecionar elegíveis")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self.toggle_all)
        action_row.addWidget(self.select_all)
        action_row.addStretch()
        self.convert_button = QPushButton("Reconstruir EPUB selecionados")
        self.convert_button.setObjectName("convertButton")
        self.convert_button.clicked.connect(self.convert_books)
        self.convert_button.setEnabled(False)
        action_row.addWidget(self.convert_button)
        main_layout.addLayout(action_row)
        content.addWidget(main, 1)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            * { font-family: 'Segoe UI'; font-size: 10.5pt; }
            #root, #mainPanel { background: #f4f6f8; }
            #header { background: #111827; border-bottom: 3px solid #f59e0b; }
            #brand { color: white; font-weight: 800; font-size: 14pt; letter-spacing: 1px; }
            #deviceStatus { color: #d1fae5; background: #064e3b; padding: 7px 12px; border-radius: 12px; }
            #sidebar { background: #1f2937; }
            #sectionLabel { color: #f59e0b; font-weight: 800; font-size: 9pt; letter-spacing: 1px; }
            #sectionLabelDark { color: #374151; font-weight: 700; }
            #heading { color: #111827; font-weight: 750; font-size: 17pt; }
            #muted { color: #9ca3af; font-size: 9pt; }
            #mutedDark { color: #6b7280; }
            #notice { color: #cbd5e1; background: #111827; padding: 14px; border-radius: 8px; font-size: 9pt; }
            QLineEdit { background: white; color: #111827; border: 1px solid #d1d5db; border-radius: 6px; padding: 9px 11px; }
            #sidebar QLineEdit { background: #374151; color: white; border-color: #4b5563; }
            QPushButton { background: white; color: #1f2937; border: 1px solid #cbd5e1; border-radius: 6px; padding: 9px 15px; font-weight: 650; }
            QPushButton:hover { border-color: #f59e0b; background: #fffaf0; }
            QPushButton:disabled { color: #9ca3af; background: #e5e7eb; border-color: #e5e7eb; }
            #primaryButton { background: #f59e0b; color: #111827; border: none; padding: 12px; }
            #primaryButton:hover { background: #fbbf24; }
            #convertButton { background: #111827; color: white; border: none; padding: 12px 22px; }
            #convertButton:hover { background: #273449; }
            #bookCard { background: white; border: 1px solid #e5e7eb; border-radius: 9px; }
            #bookCard:hover { border-color: #f59e0b; }
            #bookIcon { color: #111827; background: #f59e0b; border-radius: 5px; font-size: 20pt; font-weight: 900; }
            #bookTitle { color: #111827; font-weight: 700; font-size: 11.5pt; }
            #bookAuthor { color: #4b5563; }
            #bookStatus { color: #047857; font-size: 8.5pt; }
            #bookWarning { color: #b45309; font-size: 8.5pt; }
            #emptyState { color: #6b7280; padding: 50px; font-size: 11pt; }
            QProgressBar { background: #e5e7eb; border: none; border-radius: 3px; height: 6px; }
            QProgressBar::chunk { background: #f59e0b; border-radius: 3px; }
            #activity { background: #111827; color: #d1d5db; border: none; border-radius: 6px; padding: 8px; font-family: Consolas; font-size: 9pt; }
            QScrollArea { background: transparent; }
            QCheckBox { color: #374151; spacing: 8px; }
            #sidebar QCheckBox { color: white; }
            """
        )

    def _load_initial_library(self) -> None:
        saved = self.settings.value("library_root", "")
        candidate = Path(str(saved)) if saved else latest_snapshot(self.snapshots_root)
        if candidate and candidate.is_dir():
            self.load_library(candidate)
        else:
            self.refresh_cards([])

    def _save_output(self) -> None:
        self.settings.setValue("output_dir", self.output_edit.text().strip())

    def choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Escolha a pasta dos EPUBs", self.output_edit.text())
        if selected:
            self.output_edit.setText(selected)
            self._save_output()

    def open_output(self) -> None:
        output = Path(self.output_edit.text().strip()).expanduser()
        output.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.resolve())))

    def log(self, message: str) -> None:
        self.activity.append(message)

    def set_busy(self, busy: bool) -> None:
        self.collect_button.setEnabled(not busy)
        self.convert_button.setEnabled(not busy and any(book.eligible for book in self.books))

    def start_job(self, job: Callable, success: Callable[[object], None]) -> None:
        if self.active_job and self.active_job.isRunning():
            return
        self.set_busy(True)
        self.progress.setValue(0)
        thread = JobThread(job)
        self.active_job = thread
        thread.progress.connect(lambda percent, message: (self.progress.setValue(percent), self.log(message)))
        thread.succeeded.connect(success)
        thread.failed.connect(self.job_failed)
        thread.finished.connect(lambda: self.set_busy(False))
        thread.finished.connect(self._job_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _job_finished(self) -> None:
        self.active_job = None

    def job_failed(self, message: str) -> None:
        self.progress.setValue(0)
        self.log("Falha: " + message)
        QMessageBox.critical(self, "Não foi possível concluir", message)

    def collect_from_ldplayer(self) -> None:
        self.log("Iniciando coleta segura do LDPlayer…")

        def job(progress: Callable[[int, str], None]) -> Path:
            return AdbClient().collect_snapshot(self.snapshots_root, progress=progress)

        self.start_job(job, self.collection_succeeded)

    def collection_succeeded(self, result: object) -> None:
        snapshot = Path(str(result))
        self.device_status.setText("LDPlayer: conectado")
        self.load_library(snapshot)
        self.progress.setValue(100)
        self.log(f"Snapshot pronto: {snapshot.name}")

    def load_library(self, snapshot: Path) -> None:
        self.library_root = snapshot
        self.device_status.setText("LDPlayer: snapshot carregado")
        self.settings.setValue("library_root", str(snapshot))
        self.books = scan_library(snapshot)
        self.library_summary.setText(snapshot_description(snapshot))
        self.refresh_cards(self.books)

    def refresh_cards(self, books: list[CachedBook]) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.cards = []
        if not books:
            self.empty_label = QLabel(
                "Nenhum ebook offline foi encontrado. Baixe um livro no Everand e atualize a biblioteca."
            )
            self.empty_label.setObjectName("emptyState")
            self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_label.setWordWrap(True)
            self.cards_layout.addWidget(self.empty_label, 1)
        else:
            for book in books:
                card = BookCard(book)
                self.cards.append(card)
                self.cards_layout.addWidget(card)
            self.cards_layout.addStretch()
        eligible = sum(book.eligible for book in books)
        self.count_label.setText(f"{len(books)} livro(s) • {eligible} elegível(is)")
        busy = bool(self.active_job and self.active_job.isRunning())
        self.convert_button.setEnabled(eligible > 0 and not busy)
        self.filter_books(self.search.text())

    def filter_books(self, query: str) -> None:
        for card in self.cards:
            card.setVisible(card.matches(query.strip()))

    def toggle_all(self, checked: bool) -> None:
        for card in self.cards:
            if card.book.eligible and card.isVisible():
                card.checkbox.setChecked(checked)

    def convert_books(self) -> None:
        if not self.library_root:
            return
        selected = [card.book for card in self.cards if card.checkbox.isChecked() and card.book.eligible]
        if not selected:
            QMessageBox.information(self, "Seleção vazia", "Selecione ao menos um livro elegível.")
            return
        output = Path(self.output_edit.text().strip()).expanduser()
        self._save_output()
        library = self.library_root
        self.log(f"Reconstruindo {len(selected)} livro(s)…")

        def job(progress: Callable[[int, str], None]) -> list[dict]:
            return convert_selected(library, selected, output, progress)

        self.start_job(job, self.conversion_succeeded)

    def conversion_succeeded(self, result: object) -> None:
        reports = list(result) if isinstance(result, list) else []
        self.progress.setValue(100)
        total = len(reports)
        self.log(f"Concluído: {total} EPUB(s) reconstruído(s) e validado(s).")
        QMessageBox.information(
            self,
            "EPUB pronto",
            f"{total} arquivo(s) foram reconstruído(s) e passaram pela validação interna.",
        )


def run_gui(data_root: Path) -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Everand EPUB Studio")
    app.setWindowIcon(app_icon())
    window = MainWindow(data_root)
    window.show()
    return app.exec()


def smoke_test_ui(data_root: Path, screenshot: Path) -> int:
    """Construct and render the packaged UI without requiring user interaction."""
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(app_icon())
    window = MainWindow(data_root)
    window.move(-1800, 40)
    window.show()
    app.processEvents()
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    canvas = QImage(window.size(), QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#f4f6f8"))
    painter = QPainter(canvas)
    window.render(painter, QPoint())
    painter.end()
    if not canvas.save(str(screenshot)):
        window.close()
        raise OSError(f"Não foi possível salvar a captura de teste: {screenshot}")
    window.close()
    app.processEvents()
    return 0
