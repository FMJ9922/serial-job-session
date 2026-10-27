#!/usr/bin/env node
/**
 * End-to-end smoke check for a non-ASCII payload over stdin.
 *
 * The `sjs` tool sends its JSON payload through the child's stdin. On Windows a
 * cp936 console decoded those UTF-8 bytes as GBK, which produced lone
 * surrogates inside `sjs.py` ("'utf-8' codec can't encode character ...:
 * surrogates not allowed"). The plugin now forces UTF-8 for every Python child;
 * this check reproduces the tool's exact setup — Chinese payload, stdin, the
 * real CLI — and fails if that regresses.
 *
 * Run with `npm run smoke:stdin`, or `node dsh/smoke-stdin.mjs`.
 * Requires Python 3.11+ on PATH (or `SJS_PYTHON`).
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { pythonEnv, resolvePython } from './index.js'

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(PLUGIN_DIR, '..')
const CLI = path.join(ROOT, 'scripts', 'sjs.py')

/**
 * Run the CLI once with `init --dry-run` and a Chinese payload on stdin.
 *
 * @param {string} interpreter resolved Python executable.
 * @param {string} instance instance path passed to `init`.
 * @returns {Promise<{ code: number|null, stdout: string, stderr: string }>} outcome.
 */
function runInit(interpreter, instance) {
  const payload = JSON.stringify({
    route_key: '中文探针',
    goal: '验证中文 stdin 解码：任务、方向与验收标准都要能正常读写',
    authorization: { paths: ['.'], tools: ['shell'] },
    policy: { mode: 'manual', on_failure: 'skip' },
    modules: { estimates: false, feishu: false },
  })
  return new Promise((resolve) => {
    const child = spawn(interpreter, [CLI, 'init', '--instance', instance, '--input', '-', '--dry-run'], {
      cwd: ROOT,
      windowsHide: true,
      env: pythonEnv(),
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      stdout += chunk
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk
    })
    child.on('error', (error) => resolve({ code: null, stdout, stderr: String(error) }))
    child.on('close', (code) => resolve({ code, stdout, stderr }))
    child.stdin.end(payload)
  })
}

const workspace = mkdtempSync(path.join(tmpdir(), 'sjs-stdin-'))
try {
  const interpreter = await resolvePython()
  const outcome = await runInit(interpreter.executable, path.join(workspace, 'demo'))
  assert.equal(outcome.code, 0, `init exited ${outcome.code}: ${outcome.stderr || outcome.stdout}`)
  const parsed = JSON.parse(outcome.stdout)
  assert.equal(parsed.ok, true, `init reported: ${outcome.stdout}`)
  assert.equal(parsed.dry_run, true)
  assert.equal(parsed.config.goal.includes('中文'), true, 'the goal must survive UTF-8 round-tripping')
  assert.equal(parsed.config.route_key, '中文探针')
  console.log(
    `smoke:stdin OK — Chinese payload round-tripped through stdin with ${interpreter.executable} (${interpreter.version}), chcp-independent.`,
  )
} finally {
  rmSync(workspace, { recursive: true, force: true })
}
