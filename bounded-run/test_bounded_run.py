"""Tests for bounded_run.py: every bound is proved against a real misbehaving process.

Each test drives the real script in a subprocess and checks with ``ps`` that nothing
it started survives. The tests must stay safe when bounded_run itself is broken (a
mutant, a regression), because that is exactly when its bounds do not hold: every
loop here has a fixed number of passes, every sleep is short, and every test that
starts a runner kills the groups it created in a ``finally``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("bounded_run.py")
sys.path.insert(0, str(SCRIPT.parent))
import bounded_run  # noqa: E402


def run_tool(*args: str, registry: Path, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """Run bounded_run.py with an isolated registry and no disk floor.

    Args:
        *args: Arguments after the registry and floor options.
        registry: The registry directory to use.
        timeout: The test's own bound on the tool.

    Returns:
        The finished process.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--registry", str(registry), "--min-free-gb", "0",
         "--abort-free-gb", "0", *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def alive(pid: int) -> bool:
    """Say whether a process exists and is not a zombie.

    Args:
        pid: The process id.

    Returns:
        True while it runs.
    """
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def kill_group_quietly(pgid: int) -> None:
    """Kill a process group if it still exists: every test's last word, so that even a
    broken bounded_run (a mutant, a regression) cannot leave a test's processes behind.

    Args:
        pgid: The process group id.
    """
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


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


def test_the_command_status_and_output_pass_through(tmp_path: Path) -> None:
    """A well-behaved command's exit status and output come back unchanged."""
    result = run_tool("--wall", "20", "--", sys.executable, "-c", "print('hello'); raise SystemExit(3)",
                      registry=tmp_path)

    assert result.returncode == 3
    assert "hello" in result.stdout
    assert "exited; status 3" in result.stderr
    assert "0 straggler(s) killed; 0 survivor(s)" in result.stderr
    assert not list(tmp_path.glob("*.json")), "a finished run leaves no live record"


def test_the_wall_limit_kills_the_whole_tree_not_just_the_command(tmp_path: Path) -> None:
    """At the deadline the command and every process it started are killed: status 124."""
    pids = tmp_path / "pids"
    script = f"sleep 30 & echo $! >> {pids}; sleep 30 & echo $! >> {pids}; wait"
    started = time.monotonic()

    result = run_tool("--wall", "2", "--", "bash", "-c", script, registry=tmp_path)

    assert result.returncode == bounded_run.EXIT_TIMEOUT
    assert time.monotonic() - started < 15
    children = [int(line) for line in pids.read_text().split()]
    assert len(children) == 2
    assert not any(alive(pid) for pid in children), "a grandchild survived the deadline"
    assert "0 survivor(s)" in result.stderr


def test_stragglers_left_behind_by_a_finished_command_are_killed_and_counted(tmp_path: Path) -> None:
    """A command that exits but leaves a background child behind does not leave it running."""
    pid_file = tmp_path / "pid"

    result = run_tool("--wall", "20", "--", "bash", "-c", f"sleep 30 & echo $! > {pid_file}; exit 0",
                      registry=tmp_path)

    assert result.returncode == 0
    assert "1 straggler(s) killed; 0 survivor(s)" in result.stderr
    assert not alive(int(pid_file.read_text()))


def test_the_watchdog_kills_the_group_even_when_the_runner_is_killed(tmp_path: Path) -> None:
    """SIGKILL cannot be caught, so the bound must hold without the runner: the incident's path."""
    pid_file = tmp_path / "pid"
    runner = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--min-free-gb", "0", "--abort-free-gb", "0",
         "--wall", "3", "--", "bash", "-c", f"echo $$ > {pid_file}; for i in $(seq 1 300); do sleep 0.2; done"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    looping = 0
    try:
        assert wait_until(pid_file.exists, 10)
        looping = int(pid_file.read_text())
        assert alive(looping)

        runner.send_signal(signal.SIGKILL)
        runner.wait(timeout=10)

        assert alive(looping), "the command is orphaned now, as in the incident"
        assert wait_until(lambda: not alive(looping), 3 + bounded_run.WATCHDOG_SLACK_S + 5), "the watchdog did not fire"
        check = run_tool("--check", registry=tmp_path)
        assert check.returncode == 0, check.stdout
    finally:
        kill_group_quietly(runner.pid)
        if looping:
            kill_group_quietly(looping)


def test_check_reports_a_live_run_and_clears_once_it_ends(tmp_path: Path) -> None:
    """--check is the end-of-work proof: 1 while anything registered runs, 0 after."""
    runner = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--min-free-gb", "0", "--abort-free-gb", "0",
         "--wall", "4", "--", "sleep", "30"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    command_group = 0
    try:
        assert wait_until(lambda: bool(list(tmp_path.glob("*.json"))), 10)
        (path,) = tmp_path.glob("*.json")
        command_group = json.loads(path.read_text())["pid"]
        during = run_tool("--check", registry=tmp_path)
        runner.wait(timeout=30)
        after = run_tool("--check", registry=tmp_path)
    finally:
        kill_group_quietly(runner.pid)
        if command_group:
            kill_group_quietly(command_group)

    assert during.returncode == 1 and "LIVE" in during.stdout
    assert after.returncode == 0 and "no live runs" in after.stdout


def test_a_writer_cannot_grow_a_deleted_file_past_the_file_size_cap(tmp_path: Path) -> None:
    """The incident's file was deleted and still open; RLIMIT_FSIZE bounds it all the same."""
    writer = (
        "import os, tempfile\n"
        "f = tempfile.TemporaryFile()\n"  # unlinked at creation, like pytest's capture file
        "written = 0\n"
        "try:\n"
        "    while True:\n"
        "        f.write(b'x' * 65536); f.flush(); written += 65536\n"
        "except OSError as exc:\n"
        "    print('stopped at', written, exc.errno)\n"
    )

    result = run_tool("--wall", "30", "--max-file-mb", "2", "--", sys.executable, "-c", writer, registry=tmp_path)

    assert "stopped at" in result.stdout
    stopped = int(result.stdout.split("stopped at")[1].split()[0])
    assert stopped <= 2 * 1024 * 1024
    assert "exited" in result.stderr


def test_a_busy_loop_dies_of_the_cpu_limit_even_with_wall_time_left(tmp_path: Path) -> None:
    """RLIMIT_CPU holds for a process that escaped every other bound."""
    started = time.monotonic()

    result = run_tool("--wall", "60", "--cpu", "1", "--", sys.executable, "-c", "while True: pass",
                      registry=tmp_path)

    assert time.monotonic() - started < 20
    assert result.returncode == 128 + signal.SIGXCPU, result.stderr


def test_the_disk_floor_refuses_to_start_the_command(tmp_path: Path) -> None:
    """Below --min-free-gb nothing runs and the status is 125."""
    marker = tmp_path / "ran"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--min-free-gb", "1e12", "--",
         "touch", str(marker)],
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode == bounded_run.EXIT_DISK
    assert "refused" in result.stderr
    assert not marker.exists()


def test_falling_below_the_abort_floor_stops_a_running_command(tmp_path: Path, mocker) -> None:
    """Free space measured while the command runs stops it: status 125."""
    readings = iter([100.0] + [1.0] * 1000)
    mocker.patch.object(bounded_run, "lowest_free_gb", side_effect=lambda paths: next(readings))
    limits = bounded_run.Limits(wall_s=30, cpu_s=30, max_file_bytes=1 << 20, min_free_gb=50, abort_free_gb=20)

    started = time.monotonic()
    status = bounded_run.run(limits, ["sleep", "30"], tmp_path, None, tail_kb=1)

    assert status == bounded_run.EXIT_DISK
    assert time.monotonic() - started < 15


def test_a_record_is_written_for_the_life_of_the_run(tmp_path: Path) -> None:
    """The registry record names the group, the command and the deadline while the run lives."""
    runner = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--registry", str(tmp_path), "--min-free-gb", "0", "--abort-free-gb", "0",
         "--wall", "3", "--", "sleep", "30"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    record: dict = {}
    try:
        assert wait_until(lambda: bool(list(tmp_path.glob("*.json"))), 10)
        (path,) = tmp_path.glob("*.json")
        record = json.loads(path.read_text())
        runner.wait(timeout=30)
    finally:
        kill_group_quietly(runner.pid)
        if record:
            kill_group_quietly(record["pid"])

    assert record["command"] == ["sleep", "30"]
    assert record["deadline"] - record["started"] == pytest.approx(3 + bounded_run.WATCHDOG_SLACK_S, abs=0.5)
    assert not path.exists()


def test_without_a_command_the_tool_refuses() -> None:
    """A run needs a command after --."""
    with pytest.raises(SystemExit):
        bounded_run.main(["--wall", "5"])


def test_a_nested_run_keeps_the_tighter_inherited_limits(tmp_path: Path) -> None:
    """Inside a run capped at 2 MB, a nested run asking for 1 GB still gets 2 MB, and runs."""
    inner = [sys.executable, str(SCRIPT), "--registry", str(tmp_path / "inner"), "--min-free-gb", "0",
             "--abort-free-gb", "0", "--max-file-mb", "1024", "--", sys.executable, "-c",
             "import resource; print('fsize', resource.getrlimit(resource.RLIMIT_FSIZE)[1])"]

    result = run_tool("--wall", "30", "--max-file-mb", "2", "--", *inner, registry=tmp_path)

    assert f"fsize {2 * 1024 * 1024}" in result.stdout, result.stdout + result.stderr

