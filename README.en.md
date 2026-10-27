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
