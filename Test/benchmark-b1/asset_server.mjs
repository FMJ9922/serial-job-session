#!/usr/bin/env node
// Isolated image-assets MCP adapter. No dependencies or credentials are copied.
import { appendFile, lstat, mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';

const INSTALLED = 'C:/Users/Administrator/.cursor/mcp-servers/grok-imagine';
const MODEL = 'grok-imagine-image-2.0';
const CALIBRATION_ROOT = 'E:/Test/.benchmark-admin/b1-asset-preflight';
const MIME = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif' };

export function isWithin(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

export async function noLinks(candidate, allowMissing = false) {
  const resolved = path.resolve(candidate);
  const parsed = path.parse(resolved);
  let current = parsed.root;
  for (const component of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    try {
      if ((await lstat(current)).isSymbolicLink()) throw new Error('Symbolic links and junctions are prohibited');
    } catch (error) {
      if (allowMissing && error.code === 'ENOENT') continue;
      throw error;
    }
  }
  return resolved;
}

export async function scopedInput(project, source) {
  if (/^(?:https?:|data:)/i.test(source)) throw new Error('Only local files in the assigned project are accepted');
  const candidate = path.isAbsolute(source) ? path.resolve(source) : path.resolve(project, source);
  if (!isWithin(project, candidate)) throw new Error('Image source is outside the assigned project');
  await noLinks(candidate);
  const canonical = await realpath(candidate);
  if (!isWithin(await realpath(project), canonical)) throw new Error('Image source escaped the assigned project');
  const mime = MIME[path.extname(candidate).toLowerCase()];
  if (!mime) throw new Error('Unsupported image file extension');
  if (!(await lstat(candidate)).isFile()) throw new Error('Image source must be a file');
  return `data:${mime};base64,${(await readFile(candidate)).toString('base64')}`;
}

async function key() {
  const value = (process.env.XAI_API_KEY || process.env.XAIAPI_KEY || await readFile(path.join(INSTALLED, 'api-key.txt'), 'utf8')).trim();
  if (!value || value.startsWith('PUT_')) throw new Error('Image service credential unavailable');
  return value;
}

function baseUrl() {
  const value = new URL(process.env.XAI_API_BASE_URL || process.env.XAIAPI_BASE_URL || 'https://api.x.ai/v1');
  if (value.protocol !== 'https:' || value.username || value.password || value.search || value.hash) throw new Error('Image service requires an HTTPS base URL without credentials or query');
  return value.href.replace(/\/$/, '');
}

async function request(endpoint, body) {
  const response = await fetch(`${baseUrl()}${endpoint}`, {
    method: 'POST', headers: { Authorization: `Bearer ${await key()}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal: AbortSignal.timeout(180000),
  });
  // Do not expose upstream response bodies: they could echo credentials or payload.
  if (!response.ok) throw new Error(`Image service HTTP ${response.status}`);
  return response.json();
}

async function safeDirectory(directory) {
  await noLinks(directory, true);
  await mkdir(directory, { recursive: true });
  await noLinks(directory);
  return realpath(directory);
}

async function saveImages(data, output) {
  const directory = await safeDirectory(output);
  const files = [];
  for (const item of data?.data || []) {
    // Request base64 only; never follow provider URLs into an uncontrolled network.
    if (!item.b64_json) throw new Error('Image service did not return requested base64 data');
    const buffer = Buffer.from(item.b64_json, 'base64');
    if (!buffer.length) throw new Error('Image service returned an empty image');
    const png = buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    const jpg = buffer[0] === 255 && buffer[1] === 216;
    const webp = buffer.subarray(0, 4).toString() === 'RIFF' && buffer.subarray(8, 12).toString() === 'WEBP';
    if (!png && !jpg && !webp) throw new Error('Image service returned unsupported image bytes');
    const extension = png ? '.png' : jpg ? '.jpg' : '.webp';
    await noLinks(directory);
    const destination = path.join(directory, `${Date.now()}-${randomUUID()}${extension}`);
    await writeFile(destination, buffer, { flag: 'wx' });
    files.push({ path: destination, bytes: buffer.length });
  }
  if (!files.length) throw new Error('Image service returned no images');
  return files;
}

function options(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index++) {
    const name = argv[index];
    if (name === '--preflight') result.preflight = true;
    else if (['--project', '--log', '--output'].includes(name) && argv[index + 1] && !argv[index + 1].startsWith('--')) result[name.slice(2)] = argv[++index];
    else throw new Error('Unknown or missing command argument');
  }
  return result;
}

async function main() {
  const args = options(process.argv.slice(2));
  if (args.preflight) {
    const output = path.resolve(args.output || CALIBRATION_ROOT);
    if (!isWithin(CALIBRATION_ROOT, output)) throw new Error('Calibration output must stay within the administrator preflight directory');
    const directory = await safeDirectory(output);
    await key();
    const metadata = { time: new Date().toISOString(), credential_available: true, model: MODEL, endpoint: baseUrl(), calibration_only: true };
    await writeFile(path.join(directory, 'availability.json'), JSON.stringify(metadata, null, 2));
    const files = await saveImages(await request('/images/generations', {
      model: MODEL, prompt: 'A single neutral gray circle centered on a plain white background. No text, no game assets, no code. Technical service calibration only.',
      n: 1, aspect_ratio: '1:1', resolution: '1k', quality: 'low', response_format: 'b64_json',
    }), directory);
    await writeFile(path.join(directory, 'result.json'), JSON.stringify({ ...metadata, success: true, files }, null, 2));
    process.stdout.write(JSON.stringify({ success: true, model: MODEL, files }) + '\n');
    return;
  }
  if (!args.project || !args.log || args.output) throw new Error('Required: --project PROJECT --log LOG');
  const project = await noLinks(args.project);
  const projectReal = await realpath(project);
  if (!(await lstat(path.join(project, 'Assets'))).isDirectory()) throw new Error('Project Assets directory is missing');
  const output = path.join(projectReal, 'Assets', 'Generated', 'Images');
  const logPath = path.resolve(args.log);
  await noLinks(logPath, true);
  await safeDirectory(path.dirname(logPath));
  const log = async (record) => {
    await noLinks(logPath, true);
    await appendFile(logPath, JSON.stringify({ time: new Date().toISOString(), ...record }) + '\n', 'utf8');
  };
  const require = createRequire(path.join(INSTALLED, 'package.json'));
  const { McpServer } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/server/mcp.js')).href);
  const { StdioServerTransport } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/server/stdio.js')).href);
  const { z } = require('zod');
  const server = new McpServer({ name: 'b1-isolated-image-assets', version: '1.0.0' });
  const schema = { prompt: z.string().min(1).max(20000), n: z.number().int().min(1).max(10).optional(), aspect_ratio: z.enum(['1:1','16:9','9:16','4:3','3:4','3:2','2:3','2:1','1:2','19.5:9','9:19.5','20:9','9:20','auto']).optional(), resolution: z.enum(['1k','2k']).optional(), quality: z.enum(['low','medium']).optional() };
  const invoke = async (tool, input) => {
    const callId = randomUUID();
    // Record textual requests and source paths, never image data or credentials.
    const loggedInput = { ...input };
    if (loggedInput.image_urls) loggedInput.image_urls = loggedInput.image_urls.map((source) => /^(?:https?:|data:)/i.test(source) ? '[non-local source redacted]' : source);
    await log({ event: 'request', call_id: callId, tool, arguments: loggedInput });
    try {
      const { image_urls: sources, ...parameters } = input;
      const body = { ...parameters, model: MODEL, response_format: 'b64_json' };
      if (sources) {
        const resolved = await Promise.all(sources.map((source) => scopedInput(projectReal, source)));
        if (resolved.length === 1) body.image = { url: resolved[0], type: 'image_url' };
        else body.images = resolved.map((url) => ({ url, type: 'image_url' }));
      }
      // Recheck the root and output ancestors immediately before requesting/writing.
      await noLinks(project);
      await noLinks(output, true);
      const files = await saveImages(await request(sources ? '/images/edits' : '/images/generations', body), output);
      await log({ event: 'response', call_id: callId, tool, success: true, files });
      return { content: [{ type: 'text', text: JSON.stringify({ model: MODEL, files }) }], isError: false };
    } catch (error) {
      const message = /^Image service HTTP \d+$/.test(error.message) ? error.message : error.name === 'TimeoutError' ? 'Image service timed out' : 'Image request failed or violated project file scope';
      await log({ event: 'response', call_id: callId, tool, success: false, error: message });
      return { content: [{ type: 'text', text: message }], isError: true };
    }
  };
  server.tool('generate_image', 'Generate visual assets only. Outputs are saved inside this project Assets/Generated/Images. Never request code, planning, or reviews.', schema, (input) => invoke('generate_image', input));
  server.tool('edit_image', 'Edit visual assets only. image_urls must be local image paths inside this assigned project; URLs/data URIs and cross-project sources are prohibited.', { ...schema, image_urls: z.array(z.string().max(4096)).min(1).max(3) }, (input) => invoke('edit_image', input));
  await server.connect(new StdioServerTransport());
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(() => { process.stderr.write('Asset adapter failed; check scoped paths, installed SDK and service access.\n'); process.exitCode = 1; });
}
