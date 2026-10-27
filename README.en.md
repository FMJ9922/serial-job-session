[简体中文](README.md) | English

# serial-job-session v5

A lightweight long-running workflow for AI agents: **shared methods, instance parameters, append-only records**. Contracts, independent review, bounded rework, reusable agent sessions and nested tasks share one event store.

## Quick start

Python 3.11+, standard library only. Install in your host's skill directory; instances must live outside the skill. Review and edit an example configuration before using it:

```bash
python scripts/sjs.py init --instance <project>/.cursor/workflows/demo --input examples/manual.json --dry-run
python scripts/sjs.py init --instance <project>/.cursor/workflows/demo --input examples/manual.json
python scripts/sjs.py context --instance <project>/.cursor/workflows/demo
```

At creation, ask only for missing choices: manual / conditional / full AI; conditional risk and score thresholds; failure handling; optional estimates/timing and Feishu Base mirroring. **There is no default autonomy mode.** Suggested values need human confirmation. Existing answers persist across tasks; task-specific exceptions are recorded explicitly.

Examples: [manual](examples/manual.json), [conditional](examples/conditional.json), [full AI](examples/full-ai.json). Their paths, thresholds and budgets are examples, not universal authorization. Host routing must explicitly invoke the skill; see [installation](INSTALL.md).

## Install as a DSH (DeepSeek Harness) bundle

This repository is also a **DSH bundle**. When installed it registers `SKILL.md` as the Harness skill `serial-job-session` (the repository root stays its resource base) and adds an `sjs` tool that runs `scripts/sjs.py` inside the calling session's working directory, so callers never deal with the checkout path or the interpreter location. Every daily subcommand — `init`, `context`, `start`, `apply`, `check`, `recover` and the rest — is reachable through that one tool.

Pick one installation (do not mix them):

- Local directory, for development and live edits: `dsh plugin add <absolute path of this repository>`, or have an agent call `plugin_manager` `install_bundle` with that path as `target`.
- From GitHub: `dsh plugin add github:FMJ9922/serial-job-session`.

After installation the Plugins page lists **Serial Job Session**, new sessions advertise `serial-job-session`, and the `sjs` tool is available. Python 3.11+ is required; set `SJS_PYTHON` when `python` is not on `PATH`, and `SJS_TIMEOUT_MS` to change the per-call timeout (default 120000 ms).

Three differences from a plain skill install:

1. **Do not also copy or clone this repository into `~/.dsh/skills` or a project `.dsh/skills`.** A same-named filesystem entry shadows the bundle registration — measured here: the filesystem copy registered first, won the name, and shipped `disable-model-invocation: true`, so the model catalog never saw the skill. Keep exactly one installation.
2. The bundle registration permits model invocation by default (`invocation.modelInvocable: true`). That intentionally relaxes the `disable-model-invocation: true` written for filesystem hosts; set `SJS_MODEL_INVOCABLE=false` in the Host environment before starting DSH to keep the stricter user-only policy.
3. The bundle is only glue: `dsh/index.js` (Host half), `dsh/cordis.patch.yml` (inserted row), `dsh/locale/{en,zh}.json` and `dsh/icon.svg` (Plugins page), and `dsh/plugin.test.mjs` (offline checks via `node --test dsh/plugin.test.mjs`). `scripts/`, `references/` and `schemas/` stay the single source of truth.

Model invocation is on by default because the Harness catalog *is* its on-demand loading path, and the entry description already restricts use to an explicit name, a `route_key：意图` phrase, or an already-bound workflow. Turn it off with the switch above if you want invocation to stay strictly human.

Maintenance note: `check_skill_integrity.py` excludes `dsh/`, `node_modules/` and the root `package.json` from the skill-text baseline, so bundle packaging never counts as skill content.

## Instance layout

```text
demo/
  run.json
  state.json
  records/
    tasks-000001.jsonl
    direction-000001.jsonl
```

The creation configuration is immutable. Later policy changes are events. Logs are authoritative; the snapshot is disposable. Optional artifacts/ and overrides/ stay flat, at most two file levels deep. Nested task IDs never create nested instances or duplicate the framework.

The task log rolls after 20 completed top-level tasks or 512 KiB; the direction log after 20 records or 512 KiB. Limits are configurable. Corrections append to the current segment. Warm context reads the snapshot and new records, not sealed history or evidence contents.

## Behavior

- Manual: people decide starts, direction and acceptance.
- Conditional: agents proceed only within confirmed score, risk, authorization and evidence requirements.
- Full AI: agents decide within authorized scope and budgets. Low confidence or high risk does not automatically request a human, but failed or unknown mandatory checks never become passes.

Scores are judgments, not calibrated success probabilities. Default rework is three rounds after the first implementation. On exhaustion, preserve results and skip to independent work unless stop was selected. Failed tasks never satisfy dependencies.

Reuse suitable role/domain sessions through the host's existing collaboration tools. Validate handles and refresh role, contract and authorization after compaction. Recreate expired sessions using a bounded handoff. Reviewers cannot review their own implementation; cache savings are measured, not promised.

Nested workflows are built in: two child levels and eight direct children per parent by default, adjustable at creation. All required children must be accepted before the parent. Shared editor resources and the controller event log have one writer.

Estimates/timing and Feishu mirroring are optional. Disabled modules do not collect host data or call remote services. Enabled timing preserves unknowns; Feishu uses idempotent task keys and readback, with retry receipts in the same log.

## Commands and verification

The daily CLI is `scripts/sjs.py`: init / context / start / apply / handoff / check / rebuild / recover / stats / sync. Routes use an explicit registry, never a search across all instances. API examples, recovery and the full operation list are in [runtime](references/runtime.md); policies and modules are linked from [SKILL.md](SKILL.md).

```bash
python -m unittest discover -s scripts -p "test*.py"
python -m unittest discover -s Test/serial-job-session -p "test*.py"
python scripts/check_skill_integrity.py
```

v5 replaces the previous runtime. There is no v3/v4 execution compatibility or importer. Existing business instances are untouched; create a new v5 instance. See [contributing](CONTRIBUTING.md), [changelog](CHANGELOG.md) and [security](SECURITY.md).

MIT. Author: 沈劢. Copyright: 北京钛核互动信息技术有限公司. [Credits](CREDITS.md).
