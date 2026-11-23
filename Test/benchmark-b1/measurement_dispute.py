"""Explicit evidence-only resolution of a measured, unresolved B1 decision.

Never edits audit.json or supplies a default score. A trusted judge authorizes
one exact dispute; a real independent reviewer decides it; a separate actual
trace review must precede acceptance. Legacy reports need no sidecar.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
import subprocess
import stat
from pathlib import Path
import sys
import uuid

CHECK_ID = '057'
OVERLAY = 'measurement-dispute.json'
RECEIPT = 'judge_measurement_dispute.json'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def api():
    try:
        from . import audit
    except ImportError:
        import audit
    return audit


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Ambiguous dispute JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Non-standard dispute JSON constant')
    return json.loads(api().check_links(path).read_bytes(),
                      object_pairs_hook=pairs, parse_constant=constant)


def ref(root, record):
    require(isinstance(record, dict) and set(record) >= {'path', 'sha256'},
            'Dispute reference requires path/SHA')
    p = Path(record['path'])
    require(not p.is_absolute(), 'Dispute controller references must be relative')
    p = api().check_links(root / p).resolve()
    require(p.is_relative_to(root.resolve()) and p.is_file()
            and api().sha256(p) == record['sha256'], 'Dispute source reference changed/escaped')
    return p


def original_dispute(report, check, evidence_root):
    """This is deliberately not a general exemption for unmeasured/null items."""
    a = api()
    require(check.get('id') == CHECK_ID and check.get('status') == 'review_required'
            and check.get('score') is None, 'Not the explicitly measured unresolved057 state')
    require(check.get('candidate_hash') == report.get('candidate_hash')
            and check.get('review_required') is False
            and check.get('review') is None and check.get('adjudication') is None,
            'Dispute original identity/review state changed')
    require(all(isinstance(check.get(k), str) and check[k].strip()
                for k in ('operation', 'expected', 'actual')), 'Blank measurement is not a dispute')
    a.evidence_records(check.get('evidence'), evidence_root)
    require(check.get('expected') == next(c['title'] for c in a.spec.CHECKS if c['id'] == CHECK_ID),
            'Dispute expected requirement differs from frozen B1')


def eligible(report_path, authority, primary_checkpoint, journal_head, *, evidence_root=None):
    """Read-only gate for a technical-source review or an independent decision."""
    a = api(); report_path = a.check_links(report_path).resolve()
    evidence_root = Path(evidence_root) if evidence_root is not None else report_path.parent / 'evidence'
    report = read(report_path); checks = report.get('checks')
    require(isinstance(checks, list) and len(checks) == 100
            and {c.get('id') for c in checks} == {c['id'] for c in a.spec.CHECKS},
            'Dispute needs exact100 IDs')
    require(authority.get('schema') == 'b1-measurement-dispute-authority-v1'
            and authority.get('check_id') == CHECK_ID
            and authority.get('report_sha256') == a.sha256(report_path)
            and authority.get('candidate_hash') == report.get('candidate_hash')
            and authority.get('opaque_id') == report.get('opaque_id')
            and authority.get('primary_checkpoint') == primary_checkpoint
            and authority.get('journal_head') == journal_head
            and authority.get('actual_measured_dispute') is True
            and authority.get('basis'), 'Exact independently classified dispute authority required')
    target = next(c for c in checks if c['id'] == CHECK_ID)
    original_dispute(report, target, evidence_root)
    require(authority.get('original_check_sha256') == digest(target)
            and authority.get('original_evidence') == target['evidence'],
            'Dispute authority changed original check/evidence')
    classification = authority.get('classification_review')
    classified = read(ref(evidence_root.parent.parent, classification))
    require(classified.get('schema') == 'b1-measurement-dispute-classification-v1'
            and classified.get('check_id') == CHECK_ID and classified.get('actual_measured_dispute') is True
            and classified.get('report_sha256') == authority['report_sha256']
            and classified.get('original_check_sha256') == digest(target)
            and classified.get('primary_checkpoint') == primary_checkpoint
            and classified.get('reviewer_id') and classified['reviewer_id'] != report['auditor']['id'],
            'Independent actual measured-dispute classification missing')
    for check in checks:
        if check is target:
            continue
        require(check.get('status') == 'verified' and type(check.get('score')) is int
                and check['score'] in (0, 1), 'Other99 retain strict completed-measurement gate')
        require(all(isinstance(check.get(k), str) and check[k].strip()
                    for k in ('operation', 'expected', 'actual')), 'Other99 measurement incomplete')
        a.evidence_records(check.get('evidence'), evidence_root)
    return report, target


def decision(overlay, report, check, reviewer):
    a = api()
    require(overlay.get('schema') == 'b1-measurement-dispute-decision-v1'
            and overlay.get('check_id') == CHECK_ID
            and overlay.get('candidate_hash') == report['candidate_hash']
            and overlay.get('opaque_id') == report['opaque_id']
            and overlay.get('original_check_sha256') == digest(check), 'Overlay original binding mismatch')
    value = overlay.get('decision')
    require(isinstance(value, dict) and value.get('auditor_id') == reviewer
            and reviewer != report['auditor']['id'] and value.get('status') == 'verified',
            'Overlay needs a distinct actual independent auditor decision')
    require(value.get('expected') == check['expected'], 'Overlay cannot change frozen expected requirement')
    require(all(isinstance(value.get(k), str) and value[k].strip()
                for k in ('operation', 'expected', 'actual')), 'Overlay requires real textual measurement/evidence reasoning')
    return value


def native_checkpoint(native, final, checkpoint):
    """Exact actual terminal/final/session bytes; no whitespace equivalence."""
    require(api().sha256(native) == checkpoint.get('native_sha256')
            and api().sha256(final) == checkpoint.get('final_sha256'), 'Dispute actual native/final SHA mismatch')
    identities, messages, last = set(), [], None
    try:
        events = list(execution_helper().json_events(native, allow_diagnostics=True, with_line=True))
    except RuntimeError as exc:
        raise ValueError('Malformed dispute actual native event') from exc
    for number, event in events:
        i = number - 1
        if event.get('type') == 'thread.started': identities.add(event.get('thread_id'))
        if event.get('type') in ('turn.started', 'turn.completed', 'turn.failed'): last = (event['type'], i)
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            require(isinstance(item.get('text'), str), 'Actual final must be text')
            messages.append((item['text'].encode('utf-8'), i))
    require(identities == {checkpoint['session_id']} and last and last[0] == 'turn.completed'
            and messages and messages[-1][1] < last[1] and messages[-1][0] == final.read_bytes()
            and final.read_bytes().strip(), 'Dispute actual completed-turn identity missing')


def resolve(report_path, report, check):
    out = api().check_links(report_path).resolve().parent.parent
    require(read(report_path) == report and next(c for c in report['checks'] if c['id'] == CHECK_ID) == check,
            'Dispute must validate the actual current report/check bytes')
    return _resolve(report_path, report, check, read(out / 'judge_manifest.json'))


def technical_prerequisite(report_path):
    """Explicit typed057 branch; ordinary canonical approval stays strict."""
    a = api(); report_path = Path(report_path); out = report_path.parent.parent
    judge = read(out / 'judge_manifest.json')
    if judge.get('technical_adjudication_acceptance'):
        return a.technical_adjudication_module().resolve(report_path, {CHECK_ID}, action='measurement_dispute')
    return a.provenance_module().validate_workspace(report_path.parent / 'candidate', report_path.parent / 'runtime',
        report_path.parent / 'evidence', read(report_path.parent / 'manifest.json')['operational_provenance'], require_review=True)


def technical_binding(chain):
    if not chain.get('technical_mode'):
        return None
    return {k: chain[k] for k in ('technical_mode', 'canonical_review_approved', 'canonical_reviewer_id',
        'canonical_review_sha256', 'technical_adjudicator_id', 'receipt_ref', 'verdict_ref', 'input_capsule_ref', 'participants_annex_ref')}


def _resolve(report_path, report, check, judge):
    """Validate a controller-anchored accepted overlay. No receipt => no bypass."""
    a = api(); report_path = a.check_links(report_path).resolve(); out = report_path.parent.parent
    original_dispute(report, check, report_path.parent / 'evidence')
    anchor = judge.get('measurement_dispute_acceptance')
    require(anchor and anchor.get('path') == RECEIPT, 'Missing controller acceptance anchor for unresolved measurement')
    receipt = read(ref(out, anchor))
    require(receipt.get('schema') == 'b1-measurement-dispute-acceptance-v1'
            and receipt.get('module_sha256') == a.sha256(Path(__file__))
            and receipt.get('execution_helper_sha256') == a.sha256(Path(execution_helper().__file__))
            and receipt.get('process_generation_sha256') == a.sha256(Path(generation_module().__file__))
            and receipt.get('check_id') == CHECK_ID
            and receipt.get('original_check_sha256') == digest(check)
            and receipt.get('candidate_hash') == report['candidate_hash'], 'Acceptance original/module binding mismatch')
    original_path = ref(out, receipt['original_report']); original = read(original_path)
    require(a.sha256(original_path) == receipt['report_sha256'], 'Original primary report snapshot changed')
    same_primary(original, report)
    authority_path = ref(out, receipt['authority']); authority = read(authority_path)
    eligible(original_path, authority, receipt['primary_checkpoint'], receipt['journal_head'],
             evidence_root=report_path.parent / 'evidence')
    require(receipt['primary_checkpoint']['session_id'] == authority['primary_checkpoint']['session_id'], 'Primary checkpoint binding mismatch')
    native_checkpoint(ref(out, receipt['primary_native']), ref(out, receipt['primary_final']), receipt['primary_checkpoint'])
    writer_inventory(out, authority)
    quiescence = read(ref(out, receipt['acceptance_quiescence']))
    require(quiescence.get('schema') == 'b1-dispute-quiescence-v1'
            and quiescence.get('module_sha256') == a.sha256(Path(__file__))
            and quiescence.get('writer_inventory_sha256') == authority['writer_inventory']['sha256']
            and len(quiescence.get('censuses', [])) == 2
            and all(c.get('complete') is True for c in quiescence['censuses'])
            and len(quiescence.get('verified', [])) == 2
            and all(c.get('no_assigned_editor') is True and c.get('no_known_writers') is True for c in quiescence['verified'])
            and len(quiescence.get('auxiliary_generation_checks', [])) == 2
            and all(c.get('no_live_historical_auxiliary_generations') is True for c in quiescence['auxiliary_generation_checks']),
            'Missing actual acceptance quiescence source proof')
    independent = receipt['independent']; reviewer = independent.get('auditor_id')
    require(reviewer and reviewer not in (report['auditor']['id'], receipt['provenance_reviewer_id'])
            and independent.get('model') == 'gpt-6.1-sol' and independent.get('effort') == 'high'
            and independent.get('session_id') == independent['checkpoint']['session_id']
            and independent.get('attempt') == independent['checkpoint']['attempt'], 'Actual dispute reviewer identity/model mismatch')
    models = read(ref(out, independent['model_evidence']))
    require(independent['session_id'] in models and models and all(rows and all(c == {'model': 'gpt-6.1-sol', 'effort': 'high'} for c in rows) for rows in models.values()), 'Missing/wrong own model evidence')
    native = ref(out, independent['native']); final = ref(out, independent['final'])
    native_checkpoint(native, final, independent['checkpoint'])
    trace_manifest = read(ref(out, independent['trace_manifest']))
    require(trace_manifest.get('role') == 'review' and trace_manifest.get('auditor_id') == reviewer
            and trace_manifest.get('session_id') == independent['session_id']
            and trace_manifest.get('turn') == independent['attempt'], 'Actual trace export owner mismatch')
    entries = trace_manifest.get('files', [])
    matching = [r for r in entries if r.get('source_file') == f"turn-{independent['attempt']}.jsonl"]
    require(len(matching) == 1 and matching[0].get('source_sha256') == a.sha256(native)
            and matching[0].get('exported_sha256') == a.sha256(native), 'Actual native is not anchored to same exported turn')
    actual_map = {r['path']: r['sha256'] for r in receipt['actual_trace_files']}
    expected_map = {independent['native']['path']: independent['native']['sha256'],
                    independent['final']['path']: independent['final']['sha256']}
    for entry in entries:
        name = entry.get('source_file', '')
        require(isinstance(name, str) and all(p not in ('', '.', '..') for p in name.split('/'))
                and ':' not in name and '\\' not in name
                and (name == f"turn-{independent['attempt']}.jsonl" or name in ('unity.jsonl', 'unity.native.stderr.log')
                     or name.startswith('home/sessions/') and name.endswith('.jsonl'))
                and entry.get('source_snapshot_file') == f"trace-snapshots/{independent['attempt']}/{name}"
                and entry.get('exported_file') == name, 'Actual trace source scope changed')
        require(entry.get('source_sha256') == entry.get('exported_sha256') and entry.get('redacted') is False,
                'Dispute original trace byte claim changed')
        expected_map['private/review/' + entry['source_snapshot_file']] = entry['source_sha256']
        expected_map[f"blind/evidence/tool-traces/review/{independent['attempt']}/" + entry['exported_file']] = entry['exported_sha256']
    require(actual_map == expected_map and len(actual_map) == len(receipt['actual_trace_files']),
            'Actual source/export trace coverage incomplete or ambiguous')
    for record in receipt['actual_trace_files']:
        ref(out, record)
    verify_trace_execution(out, independent, entries, models)
    trace_review = read(ref(out, receipt['actual_trace_review']))
    require(trace_review.get('schema') == 'b1-measurement-dispute-actual-review-v1'
            and trace_review.get('approved') is True and trace_review.get('reviewer_id')
            and trace_review['reviewer_id'] not in (reviewer, report['auditor']['id'])
            and trace_review.get('report_sha256') == receipt['report_sha256']
            and trace_review.get('decision_sha256') == receipt['overlay']['sha256']
            and trace_review.get('independent_checkpoint') == independent['checkpoint']
            and trace_review.get('trace_files') == receipt['actual_trace_files']
            and trace_review.get('writer_inventory_sha256') == authority['writer_inventory']['sha256']
            and trace_review.get('protected_evidence_sha256') == receipt['protected_evidence']['sha256']
            and trace_review.get('old_evidence_and_journal_unchanged') is True
            and trace_review.get('fresh_quiescence_verified') is True
            and trace_review.get('no_unity_calls_all_owners') is True
            and trace_review.get('no_runtime_or_save_writes') is True,
            'Independent actual source/trace/effects review is missing or stale')
    overlay_path = ref(report_path.parent / 'evidence', receipt['overlay']); overlay = read(overlay_path)
    value = decision(overlay, report, check, reviewer)
    score = a.decision(value, report['auditor']['id'], report_path.parent / 'evidence')
    require(overlay.get('report_sha256') == receipt['report_sha256']
            and overlay.get('journal_head') == receipt['journal_head'], 'Decision report/head mismatch')
    require(not (report_path.parent / 'evidence/provenance/active-operation.json').exists(), 'Active operation blocks accepted dispute')
    chain = technical_prerequisite(report_path)
    if chain.get('technical_mode'):
        require(receipt.get('technical_adjudication') == technical_binding(chain)
                and reviewer != chain['technical_adjudicator_id'], 'Dispute omitted or impersonated independent technical adjudicator')
        require(trace_review.get('technical_adjudication') == technical_binding(chain)
                and quiescence.get('technical_participants_annex_sha256') == chain['participants_annex_ref']['sha256'],
                'Actual dispute trace/fresh proof omitted technical participant annex')
    prefix = receipt['journal_operations']; ids = chain['operations']
    require(ids[:len(prefix)] == prefix and prefix, 'Accepted dispute operation prefix changed')
    last = read(report_path.parent / 'evidence/provenance/operations' / (prefix[-1] + '.json'))
    require(last['operation_sha256'] == receipt['journal_head']
            and chain['reviewer_id'] == receipt['provenance_reviewer_id'], 'Accepted operation head/provenance reviewer changed')
    protected = read(ref(out, receipt['protected_evidence']))
    require(set(receipt['protected_originals']) == {'provenance-review.json', 'provenance/journal-head.json'},
            'Both original provenance review/head byte snapshots are required')
    for name, record in receipt['protected_originals'].items():
        require(name in ('provenance-review.json', 'provenance/journal-head.json')
                and a.sha256(ref(out, record)) == protected[name], 'Original canonical/head bytes not preserved')
    allowed = ()
    if chain['journal_head'] != receipt['journal_head']:
        extension = read(ref(out, judge.get('measurement_dispute_head_extension', {})))
        require(extension.get('schema') == 'b1-measurement-dispute-head-extension-v1'
                and extension.get('accepted_receipt_sha256') == anchor['sha256']
                and extension.get('from_head') == receipt['journal_head']
                and extension.get('to_head') == chain['journal_head']
                and extension.get('new_operation_ids') == ids[len(prefix):]
                and extension.get('current_digest') == chain['current_digest']
                and extension.get('reference_digest') == chain['reference_digest']
                and extension.get('independently_reviewed') is True
                and extension.get('evidence_only_extension') is True
                and extension.get('old_measurement_evidence_preserved') is True
                and extension.get('reviewer_id')
                and extension['reviewer_id'] not in (reviewer, report['auditor']['id'], chain['reviewer_id']),
                'New journal head needs exact independent source/trace/effects extension approval')
        a.evidence_records(extension.get('evidence'), report_path.parent / 'evidence')
        allowed = ('provenance-review.json', 'provenance/journal-head.json')
    preserve_existing(report_path.parent / 'evidence', protected, allowed_changes=allowed)
    return {'score': score, 'decision': value, 'primary_status': check['status'], 'primary_score': None,
            'original_primary_report_sha256': receipt['report_sha256'],
            'overlay_sha256': receipt['overlay']['sha256'], 'acceptance_sha256': anchor['sha256'],
            'evidence': value['evidence'], 'basis': 'independent_measured_dispute_overlay'}


def execution_helper():
    try:
        from . import closed_seal
    except ImportError:
        import closed_seal
    require(Path(closed_seal.__file__).resolve().parent == Path(__file__).resolve().parent,
            'Dispute execution helper must be colocated')
    return closed_seal


def verify_trace_execution(out, independent, entries, models):
    h = execution_helper()
    sessions = [out / 'private/review' / entry['source_snapshot_file'] for entry in entries
                if entry['source_file'].startswith('home/sessions/')]
    require(sessions, 'No actual own model rollout evidence')
    nodes = h.rollout_own_streams(sessions)
    require(1 <= len(nodes) <= 3, 'This one-item dispute turn permits at most two descendants total')
    sid = independent['session_id']
    require({key for key, node in nodes.items() if node['parent'] is None} == {sid},
            'Dispute actual unique root/session identity mismatch')
    actual_models = {}
    for owner, node in nodes.items():
        for _, event in node['own']:
            if event['type'] != 'response_item':
                continue
            payload = event['payload']; name = payload.get('name', '')
            if payload.get('type') in ('function_call', 'custom_tool_call'):
                require(isinstance(name, str), 'Malformed actual own tool name')
                import re
                require(not re.match(r'(?i)(?:functions\.)?(?:mcp__unity__|mcp\.unity\.|unity\.)', name),
                        'Evidence-only dispute owner invoked Unity')
            require(not (payload.get('type') == 'mcp_tool_call' and payload.get('server') == 'unity'),
                    'Evidence-only dispute owner invoked Unity')
        contexts = [event['payload'] for _, event in node['own'] if event['type'] == 'turn_context']
        actual_models[owner] = [{'model': p.get('model'), 'effort': p.get('effort', p.get('reasoning_effort'))} for p in contexts]
    require(actual_models == models and all(rows for rows in actual_models.values()),
            'Actual own rollout model contexts differ from claimed evidence')
    # Generic exec/script calls also need the independent actual source/trace
    # review's all-owner no-Unity attestation; tool-name checks cannot prove it.
    paired = h.rollout_pairs(sessions)
    require(all(v['terminal_event_present'] and not v['record_incomplete'] and not v['pending']
                and not v['background_active'] for v in paired.values()), 'Dispute own execution still pending')
    native = ref(out, independent['native'])
    require(not h.native_pairs([native])['pending'], 'Dispute native tool execution still pending')
    for event in h.json_events(native, allow_diagnostics=True):
        item = event.get('item', {})
        require(not (item.get('type') == 'mcp_tool_call' and item.get('server') == 'unity'),
                'Evidence-only dispute invoked Unity')


def same_primary(original, current):
    """Only the already flagged067 may later append ordinary review metadata."""
    lhs, rhs = copy.deepcopy(original), copy.deepcopy(current)
    require(len(lhs.get('checks', [])) == len(rhs.get('checks', [])) == 100,
            'Primary report coverage changed after dispute')
    for old, new in zip(lhs['checks'], rhs['checks']):
        require(old.get('id') == new.get('id'), 'Primary check ordering changed')
        if old['id'] != '067':
            continue
        for field in ('review', 'adjudication'):
            if old.get(field) is None:
                if new.get(field) is not None:
                    require(old.get('review_required') is True and bool(old.get('review_reason')),
                            'Unflagged item cannot acquire a review through measurement overlay')
                new[field] = None
    require(lhs == rhs, 'Primary fields/original independent decisions changed after dispute')


def load_runtime(expected):
    """Actual execution only from a fully hashed, newly reviewed frozen bundle."""
    here = Path(__file__).absolute().parent; manifest = here / 'manifest.json'
    # No audit/runner imports before byte verification. lstat every ancestor;
    # reject junctions and dangling symlinks without following them.
    def local_bytes(path):
        path = Path(path).absolute()
        for part in [path, *path.parents]:
            info = part.lstat()
            require(not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, 'st_file_attributes', 0) & 0x400,
                    'Frozen dispute path contains reparse/link')
        return path.read_bytes()
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate runtime manifest key'); result[key] = value
        return result
    def constant(_): raise ValueError('NonJSON runtime manifest')
    raw = local_bytes(manifest)
    require(hashlib.sha256(raw).hexdigest() == expected, 'Dispute CLI requires exact frozen runtime manifest')
    files = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)['files']
    require(isinstance(files, dict), 'Runtime manifest files must be an object')
    required = {'measurement_dispute.py', 'audit.py', 'audit_runner.py', 'runner.py',
                'closed_seal.py', 'operation_provenance.py', 'spec.py', 'runtime.py',
                'process_generation.py', 'technical_adjudication.py', 'consistency.py', 'operation-policy.json'}
    require(required <= set(files), 'Runtime omits a dispute execution dependency')
    payloads = {}
    for name, entry in files.items():
        require(isinstance(name, str) and not Path(name).is_absolute()
                and all(p not in ('', '.', '..') for p in name.replace('\\', '/').split('/'))
                and ':' not in name, 'Runtime payload path escaped')
        payload = local_bytes(here / name)
        require(isinstance(entry, dict) and hashlib.sha256(payload).hexdigest() == entry.get('sha256')
                and type(entry.get('bytes')) is int and len(payload) == entry['bytes'],
                'Frozen runtime payload changed')
        payloads[name] = payload
    policy = json.loads(payloads['operation-policy.json'], object_pairs_hook=unique, parse_constant=constant)
    policy_name = policy.get('path')
    require(policy.get('version') == 1 and isinstance(policy_name, str)
            and policy_name in payloads
            and hashlib.sha256(payloads[policy_name]).hexdigest() == policy.get('sha256'),
            'Frozen operation policy target omitted or changed')
    here = here.resolve()  # Compare normalized Windows short/long paths only after lstat verification.
    names = ('spec', 'runner', 'operation_provenance', 'process_generation',
             'closed_seal', 'audit', 'technical_adjudication', 'consistency', 'audit_runner', 'runtime')
    for name in (*names, 'measurement_dispute'):
        if name in sys.modules:
            require(Path(sys.modules[name].__file__).resolve() == here / (name + '.py'), 'Cached runtime module is from another bundle')
    sys.path.insert(0, str(here))
    # Import only the verified source bytes. Normal import may execute an
    # unmanifested timestamp-valid .pyc even after its .py passed every hash.
    import types
    sys.modules['measurement_dispute'] = sys.modules[__name__]
    for name in names:
        module = types.ModuleType(name)
        module.__file__ = str(here / (name + '.py'))
        module.__package__ = ''
        sys.modules[name] = module
        exec(compile(payloads[name + '.py'], module.__file__, 'exec'), module.__dict__)
    audit_runner, runtime = sys.modules['audit_runner'], sys.modules['runtime']
    require(Path(audit_runner.__file__).resolve() == here / 'audit_runner.py', 'Loaded runner is not frozen colocated source')
    runtime.configure_environment()
    runtime.patch_audit(audit_runner)
    audit_runner._measurement_dispute_operation_policy = runtime.audit_operation_policy(here)
    require(audit_runner._measurement_dispute_operation_policy is not None, 'Explicit frozen user operation policy required')
    return audit_runner


def writer_inventory(out, authority):
    inventory = read(ref(out, authority['writer_inventory']))
    require(inventory.get('schema') == 'b1-dispute-known-writers-v1'
            and inventory.get('report_sha256') == authority['report_sha256']
            and inventory.get('primary_checkpoint') == authority['primary_checkpoint']
            and inventory.get('opaque_id') == authority['opaque_id'], 'Actual writer-generation inventory not bound')
    direct = inventory.get('direct_actor_pids'); aux = inventory.get('auxiliary_generations')
    require(isinstance(direct, list) and direct and all(type(p) is int and p > 0 for p in direct)
            and len(set(direct)) == len(direct) and isinstance(aux, list), 'Known direct actor inventory missing')
    require(isinstance(inventory.get('known_instance_ids'), list) and inventory['known_instance_ids'], 'Known native instance IDs missing')
    generations = {}
    for row in aux:
        pid = row.get('ProcessId'); require(type(pid) is int and pid > 0 and pid not in generations
                                          and pid not in direct, 'Auxiliary generation is duplicate/direct PID')
        generation_module().creation_ms(row.get('CreationDate')); generations[pid] = row
    require(inventory.get('source_effects_review'), 'Lossy/auxiliary writer source-effects review required')
    ref(out, inventory['source_effects_review'])
    return inventory, generations


def generation_module():
    try:
        from . import process_generation
    except ImportError:
        import process_generation
    require(Path(process_generation.__file__).resolve().parent == Path(__file__).resolve().parent,
            'Generation verifier must be the colocated shared source')
    return process_generation


def helper(ar):
    manifest = read(Path(ar.__file__).with_name('manifest.json'))
    return ar.closed_helper(manifest['files']['closed_seal.py']['sha256'])


def verify_actors(ar, h, out, case):
    ar.verify_exported_tool_traces(out, case)
    for role, state in ar.actual_participants(case).items():
        if role == 'review' and state.get('attempts') == 0 and state.get('session_id') is None:
            continue
        require(role in ('primary', 'review', 'provenance', 'technical_adjudication') and state.get('attempts'), 'Unknown/unexecuted actor')
        require(state.get('completed_turn_checkpoint')
                and ar.completed_turn_checkpoint(out, role, state) == state['completed_turn_checkpoint'],
                'Actual actor completed checkpoint missing')
        require(ar.model_metadata(out / 'private' / role / 'home') == state.get('model_evidence'),
                'Actual actor own model evidence changed')
        require(state['execution_attempts'][-1].get('process_tree_stopped') is True,
                'Actual actor process tree not stopped')
    native, sessions, _ = ar.closed_log_files(out, case)
    n, own = h.native_pairs(native), h.rollout_pairs(sessions)
    require(n['attempt_logs'] == sum(s['attempts'] for s in ar.actual_participants(case).values()) and not n['pending'],
            'Native execution incomplete; no implicit cancellation')
    require(all(v['terminal_event_present'] and not v['record_incomplete'] and not v['pending']
                and not v['background_active'] for v in own.values()), 'Known execution/background remains pending')
    # process_status_not_exported is not completion. The independent actual
    # review and fresh census remain mandatory even when pairs have no handle.
    return own


def scope(ar, h, out, case):
    saves = Path(os.environ['USERPROFILE']) / 'AppData/LocalLow/SJSBenchmark' / ('B1Audit-' + case['opaque_id'])
    return {'U': ar.audit.inventory(out / 'blind/candidate'),
            'R': ar.provenance_api().inventory(ar.runtime_project(out)),
            'saves': h.save_inventory(saves), 'logs': ar.closed_log_files(out, case)[2],
            'evidence': evidence_inventory(out / 'blind/evidence')}


def evidence_inventory(root):
    root = api().check_links(root).resolve(); result = {}
    for supplied in sorted(root.rglob('*')):
        path = api().check_links(supplied)
        if path.is_file(): result[path.relative_to(root).as_posix()] = api().sha256(path)
    return result


def preserve_existing(root, original, *, allowed_changes=()):
    current = evidence_inventory(root)
    require(all(name in current and (name in allowed_changes or current[name] == sha)
                for name, sha in original.items()), 'Independent dispute modified/deleted old evidence or journal')


def closed_seal_dispute_proof(ar, request, result):
    req = read(request); out = Path(req['out']); case = req['case']; h = helper(ar)
    require(req['module_sha256'] == api().sha256(Path(__file__)), 'Collector source identity changed')
    own = verify_actors(ar, h, out, case)
    inventory, generations = writer_inventory(out, req['authority'])
    actors = {e['cli_pid'] for s in ar.actual_participants(case).values() for e in s['execution_attempts']}
    actors.update(e['pid'] for e in case.get('root_lifecycle_attempts', []) if type(e.get('pid')) is int)
    if type(case.get('initial_editor_pid')) is int: actors.add(case['initial_editor_pid'])
    actors.update(inventory['direct_actor_pids'])
    technical = None
    if read(out / 'judge_manifest.json').get('technical_adjudication_acceptance'):
        technical = ar.technical_eligibility(out, case, {CHECK_ID}, action='measurement_dispute')
        ta = api().technical_adjudication_module()
        acceptance = read(ta.reference(out, technical['receipt_ref']))
        capsule = read(ta.reference(out, technical['input_capsule_ref']))
        _, appended, direct = ta.generation_annex(out, acceptance, capsule)
        actors.update(r['pid'] for r in direct)
        for record in appended:
            row = record['row']
            if row['ProcessId'] not in actors:
                generations[row['ProcessId']] = row
    require(not actors.intersection(generations), 'Auxiliary generation must not be a numeric direct-actor seed')
    x = scope(ar, h, out, case); c1 = h.collect_census()
    instances = sorted(set(inventory['known_instance_ids']) | {case['editor_identity']})
    v1 = h.verify_census(c1, ar.runtime_project(out), out / 'private', {}, own, instances, sorted(actors))
    aux1 = generation_module().verify_auxiliary_absence(c1, generations, set(v1['excluded_pids']))
    y = scope(ar, h, out, case); c2 = h.collect_census()
    v2 = h.verify_census(c2, ar.runtime_project(out), out / 'private', {}, own, instances, sorted(actors))
    aux2 = generation_module().verify_auxiliary_absence(c2, generations, set(v2['excluded_pids']))
    z = scope(ar, h, out, case)
    require(x == y == z, 'Dispute source/save/log inventory unstable')
    proof = {'schema': 'b1-dispute-quiescence-v1', 'module_sha256': req['module_sha256'],
                    'censuses': [c1, c2], 'verified': [v1, v2], 'stable_scope': x,
                    'tool_pairs': own, 'auxiliary_generation_checks': [aux1, aux2],
                    'writer_inventory_sha256': req['authority']['writer_inventory']['sha256'], 'normal_exit_not_inferred': True}
    if technical:
        proof['technical_participants_annex_sha256'] = technical['participants_annex_ref']['sha256']
    create(result, proof)


def create(path, value):
    path = api().check_links(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') + b'\n')


def once(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream: stream.write(content)


def fresh(ar, case, out, root, label, manifest_sha, authority):
    request, result = root / (label + '-request.json'), root / (label + '-proof.json')
    create(request, {'module_sha256': api().sha256(Path(__file__)), 'out': str(out), 'case': case, 'authority': authority})
    command = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()),
               '--closed_seal-dispute-proof', str(request), str(result), manifest_sha]
    proc = subprocess.run(command, capture_output=True, timeout=240)
    once(root / (label + '.stdout'), proc.stdout); once(root / (label + '.stderr'), proc.stderr)
    require(proc.returncode == 0 and result.is_file(), 'Fresh actual census/toolpairs/stability blocked')
    return result


def execute(ar, args):
    """No Unity launch or runtime calls. One real original review turn, then stop."""
    admin = api().check_links(args.admin).resolve()
    require(api().sha256(args.authority) == args.authority_sha256, 'Root dispute authority changed')
    authority = read(args.authority)
    with ar.lock(admin / 'audits'):
        controller = admin / 'audits/controller.json'; control = ar.read(controller)
        cases = [c for c in control['groups'].values() if c['opaque_id'] == args.opaque]
        require(len(cases) == 1, 'Opaque dispute case not unique'); case = cases[0]
        out = admin / 'audits/cases' / args.opaque; report_path = out / 'blind/audit.json'
        ar.all_sealed(admin); ar.guard_candidate(out)
        require(case.get('status') == 'paused' and not (out / 'blind/evidence/provenance/active-operation.json').exists(),
                'Dispute must start from a stopped inactive audit')
        h = helper(ar); verify_actors(ar, h, out, case)
        primary = case['roles']['primary']; cp = primary['completed_turn_checkpoint']
        chain = technical_prerequisite(report_path)
        require(chain['reviewer_id'] == case['roles']['provenance']['auditor_id'], 'Actual provenance approval missing')
        report, check = eligible(report_path, authority, cp, authority['journal_head'])
        require(chain['journal_head'] == authority['journal_head'], 'Actual operation head changed before dispute')
        root = out / 'private/measurement-dispute'
        persist = lambda: ar.save(controller, control)
        if args.command == 'run':
            state = case['roles'].get('review')
            require(state is None or state.get('attempts') == 0 and state.get('session_id') is None,
                    'Initial dispute uses unused original review0 only; no counter reset')
            require(not root.exists() and not (out / 'blind/evidence' / OVERLAY).exists(), 'Dispute stage already exists')
            root.mkdir(parents=True, exist_ok=False)
            once(root / 'report-original.json', report_path.read_bytes())
            once(root / 'authority.json', args.authority.read_bytes())
            create(root / 'primary-state.json', primary)
            protected = evidence_inventory(out / 'blind/evidence')
            create(root / 'protected-evidence.json', protected)
            for name in ('provenance-review.json', 'provenance/journal-head.json'):
                once(root / 'protected-originals' / name, (out / 'blind/evidence' / name).read_bytes())
            before = scope(ar, h, out, case); fresh(ar, case, out, root, 'before-review', args.runtime_manifest_sha256, authority)
            state = case['roles'].setdefault('review', {'attempts': 0, 'session_id': None, 'auditor_id': uuid.uuid4().hex})
            require(state['auditor_id'] not in (primary['auditor_id'], chain['reviewer_id'], chain.get('technical_adjudicator_id')), 'Dispute reviewer is not independent')
            state.setdefault('measurement_dispute_authorizations', []).append(
                {'authority_sha256': args.authority_sha256, 'report_sha256': api().sha256(report_path),
                 'check_id': CHECK_ID, 'journal_head': chain['journal_head'], 'reserved_attempt': 1})
            persist()  # Consume once before tool setup/model launch, including failures.
            prompt = ((out / 'blind/prompt.md').read_text(encoding='utf-8') + '\n'
                      'You are the distinct independent measurement-dispute reviewer, auditor_id=' + state['auditor_id'] +
                      '. Decide ONLY057 from its original actual evidence and frozen requirement. Primary left a measured dispute, not an established pass/fail. '
                      'Do not change audit.json, game, runtime, saves, old evidence or journal. No Unity launch, runtime tool, OS input or foreground operation. '
                      'Do not infer pass from source, default missing evidence to zero, or reuse primary conclusions as proof. '
                      'If evidence cannot settle the item, leave a blocked decision with score null and exact missing evidence. '
                      'Otherwise create evidence/' + OVERLAY + ' as schema b1-measurement-dispute-decision-v1, '
                      'candidate_hash,opaque_id,check_id=057,report_sha256,original_check_sha256,journal_head,decision '
                      '{auditor_id,status:verified,score:integer0or1,operation,expected:exact frozen057,actual,evidence:[{path,sha256}]}. '
                      'Use actual evidence and record how it supports the decision. Do not act on067 or any other item. '
                      'All descendants exact gpt-6.1-sol/high; this one-item evidence-only task permits at most two descendants TOTAL '
                      '(at most three actual session owners), as well as the existing concurrent team limit. '
                      + json.dumps({'report_sha256': api().sha256(report_path), 'original_check_sha256': digest(check),
                                    'journal_head': chain['journal_head'], 'candidate_hash': report['candidate_hash'], 'opaque_id': args.opaque})
                      + ar.measurement_restart_protocol() + '\nUser operation constraint SHA256: '
                      + ar._measurement_dispute_operation_policy['sha256'] + '\n'
                      + ar._measurement_dispute_operation_policy['text'])
            try:
                case.update(status='running', phase='measurement_dispute'); persist()
                ar.invoke(control, case, 'review', prompt, out, persist)
                require(api().sha256(report_path) == authority['report_sha256'], 'Reviewer edited primary report')
                verify_actors(ar, h, out, case); ar.guard_candidate(out)
                preserve_existing(out / 'blind/evidence', protected)
                after = scope(ar, h, out, case)
                require(all(before[k] == after[k] for k in ('U', 'R', 'saves')), 'Evidence-only dispute changed runtime/saves')
                fresh(ar, case, out, root, 'after-review', args.runtime_manifest_sha256, authority)
                value = read(out / 'blind/evidence' / OVERLAY)
                decision(value, report, check, state['auditor_id'])
                ar.audit.decision(value['decision'], primary['auditor_id'], out / 'blind/evidence')
                create(root / 'completed-review.json', {'state': state, 'primary_checkpoint': cp, 'journal_head': chain['journal_head'],
                        'journal_operations': chain['operations'], 'overlay_sha256': api().sha256(out / 'blind/evidence' / OVERLAY),
                        'before_scope': before, 'after_scope': after, 'accepted': False,
                        'pending_independent_actual_trace_review': True})
            finally:
                case.update(status='paused', phase='post_primary'); persist()
            return
        require(args.actual_review and args.actual_review_sha256
                and api().sha256(args.actual_review) == args.actual_review_sha256, 'Accept needs exact independent actual trace review')
        completed = read(root / 'completed-review.json'); state = case['roles']['review']
        require(completed['state'] == state and state['attempts'] == 1, 'Exact completed dispute turn changed before accept')
        require(api().sha256(report_path) == authority['report_sha256'], 'Primary changed before dispute acceptance')
        protected = read(root / 'protected-evidence.json')
        preserve_existing(out / 'blind/evidence', protected)
        accept_proof = fresh(ar, case, out, root, 'before-accept', args.runtime_manifest_sha256, authority)
        once(root / 'actual-trace-review.json', args.actual_review.read_bytes())
        create(root / 'actual-model-evidence.json', state['model_evidence'])
        manifest = state['exported_tool_trace_manifests'][-1]
        create(root / 'actual-export-manifest.json', manifest)
        def relative(path): return {'path': path.relative_to(out).as_posix(), 'sha256': api().sha256(path)}
        actual_files = []
        for entry in manifest['files']:
            actual_files.extend([relative(out / 'private/review' / entry['source_snapshot_file']),
                                 relative(out / 'blind/evidence/tool-traces/review/1' / entry['exported_file'])])
        native, final = out / 'private/review/turn-1.jsonl', out / 'private/review/turn-1.final.txt'
        actual_files.extend([relative(native), relative(final)])
        actual_files = list({r['path']: r for r in actual_files}.values())
        receipt = {'schema': 'b1-measurement-dispute-acceptance-v1', 'module_sha256': api().sha256(Path(__file__)),
                   'execution_helper_sha256': api().sha256(Path(h.__file__)),
                   'process_generation_sha256': api().sha256(Path(generation_module().__file__)),
                   'report_sha256': authority['report_sha256'], 'original_report': relative(root / 'report-original.json'),
                   'original_check_sha256': digest(check), 'candidate_hash': report['candidate_hash'], 'check_id': CHECK_ID,
                   'authority': relative(root / 'authority.json'), 'primary_checkpoint': cp,
                   'primary_native': relative(out / 'private/primary' / f"turn-{cp['attempt']}.jsonl"),
                   'primary_final': relative(out / 'private/primary' / f"turn-{cp['attempt']}.final.txt"),
                   'journal_head': chain['journal_head'], 'journal_operations': chain['operations'],
                   'provenance_reviewer_id': chain['reviewer_id'],
                   'independent': {'auditor_id': state['auditor_id'], 'session_id': state['session_id'], 'attempt': 1,
                       'model': 'gpt-6.1-sol', 'effort': 'high', 'checkpoint': state['completed_turn_checkpoint'],
                       'model_evidence': relative(root / 'actual-model-evidence.json'), 'trace_manifest': relative(root / 'actual-export-manifest.json'),
                       'native': relative(native), 'final': relative(final)},
                   'actual_trace_files': actual_files, 'actual_trace_review': relative(root / 'actual-trace-review.json'),
                   'acceptance_quiescence': relative(accept_proof),
                   'protected_evidence': relative(root / 'protected-evidence.json'),
                   'protected_originals': {name: relative(root / 'protected-originals' / name)
                       for name in ('provenance-review.json', 'provenance/journal-head.json')},
                   'overlay': {'path': OVERLAY, 'sha256': api().sha256(out / 'blind/evidence' / OVERLAY)}}
        if chain.get('technical_mode'):
            receipt['technical_adjudication'] = technical_binding(chain)
        # Check all semantics before publishing the trusted private judge anchor.
        target = out / RECEIPT; create(target, receipt)
        judge_path = out / 'judge_manifest.json'; original_judge = judge_path.read_bytes(); judge = read(judge_path)
        require('measurement_dispute_acceptance' not in judge, 'Acceptance anchor already exists')
        once(root / 'judge-manifest-before-acceptance.json', original_judge)
        judge['measurement_dispute_acceptance'] = relative(target)
        # Validate against a temporary in-memory anchor through the resolver is
        # handled below; any failure preserves evidence, but does not publish.
        validate_acceptance_contents(out, report_path, report, check, receipt, judge)
        ar.save(judge_path, judge)
        create(root / 'accepted.json', {'receipt': relative(target), 'actual_trace_review_sha256': args.actual_review_sha256,
                                      'primary_report_unchanged': api().sha256(report_path) == authority['report_sha256'],
                                      'no_business067_review_or_pipeline_started': True})


def validate_acceptance_contents(out, report_path, report, check, receipt, judge):
    """Validate prepublication without writing a speculative judge anchor."""
    # resolve accepts a caller-supplied trusted anchor solely for this local
    # prepublication check; ordinary callers must read judge_manifest.json.
    return _resolve(report_path, report, check, judge)


def main(argv=None):
    if argv is None and len(sys.argv) == 5 and sys.argv[1] == '--closed_seal-dispute-proof':
        ar = load_runtime(sys.argv[4]); closed_seal_dispute_proof(ar, Path(sys.argv[2]), Path(sys.argv[3])); return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'accept'])
    parser.add_argument('--admin', type=Path, required=True)
    parser.add_argument('--opaque', required=True)
    parser.add_argument('--authority', type=Path, required=True)
    parser.add_argument('--authority-sha256', required=True)
    parser.add_argument('--runtime-manifest-sha256', required=True)
    parser.add_argument('--actual-review', type=Path)
    parser.add_argument('--actual-review-sha256')
    args = parser.parse_args(argv)
    ar = load_runtime(args.runtime_manifest_sha256)
    execute(ar, args)


if __name__ == '__main__':
    main()
