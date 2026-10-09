"""Tests for bounded_run.py: every bound is proved against a real misbehaving process.

Each test drives the real script in a subprocess and checks with ``ps`` that nothing
it started survives. The tests must stay safe when bounded_run itself is broken (a
mutant, a regression), because that is exactly when its bounds do not hold: every
loop here has a fixed number of passes, every sleep is short, and every test that
starts something kills what it created (process groups, escaped processes) in a
``finally``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("bounded_run.py")
sys.path.insert(0, str(SCRIPT.parent))
import bounded_run  # noqa: E402

# Fixed, not read from the module, so a change to the slack is a test failure.
EXPECTED_SLACK_S = 11.0
NO_FLOOR = ("--min-free-gb", "0", "--abort-free-gb", "0")


def tool(*args: str, registry: Path) -> list[str]:
    """Return the command line for bounded_run.py with an isolated registry.

    Args:
        *args: The rest of the arguments.
        registry: The registry directory.

    Returns:
        The argv.
    """
    return [sys.executable, str(SCRIPT), "--registry", str(registry), *args]


def env(owner: str = "tests") -> dict[str, str]:
    """Return an environment carrying a test owner.

    Args:
        owner: The ``BOUNDED_RUN_OWNER`` value.

    Returns:
        The environment.
    """
    return {**os.environ, "BOUNDED_RUN_OWNER": owner}


def run_tool(*args: str, registry: Path, timeout: float = 60.0, owner: str = "tests") -> subprocess.CompletedProcess[str]:
    """Run bounded_run.py to completion with an isolated registry and no disk floor.

    Args:
        *args: Arguments after the registry and floors.
        registry: The registry directory.
        timeout: The test's own bound on the tool.
        owner: The owner for the run.

    Returns:
        The finished process.
    """
    return subprocess.run(tool(*NO_FLOOR, *args, registry=registry), capture_output=True, text=True,
                          timeout=timeout, check=False, env=env(owner))


def start_tool(*args: str, registry: Path, owner: str = "tests") -> subprocess.Popen[bytes]:
    """Start bounded_run.py in its own session, so the test can kill the runner alone.

    Args:
        *args: Arguments after the registry and floors.
        registry: The registry directory.
        owner: The owner for the run.

    Returns:
        The running runner.
    """
    return subprocess.Popen(tool(*NO_FLOOR, *args, registry=registry), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True, env=env(owner))


def alive(pid: int) -> bool:
    """Say whether a process exists and is not a zombie.

    Args:
        pid: The process id.

    Returns:
        True while it runs.
    """
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def wait_until(predicate, timeout: float) -> bool:
    """Poll ``predicate`` until it holds or ``timeout`` passes.

    Args:
        predicate: A function of no arguments.
        timeout: Seconds to wait.

    Returns:
        Whether it held in time.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def kill_quietly(*, pgids: tuple[int, ...] = (), pids: tuple[int, ...] = ()) -> None:
    """Kill process groups and processes if they still exist: every such test's last word.

    Args:
        pgids: Process groups to kill.
        pids: Processes to kill.
    """
    for pgid in pgids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def read_pids(path: Path) -> list[int]:
    """Read the process ids a test command wrote, one per line.

    Args:
        path: The file.

    Returns:
        The ids (none if the file is absent).
    """
    return [int(x) for x in path.read_text().split()] if path.exists() else []


def record_of(registry: Path) -> dict:
    """Return the one live record in ``registry``.

    Args:
        registry: The registry directory.

    Returns:
        The record.
    """
    (path,) = registry.glob("*.json")
    return json.loads(path.read_text())


# --- basic behaviour -------------------------------------------------------------------


def test_the_command_status_and_output_pass_through(tmp_path: Path) -> None:
    """A well-behaved command's exit status and output come back unchanged."""
    result = run_tool("--wall", "20", "--", sys.executable, "-c", "print('hello'); raise SystemExit(3)",
                      registry=tmp_path)

    assert result.returncode == 3
    assert "hello" in result.stdout
    assert "exited; status 3" in result.stderr
    assert "0 straggler(s) killed; 0 escaped process(es) killed; 0 survivor(s)" in result.stderr
    assert not list(tmp_path.glob("*.json")) and not list(tmp_path.glob("*.stopping"))


def test_the_wall_limit_kills_the_whole_tree_not_just_the_command(tmp_path: Path) -> None:
    """At the deadline the command and every process it started are killed: status 124."""
    pids = tmp_path / "pids"
    script = f"sleep 30 & echo $! >> {pids}; sleep 30 & echo $! >> {pids}; wait"
    started = time.monotonic()
    try:
        result = run_tool("--wall", "2", "--", "bash", "-c", script, registry=tmp_path)
    finally:
        kill_quietly(pids=tuple(read_pids(pids)))

    assert result.returncode == bounded_run.EXIT_TIMEOUT
    assert time.monotonic() - started < 15
    assert len(read_pids(pids)) == 2
    assert not any(alive(pid) for pid in read_pids(pids)), "a grandchild survived the deadline"
    assert "0 survivor(s)" in result.stderr


def test_stragglers_left_behind_by_a_finished_command_are_killed_and_counted(tmp_path: Path) -> None:
    """A command that exits but leaves a background child behind does not leave it running."""
    pid_file = tmp_path / "pid"
    try:
        result = run_tool("--wall", "20", "--", "bash", "-c", f"sleep 30 & echo $! > {pid_file}; exit 0",
                          registry=tmp_path)
    finally:
        kill_quietly(pids=tuple(read_pids(pid_file)))

    assert result.returncode == 0
    assert "1 straggler(s) killed" in result.stderr and "0 survivor(s)" in result.stderr
    assert f"bounded-run: straggler {read_pids(pid_file)[0]}: sleep 30" in result.stderr
    assert not alive(read_pids(pid_file)[0])


def test_a_renamed_copy_knows_its_own_watchdog(tmp_path: Path) -> None:
    """An outer bound pinned to a copy under another name counts no straggler and waits no grace."""
    copy = tmp_path / "runner-pinned.py"
    copy.write_text(SCRIPT.read_text())
    started = time.monotonic()

    result = subprocess.run([sys.executable, str(copy), "--registry", str(tmp_path / "reg"), *NO_FLOOR,
                             "--wall", "20", "--", "true"], capture_output=True, text=True, timeout=60, env=env())

    assert result.returncode == 0
    assert "0 straggler(s) killed" in result.stderr, result.stderr
    assert time.monotonic() - started < bounded_run.GRACE_S, "the TERM grace was not cut short"


def test_a_hang_reports_124_not_the_cpu_limit_at_the_default_cpu_bound(tmp_path: Path) -> None:
    """With the default CPU limit (wall + 16 s) a busy hang is a timeout (124), not a 152.

    Three trials: with the old default (the wall) a busy hang gave 152 in 3 of 5.
    """
    statuses = [run_tool("--wall", "3", "--", sys.executable, "-c", "while True: pass",
                         registry=tmp_path).returncode for _ in range(3)]

    assert statuses == [bounded_run.EXIT_TIMEOUT] * 3


# --- the watchdog: the incident's path ---------------------------------------------------


def test_the_watchdog_kills_the_whole_group_at_the_fixed_slack_after_the_runner_is_killed(
    tmp_path: Path,
) -> None:
    """SIGKILL cannot be caught: the watchdog must fire, at deadline + 11 s, and kill grandchildren too."""
    pids = tmp_path / "pids"
    # The leader and a grandchild both loop for at most 60 s on their own: longer than
    # this test waits, so only the watchdog can be what ends them inside the wait.
    script = (f"(for i in $(seq 1 300); do sleep 0.2; done) & echo $! >> {pids}; echo $$ >> {pids}; "
              "for i in $(seq 1 300); do sleep 0.2; done")
    runner = start_tool("--wall", "2", "--", "bash", "-c", script, registry=tmp_path)
    try:
        assert wait_until(lambda: len(read_pids(pids)) == 2, 10)
        record = record_of(tmp_path)
        runner.send_signal(signal.SIGKILL)
        runner.wait(timeout=10)
        fire_at = record["started"] + 2 + EXPECTED_SLACK_S

        time.sleep(max(0.0, fire_at - 3 - time.time()))
        assert all(alive(pid) for pid in read_pids(pids)), "killed before the watchdog's time"
        assert wait_until(lambda: not any(alive(p) for p in read_pids(pids)), fire_at + 4 - time.time()), (
            "the watchdog did not kill the leader and the grandchild"
        )
    finally:
        kill_quietly(pgids=(runner.pid, *read_pids(pids)), pids=tuple(read_pids(pids)))


def test_a_term_ignoring_command_dies_when_the_runner_is_terminated_then_killed_mid_grace(
    tmp_path: Path,
) -> None:
    """The review's blocker: the runner's own SIGTERM must not disarm the watchdog."""
    pid_file = tmp_path / "pid"
    script = f"trap '' TERM; echo $$ > {pid_file}; for i in $(seq 1 300); do sleep 0.2; done"
    runner = start_tool("--wall", "60", "--", "bash", "-c", script, registry=tmp_path)
    try:
        assert wait_until(pid_file.exists, 10)
        command = read_pids(pid_file)[0]
        runner.send_signal(signal.SIGTERM)
        time.sleep(1)
        runner.send_signal(signal.SIGKILL)
        runner.wait(timeout=10)

        assert wait_until(lambda: not alive(command), bounded_run.GRACE_S + 1 + 6), (
            "a TERM-ignoring command outlived a runner killed mid-grace"
        )
    finally:
        kill_quietly(pgids=(runner.pid, *read_pids(pid_file)), pids=tuple(read_pids(pid_file)))


def test_a_command_that_signals_its_own_group_cannot_disarm_the_watchdog(tmp_path: Path) -> None:
    """Every catchable signal the command sends its own group reaches the watchdog, which ignores it."""
    pid_file = tmp_path / "pid"
    signals = "INT TERM HUP QUIT USR1 USR2 ALRM PROF VTALRM XCPU ABRT TSTP TTIN TTOU XFSZ PIPE"
    sends = "; ".join(f"kill -{name} 0" for name in signals.split())
    script = (f"trap '' {signals}; echo $$ > {pid_file}; {sends}; "
              "for i in $(seq 1 300); do sleep 0.2; done")
    runner = start_tool("--wall", "2", "--", "bash", "-c", script, registry=tmp_path)
    try:
        assert wait_until(pid_file.exists, 10)
        command = read_pids(pid_file)[0]
        time.sleep(0.5)
        runner.send_signal(signal.SIGKILL)
        runner.wait(timeout=10)

        assert wait_until(lambda: not alive(command), 2 + EXPECTED_SLACK_S + 6)
    finally:
        kill_quietly(pgids=(runner.pid, *read_pids(pid_file)), pids=tuple(read_pids(pid_file)))


def test_terminating_the_runner_stops_the_command_and_exits_143(tmp_path: Path) -> None:
    """The runner's SIGTERM handler kills the group before it exits."""
    pid_file = tmp_path / "pid"
    runner = start_tool("--wall", "60", "--", "bash", "-c",
                        f"echo $$ > {pid_file}; for i in $(seq 1 300); do sleep 0.2; done", registry=tmp_path)
    try:
        assert wait_until(pid_file.exists, 10)
        command = read_pids(pid_file)[0]
        runner.send_signal(signal.SIGTERM)
        code = runner.wait(timeout=20)

        assert code == 128 + signal.SIGTERM
        assert not alive(command)
    finally:
        kill_quietly(pgids=(runner.pid, *read_pids(pid_file)), pids=tuple(read_pids(pid_file)))


def test_the_watchdog_enforces_the_disk_floor_without_the_runner(tmp_path: Path) -> None:
    """Below --abort-free-gb the group dies even after the runner is gone."""
    pid_file = tmp_path / "pid"
    runner = subprocess.Popen(
        tool("--min-free-gb", "0", "--abort-free-gb", "1e12", "--wall", "60", "--", "bash", "-c",
             f"echo $$ > {pid_file}; for i in $(seq 1 300); do sleep 0.2; done", registry=tmp_path),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=env(),
    )
    try:
        assert wait_until(pid_file.exists, 10)
        command = read_pids(pid_file)[0]
        runner.send_signal(signal.SIGKILL)
        runner.wait(timeout=10)

        assert wait_until(lambda: not alive(command), 5), "the watchdog ignored the disk floor"
    finally:
        kill_quietly(pgids=(runner.pid, *read_pids(pid_file)), pids=tuple(read_pids(pid_file)))


# --- processes that leave the group ------------------------------------------------------


def escaper_script(pid_file: Path, child: list[str]) -> str:
    """Return Python that starts ``child`` in its own session, records its pid, and lingers 1.5 s.

    Real escapees come from long-lived parents (pytest under a hung test); the linger
    gives the runner's and the watchdog's twice-a-second tracking time to see it.

    Args:
        pid_file: Where to write the escapee's pid.
        child: The escapee's command.

    Returns:
        The script.
    """
    return (
        "import subprocess, time\n"
        f"p = subprocess.Popen({child!r}, start_new_session=True)\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(1.5)\n"
    )


LOOPING_BASH = ["bash", "-c", "for i in $(seq 1 300); do sleep 0.2; done"]
LOOPING_PYTHON = [sys.executable, "-c", "import time\nfor _ in range(300): time.sleep(0.2)"]


@pytest.mark.parametrize("child", [LOOPING_BASH, LOOPING_PYTHON], ids=["system-binary", "python"])
def test_a_process_that_leaves_the_group_is_found_and_killed(tmp_path: Path, child: list[str]) -> None:
    """``start_new_session=True`` escapes the group; descendant tracking (and the tag) still find it."""
    pid_file = tmp_path / "pid"
    escaper = escaper_script(pid_file, child)
    try:
        result = run_tool("--wall", "20", "--", sys.executable, "-c", escaper, registry=tmp_path)
    finally:
        kill_quietly(pids=tuple(read_pids(pid_file)))

    escaped = re.search(r"(\d+) escaped process\(es\) killed", result.stderr)
    assert escaped and int(escaped.group(1)) >= 1, result.stderr  # the escapee, and any child of its own
    assert "0 survivor(s)" in result.stderr
    assert not alive(read_pids(pid_file)[0])


def test_the_watchdog_kills_an_escapee_after_the_runner_is_killed(tmp_path: Path) -> None:
    """The incident's full shape: a process outside the group, and no runner. The watchdog finds it."""
    pid_file = tmp_path / "pid"
    script = escaper_script(pid_file, LOOPING_BASH) + "time.sleep(60)\n"
    runner = start_tool("--wall", "2", "--", sys.executable, "-c", script, registry=tmp_path)
    try:
        assert wait_until(pid_file.exists, 10)
        escapee = read_pids(pid_file)[0]
        time.sleep(1.0)  # past one tracking poll
        runner.send_signal(signal.SIGKILL)
        runner.wait(timeout=10)

        assert wait_until(lambda: not alive(escapee), 2 + EXPECTED_SLACK_S + 6), "the escapee outlived the watchdog"
    finally:
        kill_quietly(pgids=(runner.pid, *read_pids(pid_file)), pids=tuple(read_pids(pid_file)))


# --- kernel limits -----------------------------------------------------------------------


def test_a_writer_cannot_grow_a_deleted_file_past_the_file_size_cap(tmp_path: Path) -> None:
    """The incident's file was deleted and still open; RLIMIT_FSIZE bounds it all the same."""
    writer = (
        "import tempfile\n"
        "f = tempfile.TemporaryFile()\n"  # unlinked at creation, like pytest's capture file
        "written = 0\n"
        "try:\n"
        "    for _ in range(100000):\n"
        "        f.write(b'x' * 65536); f.flush(); written += 65536\n"
        "except OSError as exc:\n"
        "    print('stopped at', written, exc.errno)\n"
    )

    result = run_tool("--wall", "30", "--max-file-mb", "2", "--", sys.executable, "-c", writer, registry=tmp_path)

    assert "stopped at" in result.stdout
    assert int(result.stdout.split("stopped at")[1].split()[0]) <= 2 * 1024 * 1024


def test_a_non_python_writer_dies_of_the_file_size_cap_with_153(tmp_path: Path) -> None:
    """SIGXFSZ is restored to its default before the exec, so ``head`` dies of it at the cap."""
    script = f"head -c 4000000 /dev/zero > {tmp_path / 'big'}; echo exit=$?"

    result = run_tool("--wall", "30", "--max-file-mb", "1", "--", "bash", "-c", script, registry=tmp_path)

    assert f"exit={128 + signal.SIGXFSZ}" in result.stdout, result.stdout + result.stderr
    assert (tmp_path / "big").stat().st_size <= 1024 * 1024


def test_a_pure_busy_loop_dies_of_an_explicit_cpu_limit(tmp_path: Path) -> None:
    """RLIMIT_CPU stops a computation loop (on macOS, not a loop that makes system calls)."""
    started = time.monotonic()

    result = run_tool("--wall", "60", "--cpu", "1", "--", sys.executable, "-c", "while True: pass",
                      registry=tmp_path)

    assert time.monotonic() - started < 20
    assert result.returncode == 128 + signal.SIGXCPU, result.stderr


def test_a_nested_run_keeps_the_tighter_inherited_limits(tmp_path: Path) -> None:
    """Inside a run capped at 2 MB, a nested run asking for 1 GB still gets 2 MB, and runs."""
    inner = [sys.executable, str(SCRIPT), "--registry", str(tmp_path / "inner"), *NO_FLOOR,
             "--max-file-mb", "1024", "--", sys.executable, "-c",
             "import resource; print('fsize', resource.getrlimit(resource.RLIMIT_FSIZE)[1])"]

    result = run_tool("--wall", "30", "--max-file-mb", "2", "--", *inner, registry=tmp_path)

    assert f"fsize {2 * 1024 * 1024}" in result.stdout, result.stdout + result.stderr


# --- disk floors -------------------------------------------------------------------------


def test_the_disk_floor_refuses_to_start_the_command(tmp_path: Path) -> None:
    """Below --min-free-gb nothing runs and the status is 125."""
    marker = tmp_path / "ran"

    result = subprocess.run(tool("--min-free-gb", "1e12", "--", "touch", str(marker), registry=tmp_path),
                            capture_output=True, text=True, timeout=30, check=False, env=env())

    assert result.returncode == bounded_run.EXIT_DISK
    assert "refused" in result.stderr and "never lower the floor" in result.stderr
    assert not marker.exists()


def test_a_disk_floor_stop_reports_125_even_when_the_watchdog_sees_it_first(tmp_path: Path) -> None:
    """The watchdog checks the floor within milliseconds of the start, the runner at 0.5 s:
    whichever stops the run, the status is 125, never a 137 that reads as a hang."""
    statuses = [
        subprocess.run(tool("--min-free-gb", "0", "--abort-free-gb", "1e12", "--wall", "20", "--", "sleep", "10",
                            registry=tmp_path), capture_output=True, text=True, timeout=60, env=env()).returncode
        for _ in range(3)
    ]

    assert statuses == [bounded_run.EXIT_DISK] * 3


def test_falling_below_the_abort_floor_stops_a_running_command(tmp_path: Path, mocker) -> None:
    """Free space measured by the runner while the command runs stops it: status 125."""
    readings = iter([100.0] + [1.0] * 1000)
    mocker.patch.object(bounded_run, "lowest_free_gb", side_effect=lambda paths: next(readings))
    limits = bounded_run.Limits(wall_s=30, cpu_s=30, max_file_bytes=1 << 20, min_free_gb=50, abort_free_gb=20)

    started = time.monotonic()
    status = bounded_run.run(limits, ["sleep", "30"], tmp_path, None, tail_kb=1, owner="tests")

    assert status == bounded_run.EXIT_DISK
    assert time.monotonic() - started < 15


def test_run_restores_the_signal_handlers_it_installed(tmp_path: Path) -> None:
    """In-process callers (and pytest itself) keep their own handlers afterwards."""
    sigs = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    before = {sig: signal.getsignal(sig) for sig in sigs}
    limits = bounded_run.Limits(wall_s=10, cpu_s=10, max_file_bytes=1 << 20, min_free_gb=0, abort_free_gb=0)

    bounded_run.run(limits, ["true"], tmp_path, None, tail_kb=1, owner="tests")

    assert {sig: signal.getsignal(sig) for sig in sigs} == before


# --- the registry and --check ------------------------------------------------------------


def test_check_reports_only_the_callers_own_runs_unless_asked_for_all(tmp_path: Path) -> None:
    """An agent's --check must not list (or clear) another agent's healthy run."""
    mine = start_tool("--wall", "6", "--", "sleep", "30", registry=tmp_path, owner="agent-a")
    theirs = start_tool("--wall", "6", "--", "sleep", "30", registry=tmp_path, owner="agent-b")
    groups: list[int] = []
    try:
        assert wait_until(lambda: len(list(tmp_path.glob("*.json"))) == 2, 10)
        groups = [json.loads(p.read_text())["pid"] for p in tmp_path.glob("*.json")]
        check = [sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--check"]
        as_a = subprocess.run(check, capture_output=True, text=True, timeout=30, env=env("agent-a"))
        as_c = subprocess.run(check, capture_output=True, text=True, timeout=30, env=env("agent-c"))
        everyone = subprocess.run([*check, "--all"], capture_output=True, text=True, timeout=30,
                                  env=env("agent-c"))
        mine.wait(timeout=30)
        theirs.wait(timeout=30)
        after = subprocess.run(check, capture_output=True, text=True, timeout=30, env=env("agent-a"))
    finally:
        kill_quietly(pgids=(mine.pid, theirs.pid, *groups))

    assert as_a.returncode == 1 and as_a.stdout.count("LIVE") == 1 and "agent-a" in as_a.stdout
    assert as_c.returncode == 0 and "no live runs" in as_c.stdout
    assert everyone.returncode == 1 and everyone.stdout.count("LIVE") == 2
    assert after.returncode == 0


def test_check_without_an_owner_set_checks_every_run_and_says_so(tmp_path: Path) -> None:
    """An agent whose export did not survive to its --check call must not get a false all-clear."""
    unset = {k: v for k, v in os.environ.items() if k != "BOUNDED_RUN_OWNER"}
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    runner = subprocess.Popen(tool(*NO_FLOOR, "--wall", "6", "--", "sleep", "30", registry=tmp_path),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                              env=unset)
    groups: list[int] = []
    try:
        assert wait_until(lambda: bool(list(tmp_path.glob("*.json"))), 10)
        groups = [record_of(tmp_path)["pid"]]
        result = subprocess.run([sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--check"],
                                capture_output=True, text=True, timeout=30, env=unset, cwd=elsewhere)
        runner.wait(timeout=30)
    finally:
        kill_quietly(pgids=(runner.pid, *groups))

    assert result.returncode == 1 and "LIVE" in result.stdout, result.stdout
    assert "is not set" in result.stdout and "checking every owner" in result.stdout


def test_check_skips_a_partial_record(tmp_path: Path) -> None:
    """A half-written record is skipped, not a crash."""
    (tmp_path / "20260101T000000-0123abcd.json").write_text('{"run_id": "x", "own')

    result = subprocess.run([sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--check"],
                            capture_output=True, text=True, timeout=30, env=env())

    assert result.returncode == 0 and "no live runs" in result.stdout, result.stderr


def test_check_does_not_mistake_a_reused_group_id_for_the_run(tmp_path: Path) -> None:
    """A stale record whose group id now belongs to another process is not reported as live."""
    other = subprocess.Popen(["sleep", "20"], start_new_session=True)
    try:
        stale = {"run_id": "20260101T000000-0123abcd", "owner": "tests", "pid": other.pid,
                 "leader_start": "Thu Jan  1 00:00:00 2026", "command": ["x"], "cwd": "/", "started": 0.0,
                 "deadline": 1.0, "log": "/dev/null"}
        (tmp_path / "20260101T000000-0123abcd.json").write_text(json.dumps(stale))

        result = subprocess.run([sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--check"],
                                capture_output=True, text=True, timeout=30, env=env())
    finally:
        kill_quietly(pgids=(other.pid,))

    assert result.returncode == 0 and "no live runs" in result.stdout
    assert not list(tmp_path.glob("*.json")), "the stale record of this owner is removed"


def test_a_record_names_the_owner_group_command_and_deadline(tmp_path: Path) -> None:
    """The registry record is written whole while the run lives, and removed after."""
    runner = start_tool("--wall", "3", "--", "sleep", "30", registry=tmp_path, owner="agent-r")
    record: dict = {}
    try:
        assert wait_until(lambda: bool(list(tmp_path.glob("*.json"))), 10)
        record = record_of(tmp_path)
        runner.wait(timeout=30)
    finally:
        kill_quietly(pgids=(runner.pid,) + ((record["pid"],) if record else ()))

    assert record["owner"] == "agent-r"
    assert record["command"] == ["sleep", "30"]
    assert record["leader_start"]
    assert record["deadline"] - record["started"] == pytest.approx(3 + EXPECTED_SLACK_S, abs=0.5)
    assert not list(tmp_path.glob("*.json"))


def test_the_tracker_never_takes_a_reused_pid_for_a_known_descendant(mocker) -> None:
    """A pid seen earlier but now belonging to another process (another start time) is not killed."""
    tables = iter([
        {100: (1, 100, "Thu Oct  9 10:00:00 2026"), 101: (100, 100, "Thu Oct  9 10:00:01 2026")},
        {101: (1, 101, "Thu Oct  9 10:05:00 2026"), 102: (1, 102, "Thu Oct  9 10:00:02 2026")},
    ])
    mocker.patch.object(bounded_run, "process_table", side_effect=lambda: next(tables))
    tracker = bounded_run.Tracker(100)
    tracker.update()

    assert tracker.escapees() == [], "pid 101 was reused by an unrelated process"


def test_pruning_deletes_only_this_tools_own_old_logs(tmp_path: Path) -> None:
    """A foreign *.log in the registry is never touched, however old."""
    ours = [tmp_path / f"20200101T000000-0123abcd{suffix}" for suffix in (".log", ".stopping", ".disk")]
    theirs = tmp_path / "build.log"
    for path in (*ours, theirs):
        path.write_text("x")
        os.utime(path, (0, 0))

    bounded_run.prune_logs(tmp_path)

    assert not any(path.exists() for path in ours) and theirs.exists()


@pytest.mark.parametrize("wall", ["inf", "nan", "0", "-1"])
def test_a_wall_limit_that_is_not_finite_and_positive_is_refused(wall: str, tmp_path: Path) -> None:
    """``--wall inf`` would leave no deadline at all. (A scratch registry: a broken guard runs only ``true``.)"""
    with pytest.raises(SystemExit):
        bounded_run.main(["--registry", str(tmp_path), "--wall", wall, "--", "true"])


def test_without_a_command_the_tool_refuses() -> None:
    """A run needs a command after --."""
    with pytest.raises(SystemExit):
        bounded_run.main(["--wall", "5"])
