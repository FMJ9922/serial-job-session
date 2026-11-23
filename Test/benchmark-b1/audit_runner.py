"""Sequential blind B1 audits. Does not start until all six deliveries are sealed.

The controller directory is private judge data; auditors may read only their
anonymous blind directory and their own isolated skill home. No game repairs.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import types
import uuid

try:
    from . import audit, runner, spec
except ImportError:
    import audit
    import runner
    import spec

MODEL = 'gpt-6.1-sol'
EFFORT = 'high'
MAX_CORRECTIONS = 3
AA_PREFLIGHT_SOURCES = {
    'Editor/Settings/ProjectConfigData.cs': '0b7fab0a602f1d6d6c423d744cfbae3e36f9b2ca955adbcafd53198b9c08f761',
    'Editor/Build/DataBuilders/BuildScriptPackedMode.cs': 'eb71fcece8ccfb0b39e021059f38fef4f804ddf28a7b430493d1b617617a775a',
}
AA_PREFLIGHT_119_SOURCES = {
    'Editor/Settings/ProjectConfigData.cs': '76f58cc4db7fd4d0eb01bd8ae86b2521220e1ee9b14523d00d078dfe6b71edc2',
    'Editor/Build/DataBuilders/BuildScriptPackedMode.cs': '39c24e19121f4669c64c3c2dd5b14826112aeeb77986725d160a51f7663f00c2',
}

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def save(path, value):
    runner.write_json(path, value)

def all_sealed(admin):
    manifest = runner.manifest(admin)
    for group in manifest['groups']:
        entry = admin / 'sealed' / group
        if not (entry / 'seal.json').is_file():
            raise RuntimeError('All six development groups must be sealed before any audit starts')
        seal = read(entry / 'seal.json')
        state = read(entry / 'state.json')
        if state['status'] not in runner.TERMINAL or not state.get('process_tree_stopped'):
            raise RuntimeError(f'Nonterminal delivery: {group}')
        # All six formal editors must have quiescence evidence before a new
        # anonymous editor is introduced; an absent marker is not evidence.
        if state.get('requires_terminal_drain') is not True or state.get('terminal_drain', {}).get('passed') is not True:
            raise RuntimeError(f'Unity terminal drain was not confirmed: {group}')
        if runner.inventory(entry / 'project') != seal['source_files']:
            raise RuntimeError(f'Sealed delivery changed: {group}')
    return manifest

@contextmanager
def lock(root):
    path = root / 'AUDIT_CONTROLLER.lock'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink()

def initialize(admin, root, editor, auth_home, *, operational=True):
    manifest = all_sealed(admin)
    if root.exists():
        raise RuntimeError('Refusing to overwrite existing audit controller')
    if not Path(editor).is_file():
        raise RuntimeError('Explicit Unity 2022.3.62f2 executable required')
    root.mkdir(parents=True)
    groups = {}
    for group in manifest['order']:
        opaque = uuid.uuid4().hex
        out = root / 'cases' / opaque
        primary_id = uuid.uuid4().hex
        judge = audit.init_audit(admin / 'sealed' / group, out, opaque, operational=operational, primary_id=primary_id)
        terminal = read(admin / 'sealed' / group / 'state.json')
        judge['metrics'].update(time_seconds=terminal.get('effective_seconds'), cost=terminal.get('fees'),
                                subagents=terminal.get('observations', {}).get('child_session_count'))
        # Raw usage is retained in delivery logs. Unknown aggregated tokens stay null.
        save(out / 'judge_manifest.json', judge)
        groups[group] = {'opaque_id': opaque, 'status': 'ready', 'roles': {
            'primary': {'attempts': 0, 'session_id': None, 'auditor_id': primary_id}}, 'editor_launched': False}
    controller = {'version': 'B1', 'created_utc': runner.utc(), 'admin': str(admin.resolve()),
                  'editor': str(Path(editor).resolve()), 'cli': manifest['cli'],
                  'auth_home': str(Path(auth_home).resolve()), 'groups': groups,
                  'order': list(manifest['order']), 'frozen_manifest_sha256': runner.digest(admin / 'manifest.json')}
    save(root / 'controller.json', controller)
    return controller

def load_controller(root):
    control = read(root / 'controller.json')
    admin = Path(control['admin'])
    all_sealed(admin)
    if runner.digest(admin / 'manifest.json') != control['frozen_manifest_sha256']:
        raise RuntimeError('Development manifest changed since audit initialization')
    return control

def load_proxy(admin):
    # Compiling avoids generating __pycache__ in the immutable frozen directory.
    path = admin / 'frozen' / 'unity_proxy.py'
    module = types.ModuleType('b1_frozen_proxy')
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    return module


def derived_workspace(out):
    return read(out / 'blind/manifest.json').get('workspace_mode') == 'derived_operations_v1'


def runtime_project(out):
    manifest = read(out / 'blind/manifest.json')
    name = manifest.get('runtime', 'runtime') if derived_workspace(out) else 'candidate'
    if name != 'runtime' and derived_workspace(out):
        raise RuntimeError('Unknown derived runtime path')
    return out / 'blind' / name


def provenance_api():
    if __package__:
        from . import operation_provenance
    else:
        import operation_provenance
    return operation_provenance


def provenance_args(out):
    manifest = read(out / 'blind/manifest.json')
    return (out / 'blind/candidate', runtime_project(out), out / 'blind/evidence', manifest['operational_provenance'])


def begin_controller_operation(out, kind, operation_id, command):
    if not derived_workspace(out):
        return None
    manifest = read(out / 'blind/manifest.json')
    entries = ('McpCiBoot.cs',) if kind == 'editor_startup' else ('ManageEditor.cs', 'McpCiBoot.cs')
    source_refs = [{'path': path, 'sha256': sha} for path, sha in manifest['audit_baseline'].items()
                   if any(path.endswith('/' + entry) for entry in entries)]
    if not source_refs:
        raise RuntimeError('No original MCP bootstrap source evidence for controller operation')
    provenance = {'actor_id': 'audit-controller', 'invocation': {'kind': 'controller',
        'command': json.dumps(command), 'refs': [operation_id]}, 'source_refs': source_refs, 'evidence_refs': []}
    provenance_api().begin_operation(*provenance_args(out), kind, operation_id, provenance)
    return provenance


def finish_controller_operation(out, operation_id, provenance, result):
    if provenance is None:
        return
    evidence = out / 'blind/evidence'
    path = evidence / 'tool-traces/controller' / operation_id / 'result.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError('Refusing to overwrite controller operation result')
    save(path, {'utc': runner.utc(), 'operation_id': operation_id, 'provenance': provenance, 'result': result,
                'controller_source_sha256': audit.sha256(Path(__file__))})
    refs = [{'path': path.relative_to(evidence).as_posix(), 'sha256': audit.sha256(path)}]
    if isinstance(result, dict):
        refs += result.get('startup_guard_evidence', [])
        refs += result.get('addressables_preflight', {}).get('evidence_refs', [])
    completed = {**provenance, 'evidence_refs': refs}
    provenance_api().finish_operation(*provenance_args(out), operation_id, completed)


def trace_credentials(private):
    credentials = []
    auth = private / 'home/auth.json'
    if auth.is_file():
        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, str) and len(item) >= 16 and any(k in key.casefold() for k in ('token', 'key', 'secret', 'password')):
                        credentials.append(item.encode('utf-8'))
                    else:
                        collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(read(auth))
    return credentials


def trace_credential_proof(data, name, credentials=()):
    """Never rewrite bytes. Only native opaque reasoning ciphertext has spans."""
    if any(secret in data for secret in credentials):
        raise RuntimeError('Potential credential in anonymous tool trace: actual credential match')
    spans, exceptions = [], []
    if name.startswith('home/sessions/') and name.endswith('.jsonl'):
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        def reject_constant(value):
            raise ValueError('Nonstandard JSON constant')
        offset = 0
        for line_number, line in enumerate(data.splitlines(keepends=True), 1):
            if not line.strip():
                offset += len(line)
                continue
            try:
                event = json.loads(line.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
            except (ValueError, UnicodeError) as exc:
                raise RuntimeError('Malformed or ambiguous native rollout credential-scan evidence') from exc
            payload = event.get('payload') if isinstance(event, dict) else None
            blob = payload.get('encrypted_content') if isinstance(payload, dict) else None
            if (isinstance(event, dict) and event.get('type') == 'response_item'
                    and isinstance(payload, dict) and payload.get('type') == 'reasoning'
                    and isinstance(blob, str) and len(blob) >= 512
                    and re.fullmatch(r'gAAAAA[A-Za-z0-9_-]*={0,2}', blob)):
                fields = list(re.finditer(rb'"encrypted_content"\s*:\s*"([^"\\]*)"', line))
                if len(fields) != 1 or fields[0].group(1) != blob.encode('ascii'):
                    raise RuntimeError('Ambiguous opaque reasoning field byte span')
                start, end = fields[0].span(1)
                spans.append((offset + start, offset + end))
                exceptions.append({'line': line_number, 'field': 'payload.encrypted_content',
                    'start_byte': offset + start, 'end_byte': offset + end, 'length': end - start,
                    'sha256': hashlib.sha256(fields[0].group(1)).hexdigest(),
                    'reason': 'native reasoning gAAAAA long base64url opaque ciphertext; original bytes retained'})
            offset += len(line)
    patterns = (rb'\bsk-[A-Za-z0-9_-]{20,}',
                rb'(?i)authorization["\s:]+bearer\s+[A-Za-z0-9_.-]{20,}',
                rb'(?i)["\x27](?:access_token|refresh_token|id_token|api_key)["\x27]\s*:\s*["\x27][^"\x27\s]{16,}')
    ignored = []
    used_spans = set()
    for pattern_index, pattern in enumerate(patterns):
        for match in re.finditer(pattern, data):
            containing = [(start, end) for start, end in spans if start <= match.start() and match.end() <= end]
            if not containing:
                raise RuntimeError('Potential credential in anonymous tool trace; preserve private original and block export')
            used_spans.update(containing)
            ignored.append({'pattern_index': pattern_index, 'start_byte': match.start(), 'end_byte': match.end(),
                            'length': match.end() - match.start(), 'sha256': hashlib.sha256(match.group()).hexdigest()})
    return {'policy': 'native-opaque-reasoning-v1', 'actual_credentials_checked': True,
            'original_bytes_retained': True,
            'opaque_fields': [field for field in exceptions if (field['start_byte'], field['end_byte']) in used_spans],
            'ignored_generic_matches': ignored}


def export_tool_traces(out, role, state, prefix):
    """Copy only this anonymous auditor's actual traces, never auth/config/env."""
    private = out / 'private' / role
    files = [prefix.with_suffix('.jsonl')]
    files += sorted((private / 'home/sessions').rglob('*.jsonl'))
    files += [p for p in (private / 'unity.jsonl', private / 'unity.native.stderr.log') if p.is_file()]
    credentials = trace_credentials(private)
    payloads = []
    for path in files:
        data = runner.check_links(path).read_bytes()
        relative = path.relative_to(private)
        proof = trace_credential_proof(data, relative.as_posix(), credentials)
        payloads.append((relative, data, proof))
    destination = out / 'blind/evidence/tool-traces' / role / str(state['attempts'])
    destination.mkdir(parents=True, exist_ok=False)
    source_snapshot = private / 'trace-snapshots' / str(state['attempts'])
    source_snapshot.mkdir(parents=True, exist_ok=False)
    entries = []
    for relative, data, proof in payloads:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        snapshot = source_snapshot / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
        source_sha = hashlib.sha256(data).hexdigest()
        if audit.sha256(target) != source_sha or audit.sha256(snapshot) != source_sha or audit.sha256(private / relative) != source_sha:
            raise RuntimeError('Anonymous tool-trace copy hash mismatch')
        entries.append({'source_file': relative.as_posix(), 'source_snapshot_file': snapshot.relative_to(private).as_posix(),
                        'exported_file': relative.as_posix(),
                        'source_sha256': source_sha, 'exported_sha256': audit.sha256(target), 'redacted': False,
                        'credential_scan': proof})
    manifest = {'role': role, 'auditor_id': state['auditor_id'], 'session_id': state.get('session_id'),
                'turn': state['attempts'], 'files': entries,
                'scope': 'This anonymous auditor and descendants actual traces only; no auth/config/environment originals, development logs, allocation map, or OS isolation claim.'}
    save(destination / 'manifest.json', manifest)
    return manifest


def verify_exported_tool_traces(out, case):
    if not derived_workspace(out):
        return
    for role, state in actual_participants(case).items():
        if role not in ('primary', 'review', 'provenance', 'technical_adjudication'):
            raise RuntimeError('Unknown tool-trace actor role')
        manifests = state.get('exported_tool_trace_manifests', [])
        credentials = trace_credentials(out / 'private' / role)
        if len(manifests) != state['attempts'] or [m.get('turn') for m in manifests] != list(range(1, state['attempts'] + 1)):
            raise RuntimeError('Missing controller-anchored actual tool traces for an auditor turn')
        for manifest in manifests:
            turn = manifest.get('turn')
            if (type(turn) is not int or not 1 <= turn <= state['attempts']
                    or manifest.get('role') != role or manifest.get('auditor_id') != state['auditor_id']
                    or manifest.get('session_id') != state.get('session_id')):
                raise RuntimeError('Controller tool-trace identity binding changed')
            destination = out / 'blind/evidence/tool-traces' / role / str(turn)
            if read(runner.check_links(destination / 'manifest.json')) != manifest:
                raise RuntimeError('Exported tool-trace manifest differs from private controller anchor')
            seen = set()
            for item in manifest['files']:
                name = item.get('source_file', '')
                safe_parts = name.split('/')
                allowed = (name == f'turn-{turn}.jsonl' or name in ('unity.jsonl', 'unity.native.stderr.log')
                           or name.startswith('home/sessions/') and name.endswith('.jsonl'))
                if (not allowed or any(part in ('', '.', '..') or ':' in part or '\\' in part for part in safe_parts)
                        or name in seen or item.get('exported_file') != name or item.get('redacted') is not False
                        or item.get('source_snapshot_file') != f'trace-snapshots/{turn}/{name}'
                        or item.get('source_sha256') != item.get('exported_sha256')):
                    raise RuntimeError('Tool-trace file scope or original-byte claim invalid')
                seen.add(name)
                original = out / 'private' / role / item['source_snapshot_file']
                if (audit.sha256(runner.check_links(original)) != item['source_sha256']
                        or audit.sha256(runner.check_links(destination / name)) != item['source_sha256']):
                    raise RuntimeError('Tool-trace source snapshot or exported bytes changed')
                proof = trace_credential_proof(original.read_bytes(), name, credentials)
                if item.get('credential_scan') is not None and item['credential_scan'] != proof:
                    raise RuntimeError('Controller credential-scan proof differs from original trace bytes')
                if item.get('credential_scan') is None and proof['opaque_fields']:
                    raise RuntimeError('Opaque trace exception lacks controller scan proof')

def editor_launch_options(platform=None):
    if (os.name if platform is None else platform) != 'nt':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {'startupinfo': startup}


def save_startup_guard_attempt(out, operation_id, attempt, request, response=None, error=None):
    evidence = out / 'blind/evidence'
    path = evidence / 'tool-traces/controller' / operation_id / f'startup-guard-attempt-{attempt}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError('Refusing to overwrite actual startup guard response')
    save(path, {'utc': runner.utc(), 'operation_id': operation_id, 'attempt': attempt,
                'request': request, 'decoded_response': response, 'response_available': error is None,
                'transport_error': None if error is None else {'type': type(error).__name__, 'message': str(error)}})
    return {'path': path.relative_to(evidence).as_posix(), 'sha256': audit.sha256(path)}


def startup_guard_verified(response):
    if not isinstance(response, dict) or response.get('status') != 'success':
        return False
    result = response.get('result')
    if not isinstance(result, dict) or result.get('success') is not True:
        return False
    data = result.get('data')
    return isinstance(data, dict) and data.get('result') is True


def addressables_preflight(out, proxy, live, operation_id):
    """Original package's No-equivalent notification preference, never a build."""
    project = runtime_project(out)
    packages = list((project / 'Library/PackageCache').glob('com.unity.addressables@*'))
    embedded = project / 'Packages/com.unity.addressables'
    if embedded.exists():
        packages.append(embedded)
    declared = read(project / 'Packages/manifest.json').get('dependencies', {}).get('com.unity.addressables')
    folder = out / 'blind/evidence/tool-traces/controller' / operation_id / 'addressables-preflight'
    folder.mkdir(parents=True, exist_ok=False)
    refs = []
    def record(name, data):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        ref = {'path': path.relative_to(out / 'blind/evidence').as_posix(), 'sha256': audit.sha256(path)}
        refs.append(ref)
        return ref
    def json_record(name, value):
        return record(name, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
    if not packages and not declared:
        json_record('result.json', {'status': 'skipped', 'reason': 'Addressables package absent'})
        return {'status': 'skipped', 'evidence_refs': refs}
    if len(packages) != 1:
        raise RuntimeError('Addressables preflight package/source entry shape not verified')
    package_json = runner.check_links(packages[0] / 'package.json')
    identity = read(package_json)
    version = identity.get('version')
    approved = {'1.21.21': AA_PREFLIGHT_SOURCES, '1.21.19': AA_PREFLIGHT_119_SOURCES}
    if (identity.get('name') != 'com.unity.addressables' or version not in approved
            or packages[0] != embedded and packages[0].name != 'com.unity.addressables@' + version):
        raise RuntimeError('Addressables package name/version/source directory not verified')
    record('original-package/package.json', package_json.read_bytes())
    sources = []
    for relative, expected in approved[version].items():
        path = runner.check_links(packages[0] / relative)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('Addressables preflight original source differs from verified entry')
        ref = record('original-package/' + relative, data)
        sources.append({'runtime_source': path.relative_to(project).as_posix(), 'sha256': expected, 'archive': ref})
    config = runner.check_links(project / 'Library/AddressablesConfig.dat')
    def snapshot(stage):
        exists = config.is_file()
        blob = record(stage + '/AddressablesConfig.dat', config.read_bytes()) if exists else None
        return {'exists': exists, 'bytes': blob}
    before = snapshot('before')
    # Only this exact assembly/type/property set is allowed. The original
    # internal setter calls SaveData itself. Never invoke a report-option setter.
    expected_package_path = str(packages[0].resolve())
    code = '''var a = System.Array.Find(System.AppDomain.CurrentDomain.GetAssemblies(), x => x.GetName().Name == "Unity.Addressables.Editor");
if (a == null) throw new System.Exception("Addressables Editor assembly unavailable");
var p = UnityEditor.PackageManager.PackageInfo.FindForAssembly(a);
var package = p == null ? null : new { name = p.name, version = p.version, resolvedPath = p.resolvedPath };
if (p == null || p.name != "com.unity.addressables" || p.version != EXPECTED_PACKAGE_VERSION || !System.String.Equals(System.IO.Path.GetFullPath(p.resolvedPath).TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar), System.IO.Path.GetFullPath(EXPECTED_PACKAGE_PATH).TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar), System.StringComparison.OrdinalIgnoreCase)) return new { blocked = true, assembly = a.GetName().Name, package = package };
var t = a.GetType("UnityEditor.AddressableAssets.Settings.ProjectConfigData", true);
var f = System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
var g = t.GetProperty("GenerateBuildLayout", f);
var n = t.GetProperty("UserHasBeenInformedAboutBuildReportSettingPreBuild", f);
if (g == null || n == null || g.PropertyType != typeof(bool) || n.PropertyType != typeof(bool) || n.GetSetMethod(true) == null) throw new System.Exception("Unexpected Addressables preference entry");
bool gb = (bool)g.GetValue(null, null), nb = (bool)n.GetValue(null, null);
if (!nb) n.SetValue(null, true, null);
bool ga = (bool)g.GetValue(null, null), na = (bool)n.GetValue(null, null);
return new { generate_before = gb, generate_after = ga, notified_before = nb, notified_after = na, setter_called = !nb, assembly = a.GetName().Name, package = package };'''.replace('EXPECTED_PACKAGE_PATH', json.dumps(expected_package_path)).replace('EXPECTED_PACKAGE_VERSION', json.dumps(version))
    request = {'type': 'execute_code', 'params': {'action': 'execute', 'compiler': 'codedom', 'code': code}}
    json_record('request.json', request)
    response, error = None, None
    try:
        response = proxy.bridge_request(live['unity_port'], request)
    except Exception as exc:
        error = {'type': type(exc).__name__, 'message': str(exc)}
    after = snapshot('after')
    json_record('actual-response.json', {'response': response, 'transport_error': error})
    result = response.get('result', {}) if isinstance(response, dict) else {}
    data = result.get('data', {}) if isinstance(result, dict) else {}
    value = data.get('result') if isinstance(data, dict) else None
    package = value.get('package') if isinstance(value, dict) else None
    package_verified = (isinstance(package, dict) and value.get('assembly') == 'Unity.Addressables.Editor'
                        and package.get('name') == 'com.unity.addressables' and package.get('version') == version
                        and isinstance(package.get('resolvedPath'), str)
                        and os.path.normcase(os.path.abspath(package['resolvedPath'])) == os.path.normcase(expected_package_path))
    valid = (error is None and isinstance(response, dict) and response.get('status') == 'success'
             and isinstance(result, dict) and result.get('success') is True
             and package_verified and isinstance(value, dict) and all(type(value.get(k)) is bool for k in
                 ('generate_before', 'generate_after', 'notified_before', 'notified_after', 'setter_called'))
             and value['generate_before'] == value['generate_after'] and value['notified_after'] is True
             and value['setter_called'] is (not value['notified_before']))
    if valid and value['setter_called'] and not after['exists']:
        valid = False
    if valid and value['notified_before'] and before != after:
        # Archive references differ by path; compare actual existence and bytes.
        valid = (before['exists'] == after['exists'] and
                 (not before['exists'] or before['bytes']['sha256'] == after['bytes']['sha256']))
    json_record('result.json', {'verified': valid, 'before': before, 'after': after,
                               'original_package_sources': sources, 'actual_values': value,
                               'scope': 'Library preference outside Assets/Packages/ProjectSettings journal; no business build/settings changes permitted'})
    if not valid:
        raise RuntimeError('Addressables notification preflight did not verify unchanged report preference')
    return {'status': 'verified', 'evidence_refs': refs}


def ensure_editor(control, case, out, persist, timeout=300):
    project = runtime_project(out)
    version = (project / 'ProjectSettings/ProjectVersion.txt').read_text(encoding='utf-8')
    if 'm_EditorVersion: 2022.3.62f2' not in version:
        raise RuntimeError('Anonymous project uses wrong Unity version')
    proxy = load_proxy(Path(control['admin']))
    try:
        live = proxy.discover(project)
        if derived_workspace(out) and (out / 'blind/evidence/provenance/active-operation.json').exists():
            raise RuntimeError('Existing audit operation must finish before reused-editor preflight')
        operation_id = 'editor-preflight-' + uuid.uuid4().hex
        provenance = begin_controller_operation(out, 'startup', operation_id, ['reuse-existing-editor', live['id']])
        preference = addressables_preflight(out, proxy, live, operation_id)
        finish_controller_operation(out, operation_id, provenance, {'identity': live, 'addressables_preflight': preference})
        case['editor_identity'] = live['id']
        persist()
        return live
    except (ConnectionError, OSError):
        pass
    if case.get('editor_launched'):
        raise RuntimeError('Audit editor unavailable; judge must resolve infrastructure, no automatic repeated restart')
    command = [control['editor'], '-projectPath', str(project.resolve()), '-logFile',
               str((out / 'editor.log').resolve()), '-job-worker-count', '2', '-executeMethod',
               'MCPForUnity.Editor.McpCiBoot.StartStdioForCi']
    operation_id = 'editor-startup-' + uuid.uuid4().hex
    provenance = begin_controller_operation(out, 'editor_startup', operation_id, command)
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **editor_launch_options())
    case.update(editor_launched=True, initial_editor_pid=proc.pid)
    persist()
    deadline = time.monotonic() + timeout
    last = None
    guard_attempts = []
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError('Initial audit editor exited; inspect editor log without modifying game')
        try:
            live = proxy.discover(project)
            # SessionState is ephemeral editor infrastructure, never a business asset.
            guard_request = {'type': 'execute_code', 'params': {'action': 'execute', 'compiler': 'codedom',
                'code': 'UnityEditor.SessionState.SetBool("MCPForUnity.StartupConfigRewrite.Ran", true); return UnityEditor.SessionState.GetBool("MCPForUnity.StartupConfigRewrite.Ran", false);'}}
            try:
                startup_guard = proxy.bridge_request(live['unity_port'], guard_request)
            except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
                guard_attempts.append(save_startup_guard_attempt(out, operation_id, len(guard_attempts) + 1,
                                                                 guard_request, error=exc))
                raise
            guard_attempts.append(save_startup_guard_attempt(out, operation_id, len(guard_attempts) + 1,
                                                             guard_request, response=startup_guard))
            if not startup_guard_verified(startup_guard):
                raise RuntimeError('Initial audit startup guard execution did not verify its actual SessionState value')
            preference = addressables_preflight(out, proxy, live, operation_id)
            case['editor_identity'] = live['id']
            finish_controller_operation(out, operation_id, provenance, {'command': command, 'pid': proc.pid,
                'identity': live, 'startup_guard_result': startup_guard, 'startup_guard_evidence': guard_attempts,
                'addressables_preflight': preference})
            persist()
            return live
        except (ConnectionError, OSError) as exc:
            last = str(exc)
            time.sleep(1)
    raise TimeoutError(f'Initial audit editor bridge unavailable: {last}')

def setup_home(home, auth_home, unity_skill):
    if home.exists():
        return
    home.mkdir(parents=True)
    (home / 'skills').mkdir()
    runner.copy_tree(unity_skill, home / 'skills/unity-mcp-skill')
    (home / 'role.toml').write_text('model = "gpt-6.1-sol"\nmodel_reasoning_effort = "high"\n', encoding='utf-8')
    for name in ('auth.json', 'models_cache.json'):
        source = Path(auth_home) / name
        if source.exists():
            shutil.copy2(source, home / name)
            os.chmod(home / name, 0o600)

def audit_command(cli, home, project, proxy, log, final, session=None):
    command = [cli, '--no-daemon', 'exec']
    if session:
        command += ['resume', session]
    command += ['--ignore-user-config', '--ignore-rules', '--strict-config', '--json', '--model', MODEL,
                '--dangerously-bypass-approvals-and-sandbox', '--output-last-message', str(final)]
    if not session:
        command += ['--skip-git-repo-check', '--cd', str(project)]
    config = {'model_reasoning_effort': EFFORT, 'features.multi_agent': True,
              'agents.max_concurrent_threads_per_session': 2, 'agents.max_depth': 20,
              'agents.default_subagent_reasoning_effort': EFFORT,
              'mcp_servers.unity.command': sys.executable,
              'mcp_servers.unity.args': [str(proxy), '--project', str(project), '--log', str(log)]}
    for role in ('default', 'worker', 'explorer'):
        config[f'agents.{role}.config_file'] = str(home / 'role.toml')
    for key, value in config.items():
        command += ['-c', key + '=' + json.dumps(value)]
    return command + ['-']

def model_metadata(home):
    # A full-history child starts with its OWN metadata, followed by copied
    # ancestor metadata. Later metadata must never change the file's identity.
    nodes = {}
    for path in sorted((home / 'sessions').rglob('*.jsonl')):
        try:
            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError('Malformed audit rollout; model evidence is unavailable') from exc
        if not rows or not isinstance(rows[0], dict) or rows[0].get('type') != 'session_meta':
            raise RuntimeError('Audit rollout lacks first-session identity anchor')
        meta = rows[0].get('payload')
        if not isinstance(meta, dict):
            raise RuntimeError('Malformed first audit session metadata')
        sid = meta.get('id', meta.get('session_id'))
        if not isinstance(sid, str) or not sid:
            raise RuntimeError('Audit rollout identity anchor missing')
        source = meta.get('source')
        if source is not None and not isinstance(source, (str, dict)):
            raise RuntimeError('Malformed audit session source')
        if isinstance(source, dict) and (not isinstance(source.get('subagent', {}), dict)
                or not isinstance(source.get('subagent', {}).get('thread_spawn', {}), dict)):
            raise RuntimeError('Malformed audit subagent source')
        spawned = source.get('subagent', {}).get('thread_spawn', {}) if isinstance(source, dict) else {}
        parent = meta.get('parent_thread_id', spawned.get('parent_thread_id'))
        if spawned.get('parent_thread_id') and parent != spawned['parent_thread_id']:
            raise RuntimeError('Conflicting audit parent identity')
        boundary = meta.get('subagent_history_start_ordinal')
        if boundary is not None and (type(boundary) is not int or boundary < 1 or not parent):
            raise RuntimeError('Invalid full-history execution boundary')
        node = nodes.setdefault(sid, {'parent': parent, 'boundary': boundary, 'contexts': [], 'metadata_ids': set()})
        if node['parent'] != parent or node['boundary'] != boundary:
            raise RuntimeError('Conflicting first-session audit metadata across rollouts')
        for event in rows:
            if not isinstance(event, dict) or not isinstance(event.get('payload', {}), dict):
                raise RuntimeError('Malformed audit rollout metadata envelope')
            payload = event.get('payload', {})
            if event.get('type') == 'session_meta':
                identity = payload.get('id', payload.get('session_id'))
                if not isinstance(identity, str) or not identity:
                    raise RuntimeError('Malformed inherited audit metadata identity')
                node['metadata_ids'].add(identity)
            if event.get('type') == 'turn_context':
                context = {'model': payload.get('model'), 'effort': payload.get('effort', payload.get('reasoning_effort'))}
                if context != {'model': MODEL, 'effort': EFFORT}:
                    raise RuntimeError('Audit model or reasoning effort differs from required exact model/high')
                node['contexts'].append((context, payload.get('turn_id'), event.get('ordinal')))
    if not nodes:
        raise RuntimeError('Missing actual model/effort evidence for an audit session')
    result, own_ids, own_complete, visiting = {}, {}, {}, set()
    def resolve(sid):
        if sid in result:
            return
        if sid in visiting or sid not in nodes:
            raise RuntimeError('Missing/cyclic audit parent evidence')
        visiting.add(sid)
        node = nodes[sid]
        parent = node['parent']
        ancestors, ancestor_turns, ancestors_complete = set(), set(), True
        if parent:
            if not isinstance(parent, str) or parent == sid:
                raise RuntimeError('Invalid audit parent identity')
            resolve(parent)
            cursor = parent
            while cursor:
                ancestors.add(cursor)
                ancestor_turns.update(own_ids[cursor])
                ancestors_complete = ancestors_complete and own_complete[cursor]
                cursor = nodes[cursor]['parent']
        if node['metadata_ids'] - ({sid} | ancestors):
            raise RuntimeError('Unrelated inherited audit session identity')
        actual = []
        ids = set()
        complete_ids = True
        for context, turn, ordinal in node['contexts']:
            if not parent:
                owned = True
            elif node['boundary'] is not None:
                if type(ordinal) is not int or ordinal < 0 or not isinstance(turn, str) or not turn:
                    raise RuntimeError('Full-history child execution context lacks ordinal/turn identity')
                owned = ordinal >= node['boundary']
                if (owned and turn in ancestor_turns) or (not owned and turn not in ancestor_turns):
                    raise RuntimeError('Inherited context conflicts with child execution boundary')
            else:
                if not ancestor_turns or not ancestors_complete:
                    raise RuntimeError('Parent own turn identities unavailable for child attribution')
                if not isinstance(turn, str) or not turn:
                    raise RuntimeError('Child context cannot be separated from inherited parent evidence')
                owned = turn not in ancestor_turns
            if owned:
                actual.append(context)
                if isinstance(turn, str) and turn:
                    ids.add(turn)
                else:
                    complete_ids = False
        if not actual:
            raise RuntimeError('Missing actual model/effort evidence for an audit session: no own execution context')
        result[sid], own_ids[sid] = actual, ids
        own_complete[sid] = complete_ids
        visiting.remove(sid)
    for sid in nodes:
        resolve(sid)
    owners = {}
    for sid, ids in own_ids.items():
        for turn in ids:
            if turn in owners and owners[turn] != sid:
                raise RuntimeError('Conflicting own turn identity across audit sessions')
            owners[turn] = sid
    return result


def completed_turn_checkpoint(out, role, state):
    attempt = state.get('attempts', 0)
    if not attempt or not isinstance(state.get('session_id'), str) or not state['session_id']:
        return None
    prefix = out / 'private' / role / f'turn-{attempt}'
    native, final = prefix.with_suffix('.jsonl'), prefix.with_suffix('.final.txt')
    if not native.is_file() or not final.is_file():
        return None
    answer = final.read_bytes()
    if not answer.strip():
        return None
    identities, messages, terminal = set(), [], None
    try:
        events = list(runner.native_json_events(native, allow_diagnostics=True, with_line=True))
    except RuntimeError as exc:
        raise RuntimeError('Malformed completed-turn native evidence; never rerun primary to repair metadata') from exc
    for number, event in events:
        ordinal = number - 1
        if event.get('type') == 'thread.started':
            identities.add(event.get('thread_id'))
        if event.get('type') in ('turn.started', 'turn.completed', 'turn.failed'):
            terminal = (event['type'], ordinal)
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message':
            if not isinstance(item.get('text'), str):
                return None
            messages.append((item['text'].encode('utf-8'), ordinal))
    if (identities != {state.get('session_id')} or terminal is None or terminal[0] != 'turn.completed'
            or not messages or messages[-1][0] != answer or messages[-1][1] >= terminal[1]):
        return None
    native_sha = audit.sha256(native)
    if derived_workspace(out):
        # The live per-turn files are not the original evidence anchor. This
        # also applies when recovering a turn completed by an older controller
        # which failed before persisting its first checkpoint.
        manifests = state.get('exported_tool_trace_manifests', [])
        matches = [m for m in manifests if m.get('turn') == attempt]
        if (len(matches) != 1 or matches[0].get('role') != role
                or matches[0].get('auditor_id') != state.get('auditor_id')
                or matches[0].get('session_id') != state['session_id']):
            raise RuntimeError('Completed turn lacks its controller-anchored trace identity')
        entries = [item for item in matches[0].get('files', [])
                   if item.get('source_file') == f'turn-{attempt}.jsonl']
        if (len(entries) != 1 or entries[0].get('source_sha256') != native_sha
                or entries[0].get('exported_sha256') != native_sha):
            raise RuntimeError('Completed turn native bytes differ from controller-anchored original trace')
    return {'attempt': attempt, 'session_id': state['session_id'], 'native_sha256': native_sha,
            'final_sha256': audit.sha256(final), 'business_or_measurement_turn_reopened': False}


def recover_primary_postprocessing(case, out, persist):
    primary = case['roles']['primary']
    if not primary['attempts'] or case.get('phase') == 'primary_correction':
        return False
    checkpoint = completed_turn_checkpoint(out, 'primary', primary)
    if checkpoint is None:
        final = out / 'private/primary' / f'turn-{primary["attempts"]}.final.txt'
        if case.get('phase') == 'post_primary' or final.is_file() and final.read_text(encoding='utf-8').strip():
            raise RuntimeError('Post-primary final evidence unavailable; never open another primary for parser repair')
        return False
    prior = primary.get('completed_turn_checkpoint')
    if prior is not None and prior != checkpoint:
        raise RuntimeError('Completed primary final/native bytes changed')
    verify_exported_tool_traces(out, case)
    primary['model_evidence'] = model_metadata(out / 'private/primary/home')
    primary['completed_turn_checkpoint'] = checkpoint
    case['phase'] = 'post_primary'
    persist()
    return True


def authorize_supplemental_provenance(case, out, claim):
    """Explicit judge authorization buys one review turn, never approval."""
    state = case['roles']['provenance']
    if (out / 'blind/evidence/provenance/active-operation.json').exists():
        raise RuntimeError('Supplemental provenance requires an inactive operation journal')
    if state['attempts'] < 1 + MAX_CORRECTIONS or not state.get('session_id'):
        raise RuntimeError('Supplemental provenance requires the exhausted original reviewer session')
    path = audit.check_links(claim['path'])
    if audit.sha256(path) != claim['sha256']:
        raise RuntimeError('Supplemental judge authority bytes changed')
    authority = read(path)
    primary = case['roles']['primary']
    if (not authority.get('basis') or authority.get('same_provenance_session') != state['session_id']
            or authority.get('prior_attempts') != state['attempts']
            or authority.get('requirements_models_skills_scoring_changes') is not False
            or authority.get('reviewer_must_decide_independently') is not True):
        raise RuntimeError('Supplemental authority does not bind original reviewer and cumulative attempts')
    guard_candidate(out)
    verify_exported_tool_traces(out, case)
    for role, actual in (('primary', primary), ('provenance', state)):
        if (not actual.get('model_evidence') or model_metadata(out / 'private' / role / 'home') != actual['model_evidence']
                or not actual.get('completed_turn_checkpoint')
                or completed_turn_checkpoint(out, role, actual) != actual['completed_turn_checkpoint']):
            raise RuntimeError('Supplemental review lacks actual model/export/completed-turn evidence')
    checkpoint = primary['completed_turn_checkpoint']
    # The existing judge authority uses this historic field name for the
    # ORIGINAL final-file bytes SHA, not a serialized checkpoint-object hash.
    if authority.get('new_primary_checkpoint_sha256') != checkpoint['final_sha256']:
        raise RuntimeError('Supplemental authority does not bind actual primary final bytes')
    old_path = out / 'blind/evidence/provenance-review.json'
    old = read(old_path)
    current = provenance_api().validate_workspace(*provenance_args(out), require_review=False)
    if (old.get('schema') != 'b1-provenance-review-v1' or old.get('independent') is not True
            or state['auditor_id'] in {primary['auditor_id'], case['roles'].get('review', {}).get('auditor_id')}
            or old.get('reviewer_id') != state['auditor_id'] or old.get('primary_id') != primary['auditor_id']
            or old.get('candidate_hash') != current['candidate_hash']
            or old.get('manifest_sha256') != current['manifest_sha256']
            or old.get('journal_head') == current['journal_head']):
        raise RuntimeError('Supplemental review needs same canonical reviewer and genuinely newer valid journal head')
    operations = [read(out / 'blind/evidence/provenance/operations' / (name + '.json')) for name in current['operations']]
    heads = [current['manifest_sha256']] + [op['operation_sha256'] for op in operations]
    if old.get('journal_head') not in heads[:-1]:
        raise RuntimeError('Old canonical review is not an ancestor of the new valid journal head')
    added = operations[heads.index(old['journal_head']):]
    kind = claim.get('kind', authority.get('kind'))
    if kind == 'primary_remeasurement':
        floor = claim.get('reviewed_primary_attempts', authority.get('reviewed_primary_attempts'))
        if type(floor) is not int or not 0 <= floor < checkpoint['attempt']:
            raise RuntimeError('Primary remeasurement authority needs an explicit older primary attempt floor')
    elif kind == 'controller_terminal_drain':
        if not added or any(op.get('kind') != 'normal_stop_play' or op.get('provenance', {}).get('actor_id') != 'audit-controller'
                            for op in added):
            raise RuntimeError('Controller supplemental review requires actual new terminal-drain operations')
        for op in added:
            invocation = op['provenance']['invocation']
            command = json.loads(invocation['command'])
            if (invocation.get('kind') != 'controller' or command.get('tool') != 'frozen unity_proxy.drain'
                    or Path(command.get('project', '')).resolve() != runtime_project(out).resolve()):
                raise RuntimeError('Terminal drain has no exact original controller invocation')
            refs = op['provenance']['evidence_refs']
            if not any(read(out / 'blind/evidence' / ref['path']).get('result', {}).get('quiescent') is True
                       for ref in refs if ref['path'].endswith('/result.json')):
                raise RuntimeError('Terminal drain lacks actual frozen quiescence result evidence')
    else:
        raise RuntimeError('Unknown supplemental provenance authorization kind')
    used = state.get('supplemental_authorizations', [])
    if any(record['new_journal_head'] == current['journal_head'] or record['authority_sha256'] == claim['sha256'] for record in used):
        raise RuntimeError('Supplemental provenance authority/head already consumed')
    return {'utc': runner.utc(), 'kind': kind, 'basis': authority['basis'], 'authority_path': str(path),
            'authority_sha256': claim['sha256'], 'authority_primary_hash_semantics': 'original final.txt bytes SHA256',
            'original_reviewer_id': state['auditor_id'], 'session_id': state['session_id'],
            'prior_attempts': state['attempts'], 'reserved_attempt': state['attempts'] + 1,
            'old_review_sha256': audit.sha256(old_path), 'old_journal_head': old['journal_head'],
            'new_journal_head': current['journal_head'], 'new_operations': [op['operation_id'] for op in added],
            'primary_checkpoint': checkpoint,
            'primary_export_manifest_sha256': hashlib.sha256(json.dumps(primary['exported_tool_trace_manifests'][-1], sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest(),
            'explicit_judge_arguments': {key: claim[key] for key in ('kind', 'reviewed_primary_attempts') if key in claim}}


def require_primary_measurements(report, out):
    """Missing measurements belong to the primary, not an evidence-only reviewer."""
    checks = report.get('checks')
    if (not isinstance(checks, list) or len(checks) != 100
            or {c.get('id') for c in checks} != {c['id'] for c in audit.spec.CHECKS}):
        raise ValueError('Primary measurement coverage requires all 100 canonical IDs')
    missing = []
    for check in checks:
        if (check.get('id') == '057' and (out / 'judge_manifest.json').is_file()
                and read(out / 'judge_manifest.json').get('measurement_dispute_acceptance')):
            audit.measurement_dispute_module().resolve(out / 'blind/audit.json', report, check)
            continue
        if check.get('status') == 'verified' and type(check.get('score')) is int and check['score'] in (0, 1):
            continue
        if check.get('id') == '057' and check.get('status') == 'review_required' and check.get('score') is None:
            # This does not ask the root to fill a primary score. Only a fully
            # accepted independent overlay can resolve this measured dispute.
            audit.measurement_dispute_module().resolve(out / 'blind/audit.json', report, check)
        else:
            missing.append(check['id'])
    if missing:
        raise ValueError('Original primary must measure incomplete IDs before independent review: ' + ','.join(missing))
    for check in checks:
        if not all(isinstance(check.get(k), str) and check[k].strip() for k in ('operation', 'expected', 'actual')):
            raise ValueError('Original primary measurement lacks operation/expected/actual: ' + check['id'])
        audit.evidence_records(check.get('evidence'), out / 'blind/evidence')


def require_source_review_measurements(report, out, authority_path, authority_sha256):
    """Allow exact classified057 for technical provenance only, never scoring."""
    module = audit.measurement_dispute_module()
    if audit.sha256(authority_path) != authority_sha256:
        raise ValueError('Technical-source dispute authority changed')
    authority = module.read(authority_path)
    if (out / 'blind/evidence/provenance/active-operation.json').exists():
        raise ValueError('Technical-source review cannot bypass an active operation')
    chain = provenance_api().validate_workspace(*provenance_args(out), require_review=False)
    cp = authority['primary_checkpoint']
    controller = read(out.parent.parent / 'controller.json')
    cases = [c for c in controller['groups'].values() if c['opaque_id'] == read(out / 'blind/audit.json')['opaque_id']]
    if len(cases) != 1:
        raise ValueError('Technical-source actual actor case missing')
    primary = cases[0]['roles']['primary']
    if (primary.get('completed_turn_checkpoint') != cp
            or completed_turn_checkpoint(out, 'primary', primary) != cp
            or primary['execution_attempts'][-1].get('process_tree_stopped') is not True):
        raise ValueError('Technical-source original-primary actual completed turn not verified')
    verify_exported_tool_traces(out, cases[0])
    module.native_checkpoint(out / 'private/primary' / f"turn-{cp['attempt']}.jsonl",
                             out / 'private/primary' / f"turn-{cp['attempt']}.final.txt", cp)
    actual_models = model_metadata(out / 'private/primary/home')
    if cp['session_id'] not in actual_models or actual_models != primary.get('model_evidence'):
        raise ValueError('Actual original-primary model/session missing')
    actual = read(out / 'blind/audit.json')
    if actual != report:
        raise ValueError('Technical-source report differs from actual primary bytes')
    module.eligible(out / 'blind/audit.json', authority, cp, chain['journal_head'])
    return {'technical_source_review_only': True, 'business_score_authorized': False,
            'pending_measurement_dispute_id': '057', 'authority_sha256': authority_sha256}


def authorize_supplemental_business_review(case, out, claim):
    """Buy one same-session business review of genuinely new primary evidence."""
    state = case['roles']['review']
    primary = case['roles']['primary']
    if (state['attempts'] < 1 + MAX_CORRECTIONS or not state.get('session_id')
            or state['auditor_id'] in {primary['auditor_id'], case['roles'].get('provenance', {}).get('auditor_id')}
            or (out / 'blind/evidence/provenance/active-operation.json').exists()):
        raise RuntimeError('Business supplement needs inactive journal and exhausted original independent reviewer')
    path = audit.check_links(claim['path'])
    if audit.sha256(path) != claim['sha256']:
        raise RuntimeError('Supplemental business authority bytes changed')
    authority = read(path)
    current = provenance_api().validate_workspace(*provenance_args(out), require_review=False)
    report = read(out / 'blind/audit.json')
    require_primary_measurements(report, out)
    if (authority.get('schema') != 'b1-supplemental-business-review-v1'
            or not authority.get('basis') or authority.get('opaque_id') != case['opaque_id']
            or authority.get('candidate_hash') != current['candidate_hash']
            or authority.get('same_review_session') != state['session_id']
            or authority.get('reviewer_id') != state['auditor_id']
            or authority.get('prior_attempts') != state['attempts']
            or authority.get('requirements_models_skills_scoring_changes') is not False
            or authority.get('reviewer_must_decide_independently') is not True):
        raise RuntimeError('Business authority does not bind same reviewer/candidate/cumulative attempts')
    guard_candidate(out)
    verify_exported_tool_traces(out, case)
    for role, actual in (('primary', primary), ('review', state)):
        if (not actual.get('model_evidence') or model_metadata(out / 'private' / role / 'home') != actual['model_evidence']
                or not actual.get('completed_turn_checkpoint')
                or completed_turn_checkpoint(out, role, actual) != actual['completed_turn_checkpoint']):
            raise RuntimeError('Business supplement lacks actual model/export/completed-turn evidence')
    cp = primary['completed_turn_checkpoint']
    export_sha = hashlib.sha256(json.dumps(primary['exported_tool_trace_manifests'][-1], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    floor = authority.get('reviewed_primary_attempts')
    if (type(floor) is not int or not 0 <= floor < cp['attempt']
            or authority.get('new_primary_final_sha256') != cp['final_sha256']
            or authority.get('new_primary_native_sha256') != cp['native_sha256']
            or authority.get('new_primary_export_manifest_sha256') != export_sha):
        raise RuntimeError('Business supplement needs genuinely new actual primary checkpoint/export')
    old_path = out / 'blind/review-decisions.json'
    old = read(old_path)
    if (old.get('auditor_id') != state['auditor_id']
            or authority.get('old_review_sha256') != audit.sha256(old_path)
            or authority.get('new_journal_head') != current['journal_head']):
        raise RuntimeError('Business supplement original review/current head changed')
    operations = [read(out / 'blind/evidence/provenance/operations' / (name + '.json')) for name in current['operations']]
    heads = [current['manifest_sha256']] + [op['operation_sha256'] for op in operations]
    old_head = authority.get('old_journal_head')
    if old_head not in heads[:-1]:
        raise RuntimeError('Business supplement requires a newer valid descendant journal head')
    added = operations[heads.index(old_head):]
    selected = authority.get('new_measurement_operation_ids')
    if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
            or not set(selected).issubset({op['operation_id'] for op in added})):
        raise RuntimeError('Business supplement lacks new actual measurement operations')
    added = [op for op in added if op['operation_id'] in selected]
    refs = authority.get('new_measurement_evidence')
    audit.evidence_records(refs, out / 'blind/evidence')
    report_refs = {(r['path'], r['sha256']) for check in report['checks'] for r in check['evidence']}
    op_refs = set()
    for op in added:
        p = op.get('provenance', {})
        if (op.get('kind') not in ('normal_run', 'addressables_build') or p.get('actor_id') != primary['auditor_id']
                or not p.get('invocation', {}).get('refs')):
            raise RuntimeError('Business supplement selected operation is not original-primary measurement')
        op_refs.update((r['path'], r['sha256']) for r in p.get('evidence_refs', []))
    if any((r['path'], r['sha256']) not in report_refs & op_refs for r in refs):
        raise RuntimeError('Business supplement evidence must bind both primary measurements and new operation traces')
    used = state.get('supplemental_business_authorizations', [])
    if any(r['new_journal_head'] == current['journal_head'] or r['authority_sha256'] == claim['sha256'] for r in used):
        raise RuntimeError('Supplemental business authority/head already consumed')
    return {'utc': runner.utc(), 'authority_path': str(path), 'authority_sha256': claim['sha256'],
            'basis': authority['basis'], 'session_id': state['session_id'], 'original_reviewer_id': state['auditor_id'],
            'prior_attempts': state['attempts'], 'reserved_attempt': state['attempts'] + 1,
            'old_review_sha256': authority['old_review_sha256'], 'old_journal_head': old_head,
            'new_journal_head': current['journal_head'], 'primary_checkpoint': cp,
            'primary_export_manifest_sha256': export_sha, 'new_measurement_operation_ids': selected,
            'new_measurement_evidence': refs}


def invoke(control, case, role, prompt, out, persist, timeout=21600, *, supplemental_provenance=None, supplemental_business_review=None, technical_authority=None):
    technical = role == 'technical_adjudication'
    if technical:
        if supplemental_provenance is not None or supplemental_business_review is not None:
            raise RuntimeError('Technical adjunct cannot consume original-role supplemental authority')
        state = audit.technical_adjudication_module().launch_state(out, case, technical_authority)
        state['invocation_reserved'] = True
        state['stage_status'] = 'running'
        persist()  # Any setup/Popen/platform failure consumes the one initial turn.
    else:
        if technical_authority is not None or role not in ('primary', 'provenance', 'review'):
            raise RuntimeError('Unknown role or misplaced technical authority')
        state = case['roles'].setdefault(role, {'attempts': 0, 'session_id': None, 'auditor_id': uuid.uuid4().hex})
    if supplemental_provenance is not None and supplemental_business_review is not None:
        raise RuntimeError('Cannot combine supplemental judge authorities')
    if supplemental_business_review is not None:
        if role != 'review':
            raise RuntimeError('Supplemental business authority applies only to review')
        authorization = authorize_supplemental_business_review(case, out, supplemental_business_review)
        old_bytes = (out / 'blind/review-decisions.json').read_bytes()
        if hashlib.sha256(old_bytes).hexdigest() != authorization['old_review_sha256']:
            raise RuntimeError('Original business review changed before reservation')
        archive = out / 'private/review/supplemental-review-history' / (authorization['authority_sha256'] + '.json')
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open('xb') as handle:
            handle.write(old_bytes)
        authorization['old_review_archive'] = archive.relative_to(out).as_posix()
        state.setdefault('supplemental_business_authorizations', []).append(authorization)
        persist()
    elif supplemental_provenance is not None:
        if role != 'provenance':
            raise RuntimeError('Supplemental judge authority applies only to provenance')
        authorization = authorize_supplemental_provenance(case, out, supplemental_provenance)
        state.setdefault('supplemental_authorizations', []).append(authorization)
        persist()  # Reserve once before launching; never silently retry a head.
    elif not technical and state['attempts'] >= 1 + MAX_CORRECTIONS:
        raise RuntimeError(f'{role} exhausted three corrective audit turns; audit remains incomplete')
    private = out / 'private' / role
    home = private / 'home'
    admin = Path(control['admin'])
    if technical:
        home.mkdir(parents=True, exist_ok=False)
        for name in ('auth.json', 'models_cache.json'):
            source = Path(control['auth_home']) / name
            if source.is_file():
                shutil.copy2(source, home / name)
    else:
        setup_home(home, control['auth_home'], admin / 'frozen/unity-mcp-skill')
        state['attempts'] += 1
    persist()
    prefix = private / f'turn-{state["attempts"]}'
    workdir = (audit.technical_adjudication_module().input_directory(out, technical_authority) if technical else out / 'blind')
    command = (audit.technical_adjudication_module().command(control['cli'], home, workdir, prefix.with_suffix('.final.txt'))
               if technical else audit_command(control['cli'], home, out / 'blind', admin / 'frozen/unity_proxy.py',
                            private / 'unity.jsonl', prefix.with_suffix('.final.txt'), state['session_id']))
    # Proxy scope must be the actual Unity project, while CLI cwd remains blind.
    for index, arg in enumerate(command):
        if arg.startswith('mcp_servers.unity.args='):
            command[index] = 'mcp_servers.unity.args=' + json.dumps([str(admin / 'frozen/unity_proxy.py'), '--project',
                                                                       str(runtime_project(out)), '--log', str(private / 'unity.jsonl')])
    env = os.environ.copy()
    env['CODEX_HOME'] = str(home)
    for key in list(env):
        if key.startswith(('OPENAI_', 'AZURE_OPENAI', 'CODEX_CONFIG', 'CODEX_MODEL')):
            env.pop(key)
    proc = subprocess.Popen(command, cwd=workdir, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
    execution = {'attempt': state['attempts'], 'cli_pid': proc.pid, 'started_utc': runner.utc(),
                 'process_tree_stopped': False}
    state.setdefault('execution_attempts', []).append(execution)
    persist()
    tree = runner.ProcessTree(proc)
    timed_out = False
    try:
        if technical:
            helper = audit.measurement_dispute_module().execution_helper()
            census = helper.collect_census()
            rows = [r for r in census.get('processes', []) if r.get('ProcessId') == proc.pid]
            if census.get('complete') is not True or len(rows) != 1:
                raise RuntimeError('Actual live technical CLI generation not observed')
            audit.measurement_dispute_module().generation_module().creation_ms(rows[0].get('CreationDate'))
            generation_path = private / 'launch-census.json'
            with generation_path.open('x', encoding='utf-8') as handle:
                json.dump(census, handle, ensure_ascii=False, allow_nan=False)
            execution['generation_row'] = rows[0]
            execution['generation_census_ref'] = {'path': generation_path.relative_to(out).as_posix(),
                'sha256': audit.sha256(generation_path), 'bytes': generation_path.stat().st_size}
            persist()
        stdout, _ = proc.communicate(prompt.encode('utf-8'), timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        tree.stop()
        stdout, _ = proc.communicate(timeout=15)
    finally:
        tree.stop()
        execution.update(process_tree_stopped=True, ended_utc=runner.utc())
        persist()
    prefix.with_suffix('.jsonl').write_bytes(stdout)
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'thread.started':
            sid = event['thread_id']
            if state['session_id'] and state['session_id'] != sid:
                raise RuntimeError('Audit continuation changed original session')
            state['session_id'] = sid
    persist()
    if derived_workspace(out):
        state.setdefault('exported_tool_trace_manifests', []).append(export_tool_traces(out, role, state, prefix))
    checkpoint = completed_turn_checkpoint(out, role, state)
    if checkpoint is not None:
        state['completed_turn_checkpoint'] = checkpoint
        if role == 'primary':
            case['phase'] = 'post_primary'
    persist()
    if timed_out:
        raise RuntimeError('Audit turn timeout; preserve same session, incomplete audit is paused and never scored as zero')
    state['model_evidence'] = model_metadata(home)
    if not state['session_id'] or not prefix.with_suffix('.final.txt').is_file():
        raise RuntimeError('Audit infrastructure failed before final response; preserve same session and pause')
    persist()
    if technical:
        audit.technical_adjudication_module().verify_single_owner(out, state)
        state['stage_status'] = 'completed'
        persist()

def measurement_restart_protocol():
    return ('\nUniform derived-workspace measurement protocol for primary, reviewer and every descendant: '
            'If evidence/judge-tooling-availability.json exists, read it and its referenced calibration/adapter guide '
            'BEFORE choosing scripted input. Use only its bounded, actually calibrated capabilities; unsupported '
            'input channels are measurement infrastructure blockers, never grounds for a game score of zero. '
            'Tool availability does not change any business acceptance criterion. '
            'candidate/ is immutable reference U: never open it in Unity, modify it, build it, or use its original save namespace. '
            'runtime/ is disposable execution shadow R initially copied byte-for-byte from U, and is the ONLY Unity/MCP project. '
            'All 100 acceptance requirements and the original candidate hash remain unchanged. '
            'Use provenance_tool.py begin/finish around every startup, normal_run, addressables_build, normal_stop_play and editor_exit '
            'operation, including every descendant operation. Read evidence/provenance/manifest.json and the tool CLI help first. '
            'Never overlap operations. Record complete before/after bytes via the journal, exact original tool-call IDs/parameters, '
            'adapter source hashes, original source/package entry-point hashes and SHA256 evidence files in provenance. '
            'The byte journal scope is Assets, Packages and ProjectSettings, not all OS writes. Separately archive and hash '
            'actual operation outputs outside that scope: ServerData, Addressables Library catalogs/bundles, build reports, '
            'and JSON saves/settings needed by the tested requirement. Cite them as actual evidence/source references. '
            'For Addressables 091/095, record the original builder actual Error/OutputPath/success, catalog and bundle '
            'existence, and real original-game loading traces; a build invocation returning {} is insufficient evidence. '
            'Use original game save/settings UI paths, original standard Addressables configuration/builder, and normal Unity lifecycle, '
            'including normal Stop Play and normal Editor exit/save behavior. Standard operations may serialize derived data; '
            'every changed path requires independent provenance approval, not an assertion of semantic equivalence. '
            'Journal Editor exit before it begins and finish after its writes settle; consolidate required persistence checks into '
            'one hidden reopen and verify actual player-flow persistence. Do not add crash-durability requirements or restart merely '
            'for refresh. Never repair business behavior, edit original code/dependency manifests, restore/normalize original '
            'assets, change builder configuration to make a test pass, or accept a new baseline. '
            'Judges must never hand-edit original .meta files. Original GUIDs in R are locked; non-GUID .meta fields '
            'naturally written by the original Unity importer or standard lifecycle require a complete journal, actual '
            'original-source evidence and independent review of every changed path. An unchanged GUID does not establish equivalence. '
            'Only new test adapters under runtime/Assets/__B1Audit are permitted. Unknown or unjournaled changes, insufficient '
            'origin evidence, and measurement infrastructure problems block publication; they are not functional zero scores. '
            'The independent provenance reviewer checks all actual writes and original operation sources before business scoring. '
            'To locate GameView, prefer Resources.FindObjectsOfTypeAll. If creating/getting an EditorWindow is unavoidable, '
            'explicitly use focus=false; do not call window.Show, window.Focus, or any OS foreground/input API. '
            "Do not change the tested game's normal internal TextField keyboard-focus logic; this restriction concerns real desktop/window focus. "
            'Notify and enforce the same protocol for every descendant.')


def primary_prompt(out, auditor_id):
    return ((out / 'blind/prompt.md').read_text(encoding='utf-8') +
            '\nYou are the independent primary auditor. Your auditor.id must be ' + auditor_id +
            '. Read audit.json and fixtures.json. Fill all 100 canonical records using runtime evidence and SHA256 files. '
            'For each completed check use status="verified" and an integer score 0 or 1, never status="pass"/"fail". '
            'Archive and SHA256 the actual packed build report immediately after its build completes, before any Play/FastMode operation can overwrite it. '
            'The candidate reference U is immutable; use only runtime shadow R for Unity and new runtime/Assets/__B1Audit adapters. '
            'Never fix the game or turn incomplete measurements into scores. Use review_required for genuine evidence/adapter disputes. '
            'Do not fill review or adjudication yourself. You may use at most two simultaneously active descendants, all exact gpt-6.1-sol/high. '
            'Only read this anonymous blind directory, own isolated Unity skill, and public official docs; no parent directories, development logs, model allocation, other candidates, or serial-job-session. '
            'Use the existing assigned Unity instance; do not restart merely to refresh. Consolidate persistence checks into one editor restart if required, and do not repeatedly reopen. '
            'Final answer ends this audit turn. Report infrastructure blockers explicitly; no invented pass/fail results.' +
            measurement_restart_protocol())

def flagged(report):
    return [check['id'] for check in report.get('checks', []) if check.get('review_required') and not check.get('review')]

def guard_candidate(out):
    manifest = read(out / 'blind/manifest.json')
    if audit.inventory(out / 'blind/candidate', allow_adapters=not derived_workspace(out)) != manifest['audit_baseline']:
        raise RuntimeError('Anonymous candidate business files changed; audit must pause without repairs')
    for name, expected in manifest['manuals'].items():
        if audit.sha256(out / 'blind/candidate' / name) != expected:
            raise RuntimeError('Anonymous operation manual changed during audit')
    for name, expected in manifest['artifacts'].items():
        if audit.sha256(out / 'blind' / name) != expected:
            raise RuntimeError('Frozen public audit artifacts changed')
    if derived_workspace(out):
        provenance_api().validate_workspace(*provenance_args(out), require_review=False)

def review(control, case, out, persist, *, supplemental_business_review=None):
    path = out / 'blind/audit.json'
    original = read(path)
    ids = flagged(original)
    if not ids:
        return
    require_primary_measurements(original, out)
    state = case['roles'].setdefault('review', {'attempts': 0, 'session_id': None, 'auditor_id': uuid.uuid4().hex})
    if state['attempts'] >= 1 + MAX_CORRECTIONS and supplemental_business_review is None:
        raise RuntimeError('Independent review exhausted corrective turns; explicit new-measurement authority required')
    runtime_required = case.get('review_runtime_required') is True
    if supplemental_business_review is not None and runtime_required:
        raise RuntimeError('Supplemental business review is evidence-only; new runtime measurements belong to the primary')
    if runtime_required:
        ensure_editor(control, case, out, persist)
    request = {'auditor_id': state['auditor_id'], 'only_ids': ids,
               'candidate_hash': original['candidate_hash'], 'decisions': []}
    target = out / 'blind/review-decisions.json'
    # Supplemental authority binds the original review bytes. Preserve them
    # until reservation; the same reviewer replaces its own decisions.
    if supplemental_business_review is None:
        save(target, request)
    prompt = ((out / 'blind/prompt.md').read_text(encoding='utf-8') + '\nYou are a fresh independent reviewer, not the primary. '
              'Read public audit.json and its evidence only; remeasure ONLY IDs ' + json.dumps(ids) +
              '. Do not modify audit.json. Fill review-decisions.json.decisions with objects id,auditor_id,score(0/1),operation,expected,actual,evidence:[{path,sha256}]. '
              'Use your distinct reviewer auditor_id. Do not repair business code, change original decisions or inspect private primary logs. '
              'Use at most two concurrently active descendants, all exact gpt-6.1-sol/high. Only operate this anonymous blind directory, '
              'own isolated Unity skill and public official documentation. Do not read private directories, development logs or other cases. '
              'Use the existing editor, never restart merely for refresh. If blocked leave incomplete and explain; never invent evidence.' +
              measurement_restart_protocol())
    if not runtime_required:
        prompt += ('\nThis is evidence-only dispute review: no live Editor is required or promised. Do not launch Unity, '
                   'invoke Unity runtime tools or remeasure behavior. If existing actual evidence cannot settle an ID, '
                   'leave the review incomplete and identify the precise runtime remeasurement needed for judge approval.')
    before = runner.digest(path)
    while state['attempts'] < 1 + MAX_CORRECTIONS or supplemental_business_review is not None:
        guard_candidate(out)
        if supplemental_business_review is None:
            invoke(control, case, 'review', prompt, out, persist)
        else:
            invoke(control, case, 'review', prompt, out, persist, supplemental_business_review=supplemental_business_review)
            supplemental_business_review = None
        guard_candidate(out)
        if runner.digest(path) != before:
            raise RuntimeError('Independent reviewer modified the primary report')
        try:
            decisions = read(target).get('decisions', [])
            if len(decisions) != len(ids) or {d.get('id') for d in decisions} != set(ids):
                raise ValueError('Independent review is incomplete')
            for decision in decisions:
                if decision.get('auditor_id') != state['auditor_id']:
                    raise ValueError('Independent reviewer identity mismatch')
                audit.decision(decision, original['auditor']['id'], out / 'blind/evidence')
        except (ValueError, TypeError, KeyError) as exc:
            prompt = ('Continue the same independent review, only the previously assigned IDs. '
                      'Resolve exact validation error using actual evidence, never change the game or audit.json: ' + str(exc))
            continue
        by_id = {d['id']: d for d in decisions}
        for check in original['checks']:
            if check['id'] in by_id:
                check['review'] = by_id[check['id']]
        save(path, original)
        return
    raise RuntimeError('Independent review remains incomplete after three corrective turns')


def provenance_review(control, case, out, persist, *, supplemental_provenance=None):
    if not derived_workspace(out):
        return
    verify_exported_tool_traces(out, case)
    primary = case['roles']['primary']['auditor_id']
    role = case['roles'].setdefault('provenance', {'attempts': 0, 'session_id': None, 'auditor_id': uuid.uuid4().hex})
    if role['auditor_id'] == primary or role['auditor_id'] == case['roles'].get('review', {}).get('auditor_id'):
        raise RuntimeError('Provenance reviewer must be independent of all measured-operation actors')
    def approved():
        result = provenance_api().validate_workspace(*provenance_args(out), require_review=True)
        if result.get('reviewer_id') != role['auditor_id']:
            raise ValueError('Canonical provenance reviewer identity mismatch')
        return result
    if role.get('model_evidence'):
        try:
            return approved()
        except ValueError:
            pass
    error = ''
    while role['attempts'] < 1 + MAX_CORRECTIONS or supplemental_provenance is not None:
        prompt = ('You are a fresh independent provenance reviewer. Your reviewer ID is ' + role['auditor_id'] +
                  ', distinct from primary ' + primary + '. Read only this blind workspace, U reference, R shadow, '
                  'provenance_tool.py, journal byte blobs, original source/package entries and exact operation evidence. '
                  'Use controller-exported evidence/tool-traces/ anonymous actual native/session/descendant/Unity traces '
                  'and their source/export hashes to verify tool-call references; do not treat primary self-description as execution proof. '
                  'Do not inspect model allocations, other candidates, or private primary logs. Do not run Unity or perform '
                  'business tests, edit assets, fix behavior, or assign any business scores. Audit EVERY actual changed path '
                  'and operation in evidence/provenance: full before/after bytes, continuous journal, exact tool invocations, '
                  'adapter hashes and original source/package entry-point evidence. Normal original lifecycle/build serialization '
                  'may produce data writes, but do not assert semantic equivalence or approve mere resemblance. Reject unknown '
                  'or unsupported changes; request original-primary remeasurement, never asset restoration or business fixes. '
                  'Write canonical decisions ONLY to evidence/provenance-review.json using provenance_tool.py schema. '
                  'Exact schema: {schema:"b1-provenance-review-v1",manifest_sha256,candidate_hash,journal_head,primary_id,'
                  'reviewer_id,independent:true,decisions:[{operation_id,path,decision:"approved",basis,'
                  'evidence_refs:[{path,sha256}]}],final:{approved:true,runtime_digest,reference_digest,basis,'
                  'evidence_refs:[{path,sha256}]}}. Obtain exact chain/digests using validate without --require-review. '
                  'Evidence paths are relative to evidence/, may not cite provenance/ or the review document itself. '
                  'Approval requires actual original-operation evidence for every path, not just a schema-compliant assertion. '
                  'The reviewer_id must equal your ID. Include all exact operation IDs and every changed path. '
                  'Use exact gpt-6.1-sol/high for yourself and every descendant, maximum two simultaneous descendants. ' +
                  measurement_restart_protocol() + ('\nResolve exact validation error: ' + error if error else ''))
        if supplemental_provenance is None:
            invoke(control, case, 'provenance', prompt, out, persist)
        else:
            invoke(control, case, 'provenance', prompt, out, persist, supplemental_provenance=supplemental_provenance)
            supplemental_provenance = None
        guard_candidate(out)
        verify_exported_tool_traces(out, case)
        try:
            result = approved()
        except (ValueError, KeyError, TypeError) as exc:
            error = str(exc)
            role['validation_error'] = error
            persist()
            continue
        role.pop('validation_error', None)
        persist()
        return result
    raise RuntimeError('Provenance remains blocked; preserve unknown writes and request original-primary remeasurement, never score as zero')

def closed_helper(expected_sha):
    path = Path(__file__).resolve().with_name('closed_seal.py')
    if audit.sha256(path) != expected_sha:
        raise RuntimeError('Closed-editor helper differs from independently reviewed source')
    module = types.ModuleType('b1_audit_closed_seal')
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    return module


def actual_participants(case):
    if case.get('adjunct_participants'):
        return audit.technical_adjudication_module().participants(case)
    return case['roles']


def technical_eligibility(out, case, check_ids, *, action):
    module = audit.technical_adjudication_module()
    result = module.resolve(Path(out) / 'blind/audit.json', check_ids, action=action)
    state = actual_participants(case).get(module.ROLE)
    if not state or state.get('auditor_id') != result['technical_adjudicator_id']:
        raise RuntimeError('Technical result has no actual controller adjunct participant')
    return result


def closed_role_bindings(case):
    return {name: {key: state.get(key) for key in ('auditor_id', 'session_id', 'attempts')}
            for name, state in actual_participants(case).items()}


def closed_log_files(out, case):
    native, sessions, other = [], [], []
    for role, state in actual_participants(case).items():
        private = out / 'private' / role
        turns = sorted(private.glob('turn-*.jsonl'))
        if {p.name for p in turns} != {f'turn-{n}.jsonl' for n in range(1, state['attempts'] + 1)}:
            raise RuntimeError('Closed-editor proof lacks exact actual turn log coverage')
        native.extend(turns)
        sessions.extend(sorted((private / 'home/sessions').rglob('*.jsonl')))
        other.extend(p for p in (private / 'unity.jsonl', private / 'unity.native.stderr.log') if p.is_file())
    files = native + sessions + other
    return native, sessions, {p.relative_to(out).as_posix(): audit.sha256(p) for p in files}


def closed_proof_check(request):
    """Read-only child process; its explicit CLI marker proves helper identity."""
    out = Path(request['out'])
    case = request['case']
    claim = request['evidence']
    evidence_path = audit.check_links(claim['path'])
    if audit.sha256(evidence_path) != claim['sha256']:
        raise RuntimeError('Independent closed-editor evidence changed')
    evidence = read(evidence_path)
    helper = closed_helper(evidence.get('helper_sha256'))
    project = runtime_project(out)
    manifest = read(out / 'blind/manifest.json')
    if (not derived_workspace(out) or evidence.get('schema') != 'b1-audit-closed-editor-evidence-v1'
            or evidence.get('opaque_id') != case['opaque_id']
            or evidence.get('candidate_hash') != manifest['candidate_hash']
            or helper.normalized(evidence.get('runtime', '')) != helper.normalized(project)):
        raise RuntimeError('Closed-editor evidence scope or actual auditor identities changed')
    covered = evidence.get('roles')
    if not isinstance(covered, dict) or not covered:
        raise RuntimeError('Closed-editor reviewed historical roles required')
    current = closed_role_bindings(case)
    for role, prior in covered.items():
        now = current.get(role)
        if (not isinstance(prior, dict) or now is None or prior.get('auditor_id') != now['auditor_id']
                or prior.get('session_id') != now['session_id'] or type(prior.get('attempts')) is not int
                or not 1 <= prior['attempts'] <= now['attempts']):
            raise RuntimeError('Historical closed-editor actor/session/attempt changed or regressed')
    if (out / 'blind/evidence/provenance/active-operation.json').exists():
        raise RuntimeError('Closed-editor evidence cannot finish an active operation')
    reviewer = evidence.get('review', {})
    if (reviewer.get('independent') is not True or not reviewer.get('basis')
            or not isinstance(reviewer.get('reviewer_id'), str) or not reviewer['reviewer_id']
            or reviewer['reviewer_id'] in {r['auditor_id'] for r in actual_participants(case).values()}):
        raise RuntimeError('Independent closed-editor source review required')
    refs = evidence.get('lifecycle_evidence', []) + reviewer.get('evidence_refs', [])
    if (not evidence.get('lifecycle_evidence') or not reviewer.get('evidence_refs')
            or evidence.get('closure_basis') not in ('closed_cause_unconfirmed', 'normal_exit_observed')):
        raise RuntimeError('Actual lifecycle observations and independent review evidence required')
    for ref in refs:
        path = audit.check_links(ref['path'])
        if audit.sha256(path) != ref['sha256']:
            raise RuntimeError('Closed-editor actual operation/review source changed')
    actors, instances = evidence.get('known_actor_pids'), evidence.get('known_instance_ids')
    if (not isinstance(actors, list) or not actors or not isinstance(instances, list) or not instances
            or case.get('initial_editor_pid') not in actors or case.get('editor_identity') not in instances):
        raise RuntimeError('Exact known editor actors and native instance identities required')
    if not isinstance(evidence.get('saves_path'), str) or not Path(evidence['saves_path']).is_absolute():
        raise RuntimeError('Exact anonymous save path required')
    saves = audit.check_links(evidence['saves_path'])
    # The independent review fixes historical facts. Later controller-owned
    # review invocations are verified from their own actual process records,
    # complete traces, model contexts and checkpoints instead of re-signing the
    # same historical document after every read-only reviewer turn.
    dynamic_actors = list(actors)
    for role, state in actual_participants(case).items():
        floor = covered.get(role, {}).get('attempts', 0)
        executions = state.get('execution_attempts', [])
        for attempt in range(floor + 1, state['attempts'] + 1):
            records = [record for record in executions if record.get('attempt') == attempt]
            if (len(records) != 1 or records[0].get('process_tree_stopped') is not True
                    or type(records[0].get('cli_pid')) is not int or records[0]['cli_pid'] <= 0
                    or not records[0].get('started_utc') or not records[0].get('ended_utc')):
                raise RuntimeError('New audit invocation lacks actual stopped controller process evidence')
            dynamic_actors.append(records[0]['cli_pid'])
    baseline_inputs = None
    def stable_inputs():
        nonlocal baseline_inputs
        native, sessions, hashes = closed_log_files(out, case)
        inventory = {'reference': audit.inventory(out / 'blind/candidate'), 'runtime': audit.inventory(project),
                     'saves': helper.save_inventory(saves)}
        historical = evidence.get('log_hashes')
        sizes = evidence.get('log_sizes')
        if not isinstance(historical, dict) or not historical or not isinstance(sizes, dict) or set(sizes) != set(historical):
            raise RuntimeError('Historical closed-editor log hashes and byte lengths required')
        expected_turns = {f'private/{role}/turn-{n}.jsonl' for role, prior in covered.items()
                          for n in range(1, prior['attempts'] + 1)}
        if not expected_turns <= set(historical):
            raise RuntimeError('Historical review omits an actual covered native turn')
        for name, sha in historical.items():
            if name not in hashes or type(sizes[name]) is not int or sizes[name] < 0:
                raise RuntimeError('Historical closed-editor log scope changed')
            data = audit.check_links(out / name).read_bytes()
            exact_turn = name in expected_turns
            if ((exact_turn and len(data) != sizes[name]) or len(data) < sizes[name]
                    or hashlib.sha256(data[:sizes[name]]).hexdigest() != sha):
                raise RuntimeError('Historical native bytes or append-only log prefix changed')
        if inventory['reference'] != evidence.get('inventories', {}).get('reference'):
            raise RuntimeError('Closed-editor reference differs from reviewed facts')
        if baseline_inputs is None:
            baseline_inputs = (hashes, inventory)
        elif baseline_inputs != (hashes, inventory):
            raise RuntimeError('Closed-editor logs/reference/runtime/saves changed from reviewed facts during check')
        if audit.sha256(evidence_path) != claim['sha256']:
            raise RuntimeError('Closed-editor reviewed evidence changed during verification')
        for ref in refs:
            if audit.sha256(audit.check_links(ref['path'])) != ref['sha256']:
                raise RuntimeError('Closed-editor operation/review evidence changed during verification')
        guard_candidate(out)
        verify_exported_tool_traces(out, case)
        for role, state in actual_participants(case).items():
            anchored = {}
            for trace in state.get('exported_tool_trace_manifests', []):
                for entry in trace['files']:
                    name = entry['source_file']
                    if name == f'turn-{trace["turn"]}.jsonl' or trace['turn'] == state['attempts']:
                        anchored[f'private/{role}/{name}'] = entry['source_sha256']
            actual_role = {name: sha for name, sha in hashes.items() if name.startswith(f'private/{role}/')}
            if anchored != actual_role:
                raise RuntimeError('Closed-editor current logs differ from final controller-exported original bytes')
            if model_metadata(out / 'private' / role / 'home') != state.get('model_evidence'):
                raise RuntimeError('Closed-editor actual model evidence changed or is missing')
            if completed_turn_checkpoint(out, role, state) != state.get('completed_turn_checkpoint'):
                raise RuntimeError('Closed-editor completed role checkpoint missing or changed')
            if not state.get('completed_turn_checkpoint'):
                raise RuntimeError('Closed-editor role has no actual terminal checkpoint')
        return native, sessions, hashes, inventory
    native, sessions, hashes, inventory = stable_inputs()
    cancellations = None
    if evidence.get('cancelled_unity_evidence'):
        ref = evidence['cancelled_unity_evidence']
        if audit.sha256(audit.check_links(ref['path'])) != ref['sha256']:
            raise RuntimeError('Exact Unity cancellation review changed')
        cancellations = helper.validate_cancellations(ref['path'], case['opaque_id'], project,
            {str(out / name): sha for name, sha in hashes.items()})
        if not set(cancellations['known_actor_pids']) <= set(actors) or not set(cancellations['known_instance_ids']) <= set(instances):
            raise RuntimeError('Cancellation target actors omitted from closed census checks')
    tool_pairs = {'native': helper.native_pairs(native, cancellations), 'sessions': helper.rollout_pairs(sessions)}
    census_before = helper.collect_census()
    verification_before = helper.verify_census(census_before, project, out / 'private', {}, tool_pairs['sessions'], instances, dynamic_actors)
    stable_inputs()
    census_after = helper.collect_census()
    verification_after = helper.verify_census(census_after, project, out / 'private', {}, tool_pairs['sessions'], instances, dynamic_actors)
    stable_inputs()
    return {'schema': 'b1-audit-closed-editor-proof-v1', 'opaque_id': case['opaque_id'],
            'evidence_sha256': claim['sha256'], 'helper_sha256': evidence['helper_sha256'],
            'mode': 'closed_quiescent', 'quiescent': True, 'editor_restarted': False, 'no_known_writers': True,
            'closure_basis': evidence['closure_basis'], 'actual_current_roles': current,
            'census_before': census_before, 'census_after': census_after,
            'verification_before': verification_before, 'verification_after': verification_after,
            'tool_pairs': tool_pairs, 'log_hashes': hashes, 'inventories': inventory,
            'scope_limit': 'Known actors, recorded sessions/native tasks and scoped processes; not OS filesystem isolation.'}


def verify_closed_editor(control, case, out):
    claim = case.get('closed_editor_evidence')
    if not isinstance(claim, dict) or not claim.get('path') or not claim.get('sha256'):
        raise RuntimeError('Editor is unavailable; independently reviewed closed-editor evidence required')
    folder = out / 'private/closed-editor-checks' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    request = folder / 'request.json'
    result = folder / 'proof.json'
    save(request, {'out': str(out.resolve()), 'case': case, 'evidence': claim})
    command = [sys.executable, str(Path(__file__).resolve()), '--closed_seal-proof', str(request), str(result)]
    process = subprocess.run(command, capture_output=True, timeout=180)
    (folder / 'stdout.log').write_bytes(process.stdout)
    (folder / 'stderr.log').write_bytes(process.stderr)
    if process.returncode or not result.is_file():
        raise RuntimeError('Independent closed-editor proof failed; preserve private diagnostics')
    proof = read(result)
    if (proof.get('schema') != 'b1-audit-closed-editor-proof-v1' or proof.get('opaque_id') != case['opaque_id']
            or proof.get('evidence_sha256') != claim['sha256'] or proof.get('mode') != 'closed_quiescent'
            or proof.get('quiescent') is not True or proof.get('editor_restarted') is not False
            or proof.get('no_known_writers') is not True):
        raise RuntimeError('Closed-editor child proof result is invalid')
    # Full CIM command lines remain private: other candidates must stay blind.
    summary = {key: proof[key] for key in ('schema', 'opaque_id', 'evidence_sha256', 'helper_sha256',
               'mode', 'quiescent', 'editor_restarted', 'no_known_writers', 'scope_limit')}
    summary.update(private_proof_sha256=audit.sha256(result), proof_controller_source_sha256=audit.sha256(Path(__file__)))
    path = out / 'blind/evidence/tool-traces/controller' / ('closed-quiescent-' + folder.name) / 'result.json'
    path.parent.mkdir(parents=True)
    save(path, summary)
    return summary


def finalize_case(control, case, out, persist):
    """End audio/runtime before allowing the next anonymous candidate to start.

    A failed drain resumes here, without allocating another auditor turn or
    reopening an editor. Previously completed cases acquire the same evidence.
    """
    guard_candidate(out)
    if not case.get('terminal_drain', {}).get('passed'):
        case['phase'] = 'finalizing'
        persist()
        try:
            proxy = load_proxy(Path(control['admin']))
            try:
                proxy.discover(runtime_project(out))
            except (ConnectionError, OSError, TimeoutError):
                result = verify_closed_editor(control, case, out)
            else:
                result = None
            if result is None:
                operation_id = 'terminal-stop-play-' + uuid.uuid4().hex
                provenance = begin_controller_operation(out, 'normal_stop_play', operation_id,
                                                        {'tool': 'frozen unity_proxy.drain', 'project': str(runtime_project(out))})
                result = proxy.drain(runtime_project(out))
                if result.get('quiescent') is not True or result.get('editor_restarted') is not False:
                    raise RuntimeError('Audit editor drain did not verify quiescence without restart')
                finish_controller_operation(out, operation_id, provenance, result)
            record = {'utc': runner.utc(), 'passed': True, 'result': result}
        except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
            record = {'utc': runner.utc(), 'passed': False, 'error': str(exc)}
            case['terminal_drain'] = record
            save(out / 'terminal-drain.json', record)
            persist()
            raise RuntimeError('Anonymous audit editor drain failed; keep audit paused before next candidate') from exc
        case['terminal_drain'] = record
        save(out / 'terminal-drain.json', record)
        persist()
    guard_candidate(out)
    provenance_review(control, case, out, persist)
    verify_exported_tool_traces(out, case)
    if case.get('terminal_drain', {}).get('result', {}).get('mode') == 'closed_quiescent':
        # A provenance reviewer may have produced additional actual tool calls.
        # Revalidate their terminal state too; an outdated evidence document
        # must block rather than silently carry an earlier positive result.
        fresh = verify_closed_editor(control, case, out)
        case['terminal_drain'] = {'utc': runner.utc(), 'passed': True, 'result': fresh}
        save(out / 'terminal-drain.json', case['terminal_drain'])
        persist()
    result = audit.save_score(out / 'blind/audit.json')
    case.update(status='complete', phase='complete', score=result['score'], completed_utc=runner.utc())
    case.pop('error', None)
    persist()
    return result

def run_case(control, key, root):
    case = control['groups'][key]
    out = root / 'cases' / case['opaque_id']
    def persist():
        save(root / 'controller.json', control)
    if case['status'] == 'complete' and case.get('terminal_drain', {}).get('passed'):
        audit.validate_report(out / 'blind/audit.json')
        return
    finalization_pending = case['status'] == 'complete' or case.get('phase') == 'finalizing'
    case['status'] = 'running'
    persist()
    try:
        if finalization_pending:
            finalize_case(control, case, out, persist)
            return
        primary = case['roles'].setdefault('primary', {'attempts': 0, 'session_id': None, 'auditor_id': uuid.uuid4().hex})
        post_primary = recover_primary_postprocessing(case, out, persist)
        while post_primary or primary['attempts'] < 1 + MAX_CORRECTIONS:
            prompt = primary_prompt(out, primary['auditor_id'])
            if primary['attempts']:
                if not primary['session_id']:
                    raise RuntimeError('Cannot create fresh primary session after an incomplete turn')
                prompt = ('Continue the same independent audit, without game fixes or changing requirements. '
                          'Resolve these exact validation errors with measurements and evidence: ' + case.get('validation_error', case.get('error', 'Prior turn incomplete')) +
                          '. Preserve existing independent reviews. Unmeasured items remain incomplete; do not invent scores.')
            guard_candidate(out)
            if not post_primary:
                ensure_editor(control, case, out, persist)
                invoke(control, case, 'primary', prompt, out, persist)
                case['phase'] = 'post_primary'
                persist()
            post_primary = False
            guard_candidate(out)
            try:
                report = read(out / 'blind/audit.json')
                if report.get('auditor', {}).get('id') != primary['auditor_id']:
                    raise ValueError('Primary auditor identity mismatch')
                require_primary_measurements(report, out)
                provenance_review(control, case, out, persist)
                review(control, case, out, persist)
                provenance_review(control, case, out, persist)
                audit.validate_report(out / 'blind/audit.json')
            except (ValueError, KeyError, TypeError) as exc:
                case['validation_error'] = str(exc)
                case['phase'] = 'primary_correction'
                persist()
                continue
            case['phase'] = 'finalizing'
            persist()
            finalize_case(control, case, out, persist)
            return
        raise RuntimeError('Three corrective audit turns exhausted; report is incomplete')
    except (RuntimeError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        case.update(status='paused', error=str(exc), paused_utc=runner.utc())
        persist()
        raise

def summarize(control, root):
    groups, reports = [], []
    admin = Path(control['admin'])
    manifest = runner.manifest(admin)
    for key in control['order']:
        case = control['groups'][key]
        if case['status'] != 'complete' or not case.get('terminal_drain', {}).get('passed'):
            raise RuntimeError('All six audits must be complete with verified terminal drain before summary')
        state = read(admin / 'sealed' / key / 'state.json')
        groups.append({'id': key, 'model': manifest['groups'][key]['model'],
                       'use_skill': manifest['groups'][key]['use_skill'], 'opaque_id': case['opaque_id'],
                       'termination': state['status'], 'validity': 'invalid' if state['status'] == 'invalid' or not state.get('actual_model_verified') else 'valid'})
        reports.append(root / 'cases' / case['opaque_id'] / 'blind/audit.json')
    mapping = root / 'private-summary-map.json'
    save(mapping, {'groups': groups})
    return audit.summarize(mapping, reports, admin / 'results')

def main(argv=None):
    actual_argv = sys.argv[1:] if argv is None else argv
    if actual_argv and actual_argv[0] == '--closed_seal-proof':
        if len(actual_argv) != 3:
            raise ValueError('Closed proof requires exact request and result paths')
        target = Path(actual_argv[2])
        if target.exists():
            raise RuntimeError('Refusing to overwrite closed-editor proof')
        save(target, closed_proof_check(read(actual_argv[1])))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin', type=Path, default=runner.DEFAULT_ADMIN)
    parser.add_argument('--root', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init')
    init.add_argument('--editor', type=Path, required=True)
    init.add_argument('--auth-home', type=Path, default=Path.home() / '.codex')
    sub.add_parser('status')
    run = sub.add_parser('run')
    run.add_argument('--one', action='store_true', help='Audit one anonymous candidate then return')
    sub.add_parser('summarize')
    args = parser.parse_args(argv)
    admin = args.admin.resolve()
    root = (args.root or admin / 'audits').resolve()
    if args.action == 'init':
        result = initialize(admin, root, args.editor, args.auth_home)
    else:
        control = load_controller(root)
        if args.action == 'status':
            result = {key: {'opaque_id': value['opaque_id'], 'status': value['status'], 'error': value.get('error'), 'score': value.get('score')} for key, value in control['groups'].items()}
        elif args.action == 'summarize':
            result = summarize(control, root)
        else:
            with lock(root):
                for key in control['order']:
                    if (control['groups'][key]['status'] == 'complete'
                            and control['groups'][key].get('terminal_drain', {}).get('passed')):
                        continue
                    run_case(control, key, root)
                    if args.one:
                        break
                result = summarize(control, root) if all(c['status'] == 'complete' and c.get('terminal_drain', {}).get('passed') for c in control['groups'].values()) else {'status': 'incomplete'}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
