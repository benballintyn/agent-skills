#!/usr/bin/env python3
"""Run a command so that neither it nor anything it starts can outlive its bounds.

Written after an orphaned test process filled the owner's disk (2026-10-09): a
mutation runner's ``subprocess.run(timeout=150)`` killed only pytest, a process
under a hung test survived the runner, looped for 42 hours and wrote ~335 GB into
pytest's capture file, which pytest deletes as soon as it creates it, so neither
``du`` nor Finder could see it.

Every command started here is bounded four independent ways, so that any one of
them failing still leaves the others:

* **Its own process group, killed as a unit**: on the wall-clock deadline, when the
  command exits (stragglers it left in the group are killed and counted), and when
  this runner is interrupted or terminated.
* **A dead-man switch inside that group**: a watchdog process that kills the whole
  group at the deadline even if this runner has been killed (``SIGKILL`` cannot be
  caught, so the runner's own handlers are not enough).
* **Kernel limits every child inherits**, which hold even for a process that left
  the group: ``RLIMIT_CPU`` (a busy loop dies) and ``RLIMIT_FSIZE`` (no file it writes,
  named or already deleted, can grow past the cap).
* **A disk floor**: the command is refused when free space is below
  ``--min-free-gb`` and stopped if free space falls below ``--abort-free-gb`` while it
  runs.

Each run is recorded in a registry while it is live, so ``--check`` can prove,
before an agent hands back, that nothing it started is still running.

Usage::

    bounded_run.py [--wall S] [--cpu S] [--max-file-mb MB] [--min-free-gb GB]
                   [--abort-free-gb GB] [--log PATH] -- COMMAND [ARGS...]
    bounded_run.py --check          # exit 1 if any registered run is still alive

Exit status: the command's own; 124 when the wall clock ran out; 125 when the disk
floor refused or stopped the run; 128+N when the command died of signal N (24 is
``SIGXCPU``, the CPU limit; 25 is ``SIGXFSZ``, the file-size limit).
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
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

EXIT_TIMEOUT = 124
EXIT_DISK = 125
GRACE_S = 5.0
# The watchdog is the backstop: it fires only after the runner has had its own deadline
# and its full terminate-then-kill grace, so a live runner always reports the timeout.
WATCHDOG_SLACK_S = 2 * GRACE_S + 1
POLL_S = 0.5
LOG_RETENTION_S = 7 * 24 * 3600
DEFAULT_REGISTRY = Path(os.environ.get("BOUNDED_RUN_REGISTRY", Path(tempfile.gettempdir()) / "bounded-run"))


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
        run_id: This run's id.
        pid: The command's process id, which is also its process group id.
        command: The command line.
        cwd: The working directory.
        started: Unix time the run started.
        deadline: Unix time the watchdog kills the group (the wall deadline plus
            ``WATCHDOG_SLACK_S``).
        log: The file holding the command's output.
    """

    run_id: str
    pid: int
    command: list[str]
    cwd: str
    started: float
    deadline: float
    log: str


def free_gb(path: Path) -> float:
    """Return the free space, in GB, on the filesystem holding ``path``.

    Args:
        path: Any existing path on the filesystem to measure.

    Returns:
        Free space in gigabytes (10**9 bytes).
    """
    return shutil.disk_usage(path).free / 1e9


def watched_paths(log: Path) -> list[Path]:
    """Return the filesystems a run can fill: the working directory, the temp dir, the log's.

    Args:
        log: The run's log file (its directory must exist).

    Returns:
        The paths to measure.
    """
    return [Path.cwd(), Path(tempfile.gettempdir()), log.parent]


def lowest_free_gb(paths: list[Path]) -> float:
    """Return the least free space across ``paths``.

    Args:
        paths: Paths on the filesystems to measure.

    Returns:
        The smallest free space found, in GB.
    """
    return min(free_gb(path) for path in paths)


def group_members(pgid: int) -> dict[int, str]:
    """List the live processes in a process group.

    Args:
        pgid: The process group id.

    Returns:
        Each live member's process id and command line (zombies excluded), as ``ps``
        reports them.
    """
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=,pgid=,stat=,command="], capture_output=True, text=True, check=True
    ).stdout
    members = {}
    for line in listing.splitlines():
        fields = line.split(None, 3)
        if len(fields) >= 3 and int(fields[1]) == pgid and not fields[2].startswith("Z"):
            members[int(fields[0])] = fields[3] if len(fields) == 4 else ""
    return members


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

    Args:
        pgid: The process group id.
    """
    for sig, wait in ((signal.SIGTERM, GRACE_S), (signal.SIGKILL, GRACE_S)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not group_members(pgid):
                return
            time.sleep(0.1)


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


def child_main(limits: Limits, deadline: float, command: list[str]) -> None:
    """Become the bounded command: set the limits, start the watchdog, then exec.

    Runs as the first process of a new session (so its pid is the group id). The
    watchdog is forked into the same group before the exec, sleeps until the
    deadline plus ``WATCHDOG_SLACK_S`` and then kills the whole group, itself
    included, so the bound holds even if the runner that started it is gone.

    Args:
        limits: The bounds to apply.
        deadline: Unix time at which the watchdog kills the group.
        command: The command to exec.
    """
    tighten(resource.RLIMIT_CPU, limits.cpu_s, limits.cpu_s + 5)
    tighten(resource.RLIMIT_FSIZE, limits.max_file_bytes, limits.max_file_bytes)
    tighten(resource.RLIMIT_CORE, 0, 0)
    if os.fork() == 0:
        try:
            time.sleep(max(0.0, deadline + WATCHDOG_SLACK_S - time.time()))
            os.killpg(os.getpgrp(), signal.SIGKILL)
        finally:
            os._exit(0)
    # Python ignores SIGXFSZ at startup and the exec would inherit that; restore the
    # default so a non-Python command dies at the file-size cap instead of looping on
    # failed writes. (A Python command ignores it again; its writes then fail with
    # EFBIG, which still stops the file growing.)
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(f"bounded-run: cannot run {command[0]!r}: {exc}", file=sys.stderr)
        os._exit(127)


def prune_logs(registry: Path) -> None:
    """Delete this tool's own logs older than the retention period.

    Args:
        registry: The registry directory.
    """
    cutoff = time.time() - LOG_RETENTION_S
    for log in registry.glob("*.log"):
        try:
            if log.stat().st_mtime < cutoff:
                log.unlink()
        except FileNotFoundError:
            pass


def run(limits: Limits, command: list[str], registry: Path, log: Path | None, tail_kb: int) -> int:
    """Run ``command`` under ``limits`` and return its exit status.

    Args:
        limits: The bounds to hold it to.
        command: The command and its arguments.
        registry: Where live runs are recorded.
        log: Where the command's output goes, or ``None`` for one in the registry.
        tail_kb: How much of the end of the log to print when the run ends.

    Returns:
        The exit status described in the module docstring.
    """
    registry.mkdir(parents=True, exist_ok=True)
    prune_logs(registry)
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    log = log or registry / f"{run_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    paths = watched_paths(log)
    lowest = lowest_free_gb(paths)
    if lowest < limits.min_free_gb:
        print(
            f"bounded-run: refused: {lowest:.1f} GB free, below the {limits.min_free_gb:g} GB floor",
            file=sys.stderr,
        )
        return EXIT_DISK
    started = time.time()
    deadline = started + limits.wall_s
    child = [sys.executable, os.path.abspath(__file__), "--_child", json.dumps(asdict(limits)),
             repr(deadline), "--", *command]
    with open(log, "wb") as out:
        proc = subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    pgid = proc.pid
    record_path = registry / f"{run_id}.json"
    record_path.write_text(json.dumps(asdict(Record(run_id, pgid, command, os.getcwd(), started,
                                                    deadline + WATCHDOG_SLACK_S, str(log)))))

    def stop(*_: object) -> None:
        kill_group(pgid)

    atexit.register(stop)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda signum, frame: (stop(), sys.exit(128 + signum)))

    outcome = None
    while outcome is None:
        try:
            proc.wait(timeout=POLL_S)
            outcome = "exited"
        except subprocess.TimeoutExpired:
            if time.time() >= deadline:
                outcome = "timeout"
            elif lowest_free_gb(paths) < limits.abort_free_gb:
                outcome = "disk"
    stragglers = [pid for pid, cmd in group_members(pgid).items() if pid != pgid and not is_watchdog(cmd)]
    kill_group(pgid)
    proc.wait()
    survivors = sorted(group_members(pgid))
    record_path.unlink(missing_ok=True)
    atexit.unregister(stop)

    with open(log, "rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - tail_kb * 1024))
        sys.stdout.write(fh.read().decode("utf-8", "replace"))
    code = proc.returncode
    status = {"timeout": EXIT_TIMEOUT, "disk": EXIT_DISK}.get(outcome, 128 - code if code < 0 else code)
    print(
        f"bounded-run: {outcome}; status {status}; {time.time() - started:.1f}s; "
        f"{len(stragglers)} straggler(s) killed; "
        f"{len(survivors)} survivor(s); log {log}",
        file=sys.stderr,
    )
    if survivors:
        print(f"bounded-run: WARNING: processes {survivors} survived the kill", file=sys.stderr)
    return status


def check(registry: Path) -> int:
    """Report registered runs that still have live processes.

    A record whose group is empty is a run whose runner was killed but whose
    watchdog did its job; it is removed.

    Args:
        registry: The registry directory.

    Returns:
        1 if any registered run is still alive, else 0.
    """
    alive = 0
    for path in sorted(registry.glob("*.json")) if registry.exists() else []:
        record = Record(**json.loads(path.read_text()))
        members = group_members(record.pid)
        if members:
            alive += 1
            overdue = " OVERDUE" if time.time() > record.deadline else ""
            print(f"LIVE{overdue} {record.run_id} pgid={record.pid} members={sorted(members)} "
                  f"cmd={' '.join(record.command)[:120]!r} log={record.log}")
        else:
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
        limits = Limits(**json.loads(argv[1]))
        child_main(limits, float(argv[2]), argv[4:])
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wall", type=float, default=600.0, help="wall-clock seconds (default 600)")
    parser.add_argument("--cpu", type=int, help="CPU seconds per process (default: the wall limit)")
    parser.add_argument("--max-file-mb", type=int, default=1024, help="largest file any process may write (default 1024)")
    parser.add_argument("--min-free-gb", type=float, default=50.0, help="refuse to start below this (default 50)")
    parser.add_argument("--abort-free-gb", type=float, default=20.0, help="stop the run below this (default 20)")
    parser.add_argument("--log", type=Path, help="output file (default: one in the registry)")
    parser.add_argument("--tail-kb", type=int, default=64, help="log tail printed at the end (default 64)")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help=f"live-run registry (default {DEFAULT_REGISTRY})")
    parser.add_argument("--check", action="store_true", help="exit 1 if any registered run is still alive")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.registry)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give a command after --")
    if args.wall <= 0 or args.max_file_mb <= 0:
        parser.error("--wall and --max-file-mb must be positive")
    limits = Limits(
        wall_s=args.wall,
        cpu_s=args.cpu if args.cpu is not None else max(1, int(args.wall)),
        max_file_bytes=args.max_file_mb * 1024 * 1024,
        min_free_gb=args.min_free_gb,
        abort_free_gb=args.abort_free_gb,
    )
    return run(limits, command, args.registry, args.log, args.tail_kb)


if __name__ == "__main__":
    sys.exit(main())
