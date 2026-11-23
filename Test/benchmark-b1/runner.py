"""B1 controller. No model calls occur in prepare/status/preflight or tests.

The admin directory contains credentials and judge artifacts: never expose it to
contestants. Filesystem isolation is policy-based, not a security boundary.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

DEFAULT_ADMIN = Path('E:/Test/.benchmark-admin/b1')
EXCLUDE = {'.git', 'Library', 'Temp', 'Obj', 'Logs', 'obj', '.vs', '.idea',
           '__pycache__', 'UserSettings', '.benchmark-local'}
TERMINAL = {'submitted', 'abandoned', 'timeout', 'stopped', 'invalid'}

def native_json_events(path, allow_diagnostics=False, with_line=False):
    """Read native JSONL without rewriting bytes or turning diagnostics into events.

    A timestamp diagnostic ending in ': {' may contain one complete JSON error
    body. Its exact balanced object is validated before it is excluded. Typed
    envelopes, unknown root keys, duplicate keys, NaN, truncation and boundary
    crossings block. Existing adjacent indented plaintext diagnostics remain
    supported; valid standalone event JSON always takes precedence there.
    """
    path = Path(path)
    diagnostic = False
    body, stack, quoted, escaped = None, [], False, False
    origin = None
    timestamp = re.compile(r'^\d{4}-\d\d-\d\dT[^ ]+\s+(WARN|ERROR|INFO|DEBUG|TRACE)\b')

    def fail(number):
        raise RuntimeError(f'Unrecognized event at {path.name}:{number}')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate diagnostic key')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('NonJSON diagnostic constant')

    def diagnostic_value(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('item', 'thread_id', 'turn_id') or (key in ('type', 'event') and isinstance(child, str)):
                    raise ValueError('Typed envelope inside diagnostic body')
                diagnostic_value(child)
        elif isinstance(value, list):
            for child in value:
                diagnostic_value(child)

    def feed(text, number):
        nonlocal quoted, escaped
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in '{[':
                stack.append(char)
            elif char in '}]':
                if not stack or stack.pop() != ('{' if char == '}' else '['):
                    fail(number)
        # Newlines cannot continue a JSON string or a JSON escape.
        if quoted or escaped:
            fail(number)

    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if body is not None:
            # Only indented body content or the exact unindented root close.
            # No blank separator, timestamp, raw event or arbitrary text may
            # bridge an incomplete diagnostic object.
            if not line.strip() or (not line[:1].isspace() and line != '}'):
                fail(number)
            body.append(line)
            feed(line, number)
            if stack:
                continue
            try:
                value = json.loads('\n'.join(body), object_pairs_hook=unique, parse_constant=constant)
                if (not isinstance(value, dict) or not value
                        or not set(value) <= {'error', 'status', 'detail', 'body'}
                        or not set(value) & {'error', 'detail', 'body'}):
                    raise ValueError('Unknown diagnostic body schema')
                diagnostic_value(value)
            except ValueError as exc:
                raise RuntimeError(f'Unrecognized event diagnostic body: {path.name}:{origin}-{number}') from exc
            body, diagnostic = None, False
            continue
        if not line.strip():
            diagnostic = False
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            if allow_diagnostics and timestamp.match(line):
                diagnostic = True
                if re.search(r':\s*\{\s*$', line):
                    body, stack, origin = ['{'], ['{'], number
                    quoted, escaped = False, False
                continue
            if allow_diagnostics and diagnostic and line[:1] in (' ', '\t'):
                continue
            raise RuntimeError(f'Unrecognized event at {path.name}:{number}') from exc
        diagnostic = False
        if not isinstance(event, dict) or not isinstance(event.get('type', event.get('event')), str):
            raise RuntimeError(f'Unrecognized event envelope: {path.name}:{number}')
        yield (number, event) if with_line else event
    if body is not None:
        raise RuntimeError(f'Unrecognized event incomplete diagnostic body: {path.name}:{origin}')


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

@contextmanager
def controller_lock(admin):
    path = Path(admin) / 'CONTROLLER.lock'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, json.dumps({'pid': os.getpid(), 'utc': utc()}).encode())
        os.close(fd)
        yield
    finally:
        path.unlink()

def check_links(path):
    path = Path(os.path.abspath(path))
    for current in (path, *path.parents):
        try:
            status = current.lstat()
        except FileNotFoundError:
            continue
        if getattr(status, 'st_file_attributes', 0) & 0x400 or current.is_symlink():
            raise RuntimeError(f'Cannot freeze linked/reparse path: {current}')
    return path

def inventory(root):
    """Never follow symlinks/junctions, including ones hidden in cache folders."""
    root = check_links(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    found = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        base = Path(base)
        for name in dirs + files:
            p = base / name
            check_links(p)
        dirs[:] = sorted(d for d in dirs if base != root or d not in EXCLUDE)
        for name in sorted(files):
            p = base / name
            found[p.relative_to(root).as_posix()] = digest(p)
    return found

def copy_tree(source, destination):
    files = inventory(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for rel in files:
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source) / rel, target)
    if inventory(destination) != files:
        raise RuntimeError('Snapshot changed during copy')
    return files

def git_baseline(project):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(project), *args], text=True).strip()
    if git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError(f'Initial project is dirty: {project}')
    return git('rev-parse', 'HEAD')

def load_spec():
    try:
        from . import spec
    except ImportError:
        import spec
    return spec

def normalize_groups(spec):
    groups = spec.GROUPS
    if isinstance(groups, dict):
        return {k: dict(v) for k, v in groups.items()}
    return {g['id']: dict(g) for g in groups}

def overrides(group, proxy):
    args = [str(Path(proxy).resolve()), '--project', group['project'], '--log',
            str(Path(proxy).parent.parent / 'groups' / group['id'] / 'unity-tools.jsonl')]
    config = {
        'model_reasoning_effort': 'high', 'agents.max_concurrent_threads_per_session': 2,
        'agents.default_subagent_reasoning_effort': 'high',
        'agents.max_depth': 20, 'features.multi_agent': True,
        'mcp_servers.unity.command': sys.executable,
        'mcp_servers.unity.args': args,
    }
    role = (Path(proxy).parent.parent / 'groups' / group['id'] / 'home' / 'benchmark-role.toml').resolve()
    for name in ('default', 'worker', 'explorer'):
        config[f'agents.{name}.config_file'] = str(role)
    asset = Path(proxy).with_name('asset_server.mjs')
    if asset.exists():
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Frozen asset server requires Node.js')
        config['mcp_servers.assets.command'] = node
        config['mcp_servers.assets.args'] = [str(asset), '--project', group['project'], '--log',
                                            str(Path(proxy).parent.parent / 'groups' / group['id'] / 'asset-tools.jsonl')]
    return config

def cli_command(cli, group, proxy, final, session=None):
    command = [cli, '--no-daemon', 'exec']
    if session:
        command += ['resume', session]
    command += ['--ignore-user-config', '--ignore-rules', '--strict-config', '--json',
                '--model', group['model'], '--dangerously-bypass-approvals-and-sandbox',
                '--output-last-message', str(final)]
    if not session:
        command += ['--cd', group['project']]
    for key, value in overrides(group, proxy).items():
        command += ['--config', key + '=' + json.dumps(value)]
    return command + ['-']

def validate_catalog(cache, models):
    data = json.loads(Path(cache).read_text(encoding='utf-8'))
    catalog = {m['slug']: m for m in data['models']}
    for model in models:
        if model not in catalog:
            raise RuntimeError(f'Exact model unavailable in catalog: {model}; no substitution')
        levels = catalog[model].get('supported_reasoning_levels', [])
        if not any(x.get('effort') == 'high' for x in levels):
            raise RuntimeError(f'{model} does not advertise high reasoning')
    return {m: catalog[m] for m in models}

def preflight(cli, auth_home, models):
    """Strict config parsing starts an idle app server, never sends model input."""
    help_text = subprocess.check_output([cli, 'exec', '--help'], text=True)
    for flag in ['--ignore-user-config', '--strict-config', '--json', '--model']:
        if flag not in help_text:
            raise RuntimeError(f'CLI missing {flag}')
    catalog = validate_catalog(Path(auth_home) / 'models_cache.json', models)
    with tempfile.TemporaryDirectory(prefix='b1-parse-') as home:
        env = os.environ.copy()
        env['CODEX_HOME'] = home
        proc = subprocess.Popen([cli, 'app-server', '--strict-config', '--stdio',
                                 '-c', 'agents.max_concurrent_threads_per_session=2',
                                 '-c', 'agents.default_subagent_reasoning_effort="high"',
                                 '-c', 'agents.max_depth=20',
                                 '-c', 'features.multi_agent=true'], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
        try:
            time.sleep(1)
            if proc.poll() is not None:
                raise RuntimeError('CLI rejects agent config: ' + proc.stderr.read().decode(errors='replace'))
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.communicate(timeout=10)
    return {'utc': utc(), 'cli_version': subprocess.check_output([cli, '--version'], text=True).strip(),
            'catalog': catalog, 'live_model_probe': False,
            'warning': 'Catalog proves advertised IDs only; first live response still requires model verification.'}

def live_probes(cli, auth_home, models, output):
    """Optional explicitly requested neutral API probes outside formal projects."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for model in sorted(models):
        own = output / 'groups' / model
        own.mkdir(parents=True)
        home = own / 'home'
        home.mkdir()
        (home / 'benchmark-role.toml').write_text('model = ' + json.dumps(model) + '\nmodel_reasoning_effort = "high"\n', encoding='utf-8')
        for name in ('auth.json', 'models_cache.json'):
            source = Path(auth_home) / name
            if source.exists():
                shutil.copy2(source, home / name)
                os.chmod(home / name, 0o600)
        env = os.environ.copy()
        env['CODEX_HOME'] = str(home.resolve())
        final = own / 'final.txt'
        cmd = [cli, '--no-daemon', 'exec', '--ignore-user-config', '--ignore-rules', '--strict-config', '--json',
               '--model', model, '-c', 'model_reasoning_effort="high"', '--sandbox', 'read-only',
               '--skip-git-repo-check', '--cd', str(own.resolve()), '--output-last-message', str(final.resolve()), '-']
        group = {'id': model, 'model': model, 'project': str(own.resolve())}
        for key, value in overrides(group, output / 'frozen/unity_proxy.py').items():
            if not key.startswith('mcp_servers.'):
                cmd[-1:-1] = ['-c', key + '=' + json.dumps(value)]
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=os.name != 'nt')
        tree = ProcessTree(proc)
        timed_out = False
        prompt = ('Neutral infrastructure probe only. Do not read or modify any project or create any files. '
                  'Use your native spawn_agent tool to create exactly one worker subagent using the same exact model '
                  + model + ' and high reasoning. Tell that child: Reply exactly B1_CHILD_OK, do not call tools or spawn descendants. '
                  'Wait for its reply. Then your final reply must be exactly B1_PREFLIGHT_OK. '
                  'No business work or external calls except this native single subagent.')
        try:
            out, _ = proc.communicate(prompt.encode(), timeout=180)
        except subprocess.TimeoutExpired:
            timed_out = True
            tree.stop()
            out, _ = proc.communicate(timeout=10)
        finally:
            tree.stop()
        (own / 'events.jsonl').write_bytes(out)
        observed = {}
        for line in out.splitlines():
            try:
                parse_event(json.loads(line), observed)
            except (ValueError, TypeError):
                pass
        rollout_metadata(home, observed)
        models_seen = observed.get('actual_models', [])
        passed = (not timed_out and models_seen == [model] and observed.get('actual_efforts') == ['high']
                  and observed.get('child_session_count', 0) >= 1 and final.exists()
                  and final.read_text(encoding='utf-8').strip() == 'B1_PREFLIGHT_OK')
        results[model] = {'passed': passed, 'timed_out': timed_out, 'observations': observed, 'exit_code': proc.returncode}
        write_json(output / 'results.json', results)
        if not passed:
            raise RuntimeError(f'Exact live model preflight failed: {model}; see {own}')
    return results

def verify_readiness(path, models):
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if not all(report.get(key) is True for key in ('ready', 'calibration_passed', 'image_access_passed')):
        raise RuntimeError('Infrastructure readiness is not approved')
    if set(report.get('models', [])) != set(models):
        raise RuntimeError('Readiness report must verify exact three models')
    evidence = report.get('evidence_files', {})
    if not evidence:
        raise RuntimeError('Readiness needs hashed calibration/model/image evidence')
    for source, expected in evidence.items():
        if digest(source) != expected:
            raise RuntimeError(f'Readiness evidence changed: {source}')
    return report

def prepare(admin, cli, auth_home, skill, unity_skill, proxy, readiness):
    spec = load_spec()
    groups = normalize_groups(spec)
    ready = verify_readiness(readiness, {g['model'] for g in groups.values()})
    skill = Path(skill)
    # The frozen package must identify the requested release, not a newer checkout.
    package = skill / 'package.json'
    version = json.loads(package.read_text(encoding='utf-8')).get('version') if package.exists() else None
    if version is None and (skill / 'VERSION').exists():
        version = (skill / 'VERSION').read_text(encoding='utf-8').strip()
    if version != '5.2.2':
        raise RuntimeError('Skill release 5.2.2 not established by package version metadata')
    if admin.exists():
        raise RuntimeError(f'Refusing to overwrite admin directory: {admin}')
    checks = spec.CHECKS
    if len(checks) != 100:
        raise RuntimeError('Expected exactly 100 checks')
    info = preflight(cli, auth_home, {g['model'] for g in groups.values()})
    baselines = {}
    for key, group in groups.items():
        group.setdefault('project', str(Path('E:/Test') / key / 'AITest'))
        group['project'] = str(Path(group['project']).resolve())
        baselines[key] = {'commit': git_baseline(group['project']), 'files': inventory(group['project'])}
    # No contestant state exists until all nonmutating validations passed.
    admin.mkdir(parents=True)
    frozen = admin / 'frozen'
    frozen.mkdir()
    copy_tree(skill, frozen / 'serial-job-session')
    copy_tree(unity_skill, frozen / 'unity-mcp-skill')
    shutil.copy2(proxy, frozen / 'unity_proxy.py')
    asset = Path(proxy).with_name('asset_server.mjs')
    if asset.exists():
        shutil.copy2(asset, frozen / 'asset_server.mjs')
    shutil.copy2(Path(spec.__file__), frozen / 'spec.py')
    shutil.copy2(Path(__file__), frozen / 'runner.py')
    write_json(frozen / 'readiness.json', ready)
    (frozen / 'common-prompt.txt').write_text(spec.common_prompt(), encoding='utf-8')
    for key, group in groups.items():
        own = admin / 'groups' / key
        own.mkdir(parents=True)
        home = own / 'home'
        home.mkdir()
        (home / 'benchmark-role.toml').write_text('model = ' + json.dumps(group['model']) + '\nmodel_reasoning_effort = "high"\n', encoding='utf-8')
        skills = home / 'skills'
        skills.mkdir()
        copy_tree(frozen / 'unity-mcp-skill', skills / 'unity-mcp-skill')
        if group['use_skill']:
            copy_tree(frozen / 'serial-job-session', skills / 'serial-job-session')
        for name in ['auth.json', 'models_cache.json']:
            src = Path(auth_home) / name
            if src.exists():
                shutil.copy2(src, home / name)
                os.chmod(home / name, 0o600)
        prompt = spec.common_prompt() + '\n\n' + spec.treatment_prompt(group['use_skill'], str(skills / 'serial-job-session'))
        prompt += '\n本组模型必须是 ' + group['model'] + '，所有后代必须使用同型号及 high，团队同时最多 3 个 agent。仅操作自己的工程和 Unity 实例。'
        prompt += '\n最终交付答复首行使用 B1_FINAL_STATUS: submitted；如主动放弃或无法继续则使用 B1_FINAL_STATUS: abandoned。最终答复即交卷。'
        (own / 'prompt.txt').write_text(prompt, encoding='utf-8')
        write_json(own / 'state.json', {'status': 'ready', 'effective_seconds': 0, 'attempts': [], 'group': key})
    manifest = {'version': 'B1', 'created_utc': utc(), 'groups': groups, 'order': list(spec.ORDER),
                'baselines': baselines, 'preflight': info, 'cli': cli,
                'frozen_files': inventory(frozen), 'limit_seconds': 86400,
                'prompt_hashes': {k: digest(admin / 'groups' / k / 'prompt.txt') for k in groups}}
    write_json(admin / 'manifest.json', manifest)
    (admin / 'manifest.sha256').write_text(digest(admin / 'manifest.json'), encoding='ascii')
    return manifest

def manifest(admin):
    if digest(admin / 'manifest.json') != (admin / 'manifest.sha256').read_text().strip():
        raise RuntimeError('Frozen manifest hash mismatch')
    data = json.loads((admin / 'manifest.json').read_text(encoding='utf-8'))
    if inventory(admin / 'frozen') != data['frozen_files']:
        raise RuntimeError('Frozen specification/tool files changed')
    return data

class ProcessTree:
    """Windows job closes every descendant even if CLI parent exits first."""
    def __init__(self, proc):
        self.proc, self.job = proc, None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class BASIC(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                            ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                            ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
            class IO(ctypes.Structure):
                _fields_ = [(x, ctypes.c_ulonglong) for x in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
            class EXT(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', BASIC), ('IoInfo', IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                            ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.CreateJobObjectW.restype = wintypes.HANDLE
            api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            job = api.CreateJobObjectW(None, None)
            limits = EXT()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not job or not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(job, int(proc._handle)):
                proc.kill()
                if job:
                    api.CloseHandle(job)
                raise OSError(ctypes.get_last_error(), 'Cannot establish process-tree containment')
            self.job, self.api = job, api
    def stop(self):
        if self.job:
            self.api.CloseHandle(self.job)
            self.job = None
        elif os.name != 'nt':
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=30)

def parse_event(event, observed):
    typ = event.get('type')
    if typ == 'thread.started':
        observed['session_id'] = event.get('thread_id')
    if typ == 'turn.completed':
        observed['turn_completed'] = True
        observed.setdefault('usage_events', []).append(event.get('usage'))
    item = event.get('item', {})
    if typ == 'item.completed' and item.get('type') == 'agent_message':
        observed['final_text'] = item.get('text', '')
    # Only explicit metadata is evidence of actual model; requested model is not.
    for obj in (event, event.get('payload', {}), event.get('payload', {}).get('info', {})):
        if isinstance(obj, dict) and obj.get('model'):
            observed.setdefault('actual_models', []).append(obj['model'])
        if isinstance(obj, dict):
            effort = obj.get('reasoning_effort', obj.get('effort'))
            if effort:
                observed.setdefault('actual_efforts', []).append(effort)

def rollout_metadata(home, observed):
    sessions = {}
    for path in (home / 'sessions').rglob('*.jsonl') if (home / 'sessions').exists() else []:
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'turn_context':
                parse_event(event, observed)
            if event.get('type') == 'session_meta':
                payload = event.get('payload', {})
                sid = payload.get('id', payload.get('session_id'))
                if sid:
                    sessions[sid] = {'source': payload.get('source'), 'cwd': payload.get('cwd')}
    observed['actual_models'] = sorted(set(observed.get('actual_models', [])))
    observed['actual_efforts'] = sorted(set(observed.get('actual_efforts', [])))
    observed['sessions'] = sessions
    observed['child_session_count'] = max(0, len(sessions) - 1)

def seal(admin, key):
    data = manifest(admin)
    own = admin / 'groups' / key
    state = json.loads((own / 'state.json').read_text())
    if state['status'] not in TERMINAL or not state.get('process_tree_stopped'):
        raise RuntimeError('Only a stopped terminal run can be sealed')
    if state.get('requires_terminal_drain') and not state.get('terminal_drain', {}).get('passed'):
        command = [sys.executable, str(admin / 'frozen/unity_proxy.py'), '--project',
                   data['groups'][key]['project'], '--log', str(own / 'terminal-drain.jsonl'), '--drain']
        try:
            completed = subprocess.run(command, capture_output=True, timeout=180)
            report = {'utc': utc(), 'passed': completed.returncode == 0, 'exit_code': completed.returncode,
                      'stdout': completed.stdout.decode('utf-8', errors='replace'),
                      'stderr': completed.stderr.decode('utf-8', errors='replace')}
        except (OSError, subprocess.TimeoutExpired) as exc:
            report = {'utc': utc(), 'passed': False, 'error': str(exc)}
        state['terminal_drain'] = report
        state['needs_seal_review'] = not report['passed']
        if report['passed']:
            state.pop('seal_error', None)
        write_json(own / 'terminal-drain-report.json', report)
        write_json(own / 'state.json', state)
        if not report['passed']:
            raise RuntimeError('Terminal Unity drain failed; keep candidate terminal, review infrastructure before sealing')
    destination = admin / 'sealed' / key
    if destination.exists():
        raise RuntimeError('Sealed result already exists')
    destination.mkdir(parents=True)
    files = copy_tree(data['groups'][key]['project'], destination / 'project')
    group = data['groups'][key]
    product = Path(group['project']).parent.name
    saves = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'AppData/LocalLow/SJSBenchmark' / ('AITest_' + product)
    save_hashes = copy_tree(saves, destination / 'saves') if saves.exists() else {}
    for path in own.glob('attempt-*.*'):
        shutil.copy2(path, destination / path.name)
    for path in own.glob('*-tools.jsonl'):
        shutil.copy2(path, destination / path.name)
    for path in own.glob('terminal-drain*'):
        shutil.copy2(path, destination / path.name)
    shutil.copy2(own / 'state.json', destination / 'state.json')
    logs = own / 'home' / 'sessions'
    if logs.exists():
        copy_tree(logs, destination / 'sessions')
    if (own / 'infrastructure').exists():
        copy_tree(own / 'infrastructure', destination / 'infrastructure')
    if (own / 'user-policy').exists():
        copy_tree(own / 'user-policy', destination / 'user-policy')
    write_json(destination / 'seal.json', {'utc': utc(), 'baseline_commit': data['baselines'][key]['commit'],
                                         'source_files': files, 'save_files': save_hashes,
                                         'all_files': inventory(destination)})
    return destination

def approve_infrastructure(admin, key, outage_seconds, evidence):
    """Judge-only adjudication, never model-based retry or business assistance."""
    manifest(admin)
    own = admin / 'groups' / key
    state = json.loads((own / 'state.json').read_text())
    if state['status'] != 'paused' or state.get('needs_review'):
        raise RuntimeError('Only a no-final paused run can receive infrastructure adjudication')
    if not state.get('session_id'):
        raise RuntimeError('Cannot resume without the original session')
    last = state['attempts'][-1]
    if state.get('infrastructure_approved_attempt') == last['number']:
        raise RuntimeError('This attempt has already been adjudicated')
    if not math.isfinite(outage_seconds) or not 0 <= outage_seconds <= last.get('wall_seconds', 0):
        raise RuntimeError('Outage duration must be bounded by the last attempt wall duration')
    source = check_links(evidence)
    if not source.is_file():
        raise RuntimeError('Infrastructure review requires an evidence file')
    folder = own / 'infrastructure' / str(last['number'])
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, folder / 'evidence')
    review = {'approved_utc': utc(), 'attempt': last['number'], 'outage_seconds': outage_seconds,
              'source_evidence': str(source), 'evidence_sha256': digest(folder / 'evidence'),
              'decision': 'Infrastructure failure confirmed; original session may continue without business guidance.'}
    write_json(folder / 'review.json', review)
    state.setdefault('infrastructure_reviews', []).append(review)
    state['effective_seconds'] = max(0, state['effective_seconds'] - outage_seconds)
    state['infrastructure_approved_attempt'] = last['number']
    state['needs_infrastructure_review'] = False
    write_json(own / 'state.json', state)
    return state

def operation_policy(path, sha256):
    """External administrative permissions; never rewrite a frozen task prompt."""
    if path is None or not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise RuntimeError('Operation policy requires its explicit SHA256')
    path = check_links(path)
    if not path.is_file():
        raise RuntimeError('Operation policy file/hash changed')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise RuntimeError('Operation policy file/hash changed')
    text = raw.decode('utf-8')
    if not text.strip():
        raise RuntimeError('Operation policy must be nonempty')
    return {'path': str(path), 'sha256': sha256, 'text': text}


def no_final_delivery_proof(own, state):
    """Prove root commentary is not a delivery using persisted root messages."""
    observed = state.get('observations', {})
    final_text = observed.get('final_text', '')
    marker = re.compile(r'B1_FINAL_STATUS:\s*(?:submitted|abandoned)\b')
    if ((observed.get('turn_completed') and str(final_text).strip()) or marker.search(str(final_text))
            or any(p.read_text(encoding='utf-8').strip() for p in own.glob('attempt-*.final.txt'))):
        raise RuntimeError('True final delivery exists; user-policy continuation forbidden')
    sid = state.get('session_id')
    roots = []
    for path in (own / 'home/sessions').rglob('*.jsonl'):
        lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        try:
            first = json.loads(lines[0])
            if first.get('type') != 'session_meta' or not isinstance(first.get('payload'), dict):
                raise ValueError('Session identity missing')
            if first['payload'].get('id', first['payload'].get('session_id')) != sid:
                continue  # A child's final answer is not the primary delivery.
            roots.append((path, [json.loads(line) for line in lines]))
        except (ValueError, IndexError, AttributeError) as exc:
            raise RuntimeError('Malformed persisted session identity/log') from exc
    if len(roots) != 1:
        raise RuntimeError('Exactly one persisted original root session is required for no-final proof')
    path, rows = roots[0]
    envelopes = {'session_meta', 'event_msg', 'response_item', 'world_state', 'turn_context',
                 'token_usage_record', 'inter_agent_communication_metadata', 'compacted'}
    responses = {'message', 'reasoning', 'function_call', 'function_call_output', 'custom_tool_call',
                 'custom_tool_call_output', 'agent_message', 'web_search_call', 'ghost_snapshot', 'compaction'}
    event_types = {'task_started', 'task_complete', 'task_aborted', 'turn_aborted', 'token_count',
                   'item_completed', 'agent_message', 'agent_reasoning', 'error', 'warning'}
    assistant_messages = 0
    for row in rows:
        if not isinstance(row, dict) or row.get('type') not in envelopes or not isinstance(row.get('payload'), dict):
            raise RuntimeError('Unknown or malformed root-session record; no-final proof unavailable')
        payload = row['payload']
        if row['type'] == 'event_msg' and payload.get('type') not in event_types:
            raise RuntimeError('Unknown root-session event subtype')
        if row['type'] != 'response_item':
            continue
        if payload.get('type') not in responses:
            raise RuntimeError('Unknown root-session response subtype')
        if payload.get('type') != 'message':
            continue
        if payload.get('role') not in ('assistant', 'user', 'developer', 'system', 'tool') or not isinstance(payload.get('content'), list):
            raise RuntimeError('Malformed root-session message')
        if payload['role'] != 'assistant':
            continue
        assistant_messages += 1
        if payload.get('phase') == 'final_answer' or payload.get('channel') == 'final' or marker.search(json.dumps(payload['content'])):
            raise RuntimeError('Persisted original root final delivery exists')
        if payload.get('phase') != 'commentary' or payload.get('channel') not in (None, 'commentary'):
            raise RuntimeError('Unknown root assistant message phase; cannot prove no final')
    return {'session_id': sid, 'root_log': str(path), 'root_log_sha256': digest(path),
            'assistant_commentary_messages': assistant_messages, 'final_delivery_present': False}


def approve_user_policy(admin, key, policy_path, policy_sha256, evidence):
    data = manifest(admin)
    own = admin / 'groups' / key
    state = json.loads((own / 'state.json').read_text())
    policy = operation_policy(policy_path, policy_sha256)
    observations = state.get('observations', {})
    if (state.get('status') != 'paused' or state.get('needs_review')
            or state.get('process_tree_stopped') is not True or state.get('actual_model_verified') is not True
            or not state.get('session_id') or not state.get('attempts')
            ):
        raise RuntimeError('User-policy resume requires a stopped, model-verified paused original session with no final')
    final_proof = no_final_delivery_proof(own, state)
    model = data['groups'][key]['model']
    if (not observations.get('actual_models') or any(m != model for m in observations['actual_models'])
            or not observations.get('actual_efforts') or any(e != 'high' for e in observations['actual_efforts'])):
        raise RuntimeError('User-policy resume model/effort evidence mismatch')
    last = state['attempts'][-1]
    source = check_links(evidence)
    document = json.loads(source.read_text(encoding='utf-8'))
    binding = {'version': 1, 'group': key, 'attempt': last['number'], 'session_id': state['session_id'],
               'model': model, 'policy_sha256': policy_sha256, 'reviewed': True, 'final_absent': True,
               'decision': 'user_policy_resume_approved'}
    if (any(document.get(k) != v for k, v in binding.items())
            or type(document.get('version')) is not int or type(document.get('attempt')) is not int
            or document.get('reviewed') is not True or document.get('final_absent') is not True
            or not isinstance(document.get('reviewer'), str) or not document['reviewer'].strip()):
        raise RuntimeError('User-policy approval evidence must bind exact attempt/session/model/policy and no-final review')
    folder = own / 'user-policy' / f"approval-{last['number']}"
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, folder / 'evidence.json')
    if json.loads((folder / 'evidence.json').read_text(encoding='utf-8')) != document:
        raise RuntimeError('User-policy approval evidence changed while copying')
    (folder / 'policy.txt').write_bytes(policy['text'].encode('utf-8'))
    review = {**binding, 'approved_utc': utc(), 'evidence_sha256': digest(folder / 'evidence.json'),
              'evidence_path': str(folder / 'evidence.json'), 'policy': {k: policy[k] for k in ('path', 'sha256')},
              'no_final_delivery_proof': final_proof,
              'effective_seconds_unchanged': state['effective_seconds']}
    state['user_policy_resume_approval'] = review
    state['attempts'][-1]['administrative_pause_reason'] = 'user_operation_policy_change'
    state['needs_infrastructure_review'] = False
    # A permissions change is not an outage: preserve every charged second.
    write_json(own / 'state.json', state)
    return state


def run_group(admin, key, *, resume=False, limit=None, command_override=None,
              policy_path=None, policy_sha256=None):
    data = manifest(admin)
    group = data['groups'][key]
    if command_override is None:
        verify_readiness(admin / 'frozen/readiness.json', {g['model'] for g in data['groups'].values()})
    own = admin / 'groups' / key
    state_path = own / 'state.json'
    state = json.loads(state_path.read_text())
    if state['status'] not in ({'paused'} if resume else {'ready'}):
        raise RuntimeError('Run is not eligible; never overwrite or restart a previous run')
    if resume and state.get('needs_review'):
        raise RuntimeError('Judge review required; no business continuation after final delivery')
    policy = operation_policy(policy_path, policy_sha256) if policy_path is not None or policy_sha256 is not None else None
    approval = state.get('user_policy_resume_approval', {})
    policy_resume = (resume and policy is not None and approval.get('attempt') == state['attempts'][-1]['number']
                     and approval.get('session_id') == state.get('session_id')
                     and approval.get('policy_sha256') == policy['sha256'])
    if resume and approval.get('attempt') == state['attempts'][-1]['number'] and not policy_resume:
        raise RuntimeError('User-policy adjudication requires the exact approved operation policy')
    if policy_resume and digest(check_links(Path(approval['evidence_path']))) != approval['evidence_sha256']:
        raise RuntimeError('User-policy approval evidence changed')
    if policy_resume and (state.get('process_tree_stopped') is not True or state.get('actual_model_verified') is not True
            ):
        raise RuntimeError('User-policy continuation no longer has stopped/no-final/model-verified evidence')
    if policy_resume and no_final_delivery_proof(own, state) != approval.get('no_final_delivery_proof'):
        raise RuntimeError('Original root-session no-final evidence changed after policy adjudication')
    if resume and state.get('operation_policy') and (policy is None or policy['sha256'] != state['operation_policy']['sha256']):
        raise RuntimeError('Original-session operation policy must be supplied unchanged')
    if resume and not policy_resume and state.get('infrastructure_approved_attempt') != state['attempts'][-1]['number']:
        raise RuntimeError('An evidence-backed infrastructure adjudication is required before --resume')
    if not resume and inventory(group['project']) != data['baselines'][key]['files']:
        raise RuntimeError('Initial source differs from frozen baseline')
    if digest(own / 'prompt.txt') != data['prompt_hashes'][key]:
        raise RuntimeError('Frozen group prompt changed')
    if resume and not state.get('session_id'):
        raise RuntimeError('No persisted session ID; automatic fresh-session retry is forbidden')
    attempt = len(state['attempts']) + 1
    prefix = own / f'attempt-{attempt:03}'
    final = prefix.with_suffix('.final.txt')
    command = command_override or cli_command(data['cli'], group, admin / 'frozen/unity_proxy.py', final,
                                              state.get('session_id') if resume else None)
    env = os.environ.copy()
    for name in list(env):
        if any(x in name for x in ['OPENAI_API_KEY', 'AZURE_OPENAI', 'CODEX_CONFIG', 'CODEX_MODEL']):
            env.pop(name)
    env['CODEX_HOME'] = str(own / 'home')
    env['B1_GROUP'] = key
    started = time.monotonic()
    state.update(status='running', pid=None, process_tree_stopped=False)
    state['requires_terminal_drain'] = command_override is None
    state['attempts'].append({'started_utc': utc(), 'number': attempt})
    if policy:
        folder = own / 'user-policy' / f'input-{attempt}'
        folder.mkdir(parents=True, exist_ok=False)
        (folder / 'policy.txt').write_bytes(policy['text'].encode('utf-8'))
        state['operation_policy'] = {k: policy[k] for k in ('path', 'sha256')}
        state['attempts'][-1]['operation_policy_sha256'] = policy['sha256']
    if resume:
        state['attempts'][-1]['resume_basis'] = 'user_policy_resume_approval' if policy_resume else 'infrastructure_approved_attempt'
    write_json(state_path, state)
    observed = {}
    out_queue = queue.Queue()
    try:
        proc = subprocess.Popen(command, cwd=group['project'], env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=os.name != 'nt')
        tree = ProcessTree(proc)
    except (OSError, RuntimeError) as exc:
        state.update(status='paused', process_tree_stopped=True, startup_error=str(exc))
        write_json(state_path, state)
        raise
    state['pid'] = proc.pid
    write_json(state_path, state)
    def read():
        for line in iter(proc.stdout.readline, b''):
            out_queue.put(line)
        out_queue.put(None)
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    prompt = (('用户操作权限约束已更新。继续原会话的原任务，保留成果和已用时间。' if policy_resume else
               '基础设施已恢复。继续原任务，不增加需求，不重置成果。') if resume else
              (own / 'prompt.txt').read_text(encoding='utf-8'))
    if policy:
        prompt += '\n\n' + policy['text']
    proc.stdin.write(prompt.encode('utf-8'))
    proc.stdin.close()
    reason = None
    budget = data['limit_seconds'] if limit is None else limit
    try:
        with prefix.with_suffix('.jsonl').open('wb') as log:
            while True:
                elapsed = state['effective_seconds'] + time.monotonic() - started
                if (own / 'STOP').exists():
                    reason = 'stopped'
                    break
                if elapsed >= budget:
                    reason = 'timeout'
                    break
                try:
                    line = out_queue.get(timeout=.1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                log.write(line)
                log.flush()
                try:
                    parse_event(json.loads(line), observed)
                except (ValueError, TypeError):
                    pass
    except KeyboardInterrupt:
        reason = 'stopped'
    finally:
        tree.stop()
        thread.join(timeout=5)
        proc.stdout.close()
        state['effective_seconds'] += time.monotonic() - started
        state['attempts'][-1].update(ended_utc=utc(), wall_seconds=time.monotonic() - started)
        state['process_tree_stopped'] = True
        state['pid'] = None
    if final.exists():
        observed['final_text'] = final.read_text(encoding='utf-8')
        observed['turn_completed'] = True
    rollout_metadata(own / 'home', observed)
    actual = observed.get('actual_models', [])
    if any(m != group['model'] for m in actual):
        reason = 'invalid'
    if any(e != 'high' for e in observed.get('actual_efforts', [])):
        reason = 'invalid'
    if reason is None:
        reason = 'submitted' if observed.get('final_text', '').strip() and observed.get('turn_completed') else 'paused'
        if reason == 'submitted' and 'B1_FINAL_STATUS: abandoned' in observed['final_text']:
            reason = 'abandoned'
    state.update(status=reason, session_id=observed.get('session_id', state.get('session_id')),
                 observations=observed, actual_model_verified=bool(actual), fees=None,
                 ended_utc=utc(), exit_code=proc.returncode)
    if reason == 'paused':
        state['needs_infrastructure_review'] = True
    if not actual and reason in {'submitted', 'abandoned'}:
        # No claim of validity without model evidence. Requires judge inspection.
        state['status'] = 'paused'
        state['needs_review'] = 'Final response exists but actual model metadata is unavailable; do not resume business work.'
    write_json(state_path, state)
    if state['status'] in TERMINAL:
        try:
            seal(admin, key)
            state = json.loads(state_path.read_text())
        except (RuntimeError, OSError) as exc:
            state = json.loads(state_path.read_text())
            state.update(needs_seal_review=True, seal_error=str(exc))
            write_json(state_path, state)
    return state

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin', type=Path, default=DEFAULT_ADMIN)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ['prepare', 'preflight']:
        p = sub.add_parser(action)
        p.add_argument('--cli', default=shutil.which('codex') or 'codex')
        p.add_argument('--auth-home', type=Path, default=Path.home() / '.codex')
        if action == 'preflight':
            p.add_argument('--live-probes', type=Path, metavar='NEW_OUTPUT_DIRECTORY')
        if action == 'prepare':
            p.add_argument('--skill', type=Path, required=True)
            p.add_argument('--unity-skill', type=Path, required=True)
            p.add_argument('--proxy', type=Path, default=Path(__file__).with_name('unity_proxy.py'))
            p.add_argument('--readiness', type=Path, required=True)
    sub.add_parser('status')
    p = sub.add_parser('run')
    p.add_argument('--one', choices=['Demo', 'Demo1', 'Demo2', 'Demo3', 'Demo4', 'Demo5'])
    p.add_argument('--resume', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--operation-policy', type=Path)
    p.add_argument('--policy-sha256')
    for action in ['stop', 'seal']:
        p = sub.add_parser(action)
        p.add_argument('group')
    p = sub.add_parser('infra-review')
    p.add_argument('group')
    p.add_argument('--outage-seconds', type=float, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p = sub.add_parser('policy-review')
    p.add_argument('group')
    p.add_argument('--operation-policy', type=Path, required=True)
    p.add_argument('--policy-sha256', required=True)
    p.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        result = prepare(args.admin, args.cli, args.auth_home, args.skill, args.unity_skill, args.proxy, args.readiness)
    elif args.action == 'preflight':
        groups = normalize_groups(load_spec())
        result = preflight(args.cli, args.auth_home, {g['model'] for g in groups.values()})
        if args.live_probes:
            result['live_probes'] = live_probes(args.cli, args.auth_home, {g['model'] for g in groups.values()}, args.live_probes)
    else:
        data = manifest(args.admin)
        if args.action == 'status':
            result = {k: json.loads((args.admin / 'groups' / k / 'state.json').read_text()) for k in data['order']}
        elif args.action == 'stop':
            (args.admin / 'groups' / args.group / 'STOP').touch(exist_ok=False)
            result = {'stop_requested': args.group}
        elif args.action == 'seal':
            result = str(seal(args.admin, args.group))
        elif args.action == 'infra-review':
            result = approve_infrastructure(args.admin, args.group, args.outage_seconds, args.evidence)
        elif args.action == 'policy-review':
            result = approve_user_policy(args.admin, args.group, args.operation_policy, args.policy_sha256, args.evidence)
        else:
            with controller_lock(args.admin):
                result = {}
                for key in data['order']:
                    own = args.admin / 'groups' / key
                    state = json.loads((own / 'state.json').read_text())
                    if state['status'] in TERMINAL:
                        if not (args.admin / 'sealed' / key / 'seal.json').exists():
                            raise RuntimeError(f'{key} is terminal but unsealed; resolve seal review before next group')
                        continue
                    if args.one and key != args.one:
                        raise RuntimeError(f'Fixed order requires {key} before {args.one}')
                    if state['status'] == 'paused' and not args.resume:
                        raise RuntimeError(f'{key} paused; infrastructure must be reviewed before --resume')
                    if args.dry_run:
                        result[key] = cli_command(data['cli'], data['groups'][key], args.admin / 'frozen/unity_proxy.py', own / 'preview.final.txt')
                        if args.one:
                            break
                        continue
                    result[key] = run_group(args.admin, key, resume=args.resume,
                                            policy_path=args.operation_policy, policy_sha256=args.policy_sha256)
                    if result[key].get('needs_seal_review') or result[key]['status'] in {'paused', 'invalid', 'stopped'} or args.one:
                        break
                    args.resume = False
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
