from __future__ import annotations

import shutil
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web
from loguru import logger

DEFAULT_FILE_TTL_SECONDS = 3600
DEFAULT_MAX_FILES = 100


class TempStaticFileServer:
    def __init__(
        self,
        *,
        file_ttl_seconds: int = DEFAULT_FILE_TTL_SECONDS,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> None:
        self.file_ttl_seconds = file_ttl_seconds
        self.max_files = max_files
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None
        self._origin = ""

    @property
    def temp_dir(self) -> Path:
        if self._temp_dir is None:
            raise RuntimeError("static server has not been started")
        return Path(self._temp_dir.name)

    @property
    def origin(self) -> str:
        if not self._origin:
            raise RuntimeError("static server has not been started")
        return self._origin

    def file_url(self, file_path: str | Path) -> str:
        self._cleanup_temp_dir()
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)

        temp_dir = self.temp_dir.resolve()
        try:
            relative_path = path.relative_to(temp_dir)
        except ValueError:
            copied_path = self._copy_into_temp_dir(path)
            relative_path = copied_path.relative_to(temp_dir)

        return f"{self.origin}/{quote(relative_path.as_posix())}"

    async def start(self) -> None:
        if self._runner is not None:
            return

        self._temp_dir = tempfile.TemporaryDirectory(prefix="bub-xiaoai-")
        app = web.Application()
        app.router.add_static("/", self.temp_dir)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=0)
        try:
            await site.start()
        except Exception:
            await runner.cleanup()
            self._temp_dir.cleanup()
            self._temp_dir = None
            raise

        server = getattr(site, "_server", None)
        sockets = [] if server is None else list(server.sockets)
        if not sockets:
            await runner.cleanup()
            self._temp_dir.cleanup()
            self._temp_dir = None
            raise RuntimeError("failed to determine static server socket")

        host = _get_local_ip()
        port = sockets[0].getsockname()[1]
        self._runner = runner
        self._site = site
        self._origin = f"http://{host}:{port}"
        logger.info(
            "xiaoai.listener: serving temporary files from {} at {}",
            self.temp_dir,
            self._origin,
        )

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            self._origin = ""

        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def _copy_into_temp_dir(self, source_path: Path) -> Path:
        destination = self.temp_dir / source_path.name
        if destination.exists() and not source_path.samefile(destination):
            destination = self._deduplicated_path(source_path.name)
        shutil.copy2(source_path, destination)
        return destination

    def _deduplicated_path(self, file_name: str) -> Path:
        candidate = Path(file_name)
        stem = candidate.stem
        suffix = candidate.suffix
        index = 1
        while True:
            destination = self.temp_dir / f"{stem}-{index}{suffix}"
            if not destination.exists():
                return destination
            index += 1

    def _cleanup_temp_dir(self) -> None:
        if self._temp_dir is None:
            return

        files = [path for path in self.temp_dir.iterdir() if path.is_file()]
        if not files:
            return

        now = time.time()
        ttl = max(self.file_ttl_seconds, 0)
        if ttl == 0:
            expired_files = files
        else:
            expired_files = [path for path in files if now - path.stat().st_mtime > ttl]
        for path in expired_files:
            self._unlink_if_exists(path)

        if self.max_files <= 0:
            return

        remaining_files = sorted(
            (path for path in self.temp_dir.iterdir() if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        overflow = len(remaining_files) - self.max_files
        if overflow <= 0:
            return

        for path in remaining_files[:overflow]:
            self._unlink_if_exists(path)

    def _unlink_if_exists(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return


def _get_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
