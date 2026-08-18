---
name: release-vox
description: Cut a new release of the vox LLM library (the `vox-llm` package on PyPI; repo at github.com/benballintyn/vox). vox releases are automated with release-please — this skill walks through reviewing and merging the standing release PR (which tags, GitHub-Releases, and publishes to PyPI automatically), monitoring the run, handling failures, and the post-release consumer updates (Tomte, Ithildin, etc.). Use this skill whenever the user mentions releasing, shipping, publishing, cutting, or tagging vox; bumping vox's version; pushing vox to PyPI; or anything like "release v0.1.2" / "ship vox 0.2" / "cut a new vox release". Also use this when the user says "ship the X feature" if the work landed in vox and now needs a release. Trigger eagerly — under-triggering this skill costs a real human release that someone has to clean up; over-triggering just means a slightly verbose answer.
---

# Release vox

Drive a vox release. The repo is at `/Users/bb/personal/repos/vox` (GitHub: `benballintyn/vox`). It publishes to PyPI as **`vox-llm`** (the bare name `vox` was taken); the Python import name stays `vox`.

## How vox releases work

Releases are **automated with release-please** — there is no manual version bump and no manual tagging.

- `release_please.yml` runs on every push to `main`. It reads Conventional Commit PR titles since the last release and maintains a **standing release PR** titled `chore(main): release X.Y.Z`. That PR bumps `pyproject.toml`, updates `.release-please-manifest.json`, and prepends a `CHANGELOG.md` section. It re-updates itself as more PRs land.
- **Cutting a release = merging that release PR.** On merge, release-please creates the `vX.Y.Z` tag and the GitHub Release, then a `publish` job in the same workflow run calls `pypi-publish.yml` (reusable workflow) which builds and publishes to PyPI via OIDC trusted publishing.
- The **version is computed** from commit types — you do not choose it. `fix:` → patch, `feat:` → minor, `feat!:` or a `BREAKING CHANGE:` footer → major. `chore:`/`docs:`/`ci:`/`refactor:`/`test:`/`build:`/`perf:`/`style:` do not bump the version.

The one irreversible step: PyPI accepts a given version exactly once, forever. Most of this skill is about reaching that step in a good state.

## Why this skill exists

vox is the canonical LLM client across Ben's projects ("Vox: core LLM client across projects" preference in memory). Releases happen often. The mechanics are automated, but the *judgement* — is the release PR correct, is the changelog right, which consumers need bumping — and the *failure handling* are worth not re-deriving each time.

## Stage 1: Confirm what's being released

- **Are all the intended feature/fix PRs merged into `main`?** Anything still on a branch won't be in the release. `gh pr list --repo benballintyn/vox --state open`.
- **Were their PR titles Conventional Commits?** release-please only counts conventionally-typed commits. A fix merged as `chore:` won't bump the version or appear in the changelog. (See the "Squash-merge + Conventional Commits" memory — the repo's "Default commit message → Pull request title" setting must be on, or single-commit PRs land with the branch commit's type instead of the PR title.)

## Stage 2: Review the release PR

Find the standing release PR:

```bash
gh pr list --repo benballintyn/vox --state open    # look for "chore(main): release X.Y.Z"
gh pr view <N> --repo benballintyn/vox
```

Check:
- **Version** — is `X.Y.Z` what you expect given what's being shipped? If a `feat:` was mistyped `fix:`, the bump may be too small. Usually accept it (fixing commit history is not worth it) and tell the user.
- **CHANGELOG** — do the entries read well and cover the user-visible changes?

**Optional hand-edit:** you may edit the release PR's `CHANGELOG.md` directly (e.g. to add a line for a fix that landed as a mistyped `chore:`). If you do — **merge the release PR without pushing anything else to `main` first.** Any other push makes release-please regenerate the PR and clobber the edit.

If there is **no** release PR: either no versioning-type (`fix:`/`feat:`) commits have landed since the last release, or the repo setting "Allow GitHub Actions to create and approve pull requests" is off (Settings → Actions → General → Workflow permissions).

## Stage 3: Cut the release

Confirm with the user before merging — this is the point of no return:

> "Merging the release PR will publish `vox-llm X.Y.Z` to PyPI. That version is burned permanently. Proceed?"

Then merge it (squash, like every vox PR):

```bash
gh pr merge <N> --repo benballintyn/vox --squash
```

On merge, `release_please.yml` runs: the `release-please` job tags `vX.Y.Z` + creates the GitHub Release, then the `publish` job calls `pypi-publish.yml` → verify → test (3.11/3.12/3.13) → build → publish to PyPI.

## Stage 4: Monitor + failure modes

```bash
gh run watch --repo benballintyn/vox
```

Watch the `release-please` workflow run through to the end. Don't assume success.

| Symptom | Fix |
|---|---|
| No release happened after merge — only the `release-please` job ran, no `publish` | release-please didn't think it cut a release. Confirm the merged PR was the `chore(main): release ...` PR, not something else. |
| `publish` job failed before PyPI accepted | Tag + GitHub Release exist but PyPI didn't get the version. Fix the cause, then re-run publish for the tag: `gh workflow run pypi-publish.yml --repo benballintyn/vox -f tag=vX.Y.Z`. |
| `publish-pypi`: `File already exists` | The version was already published. **PyPI versions are immutable.** A new patch release is the only way forward — land a `fix:` and merge the next release PR. |
| `publish-pypi`: trusted-publisher / OIDC error on the *upload* | Mismatch with the PyPI publisher config. Check https://pypi.org/manage/project/vox-llm/settings/publishing/ — Owner `benballintyn`, Repository `vox`, Workflow filename **`pypi-publish.yml`**, Environment `pypi`. |
| `publish-pypi`: `Invalid attestations supplied during upload: ... does not match expected Trusted Publisher` | **This should not happen on vox.** As of PR #32 (`ci(publish): disable PEP 740 attestations`), vox passes `attestations: false` to the publish action — PyPI doesn't support attestations from reusable-workflow chains and the `release_please.yml` → `pypi-publish.yml` architecture is exactly that shape. If you see this error: the workflow change was reverted, OR you're on a release pre-#32. **Break-glass for one release**: `gh workflow run pypi-publish.yml --repo benballintyn/vox -f tag=vX.Y.Z` — dispatching directly makes `pypi-publish.yml` the root workflow so the attestation matches. Permanent fix: re-apply or keep `attestations: false`. |
| tests failed in the `publish` run | A real regression on the tagged commit. The tag/Release exist but PyPI is un-published. Land the fix on `main`, which produces a new release PR for the next patch; merge that. The bad tag can be left or deleted. |

**Break-glass publish:** `gh workflow run pypi-publish.yml --repo benballintyn/vox -f tag=vX.Y.Z` builds and publishes any existing tag manually. Use it when the automated `publish` job failed for an infrastructure reason and PyPI never received the upload.

## PyPI trusted-publisher setup

Trusted publisher entry at https://pypi.org/manage/project/vox-llm/settings/publishing/:
- Owner `benballintyn`, Repository `vox`, Environment `pypi`, Workflow filename **`pypi-publish.yml`**.

Only the one entry is needed. (An earlier iteration of this skill said to also register `release_please.yml` to make PEP 740 attestations work through the reusable-workflow chain — that turned out to not actually fix the underlying issue, see the "PyPI Trusted Publishers × reusable workflows" memory page. Disabling attestations is the real fix.)

The OIDC *upload-auth* path resolves the workflow via `job_workflow_ref`, which is the callee (`pypi-publish.yml`) when called via `workflow_call` from `release_please.yml`. That matches the single trusted-publisher entry above. PEP 740 attestation signing uses `workflow_ref` (the caller, `release_please.yml`) — which PyPI rejects regardless of how many publishers are registered. We sidestep that whole path by setting `attestations: false`.

## Stage 5: Post-release

1. **Verify on PyPI:**
   ```bash
   curl -s https://pypi.org/pypi/vox-llm/json | python3 -c "import sys,json; print(sorted(json.load(sys.stdin)['releases']))"
   ```
   The new version should be listed.

2. **Tell the user**: version, PyPI URL (`https://pypi.org/p/vox-llm`),
   and GitHub Release URL.

The release is done at that point. Do **not** mention downstream
consumers (Tomte, Ithildin, etc.) — bumping their `vox-llm` pin is a
separate task on their own cadence, and surfacing it as a "follow-up"
here turns every vox release into a nudge toward unrelated work.

## Reference

- `release_please.yml` — runs release-please; `publish` job calls `pypi-publish.yml` when a release is cut.
- `pypi-publish.yml` — reusable workflow (`workflow_call` + `workflow_dispatch`); verify → test → build → publish to PyPI.
- `pr_title.yml` — enforces Conventional Commit PR titles (the input release-please parses).
- `run_tests.yml` — CI on every push/PR (ruff + mypy + pytest). Red on `main` ⇒ a release will fail too.
- Memory: "Vox: core LLM client across projects" (upstream-first policy), "Squash-merge + Conventional Commits" (why PR titles are load-bearing), "GitHub Actions: GITHUB_TOKEN events don't trigger workflows" (why publish is a called workflow, not tag-triggered).
