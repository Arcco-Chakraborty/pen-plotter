"""Threaded grbl connection.

Everything the GUI needs to talk to the controller lives here: a reader thread, a
status poller, and a streamer. Nothing in this module touches Tk - all traffic
comes back through `events`, a plain queue the GUI drains on its own timer.

Two things are deliberate:

* The port is opened with DTR and RTS deasserted, so connecting does NOT reset the
  Mega. This machine has no endstops, so machine zero only exists in RAM; a reset
  on connect would silently move the origin and invalidate a probed paper frame.

* Jobs are streamed with the character-counting protocol, not send-and-wait-for-ok.
  Waiting for an "ok" per line empties grbl's planner between segments, so the
  machine decelerates to a stop at every point of every curve. On a pen plotter
  that is not just slow - the pen dwells on the paper and blots at each stop.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

import serial
import serial.tools.list_ports

BAUD = 115200

# grbl's serial RX buffer. Keep the in-flight byte count below this and the
# planner stays fed. 128 is the stock RX_BUFFER_SIZE for the Mega build.
RX_BUFFER = 128

STATUS_RE = re.compile(r"<([^>]*)>")
POS_RE = re.compile(r"\b(MPos|WPos):(-?[\d.]+),(-?[\d.]+)(?:,(-?[\d.]+))?")
WCO_RE = re.compile(r"\bWCO:(-?[\d.]+),(-?[\d.]+)(?:,(-?[\d.]+))?")
BF_RE = re.compile(r"\bBf:(\d+),(\d+)")
SETTING_RE = re.compile(r"^\$(\d+)=(-?[\d.]+)")

# Realtime bytes. These jump the queue - grbl acts on them inside its serial ISR,
# so they work even when the RX buffer is full of a running job.
RT_STATUS = b"?"
RT_HOLD = b"!"
RT_RESUME = b"~"
RT_RESET = b"\x18"
RT_JOG_CANCEL = b"\x85"


@dataclass
class Status:
    state: str = "?"
    mpos: tuple[float, float] = (0.0, 0.0)
    wco: tuple[float, float] = (0.0, 0.0)
    feed: float = 0.0
    planner: int | None = None
    raw: str = ""


@dataclass
class JobProgress:
    sent: int = 0
    total: int = 0
    acked: int = 0
    state: str = "idle"  # idle | running | holding | paused | done | aborted
    note: str = ""


@dataclass
class Line:
    """One line of a job, plus whether the pen is up once it has executed.

    The flag is what makes "pause at the next pen-up" possible: the streamer can
    stop feeding at a boundary and let the buffer drain, leaving the pen off the
    paper instead of parked on it dribbling ink.
    """

    text: str
    pen_up_break: bool = False


class GrblLink:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.status = Status()
        self.progress = JobProgress()
        self.settings: dict[int, float] = {}  # filled in by any `$$` dump

        self._sp: serial.Serial | None = None
        self._wlock = threading.Lock()
        self._acks: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        self._job_thread: threading.Thread | None = None
        self._job_abort = threading.Event()
        self._job_hold = threading.Event()
        self._job_break = threading.Event()  # pause at next pen-up boundary
        self._job_resume = threading.Event()

    # ------------------------------------------------------------------ events
    def _emit(self, kind: str, payload: object = None) -> None:
        self.events.put((kind, payload))

    def log(self, msg: str, kind: str = "info") -> None:
        self._emit("log", (msg, kind))

    # -------------------------------------------------------------- connection
    @staticmethod
    def ports() -> list[str]:
        return sorted(p.device for p in serial.tools.list_ports.comports())

    @property
    def connected(self) -> bool:
        return self._sp is not None and self._sp.is_open

    def connect(self, port: str) -> bool:
        if self.connected:
            return True
        try:
            sp = serial.Serial()
            sp.port = port
            sp.baudrate = BAUD
            sp.timeout = 0.1
            # Both must be set before open() or pyserial pulses the line and the
            # board reboots. This is the whole reason for the deferred open.
            sp.dtr = False
            sp.rts = False
            sp.open()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            self.log(f"open {port} failed: {exc}", "err")
            return False

        self._sp = sp
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._reader, name="grbl-read", daemon=True),
            threading.Thread(target=self._poller, name="grbl-poll", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self.log(f"connected on {port} (board not reset)", "ok")
        self._emit("conn", True)
        return True

    def disconnect(self) -> None:
        self.abort_job(quiet=True)
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads = []
        if self._sp is not None:
            try:
                self._sp.close()
            except Exception:  # noqa: BLE001
                pass
        self._sp = None
        self.status = Status()
        self.log("disconnected")
        self._emit("conn", False)

    # ------------------------------------------------------------------ output
    def _write(self, data: bytes) -> bool:
        if not self.connected:
            return False
        try:
            with self._wlock:
                self._sp.write(data)  # type: ignore[union-attr]
            return True
        except Exception as exc:  # noqa: BLE001
            self.log(f"write failed: {exc}", "err")
            return False

    def realtime(self, byte: bytes) -> None:
        self._write(byte)

    def send(self, line: str, echo: bool = True) -> bool:
        """Send one line and let its ok be counted. Not for use during a job."""
        line = line.strip()
        if not line:
            return False
        if self.job_running:
            self.log("busy: a job is running", "err")
            return False
        if echo:
            self._emit("log", (f">> {line}", "tx"))
        return self._write((line + "\n").encode())

    def jog(self, axis: str, dist: float, feed: float) -> None:
        self.send(f"$J=G91 {axis}{dist:.3f} F{feed:.0f}")

    def jog_to(self, x: float, y: float, feed: float) -> None:
        self.send(f"$J=G90 X{x:.3f} Y{y:.3f} F{feed:.0f}")

    def jog_cancel(self) -> None:
        self.realtime(RT_JOG_CANCEL)

    # ------------------------------------------------------------------- input
    def _reader(self) -> None:
        buf = b""
        while not self._stop.is_set() and self.connected:
            try:
                chunk = self._sp.read(256)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._handle(raw.decode("ascii", "replace").strip())
        if not self._stop.is_set():
            self._emit("log", ("serial link dropped", "err"))
            self._emit("conn", False)

    def _handle(self, line: str) -> None:
        if not line:
            return
        if line.startswith("<") and line.endswith(">"):
            self._parse_status(line)
            return
        if line == "ok" or line.startswith("error:"):
            self._acks.put(line)
            if line.startswith("error:"):
                self._emit("log", (f"   {line}", "err"))
            return
        m = SETTING_RE.match(line)
        if m:
            self.settings[int(m.group(1))] = float(m.group(2))
            self._emit("settings", self.settings)
        kind = "err" if line.startswith("ALARM") else "info"
        self._emit("log", (f"   {line}", kind))
        if line.startswith("ALARM"):
            self._job_abort.set()
            self._emit("alarm", line)

    def _parse_status(self, line: str) -> None:
        st = Status(raw=line)
        body = line[1:-1]
        st.state = body.split("|", 1)[0].split(":", 1)[0]

        m = WCO_RE.search(body)
        if m:
            st.wco = (float(m.group(1)), float(m.group(2)))
        else:
            st.wco = self.status.wco  # WCO is only sent periodically; it is sticky

        m = POS_RE.search(body)
        if m:
            x, y = float(m.group(2)), float(m.group(3))
            if m.group(1) == "WPos":
                x += st.wco[0]
                y += st.wco[1]
            st.mpos = (x, y)
        else:
            st.mpos = self.status.mpos

        m = BF_RE.search(body)
        if m:
            st.planner = int(m.group(1))

        self.status = st
        self._emit("status", st)

    def _poller(self) -> None:
        while not self._stop.is_set() and self.connected:
            self._write(RT_STATUS)
            time.sleep(0.2)

    # -------------------------------------------------------------------- jobs
    @property
    def job_running(self) -> bool:
        return self._job_thread is not None and self._job_thread.is_alive()

    def start_job(self, lines: list[Line]) -> bool:
        if not self.connected:
            self.log("not connected", "err")
            return False
        if self.job_running:
            self.log("a job is already running", "err")
            return False
        self._job_abort.clear()
        self._job_hold.clear()
        self._job_break.clear()
        self._job_resume.clear()
        while not self._acks.empty():
            self._acks.get_nowait()
        self.progress = JobProgress(total=len(lines), state="running")
        self._job_thread = threading.Thread(
            target=self._stream, args=(lines,), name="grbl-stream", daemon=True
        )
        self._job_thread.start()
        return True

    def hold(self) -> None:
        """Instant feed hold. Motion stops where it is - the pen stays down."""
        if not self.job_running:
            self.realtime(RT_HOLD)
            return
        self._job_hold.set()
        self.realtime(RT_HOLD)
        self._set_job_state("holding", "feed hold - pen is still on the paper")

    def pause_at_break(self) -> None:
        """Stop feeding at the next pen-up boundary, then let the buffer drain."""
        if not self.job_running:
            return
        self._job_break.set()
        self._set_job_state("running", "will pause at the next pen-up")

    def resume(self) -> None:
        if not self.job_running:
            self.realtime(RT_RESUME)
            return
        if self._job_hold.is_set():
            self._job_hold.clear()
            self.realtime(RT_RESUME)
        self._job_break.clear()
        self._job_resume.set()
        self._set_job_state("running", "")

    def abort_job(self, quiet: bool = False) -> None:
        if not self.job_running:
            return
        self._job_abort.set()
        self._job_resume.set()
        # Feed hold first so the reset lands on a stopped machine; the firmware
        # parks the pen up on reset (spindle_stop was patched to do that), so the
        # nib comes off the paper either way.
        self.realtime(RT_HOLD)
        time.sleep(0.05)
        self.realtime(RT_RESET)
        if not quiet:
            self.log("ABORTED - soft reset sent, pen parked up", "err")
            self.log("   position may have shifted; re-anchor the paper frame", "err")

    def _set_job_state(self, state: str, note: str) -> None:
        self.progress.state = state
        self.progress.note = note
        self._emit("job", self.progress)

    def _await_ack(self, pending: deque[int]) -> bool:
        """Block until one line is acknowledged. False means give up."""
        while True:
            if self._job_abort.is_set():
                return False
            try:
                self._acks.get(timeout=0.2)
            except queue.Empty:
                if not self.connected:
                    return False
                continue
            if pending:
                pending.popleft()
            self.progress.acked += 1
            return True

    def _stream(self, lines: list[Line]) -> None:
        pending: deque[int] = deque()
        try:
            for idx, item in enumerate(lines):
                if self._job_abort.is_set():
                    break

                # Requested pause takes effect only where the pen is off the
                # paper, so the drawing is never marked by the stop.
                if self._job_break.is_set() and idx > 0 and lines[idx - 1].pen_up_break:
                    while pending:
                        if not self._await_ack(pending):
                            break
                    self._wait_idle(timeout=30)
                    self._set_job_state("paused", "paused with the pen up - Resume when ready")
                    self._job_resume.wait()
                    self._job_resume.clear()
                    if self._job_abort.is_set():
                        break

                while self._job_hold.is_set() and not self._job_abort.is_set():
                    time.sleep(0.1)

                payload = item.text + "\n"
                need = len(payload)
                while sum(pending) + need >= RX_BUFFER - 1:
                    if not self._await_ack(pending):
                        raise _Aborted
                if not self._write(payload.encode()):
                    raise _Aborted
                pending.append(need)
                self.progress.sent = idx + 1
                if idx % 5 == 0 or idx == len(lines) - 1:
                    self._emit("job", self.progress)

            while pending and not self._job_abort.is_set():
                if not self._await_ack(pending):
                    break
            if not self._job_abort.is_set():
                self._emit("job", self.progress)
                self._wait_idle(timeout=600)
        except _Aborted:
            pass
        except Exception as exc:  # noqa: BLE001
            self.log(f"stream failed: {exc}", "err")
            self._job_abort.set()

        if self._job_abort.is_set():
            self._set_job_state("aborted", "job aborted")
        else:
            self._set_job_state("done", "job complete")

    def _wait_idle(self, timeout: float) -> None:
        """Lines are acked when parsed, not when the motion finishes."""
        deadline = time.time() + timeout
        time.sleep(0.3)
        while time.time() < deadline:
            if self._job_abort.is_set() or not self.connected:
                return
            if self.status.state in ("Idle", "Alarm", "Check"):
                return
            time.sleep(0.15)


class _Aborted(Exception):
    pass
