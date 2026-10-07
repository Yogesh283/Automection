"""
Per-session Xvfb display allocation.

Never uses :99 (reserved for the existing web frontend / legacy bot path).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Set

from infra.logging_safe import log_event


@dataclass
class DisplayHandle:
    display: str  # e.g. ":100"
    number: int
    session_id: Optional[str] = None
    process: Optional[subprocess.Popen] = None
    width: int = 1920
    height: int = 1080
    depth: int = 24
    simulated: bool = False

    @property
    def env_value(self) -> str:
        return self.display


class DisplayManager:
    """Allocate, start, verify, and release per-session Xvfb displays."""

    RESERVED_DISPLAYS: Set[int] = {99}

    def __init__(
        self,
        *,
        base: Optional[int] = None,
        width: int = 1920,
        height: int = 1080,
        depth: int = 24,
        max_displays: int = 40,
        allow_simulate: Optional[bool] = None,
    ):
        self.base = int(
            base
            if base is not None
            else (os.getenv("SESSION_DISPLAY_BASE", "100") or "100")
        )
        self.width = int(os.getenv("SESSION_DISPLAY_WIDTH", str(width)) or width)
        self.height = int(os.getenv("SESSION_DISPLAY_HEIGHT", str(height)) or height)
        self.depth = int(os.getenv("SESSION_DISPLAY_DEPTH", str(depth)) or depth)
        self.max_displays = max_displays
        if allow_simulate is None:
            # On non-Linux (or missing Xvfb), allocate numbers without starting Xvfb.
            allow_simulate = os.name != "posix" or shutil.which("Xvfb") is None
        self.allow_simulate = allow_simulate
        self._lock = threading.RLock()
        self._in_use: Dict[int, DisplayHandle] = {}

    def _used_numbers(self) -> Set[int]:
        return set(self._in_use.keys()) | set(self.RESERVED_DISPLAYS)

    def allocate(self, session_id: Optional[str] = None) -> DisplayHandle:
        with self._lock:
            used = self._used_numbers()
            number = self.base
            limit = self.base + self.max_displays
            while number in used:
                number += 1
                if number >= limit:
                    raise RuntimeError("No free session displays available")
            handle = DisplayHandle(
                display=f":{number}",
                number=number,
                session_id=session_id,
                width=self.width,
                height=self.height,
                depth=self.depth,
            )
            self._in_use[number] = handle
            log_event(
                "display_allocated",
                display=handle.display,
                sessionId=session_id,
            )
            return handle

    def start_xvfb(self, handle: DisplayHandle) -> DisplayHandle:
        with self._lock:
            if handle.number not in self._in_use:
                self._in_use[handle.number] = handle

            if self.allow_simulate:
                handle.simulated = True
                handle.process = None
                log_event(
                    "display_simulated",
                    display=handle.display,
                    sessionId=handle.session_id,
                    reason="Xvfb unavailable on this host",
                )
                return handle

            if handle.process is not None and handle.process.poll() is None:
                return handle

            cmd = [
                "Xvfb",
                handle.display,
                "-screen",
                "0",
                f"{handle.width}x{handle.height}x{handle.depth}",
                "-ac",
                "+extension",
                "RANDR",
                "-nolisten",
                "tcp",
            ]
            log_path = f"/tmp/xvfb{handle.display.replace(':', '_')}.log"
            log_file = open(log_path, "a", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except Exception:
                log_file.close()
                raise

            handle.process = process
            handle.simulated = False

            if not self.wait_until_alive(handle, timeout_sec=5.0):
                self._kill_process(process)
                log_file.close()
                raise RuntimeError(f"Xvfb failed to start on {handle.display}")

            log_event(
                "display_started",
                display=handle.display,
                sessionId=handle.session_id,
                pid=process.pid,
            )
            return handle

    def allocate_and_start(self, session_id: Optional[str] = None) -> DisplayHandle:
        handle = self.allocate(session_id=session_id)
        try:
            return self.start_xvfb(handle)
        except Exception:
            self.release(handle.display)
            raise

    def is_alive(self, display: str) -> bool:
        handle = self.get(display)
        if handle is None:
            return False
        if handle.simulated:
            return True
        if handle.process is None:
            return False
        if handle.process.poll() is not None:
            return False
        return self._xdisplay_responds(handle.display)

    def wait_until_alive(self, handle: DisplayHandle, timeout_sec: float = 5.0) -> bool:
        if handle.simulated:
            return True
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if handle.process is not None and handle.process.poll() is not None:
                return False
            if self._xdisplay_responds(handle.display):
                return True
            time.sleep(0.1)
        return self._xdisplay_responds(handle.display)

    def get(self, display: str) -> Optional[DisplayHandle]:
        number = self._parse_display(display)
        with self._lock:
            return self._in_use.get(number)

    def stop(self, display: str) -> None:
        with self._lock:
            handle = self.get(display)
            if handle is None:
                return
            if handle.process is not None:
                self._kill_process(handle.process)
                handle.process = None
            log_event(
                "display_stopped",
                display=display,
                sessionId=handle.session_id,
            )

    def release(self, display: str) -> None:
        with self._lock:
            number = self._parse_display(display)
            handle = self._in_use.pop(number, None)
            if handle is None:
                return
            if handle.process is not None:
                self._kill_process(handle.process)
                handle.process = None
            log_event(
                "display_released",
                display=display,
                sessionId=handle.session_id,
            )

    def stop_and_release(self, display: Optional[str]) -> None:
        if not display:
            return
        self.stop(display)
        self.release(display)

    def recover_dead(self) -> None:
        """Drop handles whose Xvfb process exited unexpectedly."""
        with self._lock:
            dead = []
            for number, handle in list(self._in_use.items()):
                if handle.simulated:
                    continue
                if handle.process is not None and handle.process.poll() is not None:
                    dead.append(number)
            for number in dead:
                handle = self._in_use.pop(number, None)
                if handle:
                    log_event(
                        "display_recovered_dead",
                        display=handle.display,
                        sessionId=handle.session_id,
                    )

    @staticmethod
    def _parse_display(display: str) -> int:
        text = (display or "").strip()
        if text.startswith(":"):
            text = text[1:]
        return int(text)

    @staticmethod
    def _kill_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                pass

    @staticmethod
    def _xdisplay_responds(display: str) -> bool:
        """Best-effort check that the X server accepts connections."""
        if shutil.which("xdpyinfo"):
            try:
                result = subprocess.run(
                    ["xdpyinfo", "-display", display],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,
                )
                return result.returncode == 0
            except Exception:
                return False
        # Fallback: lock file created by Xvfb
        try:
            number = int(display.lstrip(":"))
        except ValueError:
            return False
        lock = f"/tmp/.X{number}-lock"
        return os.path.exists(lock)


display_manager = DisplayManager()
