# Serial Job Session (serial-job-session for DSH)

> Plugin version `5.1.1`; skill protocol version `5.0.0` (`VERSION`). They move independently — upgrading the plugin never reports `TEMPLATE_CHANGED` for existing instances.
>
> 中文：[INTRO.md](INTRO.md)

**In one sentence**: it turns a long AI task into a serial workflow with a contract, versions and evidence, so a dropped session never loses progress and acceptance rests on evidence rather than impressions.

## Problems it solves

| Common trouble | What this workflow does |
|---|---|
| Progress dies with the session or the day | Progress lives in instance files (append-only logs), not in chat memory |
| “Done” with no evidence, review by impression | Every task card carries a contract, mandatory checks and evidence paths; an independent reviewer judges each item |
| Runaway, drifting rework | At most three rework rounds after the first implementation; exhausted work is preserved and never blocks independent tasks |
| Agents “authorizing” themselves | Three decision modes (manual / conditional / full AI); the authorization boundary lives in the contract and no agent can impersonate human approval |
| Expensive multi-agent handovers | Role/domain session reuse plus a minimal handoff package; parent and child tasks share one instance and never copy the framework |
| State scattered across chat, no audit trail | The JSONL log is the only source of truth: `check` replays it, `state` is rebuilt from it |

## Why it stays lightweight

- **Shared method, data-only instances**: a new instance is four files (`run.json`, `state.json`, two JSONL logs). No template copies, no per-card Markdown.
- **Append-only**: committed events are never rewritten; revisions are new events, and failure records stay.
- **Disposable snapshot**: `state` is a projection — `check` validates it, `rebuild` recreates it.
- **Lazy modules**: estimate timing and the Feishu mirror are off by default; while off, they perform no probing and no remote calls.
- **One entry point**: `init / context / start / apply / handoff / check / rebuild / recover / stats / sync`.

## Strengths

- **Pause and resume at will**: `context` returns the goal, authorization, current card and the bounded menu in one read; a week later you can pick it up again.
- **Human decisions leave a trail**: approvals carry their source; tool calls use idempotent `command_id` values and revision checks.
- **Verifiable and auditable**: log replay plus template integrity; a template change is reported as `TEMPLATE_CHANGED` instead of silently rewriting history.
- **Bounded cost**: 12000-character context budget by default; logs rotate at 20 cards or 512 KiB.
- **Native DSH integration**: the skill loads from the session catalog; the `sjs` tool uses the session working directory and Python for you; a settings panel changes interpreter, timeout and extra arguments.
- **Failures are not laundered**: a failed or unknown mandatory check is recorded as such and cannot be overridden by a score.

## When to use it — and when not

- **Good fit**: multi-session, multi-stage engineering work that needs evidence (UI rework, refactors, migrations, batch fixes); deliveries requiring independent acceptance; mixed human + AI collaboration; long-running work that stops today and continues tomorrow.
- **Poor fit**: one-shot small changes, pure Q&A, or fully unattended runs that must never record a failure — this workflow records failures truthfully instead of hiding them.

## Using it in DSH

1. **Install** (one of the two, never both): `dsh plugin add github:FMJ9922/serial-job-session`, or a local directory with `dsh plugin add <repo path>`.
2. **Create an instance**: have the agent run `sjs init --dry-run` to confirm the goal, authorized paths, decision mode and failure handling (the mode has no default).
3. **Work day to day**: say `route_key：意图` (for example `界面优化：推进`), or have the agent call `sjs context` to read the current card.
4. **Close out**: implement → self-check → independent reviewer acceptance → close, recording the evidence-backed lessons.
5. **Tune it**: Settings → Serial Jobs, or the same page on the plugin's bundle page; interpreter, timeout and extra CLI arguments apply on the next `sjs` call.
6. **Where the data lives**: instances stay inside the project (gitignore them); plugin parameters live in `$DSH_HOME/serial-job-session.json`.

## What the plugin adds to DSH

| Piece | Role |
|---|---|
| Skill `serial-job-session` | Loaded on demand from the session catalog; its method text and `references/` resources are read in place from the repository |
| Tool `sjs` | Runs `scripts/sjs.py` in the session working directory, sending JSON payloads on stdin and returning the CLI's JSON |
| Settings panel | Settings → Serial Jobs (also on the bundle page): interpreter, timeout, extra arguments, environment probe and read-only facts |
| Local JSON API | `GET /serial-job-session/api/state`, `POST /serial-job-session/api/settings`, `POST /serial-job-session/api/probe` (same-origin only, JSON body required) |

## Cautions

- Do not also copy this repository into `~/.dsh/skills` or a project `.dsh/skills`: a same-named filesystem entry shadows the bundle registration.
- After the first installation the Harness needs **one restart** before the UI half (panel and settings page) loads.
- Python 3.11+ is required (standard library only, no pip dependencies).
- The panel API accepts same-origin requests only; neither the settings file nor instance data contains secrets.

More detail: [README](../README.en.md) and [INSTALL](../INSTALL.md).
