[简体中文](README.md) | English

# serial-job-session v5

**An AI-assisted orchestration protocol for commercial work that spans days and weeks.**

Keep sustained AI work aligned with its goals and acceptance criteria. **Persist the key facts, make handoffs traceable, and reduce dependence on chat memory and context compaction.**

Once work outgrows one agent's context, people often have to keep splitting tasks, repeating background, coordinating executors, and checking whether “done” still means what it meant before several rounds of rework. serial-job-session helps AI assist with that orchestration: goals, task contracts, candidate versions, reviews, failures and next steps live in the project. A resumed session reads the current state, follows dependencies and evaluates work against agreed criteria.

**Routes may change, with recorded reasons and explicit carryover of outstanding acceptance obligations.** People choose manual, conditional or full-AI operation and establish their involvement and authorization boundaries up front.

## Who it is for

People already using AI for commercial work that spans days or weeks: iterative development, ongoing refactors, system migrations, and engineering efforts involving multiple people or agents. People and teams supply goals, domain knowledge and success criteria; AI uses the protocol to assist with decomposition, orchestration, execution and review.

In agile development, an iteration goal can be broken into dependent, independently verifiable tasks, with routes adjusted as new evidence appears. Task records carry facts across handoffs; existing backlog, sprint and team collaboration tools retain their own roles.

## What it focuses on preserving

1. **Acceptance criteria remain effective across attempts.** Reviews bind to contract and candidate versions; a new candidate cannot reuse an old acceptance. A failed or unknown mandatory check, or one without an evidence reference, cannot be outweighed by a high score. Removing or changing an existing mandatory check requires a recorded real human approval and invalidates the old candidate and assessments.
2. **A different session or executor has a recorded basis for continuing.** Proposals, work in progress, candidates, accepted work and failures have distinct states. Append-only records retain history; `context` / `handoff` supplies the current contract, dependencies, feedback and next steps. Handoffs do not depend solely on a compacted chat summary.
3. **Routes can change without silently dropping unfinished acceptance work.** Direction assessments record alternatives, reasons and unknowns before adoption under the selected mode. When tasks are reorganized, the orchestrator must explicitly identify which contracts carry outstanding obligations. Original failures remain recorded, and failed dependencies do not count as accepted.
4. **People choose their involvement and can see key changes.** Starts, direction and acceptance follow manual, conditional or full-AI policy. Checkpoint briefs are required to explain results, evidence, next steps and pending decisions. Rework is bounded; exhaustion stops dispatch or permits independent work according to policy, preserving results and recovery references.
5. **Validated methods can be reused.** A shared skill holds methods; instances hold parameters and facts. Lessons carry evidence references, and method upgrades require explicit adoption. Projects do not need to copy the entire framework or repeat every rule at each handoff.

### A concrete example

A two-week system migration requires preserved behavior, regression tests and validation in the real environment. If the original approach fails, AI can assess an alternative and reorder tasks; real-environment validation must still be explicitly carried into subsequent contracts. A new candidate with passing unit tests but an unknown mandatory environment check cannot be accepted as complete. That gap remains visible after a session change.

### How it fits with planning and multi-agent dispatch

Planning tools help decide what to do next; dispatch tools call models and executors. This protocol focuses on maintaining goals, contracts, versions, dependencies, decisions and handoff records throughout execution. It can work alongside the host's existing planning and collaboration tools. Model selection, agent creation and tool execution are provided by the host; no fixed combination of stronger and weaker models is required.

These are this project's design priorities, not claims of market exclusivity. Workflows with equivalent mechanisms should be compared on usability and delivery outcomes in real tasks.

### Limits of the promise

- The CLI checks versions, task states, dependencies, recorded reviewer identities and mandatory check results. It does not establish that evidence is genuine, tests are sufficient, criteria are correct, or a replan semantically covers every original obligation. Actual deliverables still need tests, independent review and domain judgment.
- Authorization and role identities are source declarations, not security authentication or an OS sandbox. Logs are not a tamper-proof audit system. Reliability depends on truthful host records and appending events through public operations.
- Reporting, handoffs and obligation carryover include instructions agents must follow; compliance is not guaranteed across hosts or models. Lessons need judgment and maintenance. No automatic learning, zero-drift guarantee or fixed token/labor savings are promised.

Small one-shot tasks usually do not need this process. It becomes more useful when repeated handoffs, course corrections and verification are already a substantial part of the work.

## Quick start

Python 3.11+, standard library only. Install in your host's skill directory; instances must live outside the skill. Review and edit an example configuration before using it:

```bash
python scripts/sjs.py init --instance <project>/.cursor/workflows/demo --input examples/manual.json --dry-run
python scripts/sjs.py init --instance <project>/.cursor/workflows/demo --input examples/manual.json
python scripts/sjs.py context --view recovery --instance <project>/.cursor/workflows/demo
```

At creation, ask only for missing choices: manual / conditional / full AI; conditional risk and score thresholds; failure handling; optional estimates/timing and Feishu Base mirroring. **There is no default autonomy mode.** Suggested values need human confirmation. Existing answers persist across tasks; task-specific exceptions are recorded explicitly.

Examples: [manual](examples/manual.json), [conditional](examples/conditional.json), [full AI](examples/full-ai.json). Their paths, thresholds and budgets are examples, not universal authorization. Host routing must explicitly invoke the skill; see [installation](INSTALL.md).

## Install as a DSH (DeepSeek Harness) bundle

This repository is also a **DSH bundle**. When installed it registers `SKILL.md` as the Harness skill `serial-job-session` (the repository root stays its resource base) and adds an `sjs` tool that runs `scripts/sjs.py` inside the calling session's working directory, so callers never deal with the checkout path or the interpreter location. Every daily subcommand — `init`, `context`, `start`, `apply`, `check`, `recover` and the rest — is reachable through that one tool.

Pick one installation (do not mix them):

- Local directory, for development and live edits: `dsh plugin add <absolute path of this repository>`, or have an agent call `plugin_manager` `install_bundle` with that path as `target`.
- From GitHub: `dsh plugin add github:FMJ9922/serial-job-session`.

After installation the Plugins page lists **Serial Job Session**, new sessions advertise `serial-job-session`, the `sjs` tool is available, and Settings gains a **Serial Jobs** page for the plugin's parameters (below). Python 3.11+ is required; set `SJS_PYTHON` when `python` is not on `PATH`, and `SJS_TIMEOUT_MS` to change the per-call timeout (default 120000 ms).

Version note: from 5.2.0 on, `VERSION` (skill protocol) and `package.json`'s `version` (plugin) share one number. Template identity has two layers: `integrity` covers every installed file (including `VERSION`), while `method` tracks only shared-method files. A version bump alone no longer reports `TEMPLATE_CHANGED` for existing instances; a method-file change still requires the rebind described in INSTALL.

### Reporting cadence

Before creating an instance the protocol requires the agent to send a one-screen creation brief (North Star, success criteria, scope, authorization, mode, failure handling, modules, budget) and ask exactly one confirmation question; only then does it write the instance. Afterwards every state change (task creation, start, dispatch, candidate submission, acceptance/rework/failure/blocking, direction/nesting, close-out) requires a 2–6 line update with the same four parts: conclusion, evidence, next step, what the human must decide. Non-terminal updates are also written to `progress`; terminal conclusions and close-out rest on the actual lifecycle events. Version 5.2.0 introduced method changes: earlier instances need review and a `rebind_template` with a real human source. The 5.2.0 → 5.2.1 update changes introductions and release metadata only; method identity is unchanged. Version 5.2.2 adds readable task names and explicit work levels: in-task batches, candidate versions and rework counts remain distinct. Earlier instances need review and an explicit template rebind, preserving IDs, history and acceptance obligations.

DSH installation and usage details: [dsh/INTRO.en.md](dsh/INTRO.en.md) ([中文](dsh/INTRO.md)).

### Parameter panel (Web UI)

Settings → **Serial Jobs** (`settings.section`) edits the plugin's own runtime parameters. A save applies to the next `sjs` call without restarting the Harness:

| Parameter | Meaning |
|---|---|
| Python interpreter | Empty means auto-detect. Precedence: `SJS_PYTHON` → this setting → `python` / `python3` / `py`. |
| Call timeout (ms) | 1000–600000; `SJS_TIMEOUT_MS` wins over this setting. |
| Extra CLI arguments | One per line, appended to every `sjs` invocation (for example `--append-library`). |

The page also offers **Check environment** (probe the interpreter version and that the CLI starts) and read-only facts: repository, CLI path, skill name, settings file, version and a summary of the shared `defaults.json`. Parameters live in `serial-job-session.json` under `$DSH_HOME` (default `~/.dsh`) and travel through the bundle's own JSON API: `GET /serial-job-session/api/state`, `POST /serial-job-session/api/settings`, `POST /serial-job-session/api/probe` (same-origin only, JSON body required).

Creation-time choices (decision mode, thresholds, failure handling, estimates/Feishu modules) stay in `init` per the governance rules; the panel deliberately exposes none of them, because they belong to an instance contract and must not be changed by a global default.

Two differences from a plain skill install:

1. **Do not also copy or clone this repository into `~/.dsh/skills` or a project `.dsh/skills`.** A same-named filesystem entry shadows the bundle registration — measured here: the filesystem copy registered first, won the name, and shipped `disable-model-invocation: true`, so the model catalog never saw the skill. Keep exactly one installation.

When running the CLI by hand, save an `--input` JSON as **UTF-8 without BOM**: Windows Notepad and PowerShell's `Set-Content -Encoding utf8` add a BOM, and the CLI then reports `Unexpected UTF-8 BOM`. The simplest route is `--input -` over stdin, which the plugin's `sjs` tool already uses.

On a cp936 console, Python decodes piped stdin as GBK, so a Chinese payload fails with `surrogates not allowed`. The plugin's `sjs` tool forces `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` for every Python child and is unaffected; when running `--input -` by hand, set those two variables or pass a UTF-8 file instead.
2. The bundle is glue only: `dsh/index.js` (Host half: skill, `sjs` tool, settings file and JSON API), `dsh/client.js` (parameter panel), `dsh/cordis.patch.yml` (inserted row), `dsh/locale/{en,zh}.json` and `dsh/icon.svg` (Plugins page), and `dsh/plugin.test.mjs` (offline checks via `node --test dsh/plugin.test.mjs`). `scripts/`, `references/` and `schemas/` stay the single source of truth.

The registration permits on-demand model loading (`invocation.modelInvocable: true`). That intentionally relaxes the `disable-model-invocation: true` written for filesystem hosts, because the Harness catalog *is* its on-demand loading path, and the entry description already restricts use to an explicit name, a `route_key：意图` phrase, or an already-bound workflow.

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

The daily CLI is `scripts/sjs.py`: init / context / start / apply / handoff / check / rebuild / recover / stats / sync. Routes use an explicit registry, never a search across all instances. API examples, recovery and the full operation list are in [runtime](references/runtime.md); the disclosure contract and templates are in [reporting](references/reporting.md); policies and modules are linked from [SKILL.md](SKILL.md).

```bash
python -m unittest discover -s scripts -p "test*.py"
python -m unittest discover -s Test/serial-job-session -p "test*.py"
python scripts/check_skill_integrity.py
```

v5 replaces the previous runtime. There is no v3/v4 execution compatibility or importer. Existing business instances are untouched; create a new v5 instance. See [contributing](CONTRIBUTING.md), [changelog](CHANGELOG.md) and [security](SECURITY.md).

MIT. Author: 沈劢. Copyright: 北京钛核互动信息技术有限公司. [Credits](CREDITS.md).
