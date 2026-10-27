[简体中文](README.md) | English

# serial-job-session

**A human-gated serial task protocol: one card at a time, one acceptance contract per card, evidence before acceptance.**

A human-gated serial task protocol for AI coding agents. One card at a time, one acceptance contract per card, evidence before acceptance.

---

## What it solves

Handing a large task to an AI usually fails the same way: the human drops out of the loop, the agent chains many steps, skips dependencies, declares success on its own, or games a gate with fake data. `serial-job-session` goes the other way:

- **The human stays the orchestrator**: split the work into cards, name the next one, accept or reject.
- **The AI only does the named card**: no scope creep, no starting the next one automatically.
- **Every card has a contract**: goal, dependencies, touched surfaces, acceptance, time, evidence.
- **A single writer owns the control plane**: the main session writes `CURRENT`, the menu, `LOG` and `FACTS`; workers only write their own card and evidence.
- **A fixed state machine**: `ready → doing → awaiting_acceptance → accepted`, with `cancelled` as the only exceptional side state.
- **Gates outrank eyeballing**: when a mechanical gate is red, a human cannot simply wave it through.

This is not the same thing as a workflow that fans out many agents at once. Fan-out suits large, well-bounded one-off tasks; this protocol suits long-running engineering that needs a human to keep making calls.

---

## Quick start

```bash
# 1. Install (DSH example; see INSTALL.md for other hosts)
git clone https://github.com/FMJ9922/serial-job-session.git \
  <your-repo>/.agents/skills/serial-job-session

# 2. Run the tests
cd <your-repo>/.agents/skills/serial-job-session
python -m unittest discover -s scripts -p "test*.py"

# 3. Create an instance (confirm the path and slug when prompted)
python scripts/instantiate_domain.py --target <repo>/.cursor/workflows/<slug> \
  --domain-name "<domain-name>" --route-key "<key>"
```

Then talk to the agent with the standard commands:

```text
<route_key>：推进          # advance / show the menu
<route_key>：按菜单做 12   # run card 12
<route_key>：验收通过      # accept
<route_key>：打回：<reason> # reject
```

A bare "推进一下" does not trigger anything unless the current session is bound to exactly one instance.

---

## Protocol at a glance

| Role | Responsibility |
|---|---|
| Human | Set direction, split modules, name the next card, accept / reject |
| Menu session | Read-only; shows the current `ready` cards, never picks for the human |
| Worker | Executes exactly one named card; writes that card and its evidence |
| Closer | Closes the card: timing, status, `LOG` / `FACTS` / menu |
| Reviewer | Returns `pass / blocking` only; never closes |

Minimum card fields: `id`, `shape`, `estimate_group`, `depends_on`, `faces`, `estimate`, `estimate_source`, `actor`, `human_accept`, `status`, `attempt`. The body has seven fixed sections (goal & acceptance / dependencies & inputs / progress, issues & decisions / execution time / artifacts & verification / closure patch / facts & lessons).

Hard rules in one line: one card, one thing; do not chain a dependent card before its dependency closes; no `accepted` without timing; evidence paths are immutable; only the control-plane writer may edit shared control files; migration never rewrites `output/`; a failed liveness probe is a hard stop.

---

## Layout

```text
SKILL.md                 Entry point (loads the docs below on demand)
V4.md                    v4 state, timing, patches, direction review, migration
ESTIMATE.md              Estimation and retrospective conventions
PARALLEL.md              Parallelism criteria and prohibitions
DELEGATION.md            Managed / overnight authorization contract
GATE-PATTERNS.md         How to write mechanical gates
IMMUTABILITY.md          Immutability rules for the master copy
EVOLUTION.md             Capturing lessons
INIT.md / FIRST-RUN.md   Creating an instance / first human run
INSTALL.md               Installation for each host
FEISHU.md                Optional Feishu/Lark task table (when the local CLI works)
scripts/                 30 Python scripts (standard library only)
schemas/                 JSON Schemas
domain-pack-template/    Domain pack template
Test/serial-job-session/ Replay fixtures and goldens (maintainers)
skill-integrity.json     Master integrity baseline
```

A domain pack starts with just `ENV.md`, `CURRENT.md`, `JOB-SHAPES.md`, `jobs/`, `NORMS.md`, `FACTS.md`, `LOG.md`, plus an evidence directory.

---

## Estimation and variance

Estimates record `estimate`, `ai_net`, `elapsed`, `business_wait`, `variance`. When trustworthy timing is unavailable, write `untracked` instead of back-filling from chat timestamps. The estimate library lives at `<git-root>/.serial-job-session/estimate-library.jsonl` and can be shared across instances.

---

## Optional integrations

- **Feishu / Lark**: when the local `lark-cli` works, task tables can be mirrored; otherwise the steps are skipped and the main flow is unaffected. See [FEISHU.md](FEISHU.md).
- **Git**: commits are sequential, performed by one authorized writer; never `git add` a mixed worktree wholesale.
- **Editors / DCC**: a single-instance resource (Unity, Houdini, etc.) allows only one writing worker at a time; other parallel cards must be read-only or pure shell.

---

## Development

- Python 3.11+ (tested on 3.12), standard library only.
- Unit tests: `python -m unittest discover -s scripts -p "test*.py"`
- Replay fixtures: `python -m unittest discover -s Test/serial-job-session -p "test*.py"`
- Release gate: `python scripts/quality_gate.py --domain <instance-root>`
- Recompute the integrity baseline after editing master text: `python scripts/check_skill_integrity.py --update --maintenance-ack MODIFY_SKILL`

More detail lives in [V4.md](V4.md), [ESTIMATE.md](ESTIMATE.md), [PARALLEL.md](PARALLEL.md) and [DELEGATION.md](DELEGATION.md) (currently written in Chinese).

---

## License and attribution

- License: **MIT** — see [LICENSE](LICENSE)
- Copyright: © 2026 Beijing Taihe Interactive Information Technology Co., Ltd.
- Author: **Shen Mai (沈劢)** — contact `shenmai@taiyouxi.cn`
- Development: author-led, AI-assisted

---

## Known limits

- This release covers protocol v4. v3 cards remain readable, but migration is generic and contains no business-domain preprocessing.
- `disable-model-invocation: true` means the model will not load the skill automatically; the host must route the `{route_key}: <intent>` command to it.
- Feishu sync depends on the local CLI; without it, related commands skip or refuse, and the main flow is unaffected.
