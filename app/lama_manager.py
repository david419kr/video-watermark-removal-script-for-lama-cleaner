from __future__ import annotations

from dataclasses import dataclass
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


class LamaCleanerManager:
    def __init__(self, paths: Paths, log_fn: Callable[[str], None]) -> None:
        self._paths = paths
        self._log = log_fn
        self._instances: list[ManagedInstance] = []
        self._lama_exe = self._resolve_lama_executable()
        self._logs_dir = self._paths.workspace_root / "lama_logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)

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

    @staticmethod
    def _is_port_open(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def _is_port_free(port: int) -> bool:
        return not LamaCleanerManager._is_port_open(port)

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
        if not self._is_port_free(port):
            raise RuntimeError(
                f"Cannot start lama-cleaner on port {port}: port already in use. "
                "Close the conflicting process or change instance count."
            )

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

        started = self._wait_for_port(port, timeout_sec=15)
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

    def _wait_for_port(self, port: int, timeout_sec: float) -> bool:
        start = time.time()
        while time.time() - start <= timeout_sec:
            if self._is_port_open(port):
                return True
            time.sleep(0.2)
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
