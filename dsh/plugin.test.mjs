/**
 * Offline checks for the DSH bundle's Host half.
 *
 * They run without a Harness: `apply` receives a fake context that records
 * registrations, so the suite proves the skill and tool definitions are built
 * from the checkout and that argv mapping matches `scripts/sjs.py`.
 *
 * Run with `node --test dsh/plugin.test.mjs`.
 */

import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  apply,
  buildArgs,
  buildSkillRegistration,
  inject,
  name,
  parseFrontMatter,
  renderResultText,
} from './index.js'

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(PLUGIN_DIR, '..')

/**
 * Minimal Cordis-like context that records what a plugin registers.
 *
 * @returns {{ ctx: object, skills: object[], tools: object[] }} recorder.
 */
function fakeContext() {
  const skills = []
  const tools = []
  const disposers = []
  const ctx = {
    effect(callback) {
      const disposer = callback()
      if (typeof disposer === 'function') disposers.push(disposer)
      return () => {
        if (typeof disposer === 'function') disposer()
      }
    },
    get() {
      return undefined
    },
    skills: {
      register(registration) {
        skills.push(registration)
        return () => {}
      },
    },
    tools: {
      register(definition) {
        tools.push(definition)
        return () => {}
      },
    },
  }
  return { ctx, skills, tools, disposers }
}

test('bundle metadata names one row and declares its hard dependencies', () => {
  assert.equal(name, 'dsh-serial-job-session')
  assert.deepEqual([...inject].sort(), ['skills', 'tools'])
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

test('SJS_MODEL_INVOCABLE=false keeps the stricter user-only policy', () => {
  const previous = process.env.SJS_MODEL_INVOCABLE
  process.env.SJS_MODEL_INVOCABLE = 'false'
  try {
    const skill = buildSkillRegistration()
    assert.equal(skill.invocation.modelInvocable, false)
    assert.equal(skill.invocation.userInvocable, true)
  } finally {
    if (previous === undefined) delete process.env.SJS_MODEL_INVOCABLE
    else process.env.SJS_MODEL_INVOCABLE = previous
  }
})

test('apply registers exactly one skill and one tool, each disposable', () => {
  const { ctx, skills, tools, disposers } = fakeContext()
  apply(ctx)
  assert.equal(skills.length, 1)
  assert.equal(tools.length, 1)
  assert.equal(tools[0].name, 'sjs')
  assert.equal(disposers.length, 2)
  for (const dispose of disposers) assert.equal(typeof dispose, 'function')
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

test('argv drops empty values and unknown commands', () => {
  assert.deepEqual(buildArgs({ command: 'nope', instance: '', routeKey: null }, false), [])
  assert.deepEqual(buildArgs({ command: 'stats' }, false), ['stats'])
})

test('result rendering prefers parsed data and truncates long output', () => {
  assert.equal(renderResultText({ data: { a: 1 }, stdout: '{"a":1}' }), '{\n  "a": 1\n}')
  assert.equal(renderResultText({ stdout: 'plain' }), 'plain')
  const long = renderResultText({ stdout: 'x'.repeat(40_000) })
  assert.ok(long.length < 40_000)
  assert.ok(long.includes('output truncated'))
})
