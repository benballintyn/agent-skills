#!/usr/bin/env python3
"""Run a command so that neither it nor anything it starts can outlive its bounds.

Written after an orphaned test process filled the owner's disk (2026-10-09): a
mutation runner's ``subprocess.run(timeout=150)`` killed only pytest, a process
under a hung test survived the runner, looped for 42 hours and wrote ~335 GB into
pytest's capture file, which pytest deletes as soon as it creates it, so neither
``du`` nor Finder could see it.

Every command started here is bounded several independent ways, so that any one of
them failing still leaves the others:

* **Its own process group, killed as a unit** on the wall-clock deadline, when the
  command exits (stragglers it left in the group are killed and counted), and when
  this runner is interrupted or terminated (``SIGTERM``, ``SIGINT``, ``SIGHUP``).
* **A watchdog inside that group** that ignores every catchable signal and kills the
  whole group at the deadline, even if this runner has been killed (``SIGKILL``
  cannot be caught). It also enforces the disk floor, and when the runner starts
  stopping it brings its own deadline forward, so a runner killed in the middle of
  stopping still leaves the group a short life.
* **Escapees are tracked two ways**: processes that left the group (``setsid``,
  ``start_new_session=True``, ``process_group=0``, job control) are found and killed
  at the end of the run and by the watchdog. Both the runner and the watchdog
  remember every descendant of the group (by pid and start time, polled twice a
  second), and every child carries a run tag in its environment (``BOUNDED_RUN_ID``).
  On macOS ``ps`` shows the environment of non-system binaries only (Python, yes;
  ``/bin/bash``, no), so the descendant tracking is what finds those.
* **Kernel limits every child inherits**: ``RLIMIT_FSIZE`` (no file it writes, named
  or already deleted, grows past the cap) and ``RLIMIT_CPU``. On macOS the CPU limit
  stops a pure computation loop but NOT a loop that makes a system call each pass
  (writes, sleeps, clock reads), and the hard CPU limit is not enforced; the group
  kill, the watchdog and the run tag are what stop those.
* **A disk floor**: the command is refused when free space is below
  ``--min-free-gb``, and stopped (by the runner, or by the watchdog if the runner is
  gone) if free space falls below ``--abort-free-gb``.

Live runs are recorded in a registry with their owner (``BOUNDED_RUN_OWNER``), so
``--check`` can prove, before an agent hands back, that nothing it started is still
running, without confusing it with another agent's runs.

Usage::

    bounded_run.py [--wall S] [--cpu S] [--max-file-mb MB] [--min-free-gb GB]
                   [--abort-free-gb GB] [--log PATH] -- COMMAND [ARGS...]
    bounded_run.py --check [--all]   # exit 1 if a run of this owner is still alive

Exit status: the command's own; 124 when the wall clock ran out; 125 when the disk
floor refused or stopped the run; 128+N when the command died of signal N (152 is
``SIGXCPU``, the CPU limit; 153 is ``SIGXFSZ``, the file-size limit; 137 is
``SIGKILL``, e.g. the watchdog firing while this runner was stopped). A mutation
runner treats 124, 137 and 152 as TIMEOUT, never as a killed mutant.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import math
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from types import FrameType
from typing import Any

EXIT_TIMEOUT = 124
EXIT_DISK = 125
GRACE_S = 5.0
# The watchdog is the backstop: it fires only after the runner has had its own deadline
# and its full terminate-then-kill grace, so a live runner always reports the timeout.
WATCHDOG_SLACK_S = 2 * GRACE_S + 1
WATCHDOG_POLL_S = 0.5
POLL_S = 0.5
LOG_RETENTION_S = 7 * 24 * 3600
ENV_RUN_ID = "BOUNDED_RUN_ID"
ENV_OWNER = "BOUNDED_RUN_OWNER"
RUN_ID = re.compile(r"\A\d{8}T\d{6}-[0-9a-f]{8}\Z")
DEFAULT_REGISTRY = Path(os.environ.get("BOUNDED_RUN_REGISTRY", Path(tempfile.gettempdir()) / "bounded-run"))
WATCHDOG_IGNORES = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT, signal.SIGUSR1,
                    signal.SIGUSR2, signal.SIGALRM, signal.SIGPIPE)


@dataclass(frozen=True)
class Limits:
    """The bounds one run is held to.

    Attributes:
        wall_s: Seconds of wall-clock time before the whole group is killed.
        cpu_s: Seconds of CPU time any one process may use (``RLIMIT_CPU``).
        max_file_bytes: The largest file any process may write (``RLIMIT_FSIZE``).
        min_free_gb: Free space below which the command is not started.
        abort_free_gb: Free space below which a running command is stopped.
    """

    wall_s: float
    cpu_s: int
    max_file_bytes: int
    min_free_gb: float
    abort_free_gb: float


@dataclass(frozen=True)
class Record:
    """A live run, as written to the registry.

    Attributes:
        run_id: This run's id, also its tag in every child's environment.
        owner: Who started it (``BOUNDED_RUN_OWNER``), for ``--check``.
        pid: The command's process id, which is also its process group id.
        leader_start: The group leader's start time as ``ps`` reports it, so a
            reused process group id is not mistaken for this run.
        command: The command line.
        cwd: The working directory.
        started: Unix time the run started.
        deadline: Unix time the watchdog kills the group at the latest.
        log: The file holding the command's output.
    """

    run_id: str
    owner: str
    pid: int
    leader_start: str
    command: list[str]
    cwd: str
    started: float
    deadline: float
    log: str


def default_owner() -> str:
    """Return the owner recorded for a run: ``BOUNDED_RUN_OWNER``, else user and cwd.

    Returns:
        The owner string.
    """
    return os.environ.get(ENV_OWNER) or f"{getpass.getuser()}:{os.getcwd()}"


def free_gb(path: Path) -> float:
    """Return the free space, in GB, on the filesystem holding ``path``.

    Args:
        path: Any existing path on the filesystem to measure.

    Returns:
        Free space in gigabytes (10**9 bytes).
    """
    return shutil.disk_usage(path).free / 1e9


def lowest_free_gb(paths: list[Path]) -> float:
    """Return the least free space across ``paths``.

    Args:
        paths: Paths on the filesystems to measure.

    Returns:
        The smallest free space found, in GB.
    """
    return min(free_gb(path) for path in paths)


def ps_lines(fields: str) -> list[str]:
    """Return ``ps``'s line for every process, with only ``fields``.

    Args:
        fields: The ``-o`` field list, each with ``=`` to drop the header.

    Returns:
        One line per process.
    """
    return subprocess.run(["ps", "-A", "-o", fields], capture_output=True, text=True,
                          check=True).stdout.splitlines()


def group_members(pgid: int) -> dict[int, str]:
    """List the live processes in a process group.

    Args:
        pgid: The process group id.

    Returns:
        Each live member's process id and command line (zombies excluded).
    """
    members = {}
    for line in ps_lines("pid=,pgid=,stat=,command="):
        fields = line.split(None, 3)
        if len(fields) >= 3 and int(fields[1]) == pgid and not fields[2].startswith("Z"):
            members[int(fields[0])] = fields[3] if len(fields) == 4 else ""
    return members


def leader_start(pid: int) -> str:
    """Return a process's start time as ``ps`` reports it, or ``""`` if it is gone.

    Args:
        pid: The process id.

    Returns:
        The ``lstart`` string.
    """
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip()


def tagged_processes(run_id: str) -> list[int]:
    """Find live processes whose environment carries this run's tag.

    The tag survives every way of leaving the process group short of clearing the
    environment, so this finds what the group kill cannot.

    Args:
        run_id: The run's id.

    Returns:
        Their process ids, excluding this process.
    """
    tag = f"{ENV_RUN_ID}={run_id}"
    found = []
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if entry.name.isdigit():
                with contextlib.suppress(OSError):
                    if tag.encode() in (entry / "environ").read_bytes().split(b"\0"):
                        found.append(int(entry.name))
    else:
        listing = subprocess.run(["ps", "-E", "-A", "-o", "pid=,stat=,command="], capture_output=True,
                                 text=True).stdout
        pattern = re.compile(rf"(^|\s){re.escape(tag)}(\s|$)")
        for line in listing.splitlines():
            fields = line.split(None, 2)
            if len(fields) == 3 and not fields[1].startswith("Z") and pattern.search(fields[2]):
                found.append(int(fields[0]))
    return [pid for pid in found if pid != os.getpid()]


def process_table() -> dict[int, tuple[int, int, str]]:
    """Return every live process's parent, group and start time.

    Returns:
        ``pid -> (ppid, pgid, lstart)``, zombies excluded.
    """
    table = {}
    for line in ps_lines("pid=,ppid=,pgid=,stat=,lstart="):
        fields = line.split(None, 4)
        if len(fields) == 5 and not fields[3].startswith("Z"):
            table[int(fields[0])] = (int(fields[1]), int(fields[2]), fields[4].strip())
    return table


class Tracker:
    """Remember every descendant of a run's process group, so one that leaves it is still found.

    A process is known by its pid and start time, so a reused pid is never mistaken
    for it. Call ``update`` often: a process that forks and detaches a child, then
    exits, between two updates is not seen (the run tag covers what it can).
    """

    def __init__(self, pgid: int) -> None:
        """Start tracking the group ``pgid``.

        Args:
            pgid: The run's process group id.
        """
        self.pgid = pgid
        self.known: dict[int, str] = {}

    def update(self) -> None:
        """Add the group's members and every live descendant of anything known."""
        table = process_table()
        roots = {pid for pid, (_, pgid, _) in table.items() if pgid == self.pgid}
        roots |= {pid for pid, start in self.known.items() if pid in table and table[pid][2] == start}
        grew = True
        while grew:
            grew = False
            for pid, (ppid, _, _) in table.items():
                if ppid in roots and pid not in roots:
                    roots.add(pid)
                    grew = True
        for pid in roots:
            if pid in table:
                self.known.setdefault(pid, table[pid][2])

    def escapees(self) -> list[int]:
        """Return known descendants still alive outside the group.

        Returns:
            Their pids, excluding this process.
        """
        table = process_table()
        return [pid for pid, start in self.known.items()
                if pid in table and table[pid][2] == start and table[pid][1] != self.pgid and pid != os.getpid()]


def is_watchdog(command: str) -> bool:
    """Say whether a process is this tool's own watchdog (the forked, never-exec'd child).

    Args:
        command: The process's command line.

    Returns:
        True for the watchdog.
    """
    return "bounded_run.py --_child" in command


def kill_group(pgid: int) -> None:
    """Terminate a process group, then kill whatever is left after a grace period.

    The watchdog ignores ``SIGTERM`` by design; once only it is left, the grace
    ends and ``SIGKILL`` follows at once.

    Args:
        pgid: The process group id.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + GRACE_S
        while time.monotonic() < deadline:
            members = group_members(pgid)
            if not members or (sig == signal.SIGTERM and all(is_watchdog(c) for c in members.values())):
                break
            time.sleep(0.1)


def kill_pids(pids: list[int]) -> None:
    """Send ``SIGKILL`` to each of ``pids``, ignoring those already gone.

    Args:
        pids: Process ids.
    """
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def tighten(which: int, soft: int, hard: int) -> None:
    """Lower a resource limit, never raising one this process inherited.

    A run nested inside another bounded run (or any shell with a lower limit) keeps
    the tighter of the two; an unprivileged process cannot raise a hard limit anyway.

    Args:
        which: The ``resource.RLIMIT_*`` constant.
        soft: The soft limit wanted.
        hard: The hard limit wanted.
    """
    cur_soft, cur_hard = resource.getrlimit(which)
    if cur_hard != resource.RLIM_INFINITY:
        hard = min(hard, cur_hard)
    if cur_soft != resource.RLIM_INFINITY:
        soft = min(soft, cur_soft)
    resource.setrlimit(which, (min(soft, hard), hard))


def watchdog(config: dict[str, Any]) -> None:
    """Kill the group at the deadline, on a stop request's short deadline, or at the disk floor.

    Runs in the group as a forked copy of the child before its exec. It ignores every
    catchable signal, so the runner's own ``SIGTERM`` (or a ``kill 0`` from the
    command) cannot disarm it, and it fails closed: whatever ends its wait, it kills
    the group and every process carrying the run tag. Never returns.

    Args:
        config: The child configuration (deadline, run id, stop file, paths, floor).
    """
    for sig in WATCHDOG_IGNORES:
        signal.signal(sig, signal.SIG_IGN)
    tracker = Tracker(os.getpgrp())
    try:
        deadline = config["deadline"] + WATCHDOG_SLACK_S
        stop_file = Path(config["stop_file"])
        paths = [Path(p) for p in config["paths"]]
        while time.time() < deadline:
            tracker.update()
            if stop_file.exists():
                deadline = min(deadline, time.time() + GRACE_S + 1)
            if lowest_free_gb(paths) < config["abort_free_gb"]:
                break
            time.sleep(WATCHDOG_POLL_S)
    except BaseException:  # noqa: BLE001  (fail closed: any failure ends in the kill below)
        pass
    finally:
        with contextlib.suppress(BaseException):
            kill_pids(sorted(set(tracker.escapees()) | set(tagged_processes(config["run_id"]))))
        with contextlib.suppress(BaseException):
            os.killpg(os.getpgrp(), signal.SIGKILL)
        os._exit(0)


def child_main(config: dict[str, Any], command: list[str]) -> None:
    """Become the bounded command: set the limits, start the watchdog, then exec.

    Runs as the first process of a new session (so its pid is the group id).

    Args:
        config: The limits and the watchdog's configuration.
        command: The command to exec.
    """
    limits = Limits(**config["limits"])
    tighten(resource.RLIMIT_CPU, limits.cpu_s, limits.cpu_s + 5)
    tighten(resource.RLIMIT_FSIZE, limits.max_file_bytes, limits.max_file_bytes)
    tighten(resource.RLIMIT_CORE, 0, 0)
    if os.fork() == 0:
        watchdog(config)
    # Python ignores SIGXFSZ (and SIGPIPE) at startup and the exec would inherit that;
    # restore the defaults so a non-Python command dies at the file-size cap (153)
    # instead of looping on failed writes. A Python command ignores SIGXFSZ again: its
    # writes then fail with EFBIG, which stops the file growing, and the group kill,
    # the watchdog and the run tag bound the rest.
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(f"bounded-run: cannot run {command[0]!r}: {exc}", file=sys.stderr)
        os._exit(127)


def prune_logs(registry: Path) -> None:
    """Delete this tool's own logs (named by run id) older than the retention period.

    Args:
        registry: The registry directory.
    """
    cutoff = time.time() - LOG_RETENTION_S
    for log in registry.glob("*.log"):
        if not RUN_ID.match(log.stem):
            continue
        with contextlib.suppress(FileNotFoundError):
            if log.stat().st_mtime < cutoff:
                log.unlink()


def write_atomically(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so a reader never sees a partial file.

    Args:
        path: The destination.
        text: The content.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def run(limits: Limits, command: list[str], registry: Path, log: Path | None, tail_kb: int,
        owner: str | None = None) -> int:
    """Run ``command`` under ``limits`` and return its exit status.

    Args:
        limits: The bounds to hold it to.
        command: The command and its arguments.
        registry: Where live runs are recorded.
        log: Where the command's output goes, or ``None`` for one in the registry.
        tail_kb: How much of the end of the log to print when the run ends.
        owner: Who the run belongs to, for ``--check``; ``default_owner()`` if None.

    Returns:
        The exit status described in the module docstring.
    """
    registry.mkdir(parents=True, exist_ok=True)
    prune_logs(registry)
    owner = owner or default_owner()
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    log = log or registry / f"{run_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    paths = [Path.cwd(), Path(tempfile.gettempdir()), log.parent]
    lowest = lowest_free_gb(paths)
    if lowest < limits.min_free_gb:
        print(f"bounded-run: refused: {lowest:.1f} GB free, below the {limits.min_free_gb:g} GB floor; "
              "report it, never lower the floor to get past it", file=sys.stderr)
        return EXIT_DISK
    started = time.time()
    deadline = started + limits.wall_s
    stop_file = registry / f"{run_id}.stopping"
    config = {"limits": asdict(limits), "deadline": deadline, "run_id": run_id, "stop_file": str(stop_file),
              "paths": [str(p) for p in paths], "abort_free_gb": limits.abort_free_gb}
    env = {**os.environ, ENV_RUN_ID: run_id, ENV_OWNER: owner}
    child = [sys.executable, os.path.abspath(__file__), "--_child", json.dumps(config), "--", *command]
    with open(log, "wb") as out:
        proc = subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env)
    pgid = proc.pid
    record_path = registry / f"{run_id}.json"
    write_atomically(record_path, json.dumps(asdict(Record(
        run_id, owner, pgid, leader_start(pgid), command, os.getcwd(), started,
        deadline + WATCHDOG_SLACK_S, str(log)))))

    def stop() -> None:
        with contextlib.suppress(OSError):
            stop_file.touch()
        kill_group(pgid)

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop()
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    tracker = Tracker(pgid)
    try:
        outcome = None
        while outcome is None:
            tracker.update()
            try:
                proc.wait(timeout=POLL_S)
                outcome = "exited"
            except subprocess.TimeoutExpired:
                if time.time() >= deadline:
                    outcome = "timeout"
                elif lowest_free_gb(paths) < limits.abort_free_gb:
                    outcome = "disk"
        tracker.update()
        stragglers = [pid for pid, cmd in group_members(pgid).items() if pid != pgid and not is_watchdog(cmd)]
        stop()
        proc.wait()
        escaped = sorted(set(tracker.escapees()) | set(tagged_processes(run_id)))
        kill_pids(escaped)
        time.sleep(0.2 if escaped else 0)
        survivors = sorted(set(group_members(pgid)) | set(tracker.escapees()) | set(tagged_processes(run_id)))
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        stop_file.unlink(missing_ok=True)
        record_path.unlink(missing_ok=True)

    with open(log, "rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - tail_kb * 1024))
        sys.stdout.write(fh.read().decode("utf-8", "replace"))
    code = proc.returncode
    status = {"timeout": EXIT_TIMEOUT, "disk": EXIT_DISK}.get(outcome, 128 - code if code < 0 else code)
    print(f"bounded-run: {outcome}; status {status}; {time.time() - started:.1f}s; "
          f"{len(stragglers)} straggler(s) killed; {len(escaped)} escaped process(es) killed; "
          f"{len(survivors)} survivor(s); log {log}", file=sys.stderr)
    if survivors:
        print(f"bounded-run: WARNING: processes {survivors} survived the kill", file=sys.stderr)
    return status


def check(registry: Path, owner: str, everyone: bool) -> int:
    """Report registered runs that still have live processes.

    Only the caller's own runs are considered unless ``everyone`` is set. A record
    whose run has nothing left alive (its runner was killed, and the watchdog did
    its job) is removed, if it is the caller's.

    Args:
        registry: The registry directory.
        owner: The caller's owner string.
        everyone: Report every owner's runs (for coordinators).

    Returns:
        1 if any considered run is still alive, else 0.
    """
    alive = 0
    for path in sorted(registry.glob("*.json")) if registry.exists() else []:
        try:
            record = Record(**json.loads(path.read_text()))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            continue
        if not everyone and record.owner != owner:
            continue
        members = group_members(record.pid)
        if members and leader_start(record.pid) not in ("", record.leader_start):
            members = {}  # the group id was reused by an unrelated process
        tagged = tagged_processes(record.run_id)
        if members or tagged:
            alive += 1
            overdue = " OVERDUE" if time.time() > record.deadline else ""
            print(f"LIVE{overdue} {record.run_id} owner={record.owner!r} pgid={record.pid} "
                  f"members={sorted(set(members) | set(tagged))} cmd={' '.join(record.command)[:120]!r} "
                  f"log={record.log}")
        elif record.owner == owner:
            path.unlink(missing_ok=True)
    if not alive:
        print("bounded-run: no live runs")
    return 1 if alive else 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run, check, or (internally) become the child.

    Args:
        argv: Arguments, without the program name; ``sys.argv[1:]`` when ``None``.

    Returns:
        The exit status.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--_child"]:
        child_main(json.loads(argv[1]), argv[3:])
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--wall", type=float, default=600.0, help="wall-clock seconds (default 600)")
    parser.add_argument("--cpu", type=int, help="CPU seconds per process (default: wall + 16)")
    parser.add_argument("--max-file-mb", type=int, default=1024, help="largest file any process may write (default 1024)")
    parser.add_argument("--min-free-gb", type=float, default=50.0, help="refuse to start below this (default 50)")
    parser.add_argument("--abort-free-gb", type=float, default=20.0, help="stop the run below this (default 20)")
    parser.add_argument("--log", type=Path, help="output file (default: one in the registry)")
    parser.add_argument("--tail-kb", type=int, default=64, help="log tail printed at the end (default 64)")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help=f"live-run registry (default {DEFAULT_REGISTRY})")
    parser.add_argument("--check", action="store_true", help="exit 1 if a run of this owner is still alive")
    parser.add_argument("--all", action="store_true", help="with --check: every owner's runs")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.registry, default_owner(), args.all)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give a command after --")
    if not (math.isfinite(args.wall) and args.wall > 0) or args.max_file_mb <= 0:
        parser.error("--wall must be a finite positive number and --max-file-mb positive")
    if args.cpu is not None and args.cpu <= 0:
        parser.error("--cpu must be positive")
    limits = Limits(
        wall_s=args.wall,
        cpu_s=args.cpu if args.cpu is not None else int(args.wall) + int(WATCHDOG_SLACK_S) + 5,
        max_file_bytes=args.max_file_mb * 1024 * 1024,
        min_free_gb=args.min_free_gb,
        abort_free_gb=args.abort_free_gb,
    )
    return run(limits, command, args.registry, args.log, args.tail_kb)


if __name__ == "__main__":
    sys.exit(main())
