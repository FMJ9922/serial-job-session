/**
 * Host half of the DSH bundle that ships serial-job-session v5.
 *
 * The bundle does two things in the running Harness:
 *
 * 1. Registers the repository's `SKILL.md` as a Harness skill named
 *    `serial-job-session`, with the repository root as its resource base, so an
 *    agent can load the method and read `references/*.md` in place.
 * 2. Registers one model-facing tool, `sjs`, that runs the repository's single
 *    daily CLI (`scripts/sjs.py`) inside the calling session's working
 *    directory, so the workflow does not depend on the caller knowing where a
 *    checkout lives or which interpreter to use.
 *
 * The module imports only Node builtins: a linked bundle resolves imports from
 * its own checkout, which has no `node_modules`.
 *
 * @module dsh-serial-job-session
 */

import { readFileSync } from 'node:fs'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** Bundle identity, also the Loader row name. */
export const name = 'dsh-serial-job-session'

/** Hard dependencies: this bundle exists to add a skill and a tool. */
export const inject = ['tools', 'skills']

/** The plugin package directory (`<repo>/dsh`). */
const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url))
/** The skill repository root (`<repo>`), which is this package's parent. */
const SKILL_ROOT = path.resolve(PLUGIN_DIR, '..')
const SKILL_PATH = path.join(SKILL_ROOT, 'SKILL.md')
const CLI_PATH = path.join(SKILL_ROOT, 'scripts', 'sjs.py')
const VERSION_PATH = path.join(SKILL_ROOT, 'VERSION')

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
/** Upper bound on text handed back to the model for one call. */
const MAX_OUTPUT_CHARS = 30_000
/** Candidate interpreters, in order, when `SJS_PYTHON` is unset. */
const DEFAULT_PYTHON = ['python', 'python3', 'py']

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
    if (key === 'disable-model-invocation') {
      meta[key] = value.toLowerCase() === 'true' ? 'true' : 'false'
    } else {
      meta[key] = value
    }
  }
  const body = lines.slice(end + 1).join('\n').replace(/^\s*/, '')
  return { meta, body }
}

/**
 * Build the skill registration read from the checkout's `SKILL.md`.
 *
 * The upstream file keeps `disable-model-invocation: true` so a filesystem
 * install stays user-invoked. A bundle registration is the Harness's explicit
 * loading path, so it permits model invocation by default; set
 * `SJS_MODEL_INVOCABLE=false` in the Host environment to keep the stricter
 * user-only policy.
 *
 * @returns {Record<string, unknown>} the registration object.
 */
export function buildSkillRegistration() {
  const raw = readFileSync(SKILL_PATH, 'utf8')
  const { meta, body } = parseFrontMatter(raw)
  const modelInvocable = process.env.SJS_MODEL_INVOCABLE !== 'false'
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
    invocation: { modelInvocable, userInvocable: true },
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
 * Translate tool arguments into `sjs.py` argv.
 *
 * Options precede the subcommand so the CLI's argument parser always sees them.
 *
 * @param {Record<string, unknown>} args model-supplied arguments.
 * @param {boolean} usesStdin whether the caller supplies `input` on stdin.
 * @returns {string[]} argv without the script path.
 */
export function buildArgs(args, usesStdin) {
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
  if (Array.isArray(args.extraArgs)) {
    for (const extra of args.extraArgs) {
      if (typeof extra === 'string' && extra.length > 0) argv.push(extra)
    }
  }
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
        env: process.env,
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
 * Pick a working interpreter by trying candidates until one starts.
 *
 * @param {AbortSignal | undefined} signal cancellation.
 * @param {number} timeoutMs probe bound.
 * @returns {Promise<{ executable: string, probe?: { spawnError?: string } }>} the interpreter.
 */
async function resolveInterpreter(signal, timeoutMs) {
  const configured = process.env.SJS_PYTHON
  const candidates = configured ? [configured] : DEFAULT_PYTHON
  const probeTimeout = Math.min(timeoutMs, 30_000)
  let lastError = 'no python interpreter found'
  for (const candidate of candidates) {
    const probe = await runProcess(
      candidate,
      ['--version'],
      { cwd: SKILL_ROOT, stdin: undefined, signal, timeoutMs: probeTimeout },
    )
    if (!probe.spawnError) return { executable: candidate }
    lastError = probe.spawnError
  }
  throw new Error(
    `cannot start a Python 3.11+ interpreter for serial-job-session (${lastError}); ` +
      'install python or set the SJS_PYTHON environment variable to its executable path',
  )
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
      'tool error carrying the CLI diagnostics.',
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
          description: 'Raw extra CLI arguments, appended last, for a flag this schema does not name.',
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
      render(_args, value) {
        return [{ type: 'text', text: renderResultText(value) }]
      },
    },
    async execute(args, exec) {
      if (!COMMANDS.includes(args.command)) {
        throw new Error(`unsupported sjs command: ${String(args.command)}`)
      }
      const hasInput = args.input !== undefined && args.input !== null
      const stdin = hasInput ? JSON.stringify(args.input) : undefined
      const argv = buildArgs(args, hasInput)
      const cwd = await resolveWorkingDirectory(ctx, exec)
      const timeoutMs = Number(process.env.SJS_TIMEOUT_MS) || DEFAULT_TIMEOUT_MS
      const interpreter = await resolveInterpreter(exec.signal, timeoutMs)
      const outcome = await runProcess(interpreter.executable, [CLI_PATH, ...argv], {
        cwd,
        stdin,
        signal: exec.signal,
        timeoutMs,
      })
      if (exec.signal?.aborted) throw new Error('sjs cancelled')
      if (outcome.timedOut) {
        throw new Error(`sjs ${args.command} timed out after ${timeoutMs} ms in ${cwd}`)
      }
      if (outcome.spawnError) {
        throw new Error(`cannot start sjs: ${outcome.spawnError}`)
      }
      if (outcome.code !== 0) {
        const detail = [outcome.stderr.trim(), outcome.stdout.trim()].filter(Boolean).join('\n')
        throw new Error(
          `sjs ${args.command} exited with ${outcome.code}` +
            (detail ? `:\n${detail.slice(0, MAX_OUTPUT_CHARS)}` : ''),
        )
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
        title: `sjs ${typeof args.command === 'string' ? args.command : ''}`.trim(),
        kind: 'execute',
        rawInput: args,
      }
    },
  }
}

/**
 * Register the skill and the tool.
 *
 * @param {import('cordis').Context} ctx plugin context.
 */
export function apply(ctx) {
  ctx.effect(() => ctx.skills.register(buildSkillRegistration()))
  ctx.effect(() => ctx.tools.register(buildTool(ctx)))
}
