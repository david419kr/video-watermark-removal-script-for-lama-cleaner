from pathlib import Path


class AppConfig:
    BASE_PORT = 8080
    REQUEST_TIMEOUT_SECONDS = 600
    MAX_INSTANCE_COUNT = 8
    DEFAULT_INSTANCE_COUNT = 1

    @staticmethod
    def repo_root_from_file(file_path: Path) -> Path:
        return file_path.resolve().parents[2]


class Paths:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.ffmpeg = repo_root / "ffmpeg" / "bin" / "ffmpeg.exe"
        self.ffprobe = repo_root / "ffmpeg" / "bin" / "ffprobe.exe"

        self.workspace_root = repo_root / "gui_app" / "workspace"
        self.workspace_jobs = self.workspace_root / "jobs"
        self.workspace_masks = self.workspace_root / "masks"

        self.local_python = repo_root / ".runtime" / "python310" / "python.exe"
        self.local_lama = repo_root / ".runtime" / "python310" / "Scripts" / "lama-cleaner.exe"
