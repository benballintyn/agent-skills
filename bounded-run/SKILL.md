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

**Run every test suite, mutant, benchmark, build or long command through `bounded_run.py`.** Never write your own runner around `subprocess.run(timeout=…)`: that timeout kills one process, not what it started.

```bash
python3 ~/.agents/skills/bounded-run/bounded_run.py --wall 600 -- uv run pytest -q
```

Each command is bounded four independent ways:

| Bound | What it stops | Holds if the runner is killed? |
|---|---|---|
| Its own process group, killed as a unit on deadline, exit, SIGTERM/INT/HUP | grandchildren outliving the run | no (that is what the next rows are for) |
| A watchdog inside the group that kills it at the deadline (+11 s slack) | the incident: the runner gone, the group still looping | **yes** |
| `RLIMIT_CPU` (default: the wall limit, in CPU seconds) and `RLIMIT_FSIZE` (default 1 GB per file), inherited by every child | a busy loop; any file growing without bound, including deleted-but-open ones | **yes**, even for a process that leaves the group |
| Disk floor: refuses below `--min-free-gb` (default 50), stops below `--abort-free-gb` (default 20) | filling the disk | no |

Exit status: the command's own; **124** at the deadline; **125** at the disk floor; **128+N** for death by signal N (152 = SIGXCPU, the CPU limit; 153 = SIGXFSZ, the file-size limit). The end of the log is printed, and a summary line on stderr counts stragglers killed and survivors. **Any survivor is a bug: report it.**

## Choosing bounds

- One test-suite run: `--wall` about 3× its normal duration. tally's full suite takes about 2 minutes, so 600.
- One mutant: `--wall 300`. A hang is then a result (124), and the process is gone.
- Nested runs keep the tighter of the inherited and requested limits. A run can never loosen its parent's.
- Parallel workers: **2 by default**. Each full repo copy plus its environment costs about 0.5–1 GB, and the disk floor applies per run.

## Mutation runners

A runner that loops over mutants calls `bounded_run.py` for each one: a subprocess per mutant, with `--wall`. It reads the status: 124 is a hang, so **TIMEOUT, never KILLED**. Restore the file from a backup copy in a `finally`, never with `git checkout`, which discards your uncommitted edits.

Tests that prove a loop ends need their own bound as well: pytest-timeout, or an iteration cap in the fake. Then a mutant that removes the loop's exit fails instead of hanging.

## Before handing work back

```bash
python3 ~/.agents/skills/bounded-run/bounded_run.py --check
```

This exits 1 and lists every registered run that still has a live process. Run it before you report. If anything is listed, it is yours: say so in your report, with its pid and command. Never report "done" with a process of yours still running. A reported "hang" or "timeout" is a process to account for, not a fact about code.

Coordinators run it, and `ps -axo pid,etime,command | grep pytest`, after every agent stops.

## Limits of the tool

- A process that starts its own session (`setsid`, `start_new_session=True`) leaves the group. It still carries the CPU and file-size limits, but the watchdog and `--check` cannot see it. Bound such children yourself.
- `RLIMIT_CPU` counts CPU time per process. A process that sleeps forever is not stopped by it. Only the group kill and the watchdog stop it, and those hold only while it stays in the group.
- Logs go to the registry (default `$TMPDIR/bounded-run/`), are capped by the file-size limit, and are pruned after 7 days.

Tests: `test_bounded_run.py` proves each bound against a real misbehaving process. Run it with `bounded_run.py --wall 180 -- python -m pytest test_bounded_run.py`.
