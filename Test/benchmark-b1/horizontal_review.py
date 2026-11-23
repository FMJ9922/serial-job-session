"""Prepare and run evidence-only, blinded B1 horizontal review. No Unity calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

import audit_runner as ar

MODEL, EFFORT = 'gpt-6.1-sol', 'high'
BUNDLE_FILES = ('consistency.py', 'audit.py', 'operation_provenance.py', 'spec.py',
                'measurement_dispute.py', 'closed_seal.py', 'process_generation.py', 'runner.py', 'technical_adjudication.py')
LOCAL_FILES = ('horizontal_review.py', 'audit_runner.py', 'runner.py', 'closed_seal.py',
               'audit.py', 'spec.py', 'operation_provenance.py', 'measurement_dispute.py', 'process_generation.py', 'technical_adjudication.py')


def sha(path):
    return hashlib.sha256(ar.audit.check_links(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(ar.audit.check_links(path).read_text(encoding='utf-8'))


def write(path, value):
    path = ar.audit.check_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def under(root, name):
    path = ar.audit.check_links(root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Capsule path escaped its root')
    return path


def local_sources():
    root = Path(__file__).resolve().parent
    return {name: sha(root / name) for name in LOCAL_FILES}


def check_bundle(bundle, expected):
    if set(expected) != set(BUNDLE_FILES) or {name: sha(bundle / name) for name in BUNDLE_FILES} != expected:
        raise ValueError('Use the explicitly frozen full colocated consistency validator dependency bundle')


def build_pack(bundle, expected, reports, target):
    check_bundle(bundle, expected)
    command = [sys.executable, '-X', 'utf8', str(bundle / 'consistency.py'), '--reports', *map(str, reports), '--out', str(target)]
    result = subprocess.run(command, capture_output=True, timeout=300)
    if result.returncode:
        raise ValueError('Frozen consistency validator did not accept all six complete audits and provenance prerequisites')
    check_bundle(bundle, expected)
    pack = read(target / 'consistency.json')
    if (len(pack.get('rows', [])) != 100 or len(pack.get('candidates', [])) != 6
            or any(c.get('workspace_mode') != 'derived_operations_v1' for c in pack['candidates'])
            or [row.get('id') for row in pack['rows']] != [f'{n:03}' for n in range(1, 101)]
            or any(len(row.get('candidates', [])) != 6 for row in pack['rows'])):
        raise ValueError('Expected one complete derived 100 by 6 review pack')
    return pack


def prepare(reports, bundle, bundle_hashes, out):
    """Only synthetic tests should call this before six real audits are done."""
    reports = [ar.audit.check_links(p).resolve() for p in reports]
    if len(reports) != 6 or len(set(reports)) != 6 or any(p.name != 'audit.json' for p in reports):
        raise ValueError('Exactly six distinct complete audit.json files required')
    if out.exists() or any(out.resolve().is_relative_to(p.parent) or p.parent.is_relative_to(out.resolve()) for p in reports):
        raise ValueError('New output must be disjoint from every input audit')
    report_hashes = {str(p): sha(p) for p in reports}
    if not out.parent.is_dir():
        raise ValueError('Explicit existing judge output parent required')
    with tempfile.TemporaryDirectory(prefix='b1-horizontal-', dir=out.parent) as temporary:
        generated = Path(temporary) / 'pack'
        pack = build_pack(bundle, bundle_hashes, reports, generated)
        by_opaque = {read(path)['opaque_id']: path for path in reports}
        if set(by_opaque) != {c['opaque_id'] for c in pack['candidates']}:
            raise ValueError('Frozen pack candidate identities disagree with validated reports')
        sources = {}
        for opaque, path in by_opaque.items():
            report = read(path)
            if next(c for c in pack['candidates'] if c['opaque_id'] == opaque).get('technical_adjudication'):
                technical = ar.audit.technical_adjudication_module().resolve(path, {f'{i:03}' for i in range(1,101)}, action='horizontal')
                for ref in [technical['receipt_ref'], *technical['evidence_refs']]:
                    source = ar.audit.technical_adjudication_module().reference(path.parent.parent, ref)
                    sources.setdefault(opaque + ':' + ref['sha256'], source)
            for check in report['checks']:
                records = [check, check.get('review'), check.get('adjudication')]
                if check.get('id') == '057' and check.get('status') == 'review_required' and check.get('score') is None:
                    accepted = ar.audit.measurement_dispute_module().resolve(path, report, check)
                    records.extend([accepted['decision'], {'evidence': [
                        {'path': 'measurement-dispute.json', 'sha256': accepted['overlay_sha256']}]}])
                for record in records:
                    if record is None:
                        continue
                    for ref in record['evidence']:
                        source = Path(ref['path'])
                        source = source if source.is_absolute() else path.parent / 'evidence' / source
                        source = ar.audit.check_links(source).resolve()
                        if not source.is_relative_to((path.parent / 'evidence').resolve()) or sha(source) != ref['sha256']:
                            raise ValueError('Actual evidence escaped or changed')
                        sources.setdefault(opaque + ':' + ref['sha256'], source)
        if set(sources) != {item['id'] for item in pack['evidence_index']}:
            raise ValueError('Every frozen evidence index needs its actual original bytes')
        if {str(p): sha(p) for p in reports} != report_hashes:
            raise ValueError('Input report changed while collecting evidence')
        out.mkdir()
        private = out / 'private'
        private.mkdir()
        shutil.copytree(generated, private / 'consistency-002')
        artifacts = []
        for number, (evidence_id, source) in enumerate(sorted(sources.items()), 1):
            name = f'artifact-{number:05}'
            relative = f'private/originals/{name}'
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            digest = sha(destination)
            if digest != evidence_id.rsplit(':', 1)[1] or sha(source) != digest:
                raise ValueError('Evidence copy changed; retain incomplete output for review')
            artifacts.append({'artifact_id': name, 'evidence_id': evidence_id, 'original_sha256': digest,
                              'original_file': relative, 'bytes': destination.stat().st_size,
                              'format_hint': source.suffix, 'status': 'pending_per_artifact_blinding_review'})
        manifest = {'schema': 'b1-horizontal-preparation-v1', 'status': 'blocked_pending_blinding',
                    'bundle': str(bundle.resolve()), 'bundle_hashes': bundle_hashes,
                    'reports': report_hashes, 'local_sources': local_sources(),
                    'pack_sha256': sha(private / 'consistency-002/consistency.json'),
                    'artifacts': artifacts, 'reviewer_id': uuid.uuid4().hex,
                    'warning': 'Raw evidence and narratives are PRIVATE. No automatic anonymity or consistency claim.'}
        write(private / 'manifest.json', manifest)
        template = {'schema': 'b1-horizontal-blinding-v1', 'root_authorized': False, 'independent': False,
                    'basis': '', 'reviewer_id': '', 'actual_review_evidence': [],
                    'pack_sha256': manifest['pack_sha256'], 'reviewed_pack_file': '', 'reviewed_pack_sha256': '',
                    'artifacts': [{**item, 'approved': False, 'provided_file': '', 'provided_sha256': '',
                                   'redaction_basis': '', 'actual_review_evidence': []} for item in artifacts]}
        write(private / 'blinding-template.json', template)
        return {'status': manifest['status'], 'evidence_count': len(artifacts), 'requirements': 100, 'candidates': 6}


def check_preparation(out):
    manifest = read(out / 'private/manifest.json')
    if local_sources() != manifest['local_sources']:
        raise ValueError('Horizontal orchestration sources changed; freeze a new version')
    check_bundle(Path(manifest['bundle']), manifest['bundle_hashes'])
    if sha(out / 'private/consistency-002/consistency.json') != manifest['pack_sha256']:
        raise ValueError('Frozen private comparison pack changed')
    for name, digest in manifest['reports'].items():
        if sha(Path(name)) != digest:
            raise ValueError('Original completed report changed')
    for item in manifest['artifacts']:
        if sha(under(out, item['original_file'])) != item['original_sha256']:
            raise ValueError('Private actual evidence bytes changed')
    return manifest


def evidence_receipts(out, refs):
    if not isinstance(refs, list) or not refs:
        raise ValueError('Actual per-artifact independent blinding review evidence required')
    for ref in refs:
        if sha(under(out, ref['path'])) != ref['sha256']:
            raise ValueError('Actual blinding review evidence changed')


def stage(out, approval, approval_sha):
    """Explicit ROOT release; JSON alone never automatically proves blinding."""
    manifest = check_preparation(out)
    if sha(approval) != approval_sha:
        raise ValueError('Root blinding release authority changed')
    review = read(approval)
    if (review.get('schema') != 'b1-horizontal-blinding-v1' or review.get('root_authorized') is not True
            or review.get('independent') is not True or not review.get('basis') or not review.get('reviewer_id')
            or review['reviewer_id'] == manifest['reviewer_id'] or review.get('pack_sha256') != manifest['pack_sha256']):
        raise ValueError('Explicit root and independent per-artifact/narrative review approval required')
    evidence_receipts(out, review.get('actual_review_evidence'))
    supplied = review.get('artifacts', [])
    if len(supplied) != len(manifest['artifacts']) or {x['artifact_id'] for x in supplied} != {x['artifact_id'] for x in manifest['artifacts']}:
        raise ValueError('Blinding decisions must cover every actual evidence artifact exactly once')
    original = {x['artifact_id']: x for x in manifest['artifacts']}
    for decision in supplied:
        item = original[decision['artifact_id']]
        if (decision.get('approved') is not True or decision.get('original_sha256') != item['original_sha256']
                or decision.get('evidence_id') != item['evidence_id'] or not decision.get('redaction_basis')
                or sha(under(out, decision['provided_file'])) != decision['provided_sha256']):
            raise ValueError('An actual artifact is missing its approved original-to-provided byte mapping')
        evidence_receipts(out, decision.get('actual_review_evidence'))
    pack_path = under(out, review['reviewed_pack_file'])
    if sha(pack_path) != review['reviewed_pack_sha256']:
        raise ValueError('Reviewed narratives changed')
    pack = read(pack_path)
    original_pack = read(out / 'private/consistency-002/consistency.json')
    # Only narrative text may be blinded. Scores, source hashes, requirements,
    # candidate identities and evidence relationships stay exactly original.
    def structural(value):
        if isinstance(value, list):
            return [structural(x) for x in value]
        if isinstance(value, dict):
            narrative = {'operation', 'expected', 'actual', 'review_reason', 'narrative_notice', 'redactions_applied'}
            return {key: structural(item) for key, item in value.items() if key not in narrative}
        return value
    if structural(pack) != structural(original_pack):
        raise ValueError('Blinding changed a requirement, decision, identity, source hash or evidence relationship')
    public = out / 'capsule'
    if public.exists():
        raise ValueError('Never overwrite a previously staged capsule')
    public.mkdir()
    shutil.copyfile(pack_path, public / 'consistency.json')
    if sha(public / 'consistency.json') != review['reviewed_pack_sha256']:
        raise ValueError('Reviewed narratives changed during capsule copy')
    entries = []
    for decision in supplied:
        item = original[decision['artifact_id']]
        name = 'evidence/' + item['artifact_id'] + item['format_hint']
        destination = public / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(under(out, decision['provided_file']), destination)
        if sha(destination) != decision['provided_sha256']:
            raise ValueError('Approved capsule copy changed')
        entries.append({'artifact_id': item['artifact_id'], 'evidence_id': item['evidence_id'], 'path': name,
                        'original_sha256': item['original_sha256'], 'provided_sha256': sha(destination),
                        'redacted': item['original_sha256'] != sha(destination)})
    public_manifest = {'schema': 'b1-horizontal-capsule-v1', 'reviewer_id': manifest['reviewer_id'],
                       'pack_sha256': sha(public / 'consistency.json'), 'artifacts': entries,
                       'blinding_basis': 'Explicit root release and independent per-artifact review; no automatic anonymity guarantee.'}
    write(public / 'capsule.json', public_manifest)
    check_preparation(out)
    if sha(approval) != approval_sha:
        raise ValueError('Root release authority changed during staging')
    write(out / 'private/release.json', {'authority_path': str(approval.resolve()), 'authority_sha256': approval_sha,
                                      'capsule_sha256': sha(public / 'capsule.json')})
    return {'status': 'staged', 'artifacts': len(entries)}


def check_capsule(out):
    check_preparation(out)
    release = read(out / 'private/release.json')
    if sha(Path(release['authority_path'])) != release['authority_sha256']:
        raise ValueError('Root release bytes changed')
    root = out / 'capsule'
    if sha(root / 'capsule.json') != release['capsule_sha256']:
        raise ValueError('Capsule source anchor changed')
    capsule = read(root / 'capsule.json')
    if sha(root / 'consistency.json') != capsule['pack_sha256']:
        raise ValueError('Reviewed requirement pack changed')
    for item in capsule['artifacts']:
        if sha(under(root, item['path'])) != item['provided_sha256']:
            raise ValueError('Actual released evidence changed')
    return capsule


def validate_result(out, result_path):
    capsule = check_capsule(out)
    report = read(result_path)
    pack = read(out / 'capsule/consistency.json')
    candidates = {x['opaque_id'] for x in pack['candidates']}
    if (report.get('schema') != 'b1-horizontal-result-v1' or report.get('reviewer_id') != capsule['reviewer_id']
            or report.get('pack_sha256') != capsule['pack_sha256']):
        raise ValueError('Horizontal reviewer and released input hash must match')
    rows = report.get('checks', [])
    if len(rows) != 100 or {x.get('id') for x in rows} != {f'{n:03}' for n in range(1, 101)}:
        raise ValueError('Exactly all 100 original IDs must be reviewed once')
    by_id = {x['artifact_id']: x for x in capsule['artifacts']}
    indexed = {x['id']: x for x in pack['rows']}
    for row in rows:
        if row.get('status') not in ('consistent', 'remeasurement_required') or not row.get('basis'):
            raise ValueError('Every requirement needs a consistency decision and evidence-based reason')
        seen = row.get('candidates', [])
        if len(seen) != 6 or {x.get('opaque_id') for x in seen} != candidates:
            raise ValueError('Each requirement must examine all six candidates')
        expected = {x['opaque_id']: x for x in indexed[row['id']]['candidates']}
        for item in seen:
            refs = item.get('evidence_examined', [])
            if not refs or not item.get('actual_evidence_observation'):
                raise ValueError('An index or summary alone cannot prove actual evidence examination')
            allowed = expected[item['opaque_id']]['evidence_refs'][:]
            for field in ('review', 'adjudication'):
                if expected[item['opaque_id']].get(field):
                    allowed += expected[item['opaque_id']][field]['evidence_refs']
            for ref in refs:
                artifact = by_id.get(ref.get('artifact_id'))
                if (artifact is None or artifact['evidence_id'] not in allowed
                        or ref.get('provided_sha256') != artifact['provided_sha256'] or not ref.get('observation_locator')):
                    raise ValueError('Actual evidence reference/hash/line-page-time locator does not match this requirement')
            if {by_id[ref['artifact_id']]['evidence_id'] for ref in refs} != set(allowed):
                raise ValueError('Every original/review/adjudication evidence artifact for this candidate and requirement must be examined')
    return {'status': 'schema_valid_pending_actual_trace_review',
            'consistency_claim': 'consistent' if all(x['status'] == 'consistent' for x in rows) else 'remeasurement_required',
            'requirements': 100, 'candidate_examinations': 600, 'scores_changed': False,
            'limitation': 'Schema/hash/locator checks do not prove the agent actually inspected every artifact; independently examine actual tool traces before accepting.'}


def run(out, cli, auth_home, *, resume=False, timeout=21600):
    out = out.resolve()
    with ar.lock(out / 'private'):
        return _run(out, cli, auth_home, resume=resume, timeout=timeout)


def verify_trajectory(out, home, state, helper):
    models = ar.model_metadata(home)
    if state['session_id'] not in models:
        raise ValueError('Actual model contexts do not contain the horizontal root session owner')
    native = sorted((out / 'private').glob('turn-*.jsonl'))
    if {path.name for path in native} != {f'turn-{n}.jsonl' for n in range(1, state['attempts'] + 1)}:
        raise ValueError('Every actual horizontal native attempt must remain present exactly once')
    helper.native_pairs(native)
    pairs = helper.rollout_pairs(sorted((home / 'sessions').rglob('*.jsonl')))
    if state['session_id'] not in pairs or set(pairs) != set(models):
        raise ValueError('Native/rollout/model actual session owners disagree')
    parents = {sid: record.get('attribution', {}).get('parent') for sid, record in pairs.items()}
    if {sid for sid, parent in parents.items() if parent is None} != {state['session_id']}:
        raise ValueError('Horizontal session must be the sole actual root, not a child or unrelated root')
    for sid in parents:
        cursor, visited = sid, set()
        while cursor != state['session_id']:
            if cursor in visited or cursor not in parents or parents[cursor] is None:
                raise ValueError('Every actual horizontal descendant must belong to its original root')
            visited.add(cursor)
            cursor = parents[cursor]
    if any(record['record_incomplete'] or not record['terminal_event_present'] for record in pairs.values()):
        raise ValueError('A horizontal root/descendant lacks an actual terminal event')
    return models, pairs


def snapshot_actual_traces(out, home, prefix, state):
    root = out / 'private'
    expected = [prefix.with_suffix('.jsonl'), prefix.with_suffix('.final.txt')]
    files = [path for path in expected if path.is_file()]
    files += sorted((home / 'sessions').rglob('*.jsonl'))
    result = out / 'capsule/horizontal-result.json'
    if result.is_file():
        files.append(result)
    folder = root / 'trace-snapshots' / str(state['attempts'])
    folder.mkdir(parents=True, exist_ok=False)
    entries = []
    for index, source in enumerate(files):
        data = ar.audit.check_links(source).read_bytes()
        target = folder / f'{index:05}.bytes'
        target.write_bytes(data)
        if sha(source) != sha(target):
            raise ValueError('Actual horizontal trace changed during snapshot')
        entries.append({'source_relative': source.relative_to(out).as_posix(),
                        'snapshot_relative': target.relative_to(out).as_posix(), 'sha256': sha(target)})
    receipt = {'attempt': state['attempts'], 'session_id': state['session_id'], 'files': entries,
               'missing_sources': [path.relative_to(out).as_posix() for path in expected if not path.is_file()]}
    write(folder / 'manifest.json', receipt)
    return {'path': (folder / 'manifest.json').relative_to(out).as_posix(),
            'sha256': sha(folder / 'manifest.json'), 'receipt': receipt}


def verify_trace_history(out, state):
    manifests = state.get('trace_manifests', [])
    if len(manifests) != state['attempts']:
        raise ValueError('Every previous horizontal attempt needs its original trace snapshot')
    for number, anchored in enumerate(manifests, 1):
        path = under(out, anchored['path'])
        if sha(path) != anchored['sha256'] or read(path) != anchored['receipt']:
            raise ValueError('Controller original trace snapshot anchor changed')
        receipt = anchored['receipt']
        if receipt['attempt'] != number or receipt['session_id'] != state['session_id']:
            raise ValueError('Historical horizontal trace owner/attempt changed')
        for item in receipt['files']:
            snapshot = under(out, item['snapshot_relative'])
            if sha(snapshot) != item['sha256']:
                raise ValueError('Historical actual trace bytes changed')
            name = item['source_relative']
            if name.startswith('private/turn-') or name.startswith('private/home/sessions/'):
                original = under(out, name).read_bytes()
                data = snapshot.read_bytes()
                if (name.startswith('private/turn-') and original != data
                        or name.startswith('private/home/sessions/') and not original.startswith(data)):
                    raise ValueError('Historical native/final bytes or own session log prefix changed')


def _run(out, cli, auth_home, *, resume=False, timeout=21600):
    capsule = check_capsule(out)
    manifest = read(out / 'private/manifest.json')
    # Re-run frozen complete audit/provenance validation immediately before any model.
    with tempfile.TemporaryDirectory(prefix='b1-horizontal-recheck-', dir=out / 'private') as temporary:
        current = build_pack(Path(manifest['bundle']), manifest['bundle_hashes'], list(map(Path, manifest['reports'])), Path(temporary) / 'pack')
        if current != read(out / 'private/consistency-002/consistency.json'):
            raise ValueError('Audit prerequisites changed after blinding review')
    state_path = out / 'private/run-state.json'
    state = read(state_path) if state_path.exists() else {'attempts': 0, 'session_id': None, 'status': 'ready'}
    if (state['attempts'] and not resume) or (resume and not state.get('session_id')) or state['attempts'] >= 4:
        raise ValueError('Explicit same-session resume only, maximum four review turns')
    verify_trace_history(out, state)
    home = out / 'private/home'
    if not home.exists():
        home.mkdir()
        for name in ('auth.json', 'models_cache.json'):
            if (auth_home / name).is_file():
                shutil.copyfile(auth_home / name, home / name)
                os.chmod(home / name, 0o600)
        (home / 'role.toml').write_text('model="gpt-6.1-sol"\nmodel_reasoning_effort="high"\n', encoding='utf-8')
    state['attempts'] += 1
    state.update(status='running', model=MODEL, effort=EFFORT)
    write(state_path, state)
    prefix = out / 'private' / f'turn-{state["attempts"]}'
    command = [str(cli), '--no-daemon', 'exec']
    if state['session_id']:
        command += ['resume', state['session_id']]
    command += ['--ignore-user-config', '--ignore-rules', '--strict-config', '--json', '--model', MODEL,
                '--dangerously-bypass-approvals-and-sandbox', '--output-last-message', str(prefix.with_suffix('.final.txt'))]
    if not state['session_id']:
        command += ['--skip-git-repo-check', '--cd', str((out / 'capsule').resolve())]
    configs = {'model_reasoning_effort': EFFORT, 'features.multi_agent': True,
               'agents.max_concurrent_threads_per_session': 2, 'agents.max_depth': 20,
               'agents.default_subagent_reasoning_effort': EFFORT}
    for role in ('default', 'worker', 'explorer'):
        configs[f'agents.{role}.config_file'] = str((home / 'role.toml').resolve())
    for key, value in configs.items():
        command += ['-c', key + '=' + json.dumps(value)]
    command += ['-']
    env = os.environ.copy()
    for name in ('CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID'):
        env.pop(name, None)
    env.update(CODEX_HOME=str(home.resolve()), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    prompt = ('You are the independent B1 horizontal consistency reviewer ' + capsule['reviewer_id'] +
        '. Inspect ALL 100 IDs across ALL six anonymous candidates using consistency.json, capsule.json and ACTUAL supplied '
        'evidence bytes, including screenshots/audio/logs; indexes or primary summaries alone do not prove behavior. '
        'Read every referenced artifact, and give a precise line/page/time locator for each observation. '
        'Do not infer identity, read parent/private directories, allocations, development logs, costs or workflow conditions. '
        'Only this capsule and own isolated home are authorized. Do not start Unity, send external messages, use desktop '
        'mouse/keyboard/foreground/focus APIs, repair games, change input files, requirements, grades or audit reports. '
        'Use exact gpt-6.1-sol/high for all descendants; maximum two concurrent descendants. '
        'Write horizontal-result.json schema b1-horizontal-result-v1, reviewer_id, pack_sha256 and checks:100 unique '
        'objects {id,status:consistent|remeasurement_required,basis,candidates:[six objects {opaque_id,'
        'actual_evidence_observation,evidence_examined:[{artifact_id,provided_sha256,observation_locator}]}]}. '
        'Use remeasurement_required when evidence is inadequate or conflicting; never invent measurements. '
        'Disagreement does not itself prove unequal standards. No automatic score changes. Final answer ends this turn.')
    if resume:
        prompt += '\nContinue same review. Resolve exact validation error from prior turn: ' + state.get('error', '')
    proc = subprocess.Popen(command, cwd=out / 'capsule', env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tree = ar.runner.ProcessTree(proc)
    state.update(cli_pid=proc.pid, process_tree_stopped=False)
    write(state_path, state)
    timed_out = False
    try:
        stdout, _ = proc.communicate(prompt.encode(), timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        tree.stop()
        stdout, _ = proc.communicate(timeout=15)
    finally:
        tree.stop()
        state.update(process_tree_stopped=True, timed_out=timed_out)
    prefix.with_suffix('.jsonl').write_bytes(stdout)
    try:
        helper = ar.closed_helper(manifest['local_sources']['closed_seal.py'])
        identities = {event['thread_id'] for event in helper.json_events(prefix.with_suffix('.jsonl'), allow_diagnostics=True) if event.get('type') == 'thread.started'}
        if len(identities) != 1 or state['session_id'] and identities != {state['session_id']}:
            raise ValueError('Original horizontal session changed or is missing')
        state['session_id'] = next(iter(identities))
        write(state_path, state)
        if timed_out or proc.returncode:
            raise ValueError('Horizontal CLI timed out or failed; preserve original session and require explicit resume')
        # Reuse verified full-history model and strict original-final checkpoint techniques.
        scratch = out / 'private/checkpoint'
        (scratch / 'blind').mkdir(parents=True, exist_ok=True)
        write(scratch / 'blind/manifest.json', {'workspace_mode': 'horizontal_evidence_only'})
        target = scratch / 'private/horizontal'
        target.mkdir(parents=True, exist_ok=True)
        for suffix in ('.jsonl', '.final.txt'):
            shutil.copyfile(prefix.with_suffix(suffix), (target / prefix.name).with_suffix(suffix))
        checkpoint = ar.completed_turn_checkpoint(scratch, 'horizontal', state)
        if not checkpoint:
            raise ValueError('Actual completed horizontal turn/final bytes are unverified')
        state['completed_turn_checkpoint'] = checkpoint
        state['model_evidence'], state['actual_tool_pair_evidence'] = verify_trajectory(out, home, state, helper)
        check_capsule(out)
        state['result'] = validate_result(out, out / 'capsule/horizontal-result.json')
        state['status'] = 'pending_independent_actual_trace_review'
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        state.update(status='paused', error=str(exc))
    state.setdefault('trace_manifests', []).append(snapshot_actual_traces(out, home, prefix, state))
    write(state_path, state)
    return {key: state[key] for key in ('status', 'attempts', 'session_id')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--reports', nargs=6, required=True, type=Path)
    p.add_argument('--bundle', required=True, type=Path)
    p.add_argument('--bundle-hashes', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p = sub.add_parser('stage')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--approval', required=True, type=Path)
    p.add_argument('--approval-sha256', required=True)
    p = sub.add_parser('validate-result')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--result', required=True, type=Path)
    p = sub.add_parser('run')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--cli', required=True, type=Path)
    p.add_argument('--auth-home', required=True, type=Path)
    p.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        result = prepare(args.reports, args.bundle, read(args.bundle_hashes), args.out)
    elif args.action == 'stage':
        result = stage(args.out, args.approval, args.approval_sha256)
    elif args.action == 'validate-result':
        result = validate_result(args.out, args.result)
    else:
        result = run(args.out, args.cli, args.auth_home, resume=args.resume)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
