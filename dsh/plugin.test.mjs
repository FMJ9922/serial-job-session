/**
 * Offline checks for the DSH bundle's Host half and panel API.
 *
 * They run without a Harness: `apply` receives a fake context that records
 * registrations, and the JSON API handlers are driven with plain Node request
 * and response doubles. `scripts/sjs.py` is never executed.
 *
 * Run with `node --test dsh/plugin.test.mjs`.
 */

import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { Readable } from 'node:stream'
import { fileURLToPath } from 'node:url'

import {
  apply,
  buildArgs,
  buildSkillRegistration,
  name,
  parseFrontMatter,
  readSettings,
  renderResultText,
  sanitizeSettings,
  settingsPath,
  writeSettings,
} from './index.js'

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(PLUGIN_DIR, '..')

/**
 * Run a case with `DSH_PROFILE_DIR` pointed at a fresh directory.
 *
 * @param {(dir: string) => void | Promise<void>} body case body.
 */
async function withProfileDir(body) {
  const previous = process.env.DSH_PROFILE_DIR
  const dir = mkdtempSync(path.join(tmpdir(), 'sjs-profile-'))
  process.env.DSH_PROFILE_DIR = dir
  try {
    await body(dir)
  } finally {
    if (previous === undefined) delete process.env.DSH_PROFILE_DIR
    else process.env.DSH_PROFILE_DIR = previous
    rmSync(dir, { recursive: true, force: true })
  }
}

/**
 * Minimal Cordis-like context that records registrations, a Web route table and
 * the optional services the plugin looks up.
 *
 * @param {{ skills?: boolean, tools?: boolean, web?: boolean }} [options] which optional services exist.
 * @returns {{ ctx: object, skills: object[], tools: object[], routes: Map<string, object> }} recorder.
 */
function fakeContext(options = {}) {
  const skills = []
  const tools = []
  const routes = new Map()
  const services = {}
  if (options.skills !== false) {
    services.skills = {
      register(registration) {
        skills.push(registration)
        return () => {}
      },
    }
  }
  if (options.tools !== false) {
    services.tools = {
      register(definition) {
        tools.push(definition)
        return () => {}
      },
    }
  }
  if (options.web !== false) {
    services.webServer = {
      register(route) {
        assert.equal(routes.has(route.path), false, `duplicate route ${route.path}`)
        routes.set(route.path, route)
        return () => routes.delete(route.path)
      },
    }
  }
  const ctx = {
    effect(callback) {
      callback()
    },
    get(key) {
      return services[key]
    },
  }
  return { ctx, skills, tools, routes }
}

/**
 * Response double that records the status and JSON body.
 *
 * @returns {{ res: object, read: () => { status: number, body: any } }} doubles.
 */
function fakeResponse() {
  const state = { status: 0, headers: {}, body: '' }
  const res = {
    writeHead(status, headers) {
      state.status = status
      Object.assign(state.headers, headers ?? {})
    },
    end(chunk) {
      state.body += chunk ?? ''
    },
  }
  return {
    res,
    read() {
      let body
      try {
        body = JSON.parse(state.body)
      } catch {
        body = state.body
      }
      return { status: state.status, headers: state.headers, body }
    },
  }
}

test('the bundle keeps one row name', () => {
  assert.equal(name, 'dsh-serial-job-session')
})

test('front matter parsing handles the folded description SKILL.md uses', () => {
  const text = [
    '---',
    'name: serial-job-session',
    'description: >-',
    '  第一行',
    '  第二行',
    'disable-model-invocation: true',
    'metadata:',
    '  author: 沈劢',
    '---',
    '',
    '# 标题',
    '正文',
  ].join('\n')
  const { meta, body } = parseFrontMatter(text)
  assert.equal(meta.name, 'serial-job-session')
  assert.equal(meta.description, '第一行 第二行')
  assert.equal(meta['disable-model-invocation'], 'true')
  assert.equal(body.trim(), '# 标题\n正文')
})

test('front matter parsing leaves a plain markdown body untouched', () => {
  const { meta, body } = parseFrontMatter('# 只有正文\n')
  assert.deepEqual(meta, {})
  assert.equal(body, '# 只有正文\n')
})

test('the skill registration reads the checkout SKILL.md and its root', () => {
  const skill = buildSkillRegistration()
  assert.equal(skill.name, 'serial-job-session')
  assert.equal(skill.source, 'runtime')
  assert.equal(skill.invocation.modelInvocable, true)
  assert.equal(skill.invocation.userInvocable, true)
  assert.ok(skill.description.length > 20)
  assert.ok(skill.content.includes('串行任务会话'))
  assert.equal(skill.path, path.join(ROOT, 'SKILL.md'))
  assert.equal(skill.resourceBase.kind, 'directory')
  assert.equal(skill.resourceBase.path, ROOT)
  assert.ok(existsSync(path.join(skill.resourceBase.path, 'references', 'runtime.md')))
  assert.equal(skill.metadata.version, readFileSync(path.join(ROOT, 'VERSION'), 'utf8').trim())
  assert.equal(skill.metadata.skillFrontMatter['disable-model-invocation'], 'true')
})

test('apply registers one skill, one tool and three API routes', () => {
  const { ctx, skills, tools, routes } = fakeContext()
  apply(ctx)
  assert.equal(skills.length, 1)
  assert.equal(tools.length, 1)
  assert.equal(tools[0].name, 'sjs')
  assert.deepEqual([...routes.keys()].sort(), [
    '/serial-job-session/api/probe',
    '/serial-job-session/api/settings',
    '/serial-job-session/api/state',
  ])
})

test('apply stays loadable in a profile without tools, skills or a Web carrier', () => {
  const { ctx, skills, tools, routes } = fakeContext({ skills: false, tools: false, web: false })
  assert.doesNotThrow(() => apply(ctx))
  assert.equal(skills.length, 0)
  assert.equal(tools.length, 0)
  assert.equal(routes.size, 0)
})

test('the tool schema exposes every v5 subcommand and the shared options', () => {
  const { ctx, tools } = fakeContext()
  apply(ctx)
  const parameters = tools[0].parameters
  assert.deepEqual(parameters.required, ['command'])
  assert.deepEqual(parameters.properties.command.enum, [
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
  ])
  for (const key of ['instance', 'routeKey', 'input', 'dryRun', 'human', 'extraArgs']) {
    assert.ok(parameters.properties[key], `missing parameter ${key}`)
  }
})

test('argv puts options before the subcommand and maps booleans to flags', () => {
  const argv = buildArgs(
    {
      command: 'init',
      instance: 'workflows/demo',
      dryRun: true,
      human: false,
      taskId: 3,
      expectedRevision: 2,
      extraArgs: ['--append-library', ''],
    },
    true,
  )
  assert.deepEqual(argv, [
    '--instance',
    'workflows/demo',
    '--task-id',
    '3',
    '--expected-revision',
    '2',
    '--input',
    '-',
    '--dry-run',
    '--append-library',
    'init',
  ])
})

test('argv adds profile-level extra arguments before per-call ones', () => {
  const argv = buildArgs({ command: 'stats', extraArgs: ['--max-chars', '4000'] }, false, ['--since', '7'])
  assert.deepEqual(argv, ['--since', '7', '--max-chars', '4000', 'stats'])
})

test('argv drops empty values and unknown commands', () => {
  assert.deepEqual(buildArgs({ command: 'nope', instance: '', routeKey: null }, false), [])
  assert.deepEqual(buildArgs({ command: 'stats' }, false), ['stats'])
})

test('settings sanitizing clamps, trims, splits lines and de-duplicates', () => {
  const settings = sanitizeSettings({
    python: '  C:/Python/python.exe  ',
    timeoutMs: 99,
    extraArgs: ['--since\n 7 ', '', '--since\n7', ' x '],
  })
  assert.deepEqual(settings, {
    python: 'C:/Python/python.exe',
    timeoutMs: 1000,
    extraArgs: ['--since', '7', 'x'],
  })
  assert.equal(sanitizeSettings({ timeoutMs: 99_999_999 }).timeoutMs, 600_000)
  assert.deepEqual(sanitizeSettings(undefined), { python: '', timeoutMs: 120_000, extraArgs: [] })
  assert.deepEqual(sanitizeSettings({ python: 7, extraArgs: 'nope' }), {
    python: '',
    timeoutMs: 120_000,
    extraArgs: [],
  })
})

test('settings round-trip through the profile-scoped file', async () => {
  await withProfileDir((dir) => {
    assert.equal(settingsPath(), path.join(dir, 'serial-job-session.json'))
    assert.deepEqual(readSettings(), { python: '', timeoutMs: 120_000, extraArgs: [] })
    const stored = writeSettings({ python: 'python3', timeoutMs: 45_000, extraArgs: ['--append-library'] })
    assert.deepEqual(readSettings(), stored)
    assert.deepEqual(stored, { python: 'python3', timeoutMs: 45_000, extraArgs: ['--append-library'] })
    const raw = JSON.parse(readFileSync(path.join(dir, 'serial-job-session.json'), 'utf8'))
    assert.equal(raw.timeoutMs, 45_000)
  })
})

test('a corrupt settings file reads as the defaults', async () => {
  await withProfileDir((dir) => {
    writeSettings({ python: 'x' })
    const target = path.join(dir, 'serial-job-session.json')
    rmSync(target, { force: true })
    assert.deepEqual(readSettings().python, '')
  })
})

test('the state route answers the panel with settings and paths', async () => {
  await withProfileDir(async (dir) => {
    const { ctx, routes } = fakeContext()
    apply(ctx)
    writeSettings({ python: 'python3', timeoutMs: 60_000, extraArgs: ['--since', '3'] })
    const handler = routes.get('/serial-job-session/api/state').handler
    const { res, read } = fakeResponse()
    await handler({ method: 'GET', headers: {} }, res)
    const result = read()
    assert.equal(result.status, 200)
    assert.equal(result.body.ok, true)
    assert.equal(result.body.settings.python, 'python3')
    assert.equal(result.body.settingsPath, path.join(dir, 'serial-job-session.json'))
    assert.equal(result.body.skill.name, 'serial-job-session')
    assert.equal(result.body.repoRoot, ROOT)
    assert.ok(result.body.defaults && typeof result.body.defaults === 'object')
  })
})

test('the state route rejects a cross-site read', async () => {
  const { ctx, routes } = fakeContext()
  apply(ctx)
  const handler = routes.get('/serial-job-session/api/state').handler
  const { res, read } = fakeResponse()
  await handler({ method: 'GET', headers: { 'sec-fetch-site': 'cross-site' } }, res)
  assert.equal(read().status, 403)
})

test('the settings route persists a posted document and refuses bad input', async () => {
  await withProfileDir(async () => {
    const { ctx, routes } = fakeContext()
    apply(ctx)
    const handler = routes.get('/serial-job-session/api/settings').handler

    const request = Readable.from([JSON.stringify({ python: 'py', timeoutMs: 30_000, extraArgs: ['--dry-run'] })])
    request.method = 'POST'
    request.headers = { 'content-type': 'application/json', 'sec-fetch-site': 'same-origin' }
    const { res, read } = fakeResponse()
    await handler(request, res)
    assert.equal(read().status, 200)
    assert.deepEqual(readSettings(), { python: 'py', timeoutMs: 30_000, extraArgs: ['--dry-run'] })

    const wrongType = Readable.from(['{}'])
    wrongType.method = 'POST'
    wrongType.headers = { 'content-type': 'text/plain' }
    const wrongTypeResponse = fakeResponse()
    await handler(wrongType, wrongTypeResponse.res)
    assert.equal(wrongTypeResponse.read().status, 415)

    const malformed = Readable.from(['{not json'])
    malformed.method = 'POST'
    malformed.headers = { 'content-type': 'application/json' }
    const malformedResponse = fakeResponse()
    await handler(malformed, malformedResponse.res)
    assert.equal(malformedResponse.read().status, 400)
  })
})

test('result rendering prefers parsed data and truncates long output', () => {
  assert.equal(renderResultText({ data: { a: 1 }, stdout: '{"a":1}' }), '{\n  "a": 1\n}')
  assert.equal(renderResultText({ stdout: 'plain' }), 'plain')
  const long = renderResultText({ stdout: 'x'.repeat(40_000) })
  assert.ok(long.length < 40_000)
  assert.ok(long.includes('output truncated'))
})
