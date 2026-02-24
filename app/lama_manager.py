from __future__ import annotations

import csv
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from typing import Callable

from .config import AppConfig, Paths


@dataclass
class ManagedInstance:
    port: int
    process: subprocess.Popen
    log_path: Path | None = None


@dataclass(frozen=True)
class PortConflict:
    port: int
    pid: int
    process_name: str


if os.name == "nt":
    import ctypes.wintypes as wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class LamaCleanerManager:
    def __init__(
        self,
        paths: Paths,
        log_fn: Callable[[str], None],
        conflict_resolver: Callable[[PortConflict], bool] | None = None,
    ) -> None:
        self._paths = paths
        self._log = log_fn
        self._conflict_resolver = conflict_resolver
        self._instances: list[ManagedInstance] = []
        self._lama_exe = self._resolve_lama_executable()
        self._logs_dir = self._paths.workspace_root / "lama_logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._kernel32 = None
        self._job_handle = None
        self._init_process_job()

    def _resolve_lama_executable(self) -> Path:
        if self._paths.local_lama.exists():
            return self._paths.local_lama
        from_path = shutil.which("lama-cleaner")
        if from_path:
            return Path(from_path)
        raise FileNotFoundError(
            "lama-cleaner executable was not found. "
            "Install lama-cleaner in your current environment "
            "or provide it via .runtime/python310/Scripts."
        )

    def _init_process_job(self) -> None:
        if os.name != "nt":
            return
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = ctypes.c_void_p
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            kernel32.SetInformationJobObject.restype = ctypes.c_int
            kernel32.SetInformationJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

            job_handle = kernel32.CreateJobObjectW(None, None)
            if not job_handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                job_handle,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                error_code = ctypes.get_last_error()
                kernel32.CloseHandle(job_handle)
                raise OSError(error_code, "SetInformationJobObject failed")

            self._kernel32 = kernel32
            self._job_handle = job_handle
            self._log("Process job object initialized (KILL_ON_JOB_CLOSE enabled).")
        except Exception as exc:  # pylint: disable=broad-except
            self._kernel32 = None
            self._job_handle = None
            self._log(f"Warning: failed to initialize process job object: {exc}")

    def _assign_process_to_job(self, pid: int) -> None:
        if os.name != "nt" or self._kernel32 is None or not self._job_handle:
            return

        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        process_handle = self._kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not process_handle:
            raise OSError(ctypes.get_last_error(), f"OpenProcess failed for PID {pid}")

        try:
            ok = self._kernel32.AssignProcessToJobObject(self._job_handle, process_handle)
            if not ok:
                raise OSError(ctypes.get_last_error(), f"AssignProcessToJobObject failed for PID {pid}")
        finally:
            self._kernel32.CloseHandle(process_handle)

    @staticmethod
    def _is_port_open(port: int) -> bool:
        addresses = [("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)]
        for host, family in addresses:
            try:
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.25)
                    if sock.connect_ex((host, port)) == 0:
                        return True
            except OSError:
                continue
        return False

    @staticmethod
    def _is_port_free(port: int) -> bool:
        return not LamaCleanerManager._is_port_open(port)

    @staticmethod
    def _is_lama_process_name(process_name: str) -> bool:
        return process_name.strip().lower() == "lama-cleaner.exe"

    @staticmethod
    def _port_from_endpoint(endpoint: str) -> int | None:
        try:
            return int(endpoint.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return None

    def _pid_listening_on_port(self, port: int) -> int | None:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            if parts[0].upper() != "TCP":
                continue
            if parts[3].upper() != "LISTENING":
                continue
            local_port = self._port_from_endpoint(parts[1])
            if local_port != port:
                continue
            try:
                return int(parts[4])
            except ValueError:
                continue
        return None

    @staticmethod
    def _process_name_from_pid(pid: int) -> str:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return "unknown"
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return "unknown"
        first = lines[0]
        if first.upper().startswith("INFO:"):
            return "unknown"
        try:
            row = next(csv.reader([first]))
        except Exception:  # pylint: disable=broad-except
            return "unknown"
        if not row:
            return "unknown"
        return row[0]

    def _detect_port_conflict(self, port: int) -> PortConflict | None:
        pid = self._pid_listening_on_port(port)
        if pid is None:
            return None
        process_name = self._process_name_from_pid(pid)
        return PortConflict(port=port, pid=pid, process_name=process_name)

    @staticmethod
    def _terminate_pid(pid: int) -> bool:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            return True
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if "not found" in combined or "no running instance" in combined:
            return True
        return False

    def _wait_for_port_free(self, port: int, timeout_sec: float) -> bool:
        start = time.time()
        while time.time() - start <= timeout_sec:
            if self._is_port_free(port):
                return True
            time.sleep(0.2)
        return False

    def _handle_port_conflict_if_needed(self, port: int) -> None:
        if self._is_port_free(port):
            return

        conflict = self._detect_port_conflict(port)
        if conflict is None:
            raise RuntimeError(
                f"Cannot start lama-cleaner on port {port}: port already in use. "
                "Close the conflicting process or change instance count."
            )

        if not self._is_lama_process_name(conflict.process_name):
            raise RuntimeError(
                f"Cannot start lama-cleaner on port {port}: port in use by "
                f"{conflict.process_name} (PID {conflict.pid})."
            )

        should_terminate = False
        if self._conflict_resolver is not None:
            try:
                should_terminate = self._conflict_resolver(conflict)
            except Exception as exc:  # pylint: disable=broad-except
                self._log(f"Conflict resolver failed for port {port}: {exc}")

        if not should_terminate:
            raise RuntimeError(
                f"Cannot start lama-cleaner on port {port}: existing "
                f"{conflict.process_name} (PID {conflict.pid}) is still running."
            )

        self._log(
            f"Terminating existing {conflict.process_name} "
            f"(PID {conflict.pid}) on port {port}..."
        )
        if not self._terminate_pid(conflict.pid):
            raise RuntimeError(
                f"Failed to terminate existing {conflict.process_name} "
                f"(PID {conflict.pid}) on port {port}."
            )
        if not self._wait_for_port_free(port, timeout_sec=5):
            raise RuntimeError(
                f"Port {port} is still in use after terminating "
                f"{conflict.process_name} (PID {conflict.pid})."
            )

    def set_instance_count(self, target_count: int) -> None:
        if target_count < 1:
            raise ValueError("Instance count must be >= 1.")
        if target_count > AppConfig.MAX_INSTANCE_COUNT:
            raise ValueError(f"Instance count must be <= {AppConfig.MAX_INSTANCE_COUNT}.")

        self._remove_dead_instances()
        current_count = len(self._instances)

        if current_count == target_count:
            self._log(f"lama-cleaner instances already at target count: {target_count}")
            return

        if current_count < target_count:
            self._scale_up(target_count - current_count)
        else:
            self._scale_down(current_count - target_count)

    def ensure_default_instance(self) -> None:
        if not self._instances:
            self.set_instance_count(AppConfig.DEFAULT_INSTANCE_COUNT)

    def _scale_up(self, delta: int) -> None:
        for _ in range(delta):
            port = AppConfig.BASE_PORT + len(self._instances)
            self._start_instance(port)

    def _scale_down(self, delta: int) -> None:
        for _ in range(delta):
            inst = self._instances.pop()
            self._stop_instance(inst)

    def _start_instance(self, port: int) -> None:
        self._handle_port_conflict_if_needed(port)

        command = [
            str(self._lama_exe),
            "--model=lama",
            "--device=cuda",
            f"--port={port}",
        ]
        log_path = self._logs_dir / f"lama_{port}_{int(time.time())}.log"
        self._log(f"Starting lama-cleaner on port {port}...")
        with log_path.open("ab") as log_file:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        try:
            self._assign_process_to_job(process.pid)
        except Exception as exc:  # pylint: disable=broad-except
            self._log(f"Warning: failed to assign lama-cleaner PID {process.pid} to job object: {exc}")

        started = self._wait_for_port(
            port=port,
            process=process,
            timeout_sec=AppConfig.LAMA_START_TIMEOUT_SECONDS,
            log_path=log_path,
        )
        if not started:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            detail = self._read_log_tail(log_path)
            if detail:
                raise RuntimeError(f"lama-cleaner failed to open port {port}.\n{detail}")
            raise RuntimeError(f"lama-cleaner failed to open port {port}.")

        self._instances.append(ManagedInstance(port=port, process=process, log_path=log_path))
        self._log(f"lama-cleaner running on port {port}.")

    @staticmethod
    def _read_log_tail(path: Path, max_lines: int = 20) -> str:
        if not path.exists():
            return ""
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return ""

        tail = [line.strip() for line in lines if line.strip()][-max_lines:]
        if not tail:
            return ""
        return "Last log lines:\n" + "\n".join(tail)

    @staticmethod
    def _is_ready_log_emitted(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        markers = (
            "Press CTRL+C to quit",
            "Running on http://",
            "Uvicorn running on",
        )
        return any(marker in text for marker in markers)

    def _wait_for_port(
        self,
        port: int,
        process: subprocess.Popen,
        timeout_sec: float,
        log_path: Path,
    ) -> bool:
        start = time.time()
        while time.time() - start <= timeout_sec:
            if process.poll() is not None:
                return False
            if self._is_port_open(port):
                return True
            time.sleep(0.2)
        if process.poll() is None and self._is_ready_log_emitted(log_path):
            return True
        return False

    def _stop_instance(self, inst: ManagedInstance) -> None:
        process = inst.process
        if process.poll() is not None:
            self._log(f"lama-cleaner on port {inst.port} already exited.")
            return

        self._log(f"Stopping lama-cleaner on port {inst.port}...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self._log(f"Stopped lama-cleaner on port {inst.port}.")

    def _remove_dead_instances(self) -> None:
        alive: list[ManagedInstance] = []
        for inst in self._instances:
            if inst.process.poll() is None:
                alive.append(inst)
            else:
                self._log(f"Detected exited lama-cleaner process on port {inst.port}.")
        self._instances = alive

    def get_ports(self) -> list[int]:
        self._remove_dead_instances()
        return [inst.port for inst in self._instances]

    def stop_all(self) -> None:
        while self._instances:
            inst = self._instances.pop()
            self._stop_instance(inst)
