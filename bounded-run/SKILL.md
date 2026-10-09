---
name: bounded-run
description: Run tests, mutation runs, builds and any other long or untrusted command so that neither it nor anything it starts can outlive its bounds or fill the disk. Use for EVERY test suite, mutant, benchmark or background job an agent starts; never write your own runner with a bare subprocess timeout. Also use its --check before handing work back.
---

# bounded-run

On 2026-10-09 an orphaned test process filled the owner's disk:
- A mutation runner's `subprocess.run(timeout=150)` killed only pytest.
- A process under a hung test survived. It looped for 42 hours and wrote about 335 GB into pytest's output-capture file, which pytest deletes at creation, so `du` and Finder showed nothing.

The rule against this ("kill the process group") was already in the learnings file, as prose. This tool enforces it.

## The rule

**Run every test suite, mutant, benchmark, build or long command through `bounded_run.py`,** with `BOUNDED_RUN_OWNER` set to something unique to you (your scratch directory works) **on the same command line**. Agent shells often do not keep an `export` from one call to the next. Never write your own runner around `subprocess.run(timeout=…)`: that timeout kills one process, not what it started.

```bash
BOUNDED_RUN_OWNER=/private/tmp/my-scratch python3 ~/.agents/skills/bounded-run/bounded_run.py --wall 600 -- uv run pytest -q
```

## What bounds a command

| Bound | What it stops | Holds if the runner is killed? |
|---|---|---|
| Its own process group, killed as a unit on deadline, exit, SIGTERM/INT/HUP | grandchildren outliving the run | no (that is what the next rows are for) |
| A watchdog inside the group. It ignores every signal it can (all but SIGKILL, SIGSTOP and SIGCHLD), kills the group at the deadline + 11 s, brings that forward to 6 s once the runner starts stopping, and enforces the disk floor (the run then reports 125). | the incident: the runner gone, the group still looping | **yes** |
| Escapees, tracked two ways by both the runner and the watchdog. **Descendants:** every process descended from the group, polled twice a second. **Run tag:** `BOUNDED_RUN_ID` in the environment. | processes that left the group (`setsid`, `start_new_session=True`, `process_group=0`, job control) | **yes** |
| `RLIMIT_FSIZE` (default 1 GB per file), inherited by every child | any file growing without bound, including deleted-but-open ones like the incident's | **yes** |
| `RLIMIT_CPU` (default: wall + 16 CPU-seconds per process) | a pure computation loop | **yes**, but on macOS **not** a loop that makes a system call each pass (writes, sleeps, clock reads), and macOS does not enforce the hard CPU limit |
| Disk floor: refuses below `--min-free-gb` (default 50), stops below `--abort-free-gb` (default 20) | filling the disk | **yes** (the watchdog checks it too) |

**Exit status:**
- the command's own status;
- **124**: the deadline;
- **125**: the disk floor, whether the runner or the watchdog stopped the run;
- **128+N**: death by signal N:
  - 152 = SIGXCPU (the CPU limit);
  - 153 = SIGXFSZ (the file-size limit, for non-Python commands; a Python command's writes fail with EFBIG instead);
  - 137 = SIGKILL (for example, the watchdog firing while the runner was stopped).

The end of the log is printed. On stderr, a summary line counts stragglers killed, escapees killed and survivors, and each straggler is named with its command. **Any survivor is a bug: report it.** Do not pipe the runner through `tail` or similar, because that loses its exit status. Read the summary line and the status.

## Choosing bounds

- **One test-suite run:** `--wall` about 3× its normal duration. tally's full suite takes about 2 minutes, so 600.
- **One mutant:** `--wall 300`. A hang is then a result, and the process is gone.
- **Nested runs** keep the tighter of the inherited and requested limits. A run can never loosen its parent's.
- **Never wrap the runner** in another timeout shorter than `--wall` + 25 s. That kills the runner, not the command; the watchdog still bounds the command, but you lose the report.
- **Parallel workers: 2 by default.** Each full repo copy plus its environment costs about 0.5–1 GB, and the disk floor applies per run.
- **125 (refused or stopped at the floor):** report it. Never lower the floor to get past it.
- **Threads:** `RLIMIT_CPU` counts every thread of a process, so a healthy multithreaded process can reach 152 before the wall. Raise `--cpu` for it explicitly.

## Mutation runners

A runner that loops over mutants calls `bounded_run.py` for each one: a subprocess per mutant, with `--wall`. It reads the status:
- **124, 137 or 152 is a TIMEOUT (a hang), never KILLED.**
- A collection error is INDETERMINATE.

Mutate a **copy** in your own scratch directory, never a file someone else may be running. Restore from a backup copy in a `finally`, never with `git checkout`, which discards your uncommitted edits.

Tests that prove a loop ends need their own bound as well: pytest-timeout in **thread** mode, or an iteration cap in the fake. Then a mutant that removes the loop's exit fails instead of hanging. The default signal mode cannot stop a loop running in a worker thread.

## Before handing work back

```bash
BOUNDED_RUN_OWNER=/private/tmp/my-scratch python3 ~/.agents/skills/bounded-run/bounded_run.py --check
```

This exits 1 and lists every run of **your owner** that still has a live process. It always prints which owner it checked. Without `BOUNDED_RUN_OWNER`, it checks **every** run and says so: it never gives a quiet all-clear for someone else's owner. Run it before you report:
- If anything is listed, say so in your report, with its pid and command.
- Never report "done" with a process of yours still running.
- A reported "hang" or "timeout" is a process to account for, not a fact about code.

Coordinators run `--check --all` and `ps -axo pid,etime,command | grep pytest` after every agent stops.

## Limits of the tool

- **The escapee poll can miss a process.** If a parent starts a detached child and exits between two polls (0.5 s), descendant tracking misses that child. The run tag still finds it if it is not an Apple system binary: on macOS, `ps` shows the environment of Homebrew Python and similar binaries, but not of `/bin/bash` or `/bin/sleep`.
- **A process that clears its environment and detaches within that window is not tracked.** It still carries the file-size limit, and the CPU limit for pure computation.
- **Logs** go to the registry (default `$TMPDIR/bounded-run/`). They are capped by the file-size limit, and only this tool's own logs are pruned, after 7 days.

Tests: `test_bounded_run.py` proves each bound against a real misbehaving process. Run it with `bounded_run.py --wall 400 -- python -m pytest test_bounded_run.py`, using a previous, known-good copy of the runner as the outer bound when changing the runner itself. A copy can have any file name: the watchdog is known by its run id.
