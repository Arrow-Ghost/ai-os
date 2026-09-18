"""
The kill switch.

Design rule: the agent has no code that resists, delays or routes around a
stop. It only ever asks "should I stop?" and obeys. Three independent ways to
halt it, any one of which is enough:

  1. `touch .servant/STOP`  (or `python -m servant stop`)  -- checked before
     every single tool call and every loop turn.
  2. Ctrl-C -- SIGINT sets the same flag in memory.
  3. `pkill -f servant` -- the OS always wins.
"""

from __future__ import annotations

import signal
import threading
from pathlib import Path


class KillSwitch:
    def __init__(self, flag_file: Path):
        self.flag_file = Path(flag_file)
        self._event = threading.Event()
        self._reason = ""

    # -- queries -----------------------------------------------------------
    @property
    def engaged(self) -> bool:
        if self._event.is_set():
            return True
        if self.flag_file.exists():
            self._reason = self.flag_file.read_text().strip() or "stop flag file present"
            return True
        return False

    @property
    def reason(self) -> str:
        return self._reason or "stopped"

    def check(self) -> None:
        """Raise if we should stop. Called at every gate."""
        if self.engaged:
            raise Stopped(self.reason)

    # -- controls ----------------------------------------------------------
    def engage(self, reason: str = "manual stop") -> None:
        self._reason = reason
        self._event.set()
        self.flag_file.parent.mkdir(parents=True, exist_ok=True)
        self.flag_file.write_text(reason)

    def release(self) -> None:
        self._event.clear()
        self._reason = ""
        self.flag_file.unlink(missing_ok=True)

    def install_signal_handler(self) -> None:
        def _handler(signum, frame):  # noqa: ARG001
            self._reason = "SIGINT from operator"
            self._event.set()
            print("\n[killswitch] stop requested -- finishing current step, then halting.")

        try:
            signal.signal(signal.SIGINT, _handler)
        except ValueError:
            pass  # not on the main thread; the flag file still works


class Stopped(Exception):
    """Raised when the kill switch is engaged. Never caught by the agent."""
