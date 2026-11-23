"""Structured subprocess records with process-group termination."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def terminate_group(process, grace_seconds):
    """SIGTERM the whole process group, then SIGKILL survivors after a grace period."""
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        pgid = None
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if pgid is None:
            break
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            deadline = time.monotonic() + grace_seconds
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    # A grandchild may outlive the leader; probe the group once more.
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _pump(source, sink):
    """Copy a child's pipe into a log file as it arrives, so partial output survives a kill."""
    try:
        for chunk in iter(lambda: source.read1(65536), b""):
            sink.write(chunk)
            sink.flush()
    except (OSError, ValueError):
        pass
    finally:
        source.close()


def _feed(sink, data):
    try:
        sink.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            sink.close()
        except OSError:
            pass


class Recorder:
    """Append-only event log plus numbered stdout/stderr files for every command."""

    def __init__(self, root: Path, grace_seconds: int = 10):
        self.root = Path(root)
        self.commands = self.root / "commands"
        self.commands.mkdir(parents=True, exist_ok=True)
        self.grace_seconds = grace_seconds
        self.origin = time.monotonic()
        self.lock = threading.Lock()
        self.sequence = 0

    def event(self, kind, **fields):
        record = dict(event=kind, utc=utc_now(), monotonic_seconds=round(time.monotonic() - self.origin, 6),
                      **fields)
        with self.lock, (self.root / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(record, default=str) + "\n")
        return record

    def run(self, argv, *, cwd=None, env=None, stdin_text=None, timeout, phase, label=None):
        with self.lock:
            self.sequence += 1
            number = self.sequence
        prefix = self.commands / f"{number:04d}-{(label or phase)}"
        stdout_path, stderr_path = prefix.with_suffix(".stdout"), prefix.with_suffix(".stderr")
        argv = [str(a) for a in argv]
        self.event("command_started", command_id=number, phase=phase, label=label, argv=argv,
                   cwd=str(cwd) if cwd else None, timeout_seconds=timeout,
                   env_keys=sorted(env) if env is not None else None)
        started_utc, started = utc_now(), time.monotonic()
        timed_out = interrupted = False
        returncode = None
        # The child writes to pipes and the harness streams them to the log files. Handing the child a
        # file descriptor would expose a path inside the (sandbox-denied) run directory; Node.js aborts
        # at startup when it cannot fstat such an inherited descriptor.
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            try:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                           start_new_session=True)
            except OSError as error:
                err.write(f"failed to start: {error}\n".encode())
                result = dict(command_id=number, phase=phase, label=label, returncode=None, timed_out=False,
                              interrupted=False, start_error=str(error), started_utc=started_utc,
                              finished_utc=utc_now(), seconds=time.monotonic() - started,
                              stdout=str(stdout_path.relative_to(self.root)),
                              stderr=str(stderr_path.relative_to(self.root)))
                self.event("command_finished", **result)
                return result
            pumps = [threading.Thread(target=_pump, args=(process.stdout, out), daemon=True),
                     threading.Thread(target=_pump, args=(process.stderr, err), daemon=True)]
            if stdin_text is not None:
                pumps.append(threading.Thread(target=_feed, args=(process.stdin, stdin_text.encode()), daemon=True))
            for pump in pumps:
                pump.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_group(process, self.grace_seconds)
            except BaseException:
                interrupted = True
                terminate_group(process, self.grace_seconds)
                self.event("command_interrupted", command_id=number, phase=phase)
                raise
            finally:
                returncode = process.returncode
                # A descendant that escaped the process group could hold a pipe open; never wait forever.
                for pump in pumps:
                    pump.join(timeout=self.grace_seconds + 5)
        result = dict(command_id=number, phase=phase, label=label, returncode=returncode, timed_out=timed_out,
                      interrupted=interrupted, started_utc=started_utc, finished_utc=utc_now(),
                      seconds=time.monotonic() - started,
                      stdout=str(stdout_path.relative_to(self.root)), stderr=str(stderr_path.relative_to(self.root)))
        self.event("command_finished", **result)
        return result

    def output(self, result):
        return (self.root / result["stdout"]).read_text(errors="replace")

    def checked(self, argv, **kwargs):
        result = self.run(argv, **kwargs)
        if result["returncode"] != 0 or result["timed_out"]:
            raise CommandFailed(result, self.root)
        return self.output(result).strip()


class CommandFailed(RuntimeError):
    def __init__(self, result, root):
        self.result = result
        stderr = (Path(root) / result["stderr"]).read_text(errors="replace")[-2000:]
        reason = "timed out" if result["timed_out"] else f"exit {result['returncode']}"
        super().__init__(f"command {result['command_id']} ({result['phase']}/{result['label']}) {reason}: {stderr}")
