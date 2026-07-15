from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import uuid
import winreg
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable


PACKAGE = "com.scribd.app.reader0"
REMOTE_TEMP_PREFIX = "/data/local/tmp/everand-epub-studio-"
Progress = Callable[[int, str], None]


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""
    product: str = ""

    @property
    def label(self) -> str:
        name = self.model.replace("_", " ") if self.model else "Android"
        return f"{name} — {self.serial}"


@dataclass(frozen=True)
class Resource:
    remote_path: str
    required: bool = True

    @property
    def name(self) -> str:
        return PurePosixPath(self.remote_path).name


REQUIRED_RESOURCES = (
    Resource(f"/data/data/{PACKAGE}/app_document_cache"),
    Resource(f"/data/data/{PACKAGE}/shared_prefs/FILENAME_KEYS.xml"),
    Resource(f"/data/data/{PACKAGE}/databases/scribdData"),
    Resource(f"/data/data/{PACKAGE}/databases/Scribd.db"),
)


def _hidden_process_flags() -> tuple[int, subprocess.STARTUPINFO | None]:
    if os.name != "nt":
        return 0, None
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return subprocess.CREATE_NO_WINDOW, startup


def _registry_ldplayer_locations() -> Iterable[Path]:
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    hives = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    subkeys = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for hive in hives:
        for view in views:
            for subkey in subkeys:
                try:
                    root = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            child_name = winreg.EnumKey(root, index)
                            with winreg.OpenKey(root, child_name) as child:
                                display = str(winreg.QueryValueEx(child, "DisplayName")[0])
                                if "ldplayer" not in display.lower():
                                    continue
                                location = str(winreg.QueryValueEx(child, "InstallLocation")[0])
                                if location:
                                    yield Path(location)
                        except OSError:
                            continue


def adb_candidates(preferred: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if preferred:
        candidates.append(preferred)
    if os.environ.get("EVERAND_EPUB_ADB"):
        candidates.append(Path(os.environ["EVERAND_EPUB_ADB"]))
    for location in _registry_ldplayer_locations():
        candidates.extend((location / "adb.exe", location / "LDPlayer14" / "adb.exe"))
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for suffix in (r"LDPlayer\LDPlayer14\adb.exe", r"LDPlayer\LDPlayer9\adb.exe"):
            candidates.append(Path(f"{drive}:\\") / suffix)
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    candidates.append(local / "Android/Sdk/platform-tools/adb.exe")
    from_path = shutil.which("adb")
    if from_path:
        candidates.append(Path(from_path))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def find_adb(preferred: Path | None = None) -> Path:
    creationflags, startupinfo = _hidden_process_flags()
    for candidate in adb_candidates(preferred):
        if not candidate.is_file():
            continue
        try:
            result = subprocess.run(
                [str(candidate), "version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and "Android Debug Bridge" in result.stdout:
            return candidate.resolve()
    raise AdbError(
        "Não encontrei o ADB do LDPlayer. Instale/inicie o LDPlayer 9 ou 14 e ative a depuração ADB."
    )


def parse_devices(output: str) -> list[Device]:
    devices: list[Device] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        fields = {key: value for part in parts[2:] if ":" in part for key, value in [part.split(":", 1)]}
        devices.append(
            Device(
                serial=parts[0],
                state=parts[1],
                model=fields.get("model", ""),
                product=fields.get("product", ""),
            )
        )
    return devices


class AdbClient:
    def __init__(self, adb_path: Path | None = None) -> None:
        self.adb_path = find_adb(adb_path)

    def run(
        self,
        *args: str,
        serial: str | None = None,
        timeout: int = 60,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(self.adb_path)]
        if serial:
            command.extend(("-s", serial))
        command.extend(args)
        creationflags, startupinfo = _hidden_process_flags()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"O LDPlayer não respondeu a tempo: {' '.join(args)}") from exc
        except OSError as exc:
            raise AdbError(f"Não foi possível executar o ADB: {exc}") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise AdbError(detail or f"Falha no ADB ({result.returncode}).")
        return result

    def list_devices(self) -> list[Device]:
        self.run("start-server", timeout=20)
        return parse_devices(self.run("devices", "-l", timeout=20).stdout)

    def ensure_device(self, requested_serial: str | None = None) -> Device:
        devices = self.list_devices()
        if requested_serial:
            matching = [item for item in devices if item.serial == requested_serial]
        else:
            matching = [
                item
                for item in devices
                if item.state == "device"
                and (item.serial.startswith("emulator-") or re.match(r"^(127\.0\.0\.1|localhost):", item.serial))
            ]
            if not matching:
                matching = [item for item in devices if item.state == "device"]
        if not matching:
            raise AdbError(
                "Nenhum LDPlayer conectado. Inicie o emulador e habilite 'Depuração ADB' nas configurações."
            )
        device = matching[0]
        if device.state != "device":
            raise AdbError(f"O dispositivo {device.serial} está no estado '{device.state}'.")

        root_result = self.run("root", serial=device.serial, timeout=30, check=False)
        if root_result.returncode != 0 and "cannot run as root" in (root_result.stderr + root_result.stdout).lower():
            raise AdbError("Este LDPlayer não permite acesso root pelo ADB. Ative ROOT e reinicie o emulador.")
        self.run("wait-for-device", serial=device.serial, timeout=40)
        identity = self.run("shell", "id", serial=device.serial, timeout=15).stdout
        if "uid=0" not in identity:
            raise AdbError("O ADB está conectado, mas sem acesso root aos downloads privados do Everand.")
        package = self.run("shell", "pm", "path", PACKAGE, serial=device.serial, timeout=20, check=False)
        if package.returncode != 0 or "package:" not in package.stdout:
            raise AdbError("O aplicativo Everand não está instalado neste LDPlayer.")
        return device

    def _remote_exists(self, serial: str, path: str) -> bool:
        return self.run("shell", "test", "-e", path, serial=serial, timeout=15, check=False).returncode == 0

    def _safe_extract(self, archive: Path, destination: Path) -> None:
        destination_resolved = destination.resolve()
        with tarfile.open(archive, "r:") as bundle:
            for member in bundle.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                    raise AdbError(f"Arquivo inseguro recebido do emulador: {member.name}")
                target = (destination / Path(*name.parts)).resolve()
                if not target.is_relative_to(destination_resolved):
                    raise AdbError(f"Caminho inválido no pacote recebido: {member.name}")
            bundle.extractall(destination, filter="data")

    @staticmethod
    def _remove_owned_staging(path: Path, parent: Path) -> None:
        resolved = path.resolve()
        parent_resolved = parent.resolve()
        if resolved.parent != parent_resolved or not resolved.name.startswith(".collecting-"):
            raise AdbError(f"Recusa ao limpar pasta temporária inesperada: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)

    def collect_snapshot(
        self,
        snapshots_root: Path,
        serial: str | None = None,
        progress: Progress | None = None,
    ) -> Path:
        emit = progress or (lambda _percent, _message: None)
        snapshots_root = snapshots_root.resolve()
        snapshots_root.mkdir(parents=True, exist_ok=True)
        emit(2, "Localizando o LDPlayer…")
        device = self.ensure_device(serial)
        emit(8, f"Conectado a {device.label}")

        token = uuid.uuid4().hex[:12]
        remote_temp = REMOTE_TEMP_PREFIX + token
        staging = snapshots_root / f".collecting-{token}"
        staging.mkdir()
        was_running = bool(
            self.run("shell", "pidof", PACKAGE, serial=device.serial, timeout=10, check=False).stdout.strip()
        )
        completed = False
        try:
            if was_running:
                emit(11, "Pausando o Everand para criar uma cópia consistente…")
                self.run("shell", "am", "force-stop", PACKAGE, serial=device.serial, timeout=15)
                time.sleep(0.5)
            self.run("shell", "mkdir", "-p", remote_temp, serial=device.serial, timeout=15)

            for index, resource in enumerate(REQUIRED_RESOURCES):
                base_percent = 15 + index * 17
                if not self._remote_exists(device.serial, resource.remote_path):
                    if resource.required:
                        raise AdbError(f"Recurso necessário ausente no Everand: {resource.remote_path}")
                    continue
                remote = PurePosixPath(resource.remote_path)
                archive_name = f"{index:02d}-{resource.name}.tar"
                remote_archive = f"{remote_temp}/{archive_name}"
                local_archive = staging / archive_name
                emit(base_percent, f"Preparando {resource.name}…")
                self.run(
                    "shell",
                    "tar",
                    "-cf",
                    remote_archive,
                    "-C",
                    str(remote.parent),
                    remote.name,
                    serial=device.serial,
                    timeout=180,
                )
                emit(base_percent + 6, f"Copiando {resource.name}…")
                self.run("pull", remote_archive, str(local_archive), serial=device.serial, timeout=300)
                emit(base_percent + 11, f"Conferindo {resource.name}…")
                self._safe_extract(local_archive, staging)
                local_archive.unlink(missing_ok=True)

            emit(86, "Validando o snapshot…")
            expected = (
                staging / "app_document_cache",
                staging / "FILENAME_KEYS.xml",
                staging / "scribdData",
                staging / "Scribd.db",
            )
            if not expected[0].is_dir() or not all(path.is_file() for path in expected[1:]):
                raise AdbError("A cópia do Everand ficou incompleta; o snapshot anterior foi preservado.")

            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            final = snapshots_root / stamp
            if final.exists():
                final = snapshots_root / f"{stamp}_{token[:4]}"
            metadata = {
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "device": device.serial,
                "model": device.model,
                "adb": str(self.adb_path),
                "package": PACKAGE,
            }
            (staging / "snapshot.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            staging.rename(final)
            completed = True
            emit(100, "Biblioteca coletada com segurança.")
            return final
        finally:
            if remote_temp.startswith(REMOTE_TEMP_PREFIX):
                self.run("shell", "rm", "-rf", remote_temp, serial=device.serial, timeout=30, check=False)
            if was_running:
                self.run(
                    "shell",
                    "monkey",
                    "-p",
                    PACKAGE,
                    "-c",
                    "android.intent.category.LAUNCHER",
                    "1",
                    serial=device.serial,
                    timeout=25,
                    check=False,
                )
            if not completed and staging.exists():
                self._remove_owned_staging(staging, snapshots_root)
