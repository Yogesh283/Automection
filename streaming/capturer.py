"""
Browser screen capture for an isolated session display.

Preferred path: capture the session Xvfb framebuffer only (never the host
physical screen). On hosts without X11/Xvfb tools, a synthetic capturer is
available for tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from typing import AsyncIterator, Deque, Optional, Tuple

import numpy as np

from infra.logging_safe import log_event


class BrowserScreenCapturer(ABC):
    """Captures frames from an isolated browser session display."""

    @abstractmethod
    async def start(
        self,
        *,
        session_id: str,
        display_name: Optional[str],
        profile_path: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def latest_frame(self) -> Optional[np.ndarray]:
        """Return latest RGB frame as uint8 HxWx3, or None."""
        raise NotImplementedError

    @abstractmethod
    def frames_produced(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def frames(self) -> AsyncIterator[bytes]:
        raise NotImplementedError
        yield b""  # pragma: no cover

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError


class NullBrowserScreenCapturer(BrowserScreenCapturer):
    async def start(self, *, session_id: str, display_name: Optional[str], profile_path: str) -> None:
        self.session_id = session_id
        self.display_name = display_name
        self.profile_path = profile_path

    def latest_frame(self) -> Optional[np.ndarray]:
        return None

    def frames_produced(self) -> int:
        return 0

    async def frames(self) -> AsyncIterator[bytes]:
        if False:  # pragma: no cover
            yield b""
        return

    async def stop(self) -> None:
        return None


class SyntheticFrameCapturer(BrowserScreenCapturer):
    """
    Produces real RGB frames without X11 (unit tests / Windows).
    Frames are not from Chrome; used only when Xvfb capture is unavailable.
    """

    def __init__(self, width: int = 640, height: int = 360, fps: float = 5.0):
        self.width = width
        self.height = height
        self.fps = fps
        self.session_id = ""
        self.display_name: Optional[str] = None
        self.profile_path = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._count = 0

    async def start(self, *, session_id: str, display_name: Optional[str], profile_path: str) -> None:
        self.session_id = session_id
        self.display_name = display_name
        self.profile_path = profile_path
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=f"synth-cap-{session_id}", daemon=True)
        self._thread.start()
        log_event("capturer_started", sessionId=session_id, backend="synthetic", display=display_name)

    def _run(self) -> None:
        interval = 1.0 / max(self.fps, 0.1)
        tick = 0
        while not self._stop.is_set():
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame[:, :, 0] = (tick * 3) % 255
            frame[:, :, 1] = 40
            frame[:, :, 2] = 180
            # Moving bar so tests can detect changing frames.
            x = tick % max(self.width - 20, 1)
            frame[:, x : x + 20, :] = 255
            with self._lock:
                self._latest = frame
                self._count += 1
            tick += 1
            time.sleep(interval)

    def latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._latest is None:
                return None
            return self._latest.copy()

    def frames_produced(self) -> int:
        with self._lock:
            return self._count

    async def frames(self) -> AsyncIterator[bytes]:
        while not self._stop.is_set():
            frame = self.latest_frame()
            if frame is not None:
                yield frame.tobytes()
            await _async_sleep(1.0 / max(self.fps, 0.1))

    async def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        log_event("capturer_stopped", sessionId=self.session_id, backend="synthetic")


class XvfbFramebufferCapturer(BrowserScreenCapturer):
    """
    Capture only the given Xvfb display via ffmpeg x11grab (preferred) or
    ImageMagick `import`. Never targets the host physical console.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        fps: float = 8.0,
    ):
        self.width = int(os.getenv("SESSION_DISPLAY_WIDTH", str(width)) or width)
        self.height = int(os.getenv("SESSION_DISPLAY_HEIGHT", str(height)) or height)
        self.fps = float(os.getenv("CAPTURE_FPS", str(fps)) or fps)
        self.session_id = ""
        self.display_name: Optional[str] = None
        self.profile_path = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._count = 0
        self._backend = "ffmpeg" if shutil.which("ffmpeg") else "import"

    async def start(self, *, session_id: str, display_name: Optional[str], profile_path: str) -> None:
        if not display_name:
            raise ValueError("display_name is required for Xvfb capture")
        self.session_id = session_id
        self.display_name = display_name
        self.profile_path = profile_path
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"xvfb-cap-{session_id}",
            daemon=True,
        )
        self._thread.start()
        log_event(
            "capturer_started",
            sessionId=session_id,
            backend=self._backend,
            display=display_name,
        )

    def _run(self) -> None:
        interval = 1.0 / max(self.fps, 0.1)
        while not self._stop.is_set():
            try:
                frame = self._grab_once()
                if frame is not None:
                    with self._lock:
                        self._latest = frame
                        self._count += 1
            except Exception as error:
                log_event(
                    "capturer_frame_error",
                    sessionId=self.session_id,
                    error=type(error).__name__,
                )
            time.sleep(interval)

    def _grab_once(self) -> Optional[np.ndarray]:
        display = self.display_name
        assert display
        if self._backend == "ffmpeg" and shutil.which("ffmpeg"):
            return self._grab_ffmpeg(display)
        return self._grab_imagemagick(display)

    def _grab_ffmpeg(self, display: str) -> Optional[np.ndarray]:
        # Capture only this X display; -nolisten tcp on Xvfb keeps it local.
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-video_size",
            f"{self.width}x{self.height}",
            "-i",
            f"{display}.0",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
            env={**os.environ, "DISPLAY": display},
        )
        expected = self.width * self.height * 3
        if result.returncode != 0 or len(result.stdout) < expected:
            # Fall back once if ffmpeg x11grab fails.
            self._backend = "import"
            return self._grab_imagemagick(display)
        arr = np.frombuffer(result.stdout[:expected], dtype=np.uint8)
        return arr.reshape((self.height, self.width, 3)).copy()

    def _grab_imagemagick(self, display: str) -> Optional[np.ndarray]:
        import_bin = shutil.which("import")
        if not import_bin:
            raise RuntimeError("Neither ffmpeg x11grab nor ImageMagick import is available")
        from PIL import Image
        import io

        cmd = [import_bin, "-display", display, "-window", "root", "png:-"]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
            env={**os.environ, "DISPLAY": display},
        )
        if result.returncode != 0 or not result.stdout:
            return None
        image = Image.open(io.BytesIO(result.stdout)).convert("RGB")
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        return np.asarray(image, dtype=np.uint8)

    def latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._latest is None:
                return None
            return self._latest.copy()

    def frames_produced(self) -> int:
        with self._lock:
            return self._count

    async def frames(self) -> AsyncIterator[bytes]:
        while not self._stop.is_set():
            frame = self.latest_frame()
            if frame is not None:
                yield frame.tobytes()
            await _async_sleep(1.0 / max(self.fps, 0.1))

    async def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        log_event(
            "capturer_stopped",
            sessionId=self.session_id,
            backend=self._backend,
            frames=self._count,
        )


def build_capturer(
    *,
    display_name: Optional[str],
    prefer_real: bool = True,
) -> BrowserScreenCapturer:
    """
    Choose capturer implementation.
    Real Xvfb capture only when tools exist; otherwise synthetic for tests.
    """
    can_real = (
        prefer_real
        and os.name == "posix"
        and display_name
        and (shutil.which("ffmpeg") or shutil.which("import"))
    )
    if can_real:
        return XvfbFramebufferCapturer()
    return SyntheticFrameCapturer()


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def wait_for_frames(capturer: BrowserScreenCapturer, *, min_frames: int = 1, timeout_sec: float = 10.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if capturer.frames_produced() >= min_frames and capturer.latest_frame() is not None:
            return True
        time.sleep(0.1)
    return False
