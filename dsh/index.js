/**
 * Host half of the DSH bundle that ships serial-job-session v5.
 *
 * The bundle does four things in the running Harness:
 *
 * 1. Registers the repository's `SKILL.md` as a Harness skill named
 *    `serial-job-session`, with the repository root as its resource base, so an
 *    agent can load the method and read `references/*.md` in place.
 * 2. Registers one model-facing tool, `sjs`, that runs the repository's single
 *    daily CLI (`scripts/sjs.py`) inside the calling session's working
 *    directory, so the workflow does not depend on the caller knowing where a
 *    checkout lives or which interpreter to use.
 * 3. Persists the plugin's own runtime parameters (interpreter, timeout, extra
 *    CLI arguments) in a profile-scoped JSON file, which the `sjs` tool reads on
 *    every call — a change applies without restarting the Harness.
 * 4. Serves the small JSON API the Client panel uses to read and write those
 *    parameters, under `/serial-job-session/api/`.
 *
 * The module imports only Node builtins: a linked bundle resolves imports from
 * its own checkout, which has no `node_modules`.
 *
 * @module dsh-serial-job-session
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** Bundle identity, also the Loader row name. */
export const name = 'dsh-serial-job-session'

/** The plugin package directory (`<repo>/dsh`). */
const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url))
/** The skill repository root (`<repo>`), which is this package's parent. */
const SKILL_ROOT = path.resolve(PLUGIN_DIR, '..')
const SKILL_PATH = path.join(SKILL_ROOT, 'SKILL.md')
const CLI_PATH = path.join(SKILL_ROOT, 'scripts', 'sjs.py')
const VERSION_PATH = path.join(SKILL_ROOT, 'VERSION')
const DEFAULTS_PATH = path.join(SKILL_ROOT, 'defaults.json')

/**
 * Files the CLI needs at runtime, relative to the skill root. `sjs.py` reads the
 * integrity baseline on startup, so a package that ships the CLI without it
 * fails every subcommand; the check below turns that into one clear node-side
 * message instead of a Python traceback in the middle of a workflow.
 */
export const REQUIRED_RUNTIME_FILES = [
  'SKILL.md',
  'VERSION',
  'defaults.json',
  'skill-integrity.json',
  'scripts/sjs.py',
]

/**
 * Report missing runtime files for an installed skill root.
 *
 * @param {string} [root] skill root; defaults to this package's checkout.
 * @returns {{ ok: boolean, root: string, missing: string[] }} installation state.
 */
export function checkInstallation(root = SKILL_ROOT) {
  const missing = REQUIRED_RUNTIME_FILES.filter((relative) => !existsSync(path.join(root, relative)))
  return { ok: missing.length === 0, root, missing }
}

/** Provider name reported in the skill catalog. */
const PROVIDER = name
/** Subcommands the v5 CLI accepts. */
const COMMANDS = [
  'init',
  'context',
  'start',
  'apply',
  'handoff',
  'check',
  'rebuild',
  'recover',
  'stats',
  'sync',
]
/** Default bound on one CLI run, in milliseconds. */
const DEFAULT_TIMEOUT_MS = 120_000
/** Accepted timeout range. */
const MIN_TIMEOUT_MS = 1_000
const MAX_TIMEOUT_MS = 600_000
/** Upper bound on text handed back to the model for one call. */
const MAX_OUTPUT_CHARS = 30_000
/** Upper bound on a panel request body. */
const MAX_REQUEST_BYTES = 64 * 1024
/**
 * Environment for every Python child process.
 *
 * On Windows the console code page (for example cp936) decides how a piped
 * stdin is decoded, so Node writing UTF-8 JSON made `sjs.py` decode GBK and
 * produce lone surrogates, which then failed to re-encode. Forcing UTF-8 in the
 * child removes the dependency on the host's code page.
 *
 * @param {NodeJS.ProcessEnv} [base] base environment; defaults to this process.
 * @returns {NodeJS.ProcessEnv} environment with UTF-8 forced for Python.
 */
export function pythonEnv(base = process.env) {
  return { ...base, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
}

/** Candidate interpreters, in order, when neither settings nor `SJS_PYTHON` name one. */
const DEFAULT_PYTHON = ['python3', 'python', 'py']
/** Minimum Python the CLI supports. */
const MIN_PYTHON = { major: 3, minor: 11 }
/** Settings file name inside the profile directory. */
const SETTINGS_FILE = 'serial-job-session.json'
/** JSON API prefix served to the Client panel. */
const API_PREFIX = '/serial-job-session/api'

/**
 * Parse the small YAML subset `SKILL.md` front matter uses: scalar lines and
 * folded block scalars (`>-`).
 *
 * @param {string} text raw file text.
 * @returns {{ meta: Record<string, string>, body: string }} parsed front matter
 *   and the markdown body without it.
 */
export function parseFrontMatter(text) {
  const normalized = text.replace(/^\uFEFF/, '')
  if (!normalized.startsWith('---')) return { meta: {}, body: normalized }
  const lines = normalized.split(/\r?\n/)
  let end = -1
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i].trim() === '---') {
      end = i
      break
    }
  }
  if (end < 0) return { meta: {}, body: normalized }

  const meta = {}
  const block = lines.slice(1, end)
  for (let i = 0; i < block.length; i += 1) {
    const line = block[i]
    const match = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line)
    if (!match) continue
    const key = match[1]
    let value = match[2].trim()
    if (value === '>-' || value === '>' || value === '|' || value === '|-') {
      const parts = []
      let cursor = i + 1
      while (cursor < block.length && (/^\s+\S/.test(block[cursor]) || block[cursor].trim() === '')) {
        const piece = block[cursor].trim()
        if (piece) parts.push(piece)
        cursor += 1
      }
      value = parts.join(' ')
      i = cursor - 1
    } else if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1)
    }
    meta[key] = value
  }
  const body = lines.slice(end + 1).join('\n').replace(/^\s*/, '')
  return { meta, body }
}

/**
 * Build the skill registration read from the checkout's `SKILL.md`.
 *
 * Model invocation stays permitted: a bundle registration is the Harness's
 * on-demand loading path, and the shipped description already limits use to an
 * explicit name, a `route_key：意图` phrase, or an already-bound workflow.
 *
 * @returns {Record<string, unknown>} the registration object.
 */
export function buildSkillRegistration() {
  const raw = readFileSync(SKILL_PATH, 'utf8')
  const { meta, body } = parseFrontMatter(raw)
  let version = '0'
  try {
    version = readFileSync(VERSION_PATH, 'utf8').trim()
  } catch {
    /* VERSION is optional for the registration itself. */
  }
  return {
    name: 'serial-job-session',
    description:
      meta.description ||
      'Lightweight long-running task workflow: shared method template, append-only task records, configurable human or AI decisions, role reuse and nested tasks.',
    whenToUse:
      'Use only when the user names serial-job-session, uses a `route_key：意图` phrase, or the current conversation is already bound to that workflow; it is not the default frame for ordinary work.',
    content: body,
    source: 'runtime',
    provider: PROVIDER,
    path: SKILL_PATH,
    invocation: { modelInvocable: true, userInvocable: true },
    resourceBase: { kind: 'directory', path: SKILL_ROOT },
    metadata: {
      version,
      upstream: 'https://github.com/FMJ9922/serial-job-session',
      repositoryRoot: SKILL_ROOT,
      cli: CLI_PATH,
      skillFrontMatter: meta,
    },
  }
}

/**
 * Absolute path of the profile-scoped settings file.
 *
 * The profile directory is the natural owner: parameters apply to the profile
 * that installed the bundle, and the file survives bundle upgrades.
 *
 * @returns {string} settings file path.
 */
export function settingsPath() {
  const profileDir = process.env.DSH_PROFILE_DIR
  if (profileDir && profileDir.length > 0) return path.join(profileDir, SETTINGS_FILE)
  const home = process.env.DSH_HOME || path.join(homedir(), '.dsh')
  return path.join(home, SETTINGS_FILE)
}

/**
 * Reject impossible panel values and coerce the rest to the stored shape.
 *
 * @param {unknown} raw candidate settings document.
 * @returns {{ python: string, timeoutMs: number, extraArgs: string[] }} sanitized settings.
 */
export function sanitizeSettings(raw) {
  const source = raw && typeof raw === 'object' ? raw : {}
  const python = typeof source.python === 'string' ? source.python.trim() : ''
  const rawTimeout = Number(source.timeoutMs)
  const timeoutMs = Number.isFinite(rawTimeout)
    ? Math.min(MAX_TIMEOUT_MS, Math.max(MIN_TIMEOUT_MS, Math.round(rawTimeout)))
    : DEFAULT_TIMEOUT_MS
  const seen = new Set()
  const extraArgs = []
  if (Array.isArray(source.extraArgs)) {
    for (const entry of source.extraArgs) {
      if (typeof entry !== 'string') continue
      for (const token of entry.split(/\r?\n/)) {
        const trimmed = token.trim()
        if (trimmed.length === 0 || trimmed.length > 2000) continue
        if (seen.has(trimmed)) continue
        seen.add(trimmed)
        extraArgs.push(trimmed)
      }
    }
  }
  return { python, timeoutMs, extraArgs }
}

/**
 * Read stored settings; a missing or unreadable file reads as the defaults.
 *
 * @returns {{ python: string, timeoutMs: number, extraArgs: string[] }} settings.
 */
export function readSettings() {
  try {
    const raw = JSON.parse(readFileSync(settingsPath(), 'utf8'))
    return sanitizeSettings(raw)
  } catch {
    return sanitizeSettings({})
  }
}

/**
 * Persist settings atomically (temp file then rename).
 *
 * @param {unknown} raw candidate settings document.
 * @returns {{ python: string, timeoutMs: number, extraArgs: string[] }} stored settings.
 */
export function writeSettings(raw) {
  const settings = sanitizeSettings(raw)
  const target = settingsPath()
  mkdirSync(path.dirname(target), { recursive: true })
  const temporary = `${target}.${process.pid}.tmp`
  writeFileSync(temporary, `${JSON.stringify(settings, null, 2)}\n`, 'utf8')
  renameSync(temporary, target)
  return settings
}

/**
 * Read the shared workflow defaults shipped with the skill, for the panel's
 * read-only summary. A missing or malformed file reports null.
 *
 * @returns {Record<string, unknown> | null} parsed defaults.
 */
function readWorkflowDefaults() {
  try {
    return JSON.parse(readFileSync(DEFAULTS_PATH, 'utf8'))
  } catch {
    return null
  }
}

/**
 * Translate tool arguments into `sjs.py` argv.
 *
 * Options precede the subcommand so the CLI's argument parser always sees them.
 *
 * @param {Record<string, unknown>} args model-supplied arguments.
 * @param {boolean} usesStdin whether the caller supplies `input` on stdin.
 * @param {string[]} [settingsExtraArgs] profile-level extra arguments, added first.
 * @returns {string[]} argv without the script path.
 */
export function buildArgs(args, usesStdin, settingsExtraArgs) {
  const argv = []
  const push = (flag, value) => {
    if (value === undefined || value === null || value === '') return
    argv.push(flag, String(value))
  }
  push('--instance', args.instance)
  push('--route-key', args.routeKey)
  push('--workspace', args.workspace)
  push('--task-id', args.taskId)
  push('--command-id', args.commandId)
  push('--expected-revision', args.expectedRevision)
  push('--actor', args.actor)
  push('--role', args.role)
  push('--domain', args.domain)
  push('--host-session', args.hostSession)
  push('--since', args.since)
  push('--max-chars', args.maxChars)
  push('--event', args.event)
  if (usesStdin) argv.push('--input', '-')
  if (args.human === true) argv.push('--human')
  if (args.dryRun === true) argv.push('--dry-run')
  if (args.breakStaleLock === true) argv.push('--break-stale-lock')
  if (args.appendLibrary === true) argv.push('--append-library')
  const append = (entries) => {
    if (!Array.isArray(entries)) return
    for (const extra of entries) {
      if (typeof extra === 'string' && extra.length > 0) argv.push(extra)
    }
  }
  append(settingsExtraArgs)
  append(args.extraArgs)
  if (typeof args.command === 'string' && COMMANDS.includes(args.command)) argv.push(args.command)
  return argv
}

/**
 * Run one child process, capturing both streams.
 *
 * @param {string} executable interpreter to start.
 * @param {string[]} argv arguments after the executable.
 * @param {{ cwd: string, stdin: string | undefined, signal: AbortSignal | undefined, timeoutMs: number }} options run options.
 * @returns {Promise<{ code: number|null, stdout: string, stderr: string, timedOut: boolean, spawnError: string | undefined }>} the outcome.
 */
function runProcess(executable, argv, options) {
  return new Promise((resolve) => {
    let child
    try {
      child = spawn(executable, argv, {
        cwd: options.cwd,
        windowsHide: true,
        env: pythonEnv(),
        stdio: ['pipe', 'pipe', 'pipe'],
      })
    } catch (error) {
      resolve({
        code: null,
        stdout: '',
        stderr: '',
        timedOut: false,
        spawnError: error instanceof Error ? error.message : String(error),
      })
      return
    }
    let stdout = ''
    let stderr = ''
    let timedOut = false
    let settled = false
    const finish = (result) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      if (options.signal) options.signal.removeEventListener('abort', onAbort)
      resolve(result)
    }
    const timer =
      options.timeoutMs > 0
        ? setTimeout(() => {
            timedOut = true
            child.kill()
          }, options.timeoutMs)
        : undefined
    const onAbort = () => {
      child.kill()
    }
    if (options.signal) {
      if (options.signal.aborted) child.kill()
      else options.signal.addEventListener('abort', onAbort, { once: true })
    }
    child.stdout?.setEncoding('utf8')
    child.stderr?.setEncoding('utf8')
    child.stdout?.on('data', (chunk) => {
      stdout += chunk
    })
    child.stderr?.on('data', (chunk) => {
      stderr += chunk
    })
    child.on('error', (error) => {
      finish({
        code: null,
        stdout,
        stderr,
        timedOut: false,
        spawnError: error instanceof Error ? error.message : String(error),
      })
    })
    child.on('close', (code) => {
      finish({ code, stdout, stderr, timedOut, spawnError: undefined })
    })
    if (child.stdin) {
      if (typeof options.stdin === 'string') child.stdin.end(options.stdin)
      else child.stdin.end()
    }
  })
}

/**
 * Candidate interpreters, in precedence order: settings, `SJS_PYTHON`, defaults.
 *
 * @param {{ python: string }} settings stored settings.
 * @returns {string[]} candidates.
 */
function pythonCandidates(settings) {
  const configured = settings.python || process.env.SJS_PYTHON
  return configured ? [configured] : DEFAULT_PYTHON
}

/**
 * Parse a `python --version` banner.
 *
 * @param {unknown} text stdout or stderr of the probe.
 * @returns {{ major: number, minor: number, patch: number, text: string } | null} parsed version.
 */
export function parsePythonVersion(text) {
  const match = /Python\s+(\d+)\.(\d+)(?:\.(\d+))?/i.exec(String(text ?? ''))
  if (!match) return null
  const major = Number(match[1])
  const minor = Number(match[2])
  const patch = Number(match[3] ?? 0)
  return { major, minor, patch, text: `Python ${major}.${minor}${match[3] ? `.${patch}` : ''}` }
}

/**
 * Decide whether a parsed version satisfies the CLI's Python 3.11+ floor.
 *
 * @param {{ major: number, minor: number } | null} version parsed version.
 * @returns {boolean} whether the CLI can run on it.
 */
export function isSupportedPython(version) {
  if (!version) return false
  if (version.major !== MIN_PYTHON.major) return false
  return version.minor >= MIN_PYTHON.minor
}

/**
 * Pick a working interpreter by trying candidates until one answers with a
 * supported version.
 *
 * Requiring `Python 3.11+` matters on Windows: a `python` on PATH is often
 * Python 2.7, whose `--version` exits 0 and used to be accepted, after which the
 * CLI died on its first `from __future__ import annotations`.
 *
 * @param {string[]} candidates executable candidates.
 * @param {AbortSignal | undefined} signal cancellation.
 * @param {number} timeoutMs probe bound.
 * @returns {Promise<{ executable: string, version: string }>} the interpreter and its version.
 */
async function resolveInterpreter(candidates, signal, timeoutMs) {
  const probeTimeout = Math.min(timeoutMs, 30_000)
  const rejected = []
  for (const candidate of candidates) {
    const probe = await runProcess(
      candidate,
      ['--version'],
      { cwd: SKILL_ROOT, stdin: undefined, signal, timeoutMs: probeTimeout },
    )
    if (probe.spawnError) {
      rejected.push(`${candidate}: ${probe.spawnError}`)
      continue
    }
    const banner = `${probe.stdout}\n${probe.stderr}`.trim()
    const version = parsePythonVersion(banner)
    if (!isSupportedPython(version)) {
      rejected.push(`${candidate}: ${version ? version.text : banner || `exit ${probe.code}`} (needs Python 3.11+)`)
      continue
    }
    return { executable: candidate, version: version.text }
  }
  throw new Error(
    'cannot start a Python 3.11+ interpreter for serial-job-session: ' +
      (rejected.length > 0 ? rejected.join('; ') : 'no candidate found') +
      '. Install Python 3.11+ or set the interpreter in Settings → 串行任务参数.',
  )
}

/**
 * Resolve the interpreter a tool call would use, for callers outside `apply`
 * (the smoke scripts). Same precedence as the tool — settings, `SJS_PYTHON`,
 * then `python3` / `python` / `py` — and the same Python 3.11+ gate, so a script
 * cannot drift onto a Python 2 that happens to be named `python`.
 *
 * @param {{ python?: string, signal?: AbortSignal, timeoutMs?: number }} [options] stored setting and bounds.
 * @returns {Promise<{ executable: string, version: string }>} the interpreter and its version.
 */
export async function resolvePython(options = {}) {
  const { python = '', signal, timeoutMs = 30_000 } = options
  return resolveInterpreter(pythonCandidates({ python }), signal, timeoutMs)
}

/**
 * Resolve the calling session's working directory, so relative `--instance`
 * paths mean what the user sees in the chat.
 *
 * @param {import('cordis').Context} ctx plugin context.
 * @param {import('@deepseek-ai/dsh-tools').ToolRunContext} exec tool execution.
 * @returns {Promise<string>} absolute directory to run the CLI in.
 */
async function resolveWorkingDirectory(ctx, exec) {
  const workingDirectory = ctx.get('workingDirectory')
  if (workingDirectory && exec.agent) {
    try {
      const resolved = await workingDirectory.ensure(exec.agent, exec.signal)
      if (typeof resolved === 'string' && resolved.length > 0) return resolved
    } catch {
      /* Fall back to the Host process directory. */
    }
  }
  return process.cwd()
}

/**
 * Compose the text the model reads for a successful call.
 *
 * @param {Record<string, unknown>} value structured tool result.
 * @returns {string} rendered text.
 */
export function renderResultText(value) {
  const text = value.data === undefined ? String(value.stdout ?? '') : JSON.stringify(value.data, null, 2)
  const trimmed = text.trim()
  if (trimmed.length <= MAX_OUTPUT_CHARS) return trimmed
  return `${trimmed.slice(0, MAX_OUTPUT_CHARS)}\n… output truncated at ${MAX_OUTPUT_CHARS} characters`
}

/** Human-readable labels for `apply` operations in card titles. */
const OP_LABELS = {
  create: '创建',
  assess: '评估',
  start: '开工',
  report: '提交候选',
  accept: '验收',
  reject: '返工',
  fail: '失败',
  cancel: '取消',
  block: '阻塞',
  progress: '进展',
  nest: '拆分',
  revise_contract: '修订合同',
  patch: '补证',
  set_policy: '策略',
  assess_direction: '方向评估',
  apply_direction: '方向决定',
  assign_role: '绑定角色',
  invalidate_role: '失效角色',
  knowledge: '经验',
  set_overrides: '方法覆盖',
  set_parameters: '参数',
  estimate: '估分',
  timing: '计时',
  sync_receipt: '同步回执',
  usage: '用量',
  rebind_template: '重绑模板',
  resume: '恢复',
}

/**
 * Read the operation list of one `apply` call.
 *
 * @param {Record<string, unknown>} args tool arguments.
 * @returns {Record<string, unknown>[]} valid operation objects.
 */
function operationsOf(args) {
  const input = args && typeof args === 'object' ? args.input : undefined
  const operations = input && typeof input === 'object' && Array.isArray(input.operations) ? input.operations : []
  return operations.filter((op) => op && typeof op === 'object' && typeof op.op === 'string')
}

/**
 * Build the human-readable title of one tool card.
 *
 * @param {Record<string, unknown>} args tool arguments.
 * @returns {string} card title.
 */
export function describeCall(args) {
  const command = typeof args?.command === 'string' ? args.command : ''
  const base = `sjs ${command}`.trim()
  if (command === 'init') return `${base} · ${args.dryRun === true ? '预检创建' : '创建实例'}`
  if (command === 'context') return `${base} · 读取当前任务`
  if (command === 'start') return `${base} · 任务 ${args.taskId ?? '?'}`
  if (command === 'handoff') return `${base} · 任务 ${args.taskId ?? '?'}`
  if (command === 'check') return `${base} · 校验实例`
  if (command === 'rebuild') return `${base} · 重建快照`
  if (command === 'recover') return `${base} · 恢复尾部`
  if (command === 'stats') return `${base} · 估分统计`
  if (command === 'sync') return `${base} · 飞书同步`
  if (command === 'apply') {
    const operations = operationsOf(args)
    if (operations.length === 0) return `${base} · 写入事件`
    if (
      operations.length === 1 &&
      operations[0].op === 'create' &&
      typeof operations[0].title === 'string' &&
      operations[0].title.trim().length > 0
    ) {
      return `${base} · 创建「${operations[0].title.trim()}」`
    }
    const counts = new Map()
    for (const op of operations) {
      const label = OP_LABELS[op.op] ?? op.op
      counts.set(label, (counts.get(label) ?? 0) + 1)
    }
    const parts = []
    for (const [label, count] of counts) {
      if (parts.length === 4) {
        parts.push(`等 ${operations.length} 项`)
        break
      }
      parts.push(count > 1 ? `${label} ${count}` : label)
    }
    return `${base} · ${parts.join(' · ')}`
  }
  return base
}

/**
 * Whether a successful result still leaves a human decision pending.
 *
 * @param {Record<string, unknown>} value tool result.
 * @returns {boolean} whether any outcome was denied by policy or review.
 */
function decisionNeedsHuman(value) {
  const outcomes = value?.data?.outcomes
  return (
    Array.isArray(outcomes) &&
    outcomes.some((outcome) => outcome && outcome.decision && outcome.decision.allowed === false)
  )
}

/**
 * Whether an `apply` carries a business change rather than only a progress record.
 *
 * @param {Record<string, unknown>} args tool arguments.
 * @returns {boolean} whether a human-facing report is warranted.
 */
function hasBusinessChange(args) {
  const operations = operationsOf(args)
  if (operations.length === 0) return true
  return operations.some((op) => op.op !== 'progress')
}

/**
 * Compose the reporting reminder prepended to a successful tool result.
 *
 * @param {Record<string, unknown>} args tool arguments.
 * @param {Record<string, unknown>} value tool result.
 * @returns {string} reminder text, or an empty string when no report is due.
 */
export function reportReminder(args, value) {
  const command = typeof args?.command === 'string' ? args.command : ''
  if (value?.data?.duplicate === true) return ''
  if (command === 'apply' || command === 'start') {
    if (command === 'apply' && !hasBusinessChange(args)) return ''
    if (decisionNeedsHuman(value)) {
      return (
        '[向人类汇报] 本轮未推进：先读 outcomes 里的 decision/reason，向用户说明任务仍是 ' +
        'ready/doing/awaiting_acceptance、为什么被拦、需要用户决定什么；不要写成“已开工/已完成”。'
      )
    }
    return (
      '[向人类汇报] 先用 2–6 行说明本轮改了什么、任务 ID/标题、证据或阻塞、下一步和需要用户决定的事项；' +
      '不要只贴 JSON。'
    )
  }
  if (command === 'init') {
    return args?.dryRun === true
      ? '[向人类汇报] 预检结果不是汇报：翻译成创建简报（北极星、模式、授权、失败处置、模块、预算），' +
          '只问一个确认问题并等待回答。'
      : '[向人类汇报] 先用创建简报复述北极星与全部创建环境，再说明实例路径、route_key 和下一步。'
  }
  if (command === 'context' || command === 'handoff') {
    return (
      '[向人类汇报] 恢复会话时：有当前任务就复述北极星、当前卡和上次进展；没有当前任务就说明暂停原因、' +
      '可选卡和下一步，请用户选择或按模式选择。'
    )
  }
  return ''
}

/**
 * Build the error text for a failed CLI call, including a reporting hint.
 *
 * @param {Record<string, unknown>} args tool arguments.
 * @param {{ code?: number | null, detail?: string, timedOut?: boolean, spawnError?: string, cwd?: string, timeoutMs?: number }} outcome failure facts.
 * @returns {string} error text for the caller.
 */
export function buildFailureText(args, outcome) {
  const command = typeof args?.command === 'string' ? args.command : ''
  const detail = typeof outcome?.detail === 'string' ? outcome.detail.trim() : ''
  let message
  if (outcome?.timedOut) {
    message = `sjs ${command} timed out after ${outcome.timeoutMs} ms in ${outcome.cwd ?? ''}`
  } else if (outcome?.spawnError) {
    message = `cannot start sjs: ${outcome.spawnError}`
  } else {
    message =
      `sjs ${command} exited with ${outcome?.code}` +
      (detail ? `:\n${detail.slice(0, MAX_OUTPUT_CHARS)}` : '')
  }

  let hint = '[向人类汇报] 不要只回复“命令失败”。说明错误码、已尝试动作和需要用户做什么。'
  if (/DECISIONS_REQUIRED/.test(detail)) {
    hint =
      '[向人类汇报] DECISIONS_REQUIRED 表示创建参数缺失，不是普通失败：把 missing/choices/suggestions 逐项问用户，' +
      '不要自行选默认值，也不要原样重试。'
  } else if (/TEMPLATE_CHANGED/.test(detail)) {
    hint =
      '[向人类汇报] 共享方法已升级：先向用户说明变化、旧实例影响和需要确认的 rebind，' +
      '得到真实人类确认后才 rebind_template；不要静默重绑。'
  } else if (/REVISION_CONFLICT/.test(detail)) {
    hint =
      '[向人类汇报] revision 已变化：先重新 context 取当前 revision，说明刚刚发生了什么，再重试；' +
      '不要用旧 revision 硬重试。'
  } else if (/TASK_NOT_ACTIVE|TASK_NOT_READY/.test(detail)) {
    hint = '[向人类汇报] 任务状态不允许该操作：先读 context 说明当前状态和需要用户做什么，不要反复重试。'
  } else if (outcome?.timedOut) {
    hint = '[向人类汇报] 结果未知：先用 context/check 核实是否已提交，再决定是否重试；向用户说明不确定性。'
  }
  return `${message}\n\n${hint}`
}

/**
 * Read and parse a JSON request body with a byte bound.
 *
 * @param {import('node:http').IncomingMessage} req request.
 * @returns {Promise<unknown>} parsed body, or undefined when empty.
 */
function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let buffered = ''
    let bytes = 0
    req.setEncoding('utf8')
    req.on('data', (chunk) => {
      bytes += Buffer.byteLength(chunk, 'utf8')
      if (bytes > MAX_REQUEST_BYTES) {
        reject(new Error('request body too large'))
        req.destroy()
        return
      }
      buffered += chunk
    })
    req.on('error', reject)
    req.on('end', () => {
      if (buffered.trim().length === 0) {
        resolve(undefined)
        return
      }
      try {
        resolve(JSON.parse(buffered))
      } catch {
        reject(new Error('request body is not valid JSON'))
      }
    })
  })
}

/**
 * Refuse obvious cross-site browser writes; the panel is same-origin.
 *
 * @param {import('node:http').IncomingMessage} req request.
 * @returns {boolean} whether the request may proceed.
 */
function requestTrusted(req) {
  const site = req.headers['sec-fetch-site']
  if (typeof site === 'string' && site !== 'same-origin' && site !== 'none') return false
  return true
}

/**
 * Write one JSON response.
 *
 * @param {import('node:http').ServerResponse} res response.
 * @param {number} status HTTP status.
 * @param {unknown} value JSON payload.
 */
function sendJson(res, status, value) {
  const body = `${JSON.stringify(value, null, 2)}\n`
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(body, 'utf8'),
  })
  res.end(body)
}

/**
 * Everything the panel reads in one request.
 *
 * @param {{ python: string, timeoutMs: number, extraArgs: string[] }} settings stored settings.
 * @returns {Record<string, unknown>} state document.
 */
function panelState(settings) {
  let version = '0'
  try {
    version = readFileSync(VERSION_PATH, 'utf8').trim()
  } catch {
    /* version is informational. */
  }
  return {
    ok: true,
    plugin: name,
    version,
    repoRoot: SKILL_ROOT,
    cliPath: CLI_PATH,
    skill: { name: 'serial-job-session', modelInvocable: true },
    settingsPath: settingsPath(),
    settings,
    defaults: readWorkflowDefaults(),
    installation: checkInstallation(),
    candidates: DEFAULT_PYTHON,
    env: {
      SJS_PYTHON: process.env.SJS_PYTHON ?? null,
      SJS_TIMEOUT_MS: process.env.SJS_TIMEOUT_MS ?? null,
    },
  }
}

/**
 * Register the panel JSON API on the Web carrier.
 *
 * @param {import('cordis').Context} ctx a context that has the `webServer` service.
 */
function registerPanelRoutes(ctx) {
  const webServer = ctx.webServer ?? ctx.get('webServer')
  if (!webServer || typeof webServer.register !== 'function') return

  ctx.effect(() =>
    webServer.register({
      kind: 'exact',
      path: `${API_PREFIX}/state`,
      handler(req, res) {
        if (req.method !== 'GET') {
          sendJson(res, 405, { ok: false, error: 'method-not-allowed' })
          return
        }
        if (!requestTrusted(req)) {
          sendJson(res, 403, { ok: false, error: 'cross-site' })
          return
        }
        sendJson(res, 200, panelState(readSettings()))
      },
    }),
  )

  ctx.effect(() =>
    webServer.register({
      kind: 'exact',
      path: `${API_PREFIX}/settings`,
      async handler(req, res) {
        if (req.method !== 'POST') {
          sendJson(res, 405, { ok: false, error: 'method-not-allowed' })
          return
        }
        if (!requestTrusted(req)) {
          sendJson(res, 403, { ok: false, error: 'cross-site' })
          return
        }
        const contentType = String(req.headers['content-type'] ?? '')
        if (!contentType.toLowerCase().includes('application/json')) {
          sendJson(res, 415, { ok: false, error: 'content-type-must-be-json' })
          return
        }
        try {
          const body = await readJsonBody(req)
          const settings = writeSettings(body)
          ctx.logger?.info?.('serial-job-session settings saved')
          sendJson(res, 200, { ok: true, settings, settingsPath: settingsPath() })
        } catch (error) {
          sendJson(res, 400, {
            ok: false,
            error: error instanceof Error ? error.message : String(error),
          })
        }
      },
    }),
  )

  ctx.effect(() =>
    webServer.register({
      kind: 'exact',
      path: `${API_PREFIX}/probe`,
      async handler(req, res) {
        if (req.method !== 'POST') {
          sendJson(res, 405, { ok: false, error: 'method-not-allowed' })
          return
        }
        if (!requestTrusted(req)) {
          sendJson(res, 403, { ok: false, error: 'cross-site' })
          return
        }
        const settings = readSettings()
        const candidates = pythonCandidates(settings)
        try {
          const interpreter = await resolveInterpreter(candidates, undefined, 30_000)
          const cli = await runProcess(
            interpreter.executable,
            [CLI_PATH, '--help'],
            { cwd: SKILL_ROOT, stdin: undefined, signal: undefined, timeoutMs: 30_000 },
          )
          sendJson(res, 200, {
            ok: true,
            interpreter: interpreter.executable,
            python: interpreter.version,
            cli: { ok: cli.code === 0, exitCode: cli.code },
          })
        } catch (error) {
          sendJson(res, 200, {
            ok: false,
            error: error instanceof Error ? error.message : String(error),
            candidates,
          })
        }
      },
    }),
  )
}

/**
 * Build the `sjs` tool definition.
 *
 * @param {import('cordis').Context} ctx plugin context.
 * @returns {Record<string, unknown>} tool definition passed to `ctx.tools.register`.
 */
function buildTool(ctx) {
  return {
    name: 'sjs',
    description:
      'Run the serial-job-session v5 CLI (scripts/sjs.py) inside the current session working directory. ' +
      'It is the only daily entry point of that workflow: read a working context with `context`, then write ' +
      'an append-only event with `apply` (or the `start` shortcut). Feed JSON payloads through `input`; they ' +
      'travel to the CLI on stdin. Never pass `human: true` unless a real human gave the instruction in this ' +
      'conversation, and never invent evidence. The CLI returns UTF-8 JSON; a non-zero exit is reported as a ' +
      'tool error carrying the CLI diagnostics. Interpreter, timeout and extra arguments come from the ' +
      'plugin settings page (Settings → 串行任务参数) unless the environment overrides them. ' +
      'After every state-changing call (init/start/apply) send the user a short plain-language report before ' +
      'the next tool call: what changed, task ids and titles, evidence or blockers, next step, and what the ' +
      'human must decide. Read `outcomes` first — a successful call is not always business progress; ' +
      '`allowed:false` means the task did not advance. The skill\'s reporting reference has the checkpoint list.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['command'],
      properties: {
        command: {
          type: 'string',
          enum: COMMANDS,
          description:
            'init (create an instance, usually with dryRun first), context (read the bounded menu and current card), ' +
            'start, apply (one command with operations), handoff, check (replay and validate), rebuild (reset the snapshot), ' +
            'recover (drop an uncommitted tail), stats, sync (optional module flush).',
        },
        instance: {
          type: 'string',
          description: 'Instance directory, absolute or relative to the session working directory.',
        },
        routeKey: {
          type: 'string',
          description: 'Registered route key (for example `界面优化`) instead of an instance path.',
        },
        input: {
          type: 'object',
          description:
            'JSON payload the command expects (init run config, or an apply command with command_id, ' +
            'expected_revision, actor and operations). Sent on stdin as `--input -`.',
        },
        workspace: { type: 'string', description: 'Workspace root override for the CLI.' },
        taskId: { type: 'string', description: 'Task id for start, report, accept and related commands.' },
        commandId: { type: 'string', description: 'Unique command id for one apply/start command.' },
        expectedRevision: {
          type: 'integer',
          description: 'Revision the command expects; take it from the latest context.',
        },
        actor: { type: 'string', description: 'Actor id recorded on the event.' },
        human: {
          type: 'boolean',
          description: 'Record a real human instruction. Only with an explicit human instruction in this conversation.',
        },
        role: { type: 'string', description: 'Role context to load (orchestrator, worker, reviewer, ...).' },
        domain: { type: 'string', description: 'Domain context to load.' },
        hostSession: { type: 'string', description: 'Host session id used for role reuse.' },
        since: { type: 'string', description: 'Return only records after this revision.' },
        maxChars: { type: 'integer', description: 'Context character budget for context/handoff.' },
        dryRun: { type: 'boolean', description: 'Validate without writing (init, rebuild, recover).' },
        breakStaleLock: {
          type: 'boolean',
          description: 'Break a confirmed stale lock. Only after verifying its writer exited.',
        },
        appendLibrary: { type: 'boolean', description: 'Append to the shared estimate library.' },
        event: {
          type: 'string',
          enum: ['probe', 'provision', 'bind', 'flush'],
          description: 'Optional-module event for the sync command.',
        },
        extraArgs: {
          type: 'array',
          items: { type: 'string' },
          description: 'Raw extra CLI arguments for this call only, appended after the profile-level ones.',
        },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        required: ['ok', 'exitCode'],
        properties: {
          ok: { type: 'boolean' },
          exitCode: { type: 'integer' },
          data: {},
          stdout: { type: 'string' },
          stderr: { type: 'string' },
        },
      },
      render(args, value) {
        const text = renderResultText(value)
        const reminder = reportReminder(args, value)
        return [{ type: 'text', text: reminder ? `${reminder}\n\n${text}` : text }]
      },
    },
    async execute(args, exec) {
      if (!COMMANDS.includes(args.command)) {
        throw new Error(`unsupported sjs command: ${String(args.command)}`)
      }
      const installation = checkInstallation()
      if (!installation.ok) {
        throw new Error(
          `serial-job-session installation is incomplete: missing ${installation.missing.join(', ')} under ${installation.root}. ` +
            'The npm package must ship skill-integrity.json; reinstall a release that includes it ' +
            '(or use the GitHub/local-directory install).',
        )
      }
      const settings = readSettings()
      const hasInput = args.input !== undefined && args.input !== null
      const stdin = hasInput ? JSON.stringify(args.input) : undefined
      const argv = buildArgs(args, hasInput, settings.extraArgs)
      const cwd = await resolveWorkingDirectory(ctx, exec)
      const timeoutMs = Number(process.env.SJS_TIMEOUT_MS) || settings.timeoutMs || DEFAULT_TIMEOUT_MS
      const interpreter = await resolveInterpreter(pythonCandidates(settings), exec.signal, timeoutMs)
      const outcome = await runProcess(interpreter.executable, [CLI_PATH, ...argv], {
        cwd,
        stdin,
        signal: exec.signal,
        timeoutMs,
      })
      if (exec.signal?.aborted) throw new Error('sjs cancelled')
      if (outcome.timedOut) {
        throw new Error(buildFailureText(args, { timedOut: true, cwd, timeoutMs }))
      }
      if (outcome.spawnError) {
        throw new Error(buildFailureText(args, { spawnError: outcome.spawnError }))
      }
      if (outcome.code !== 0) {
        const detail = [outcome.stderr.trim(), outcome.stdout.trim()].filter(Boolean).join('\n')
        throw new Error(buildFailureText(args, { code: outcome.code, detail }))
      }
      let data
      const stdout = outcome.stdout.trim()
      if (stdout.length > 0) {
        try {
          data = JSON.parse(stdout)
        } catch {
          data = undefined
        }
      }
      return {
        ok: true,
        exitCode: 0,
        data,
        stdout: outcome.stdout,
        stderr: outcome.stderr,
      }
    },
    presentCall(args) {
      return {
        card: 'generic',
        title: describeCall(args),
        kind: 'execute',
        rawInput: args,
      }
    },
  }
}

/**
 * Register the skill, the tool and the panel API.
 *
 * Each service is awaited through `ctx.inject` instead of being read with
 * `ctx.get` during apply: a profile composes rows in its own order, and at boot
 * this row can apply before the skill registry, the tool registry or the Web
 * carrier exists. Reading them immediately would silently register nothing; an
 * injected child runs as soon as its service is present and is torn down with
 * it, and a profile that never mounts one of them simply skips that half.
 *
 * @param {import('cordis').Context} ctx plugin context.
 */
export function apply(ctx) {
  ctx.inject(['skills'], (scope) => {
    scope.effect(() => scope.skills.register(buildSkillRegistration()))
  })
  ctx.inject(['tools'], (scope) => {
    scope.effect(() => scope.tools.register(buildTool(scope)))
  })
  ctx.inject(['webServer'], (scope) => {
    registerPanelRoutes(scope)
  })
}
