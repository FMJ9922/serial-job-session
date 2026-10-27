#!/usr/bin/env node
/**
 * Pre-publish smoke check for the npm tarball.
 *
 * The runtime CLI (`scripts/sjs.py`) reads `skill-integrity.json` on startup, so a
 * package that ships the CLI without that file fails every subcommand on a fresh
 * install (this happened in 5.1.1). `npm publish` packs only what `package.json`
 * `files` selects, so the check asks npm itself for the file list instead of
 * re-reading that list here.
 *
 * Run with `npm run smoke:pack`, or `node dsh/check-pack.mjs`.
 */

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(PLUGIN_DIR, '..')

/** Paths the installed plugin cannot work without. */
const REQUIRED = [
  'package.json',
  'SKILL.md',
  'VERSION',
  'skill-integrity.json',
  'defaults.json',
  'roles.json',
  'scripts/sjs.py',
  'scripts/sjs_store.py',
  'scripts/sjs_config.py',
  'scripts/sjs_context.py',
  'schemas/run.schema.json',
  'references/runtime.md',
  'dsh/index.js',
  'dsh/client.js',
  'dsh/cordis.patch.yml',
  'dsh/icon.svg',
  'dsh/locale/en.json',
  'dsh/locale/zh.json',
  'dsh/INTRO.md',
  'dsh/INTRO.en.md',
]

const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const output = execFileSync(npm, ['pack', '--dry-run', '--json'], {
  cwd: ROOT,
  encoding: 'utf8',
  stdio: ['ignore', 'pipe', 'pipe'],
  // Node 22 refuses to spawn a `.cmd` shim directly on Windows (EINVAL).
  shell: process.platform === 'win32',
})
const report = JSON.parse(output)
const files = new Set(report[0].files.map((entry) => entry.path))
const missing = REQUIRED.filter((relative) => !files.has(relative))

if (missing.length > 0) {
  console.error(`smoke:pack FAILED — the tarball is missing ${missing.length} required file(s):`)
  for (const relative of missing) console.error(`  - ${relative}`)
  process.exit(1)
}

assert.ok(report[0].files.length >= REQUIRED.length)
console.log(
  `smoke:pack OK — ${report[0].filename}: ${report[0].files.length} files, all ${REQUIRED.length} required paths present.`,
)
