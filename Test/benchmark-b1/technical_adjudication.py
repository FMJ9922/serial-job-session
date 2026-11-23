"""Append-only technical eligibility. No dispatch, acceptance writer or scoring.

Original provenance approval is never manufactured. A trusted judge anchor and
two actual, distinct completed evidence-only opinions are mandatory. This module
only reads already recorded evidence and returns explicit per-check eligibility.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone

ROLE = 'technical_adjudication'
MODEL, EFFORT = 'gpt-6.1-sol', 'high'
ANCHOR = 'technical_adjudication_acceptance'
MODE = 'independent_adjudicated_scope'
CASE = '2ff6778c650143b88e9f1f2805f256be'
CANDIDATE = '5050e3ffb88b9a7fcf60076dc082e3fea051dc4ea28f2d25213e634462bd9c8b'
ORIGINAL_REPORT = 'a5640757c13c1596bb67e1b3a4d4a0fc980a506662c3423aeac2993140359191'
JOURNAL_HEAD = '46b2e1518c0f4df6135cba044b97447842185167c575056ecd1e310aa5df6086'
PROTOCOL = '6e0ac6a4a2ce7dcabaf976a3859cbe54f0c107b12af2fe7be059bfc39576dbfe'
ORIGINAL_OPINIONS = ['8aa0f7d40c938646fbe9bdb76b59ea4f60aab29b124b1c5fe2c0e064d2c44cdb',
                     '901829c455cf646b245481c833636f450305004ffb8883b5e3a9254d26e443e9']
PID60256_DIAGNOSIS = '196d5fd18e2677ef19977eead6a98de4e07fa5067664f6dd7c9b6c7479005794'
PID60256_OBSERVATION = '09b7e901e86d50f096a5fe9e911edad71c29917c47b70398537af186cc490317'
SOURCE_FILES = ('consistency.py', 'audit.py', 'operation_provenance.py', 'spec.py',
                'measurement_dispute.py', 'closed_seal.py', 'process_generation.py',
                'runner.py', 'technical_adjudication.py')
DEFECTS = frozenset(('OLD_UNBRACKETED_LOCALE_CALL', 'OLD_SINGLE_EDITOR_EXIT_PREMATURE_FINISH',
    'OLD_NATIVE_EDITOR_EXIT_COMPLETION_UNSUPPORTED', 'OLD_FINAL_NORMAL_STOP_LATE_WITNESS',
    'OLD_36212_EXIT_CAUSE_UNKNOWN', 'P3_BLOCKED_AND_ROOT_ADAPTER_HISTORY',
    'P4_OBSERVER_FAILURE_AND_057_AMBIGUITY'))
CATEGORIES = frozenset(('spec', 'report', 'primary_traces', 'provenance_traces', 'journal',
    'original_reviews', 'source_operations', 'lifecycle', 'external_state', 'observer',
    'defects', 'dependencies', 'protected_inventories'))
FORBIDDEN = frozenset(('approved', 'score', 'pass', 'business_status', 'replacement_primary_decision',
                       'historical_defect_cured'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def safe(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'Technical evidence contains a link/reparse path')
    return path


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate technical JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonstandard technical JSON constant')
    return json.loads(safe(path).read_bytes(), object_pairs_hook=pairs, parse_constant=constant)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def names(values, allowed, message, *, empty=False):
    require(isinstance(values, list) and (empty or values) and all(isinstance(v, str) for v in values)
            and len(values) == len(set(values)) and set(values) <= set(allowed), message)
    return set(values)


def reference(root, record):
    require(isinstance(record, dict) and {'path', 'sha256', 'bytes'} <= set(record),
            'Technical reference requires path/SHA/length')
    name = record['path']
    require(isinstance(name, str) and name and ':' not in name and '\\' not in name
            and not Path(name).is_absolute() and all(p not in ('', '.', '..') for p in name.split('/')),
            'Technical reference escaped its case')
    path = safe(Path(root) / name)
    require(path.resolve().is_relative_to(safe(root).resolve()) and path.is_file(), 'Technical reference escaped its case')
    raw = path.read_bytes()
    require(type(record['bytes']) is int and len(raw) == record['bytes']
            and hashlib.sha256(raw).hexdigest() == record['sha256'], 'Technical reference bytes changed')
    return path


def refs(root, values, *, empty=False):
    require(isinstance(values, list) and (empty or values), 'Actual technical evidence references required')
    paths = [reference(root, r) for r in values]
    require(len(paths) == len(set(paths)), 'Duplicate technical evidence reference')
    return paths


def api():
    try:
        from . import audit
    except ImportError:
        import audit
    require(Path(audit.__file__).resolve().parent == Path(__file__).resolve().parent,
            'Technical resolver must use its colocated audit')
    return audit


def execution_modules():
    a = api()
    dispute = a.measurement_dispute_module()
    helper, generation = dispute.execution_helper(), dispute.generation_module()
    here = Path(__file__).resolve().parent
    for module, name in ((helper, 'closed_seal.py'), (generation, 'process_generation.py'),
                         (helper.runner, 'runner.py')):
        require(Path(module.__file__).resolve() == here / name, 'Technical execution source is not colocated')
    return dispute, helper, generation


def source_pins():
    here = safe(Path(__file__).parent)
    for name in SOURCE_FILES:
        tree = ast.parse(safe(here / name).read_bytes(), filename=name)
        for node in ast.walk(tree):
            modules = ([a.name.split('.')[0] for a in node.names] if isinstance(node, ast.Import)
                       else [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module
                       else [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else [])
            for module in modules:
                local = module + '.py'
                require(not (here / local).is_file() or local in SOURCE_FILES,
                        'Unpinned actual local validation dependency: ' + local)
    return {name: hashlib.sha256(safe(here / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def participants(case):
    """Explicit adjunct registry; original role dictionary/counters stay intact."""
    roles = case.get('roles')
    require(isinstance(roles, dict) and set(roles) <= {'primary', 'provenance', 'review'},
            'Unknown original role; technical adjunct must not hide in roles')
    result = dict(roles)
    registry = case.get('adjunct_participants', {})
    require(isinstance(registry, dict) and set(registry) <= {ROLE}, 'Unknown adjunct participant')
    if registry:
        state = registry[ROLE]
        require(isinstance(state, dict) and state.get('role') == ROLE and state.get('attempts') == 1
                and type(state.get('attempts')) is int and state.get('automatic_retry') is False
                and state.get('descendant_cap') == 0 and type(state.get('descendant_cap')) is int
                and state.get('model') == MODEL and state.get('effort') == EFFORT
                and text(state.get('auditor_id')) and state['auditor_id'] not in
                    {s.get('auditor_id') for s in roles.values()}, 'Invalid one-turn technical adjunct registry')
        require(state.get('stage_status') in ('reserved', 'running', 'completed', 'failed', 'rejected', 'missing_evidence'),
                'Unknown technical stage state')
        require(text(state.get('authority_sha256')) and text(state.get('input_capsule_sha256')),
                'Technical participant needs exact one-shot authority/capsule')
        result[ROLE] = state
    return result


def new_participant(case, authority, *, auditor_id, authority_sha256):
    """Pure one-shot reservation value. Caller must persist under its lock first."""
    require(not case.get('adjunct_participants'), 'Technical initial turn already reserved; no retry or replacement')
    require(case.get('roles', {}).get('primary', {}).get('attempts') == 4
            and case.get('roles', {}).get('provenance', {}).get('attempts') == 5,
            'Original primary4/provenance5 caps must remain visible')
    require(authority.get('schema') == 'b1-technical-adjudication-authority-v1'
            and authority.get('opaque_id') == case['opaque_id']
            and authority.get('one_initial_turn') is True and authority.get('automatic_retry') is False
            and authority.get('descendant_cap') == 0 and text(authority.get('question'))
            and authority.get('merits_approved') is False, 'Only exact-question authority; never merits approval')
    state = {'role': ROLE, 'auditor_id': auditor_id, 'session_id': None, 'attempts': 1,
             'model': MODEL, 'effort': EFFORT, 'automatic_retry': False, 'descendant_cap': 0,
             'stage_status': 'reserved', 'authority_sha256': authority_sha256,
             'input_capsule_sha256': authority['input_capsule']['sha256'], 'execution_attempts': [],
             'exported_tool_trace_manifests': []}
    participants({**case, 'adjunct_participants': {ROLE: state}})
    return state


def launch_state(out, case, claim):
    """Fail closed before reserving subprocess setup; only a frozen opt-in caller."""
    require(isinstance(claim, dict), 'Explicit technical execution authority required')
    authority = read(reference(out, claim))
    validate_protocol(out, authority)
    state = participants(case).get(ROLE)
    require(state and state.get('stage_status') == 'reserved' and not state.get('invocation_reserved')
            and state.get('session_id') is None and not state.get('execution_attempts')
            and state['authority_sha256'] == claim['sha256'], 'One technical initial turn already consumed or not reserved')
    require(authority.get('schema') == 'b1-technical-adjudication-authority-v1'
            and authority.get('opaque_id') == case['opaque_id'] and authority.get('execution_authorized') is True
            and authority.get('one_initial_turn') is True and authority.get('automatic_retry') is False
            and authority.get('descendant_cap') == 0 and authority.get('merits_approved') is False
            and authority.get('source_pins') == source_pins(), 'Actual one-shot frozen-source execution authority required')
    require(state['input_capsule_sha256'] == authority['input_capsule']['sha256'], 'Reserved capsule changed')
    require(state['auditor_id'] not in set(authority.get('root_and_source_author_ids', [])) | {'root', '/root/prepare_uniform_freeze'},
            'Root/source author cannot adjudicate merits')
    capsule = read(reference(out, authority['input_capsule']))
    validate_source_authority(out, authority)
    report_path = Path(out) / 'blind/audit.json'; report = read(report_path)
    require(hashlib.sha256(report_path.read_bytes()).hexdigest() == authority['report_sha256'], 'Original report changed')
    blind = read(Path(out) / 'blind/manifest.json'); p = api().provenance_module()
    chain = p.validate_workspace(Path(out) / 'blind/candidate', Path(out) / 'blind/runtime', Path(out) / 'blind/evidence',
                                 blind['operational_provenance'], require_review=False)
    canonical_refusal(out, blind['operational_provenance'], chain, capsule['original_reviews'])
    validate_capsule(out, capsule, authority, report, chain)
    input_directory(out, claim)
    require(case['roles']['primary']['attempts'] == 4 and case['roles']['provenance']['attempts'] == 5,
            'Original caps cannot reset or grow')
    return state


def validate_source_authority(out, authority):
    require(authority.get('execution_authorized') is True and authority.get('source_pins') == source_pins(),
            'Actual frozen-source authority missing')
    review = read(reference(out, authority['merged_source_review']))
    require(review.get('status') == 'PASS_SOURCE_REVIEW_ONLY' and review.get('technical_review_passed') is True
            and all(review.get('source_pins', {}).get(n) == h for n, h in source_pins().items()),
            'Exact merged independent source review required')
    manifest = safe(Path(__file__).with_name('manifest.json'))
    require(hashlib.sha256(manifest.read_bytes()).hexdigest() == authority.get('runtime_manifest_sha256'),
            'Explicit actual frozen runtime manifest required')
    files = read(manifest).get('files', {})
    require(set(SOURCE_FILES) | {'audit_runner.py', 'runtime.py'} <= set(files), 'Frozen execution closure incomplete')
    for name, item in files.items():
        reference(manifest.parent, dict(item, path=name))
    required = {**source_pins(), **{name: hashlib.sha256(safe(manifest.parent / name).read_bytes()).hexdigest()
                                  for name in ('audit_runner.py', 'runtime.py')}}
    driver = reference(out, authority['driver_source'])
    required[driver.name] = authority['driver_source']['sha256']
    require(all(review.get('source_pins', {}).get(n) == h for n,h in required.items()),
            'Independent merged source review omits actual execution/driver dependency')
    runtime_copy = reference(out, authority['runtime_manifest'])
    require(runtime_copy.read_bytes() == manifest.read_bytes(), 'Runtime source descriptor differs from actual frozen bundle')


def validate_protocol(out, authority):
    require(authority.get('opaque_id') == CASE and authority.get('candidate_hash') == CANDIDATE
            and authority.get('report_sha256') == ORIGINAL_REPORT and authority.get('journal_head') == JOURNAL_HEAD,
            'This one-case protocol cannot waive a different audit/candidate/report/head')
    protocol = authority.get('protocol', {})
    require(protocol.get('sha256') == PROTOCOL, 'Exact independently prepared protocol bytes required')
    read(reference(out, protocol))
    require([r.get('sha256') for r in authority.get('original_reviews', [])] == ORIGINAL_OPINIONS,
            'Both exact original refusals must remain visible')


def input_directory(out, claim):
    authority = read(reference(out, claim))
    name = authority.get('input_directory')
    require(name == 'private/technical_adjudication/input', 'Dedicated anonymous read-only capsule directory required')
    directory = safe(Path(out) / name)
    require(directory.is_dir() and not (directory / 'auth.json').exists(), 'Anonymous input directory invalid')
    capsule_path = reference(out, authority['input_capsule']); capsule = read(capsule_path)
    entries = capsule.get('input_files')
    require(isinstance(entries, list) and entries, 'Exact anonymous input byte inventory required')
    seen, originals = set(), set()
    for item in entries:
        source = reference(out, item['original_ref'])
        view = reference(directory, item['view_ref'])
        require(source.read_bytes() == view.read_bytes() and view not in seen, 'Anonymous view differs or is duplicated')
        require(not {'auth.json', 'controller.json'}.intersection(view.relative_to(directory).parts),
                'Anonymous view exposes credentials/controller')
        seen.add(view); originals.add(source)
    capsule_view = safe(directory / 'capsule.json')
    require(capsule_view.read_bytes() == capsule_path.read_bytes(), 'Anonymous capsule differs from fixed input manifest')
    seen.add(capsule_view)
    actual = {safe(p) for p in directory.rglob('*') if p.is_file()}
    require(actual == seen and {reference(out,r) for r in capsule['artifacts']} <= originals,
            'Anonymous read scope omits an original input or includes unlisted files')
    return directory


def verify_single_owner(out, state):
    _, helper, _ = execution_modules()
    files = sorted((Path(out) / 'private' / ROLE / 'home/sessions').rglob('*.jsonl'))
    nodes = helper.rollout_own_streams(files)
    require(set(nodes) == {state.get('session_id')} and nodes[state['session_id']]['parent'] is None,
            'Technical actual own traces must prove exactly one root and zero descendants')
    contexts = [e['payload'] for _, e in nodes[state['session_id']]['own'] if e['type'] == 'turn_context']
    require(contexts and all(p.get('model') == MODEL and p.get('effort', p.get('reasoning_effort')) == EFFORT for p in contexts),
            'Technical own model/effort differs from authorized source')
    return nodes


def command(cli, home, case_directory, final):
    """No model call here. No Unity MCP, resume or descendant permission."""
    return [str(cli), '--no-daemon', 'exec', '--ignore-user-config', '--ignore-rules', '--strict-config',
            '--json', '--model', MODEL, '--sandbox', 'read-only', '--skip-git-repo-check',
            '--cd', str(case_directory), '--output-last-message', str(final),
            '-c', 'model_reasoning_effort="high"', '-c', 'features.multi_agent=false',
            '-c', 'agents.max_concurrent_threads_per_session=1', '-c', 'agents.max_depth=0', '-']


def prompt(authority, auditor_id):
    return ('You are the new independent technical adjudicator ' + auditor_id + '. Use gpt-6.1-sol/high. '
        'One initial turn, zero descendants, no retry. Read only this anonymous case and the exact input capsule. '
        'Do not invoke Unity directly or through exec/scripts, test gameplay, modify U/R/saves/journal/old evidence, '
        'replace original opinions, decide any business score, or infer that later evidence cures historical defects. '
        'The CLI final output is the sole verdict JSON; this sandbox is read-only. Read capsule.json and its complete listed views. '
        'Return a strict JSON verdict: supported_scope_only, rejected, or missing_evidence. '
        'Every requested claim component and necessary dependency/contamination edge needs actual independent evidence; '
        'unresolved necessary edges block that check. Complement IDs stay not_in_scope. No global approved/score/pass. '
        'Original canonical final remains false. Rejected, partial scope and missing evidence are terminal. Exact question: '
        + authority['question'] + '\nExact output contract (never fill in an unsupported eligibility): ' + json.dumps({
            'schema': 'b1-technical-measurement-adjudication-v1',
            **{k: authority[k] for k in ('opaque_id','candidate_hash','report_sha256','primary_checkpoint','journal_head')},
            'actual_adjudicator': {'auditor_id':auditor_id,'attempt':1,'model':MODEL,'effort':EFFORT},
            'input_capsule_manifest_ref': authority['input_capsule'],
            'both_original_reviews_refs': authority['original_reviews'], 'original_canonical_final_approved':False,
            'defect_findings': 'Exactly7 rows: defect_id, historical_fact_ref, original_call_source_result_refs unchanged; '
                'affected_claim_ids exactly graph incidence; status supported_independent_remeasurement/still_dependent_blocked/missing_evidence/not_reliant_with_proof; '
                'actual basis, preserved_limits, new_measurement_refs (path/sha256/bytes).',
            'check_eligibility': 'Exactly001-100: check_id, original_check_sha256, scope eligible_for_independent_acceptance/blocked/missing_evidence/not_in_scope, '
                'all_relevant_defect_ids exactly input graph, basis, limitations; dependency_paths every edge_id with status supported/blocked/missing_evidence, basis, evidence_refs. '
                'Eligible rows ALSO need all supported_components, actual_measurement_operation_ids, original_evidence_refs, independent_current_evidence_refs, integrity_refs. '
                'All refs use original case-relative path/sha256/bytes from capsule mapping, not anonymous view filenames. '
                'Do not add score/approved/pass/business_status/historical_defect_cured. Complement is not_in_scope.',
            'outcome': 'supported_scope_only/rejected/missing_evidence; rejection/missing/partial are terminal'
        },ensure_ascii=False))


def canonical_refusal(out, manifest, chain, opinions):
    """Validate the original review completely, allowing its truthful false final."""
    a = api(); p = a.provenance_module(); evidence = Path(out) / 'blind/evidence'
    require(isinstance(opinions, list) and len(opinions) == 2, 'Both complete original opinions required')
    paths = refs(out, opinions)
    current_path = evidence / 'provenance-review.json'
    require(paths[1].read_bytes() == safe(current_path).read_bytes(), 'Original canonical refusal changed')
    normalized = p._manifest(Path(out) / 'blind/candidate', Path(out) / 'blind/runtime', evidence, manifest)[3]
    records, _, head = p._chain(Path(out) / 'blind/candidate', Path(out) / 'blind/runtime', evidence, normalized)
    reviewer = None
    for path in paths:
        opinion = read(path)
        require(opinion.get('schema') == 'b1-provenance-review-v1'
                and opinion.get('manifest_sha256') == normalized['manifest_sha256']
                and opinion.get('candidate_hash') == normalized['candidate_hash']
                and opinion.get('primary_id') == normalized['primary_id'] and opinion.get('independent') is True
                and text(opinion.get('reviewer_id')) and opinion['reviewer_id'] != normalized['primary_id'],
                'Original opinion identity/origin binding invalid')
        require(reviewer is None or reviewer == opinion['reviewer_id'], 'Original reviewer identity replaced')
        reviewer = opinion['reviewer_id']
        final = opinion.get('final', {})
        require(final.get('approved') is False and text(final.get('basis')), 'Original refusal must remain false')
        p._refs(evidence, final.get('evidence_refs'))
        # Old head may differ; its historical decision set is checked against
        # its exact operation prefix, never retrospectively against newer ops.
        prefix = []
        if opinion['journal_head'] != normalized['manifest_sha256']:
            for op in records:
                prefix.append(op)
                if op['operation_sha256'] == opinion['journal_head']: break
            require(prefix and prefix[-1]['operation_sha256'] == opinion['journal_head'], 'Original opinion head not in preserved chain')
        expected = {(op['operation_id'], c['path']): op for op in prefix for c in op['changes']}
        decisions = opinion.get('decisions'); require(isinstance(decisions, list), 'Original decisions missing')
        seen = set()
        for decision in decisions:
            key = (decision.get('operation_id'), decision.get('path'))
            require(key in expected and key not in seen and decision.get('decision') in ('approved', 'rejected', 'missing_evidence')
                    and text(decision.get('basis')) and reviewer != expected[key]['provenance']['actor_id'],
                    'Original opinion decision coverage/independence invalid')
            seen.add(key); p._refs(evidence, decision.get('evidence_refs'))
        require(seen == set(expected), 'Original opinion omitted operation/path origin')
        historical_inventory = prefix[-1]['output_inventory'] if prefix else normalized['reference_inventory']
        require(final.get('runtime_digest') == p.fingerprint(historical_inventory)
                and final.get('reference_digest') == normalized['reference_digest'], 'Original opinion historical inventory binding invalid')
    current = read(paths[1]); final = current['final']
    require(current['journal_head'] == head == chain['journal_head']
            and final.get('runtime_digest') == chain['current_digest']
            and final.get('reference_digest') == chain['reference_digest'], 'Canonical false does not bind current structural chain')
    return reviewer, opinions[1]['sha256']


def validate_capsule(out, capsule, authority, report, chain):
    ids = {c['id'] for c in api().spec.CHECKS}
    require(capsule.get('schema') == 'b1-technical-adjudication-input-capsule-v1'
            and capsule.get('opaque_id') == report['opaque_id'] and capsule.get('candidate_hash') == report['candidate_hash']
            and capsule.get('report_sha256') == authority['report_sha256']
            and capsule.get('journal_head') == chain['journal_head']
            and capsule.get('primary_checkpoint') == authority['primary_checkpoint'], 'Input capsule binding mismatch')
    requested = names(authority.get('requested_check_ids'), ids, 'Explicit requested technical scope required')
    require(capsule.get('requested_check_ids') == authority['requested_check_ids'], 'Capsule scope changed')
    artifacts = capsule.get('artifacts'); refs(out, artifacts)
    require({r.get('category') for r in artifacts} >= CATEGORIES, 'Incomplete original input capsule categories')
    require(capsule.get('original_reviews') == authority.get('original_reviews')
            and len({r['sha256'] for r in capsule['original_reviews']}) == 2,
            'Exact two independent original opinions must be fixed before launch')
    protected = read(reference(out, capsule['protected_inventory']))
    refs(out, protected)
    require({(r['path'], r['sha256'], r['bytes']) for r in capsule['original_reviews']}
            <= {(r['path'], r['sha256'], r['bytes']) for r in artifacts}, 'Original opinions omitted from full capsule payload')
    inventory = read(reference(out, capsule['writer_inventory']))
    require(inventory.get('schema') == 'b1-dispute-known-writers-v1'
            and inventory.get('report_sha256') == authority['report_sha256']
            and inventory.get('primary_checkpoint') == authority['primary_checkpoint']
            and len(inventory.get('auxiliary_generations', [])) == 24,
            'Original24 writer inventory must remain separately pinned')
    require(capsule.get('original_participants') == authority.get('original_participants'),
            'Original primary4/provenance5 identities/caps cannot be replaced')
    verify_original_participants(out, capsule['original_participants'], report, authority)
    original_capsule_paths = {r['path'] for r in artifacts}
    require({r['path'] for r in protected} <= original_capsule_paths,
            'Input payload omitted protected original bytes')
    require(capsule.get('operation_ids') == chain['operations'] and len(chain['operations']) == 34,
            'Full34 operation chain must be preserved')
    graph = capsule.get('claim_dependencies')
    require(isinstance(graph, list) and len(graph) == 100 and {r.get('check_id') for r in graph} == ids,
            'Capsule requires complete explicit100 claim dependency graph')
    defects = capsule.get('defects')
    require(isinstance(defects, list) and len(defects) == len(DEFECTS)
            and {r.get('defect_id') for r in defects} == DEFECTS, 'Complete pinned defect inventory required')
    for defect in defects:
        reference(out, defect['historical_fact_ref']); refs(out, defect['original_call_source_result_refs'])
        require(text(defect.get('immutable_fact')), 'Historical defect fact missing')
    for row in graph:
        names(row.get('defect_ids'), DEFECTS, 'Unknown/duplicate dependency defect', empty=True)
        require(isinstance(row.get('components'), list) and row['components']
                and all(text(c) for c in row['components']) and len(set(row['components'])) == len(row['components']),
                'Actual claim components required')
        edges = row.get('edges'); require(isinstance(edges, list) and edges, 'Dependency/contamination edges required')
        seen = set()
        for edge in edges:
            require(text(edge.get('edge_id')) and edge['edge_id'] not in seen
                    and (edge.get('defect_id') in row['defect_ids'] or edge.get('defect_id') is None)
                    and type(edge.get('necessary')) is bool
                    and text(edge.get('from')) and text(edge.get('to')), 'Invalid necessary dependency edge')
            seen.add(edge['edge_id']); refs(out, edge.get('source_refs'))
    return requested


def verify_original_participants(out, actors, report, authority):
    dispute, h, _ = execution_modules()
    require(isinstance(actors, dict) and set(actors) == {'primary', 'provenance'}, 'Both complete original roles required')
    require(actors['primary'].get('auditor_id') == report['auditor']['id'], 'Original primary identity changed')
    all_sessions = set()
    for role, count in (('primary', 4), ('provenance', 5)):
        actor = actors[role]
        require(actor.get('attempts') == count and type(actor.get('attempts')) is int and text(actor.get('auditor_id'))
                and text(actor.get('session_id')), 'Original role identity/cumulative cap changed')
        natives = refs(out, actor.get('native_turns'))
        require(len(natives) == count and [p.name for p in natives] == [f'turn-{i}.jsonl' for i in range(1, count + 1)],
                'Original actual native turn coverage incomplete')
        final = reference(out, actor['final']); cp = actor['checkpoint']
        require(cp.get('attempt') == count and cp.get('session_id') == actor['session_id'],
                'Original actual completed invocation checkpoint required; no invented turn')
        dispute.native_checkpoint(natives[-1], final, cp)
        require(role != 'primary' or cp == authority['primary_checkpoint'], 'Original P4 checkpoint changed')
        nodes = h.rollout_own_streams(refs(out, actor.get('rollouts')))
        require({sid for sid, n in nodes.items() if n['parent'] is None} == {actor['session_id']}
                and not all_sessions.intersection(nodes), 'Original role own ancestry/identity changed')
        all_sessions.update(nodes)
        models = {sid: [{'model': e['payload'].get('model'), 'effort': e['payload'].get('effort', e['payload'].get('reasoning_effort'))}
                         for _, e in n['own'] if e['type'] == 'turn_context'] for sid, n in nodes.items()}
        require(read(reference(out, actor['model_evidence'])) == models
                and all(v and all(m == {'model': MODEL, 'effort': EFFORT} for m in v) for v in models.values()),
                'Original full-own model evidence differs')
        pairs = h.rollout_pairs([n['path'] for n in nodes.values()])
        require(all(v['terminal_event_present'] and not v['record_incomplete'] and not v['pending'] and not v['background_active']
                    for v in pairs.values()) and not h.native_pairs(natives)['pending'], 'Original actual execution remains active')
        unity = refs(out, actor.get('unity_traces'), empty=role != 'primary')
        exported_files(out, actor.get('trace_files'), set(natives) | {n['path'] for n in nodes.values()} | {final} | set(unity))
    require(actors['primary']['auditor_id'] != actors['provenance']['auditor_id'], 'Original reviewer is not independent')


def exported_files(out, exports, expected):
    require(isinstance(exports, list) and exports, 'Full source/snapshot/export files required')
    covered = set()
    for item in exports:
        source, snapshot, exported = (reference(out, item[k]) for k in ('source', 'snapshot', 'export'))
        require(source.read_bytes() == snapshot.read_bytes() == exported.read_bytes(), 'Actual source/snapshot/export bytes differ')
        require(source not in covered, 'Duplicate actual source export')
        covered.add(source)
    require(covered == expected, 'Incomplete actual trace export coverage')


def validate_verdict(out, verdict, capsule, authority, report):
    require(verdict.get('schema') == 'b1-technical-measurement-adjudication-v1'
            and not FORBIDDEN.intersection(verdict)
            and verdict.get('outcome') in ('supported_scope_only', 'rejected', 'missing_evidence'),
            'Technical verdict cannot approve or decide business scores')
    for key in ('opaque_id', 'candidate_hash', 'report_sha256', 'primary_checkpoint', 'journal_head'):
        require(verdict.get(key) == authority[key], 'Verdict original binding mismatch: ' + key)
    require(verdict.get('input_capsule_manifest_ref') == authority['input_capsule']
            and verdict.get('both_original_reviews_refs') == capsule['original_reviews']
            and verdict.get('original_canonical_final_approved') is False, 'Verdict cannot replace original opinions')
    defects = verdict.get('defect_findings')
    require(isinstance(defects, list) and len(defects) == len(DEFECTS)
            and {d.get('defect_id') for d in defects} == DEFECTS, 'Every pinned defect needs one actual finding')
    by_defect = {d['defect_id']: d for d in defects}; original = {d['defect_id']: d for d in capsule['defects']}
    for defect in defects:
        require(not FORBIDDEN.intersection(defect) and defect.get('historical_fact_ref') == original[defect['defect_id']]['historical_fact_ref']
                and defect.get('original_call_source_result_refs') == original[defect['defect_id']]['original_call_source_result_refs']
                and defect.get('status') in ('supported_independent_remeasurement', 'still_dependent_blocked', 'missing_evidence', 'not_reliant_with_proof')
                and text(defect.get('basis')) and text(defect.get('preserved_limits')), 'Defect history/status/basis missing')
        refs(out, defect.get('new_measurement_refs'))
        expected = {r['check_id'] for r in capsule['claim_dependencies'] if defect['defect_id'] in r['defect_ids']}
        require(names(defect.get('affected_claim_ids'), {c['id'] for c in report['checks']},
                      'Every defect requires its complete affected-claim set', empty=True) == expected,
                'Defect finding omitted an affected current claim')
    rows = verdict.get('check_eligibility'); ids = {c['id'] for c in report['checks']}
    require(isinstance(rows, list) and len(rows) == 100 and {r.get('check_id') for r in rows} == ids,
            'Every verdict must explicitly block the entire complement')
    checks = {c['id']: c for c in report['checks']}; graph = {r['check_id']: r for r in capsule['claim_dependencies']}
    requested = set(authority['requested_check_ids']); eligible = set()
    for row in rows:
        cid = row['check_id']; dependencies = graph[cid]
        require(not FORBIDDEN.intersection(row) and row.get('original_check_sha256') == digest(checks[cid])
                and row.get('scope') in ('eligible_for_independent_acceptance', 'blocked', 'missing_evidence', 'not_in_scope')
                and set(row.get('all_relevant_defect_ids', [])) == set(dependencies['defect_ids'])
                and text(row.get('basis')) and text(row.get('limitations')), 'Check technical scope/original digest invalid')
        require(cid in requested or row['scope'] == 'not_in_scope', 'Partial technical scope leaked to complement')
        paths = row.get('dependency_paths')
        require(isinstance(paths, list) and len(paths) == len(dependencies['edges'])
                and {p.get('edge_id') for p in paths} == {p['edge_id'] for p in dependencies['edges']},
                'Missing/unknown necessary dependency or contamination edge')
        by_edge = {p['edge_id']: p for p in paths}
        for edge in paths:
            require(edge.get('status') in ('supported', 'blocked', 'missing_evidence') and text(edge.get('basis')),
                    'Dependency edge lacks actual finding')
            refs(out, edge.get('evidence_refs'))
        if row['scope'] == 'eligible_for_independent_acceptance':
            require(verdict['outcome'] == 'supported_scope_only'
                    and set(row.get('supported_components', [])) == set(dependencies['components'])
                    and all(by_edge[e['edge_id']]['status'] == 'supported' for e in dependencies['edges'] if e['necessary'])
                    and all(by_defect[d]['status'] in ('supported_independent_remeasurement', 'not_reliant_with_proof') for d in dependencies['defect_ids']),
                    'Unresolved necessary dependency blocks this check')
            names(row.get('actual_measurement_operation_ids'), capsule['operation_ids'], 'Actual measurement operation missing')
            for key in ('original_evidence_refs', 'independent_current_evidence_refs', 'integrity_refs'):
                refs(out, row.get(key))
            eligible.add(cid)
    return eligible


def actual_actor(out, actor, final_document, forbidden_ids):
    dispute, h, _ = execution_modules()
    require(actor.get('attempt') == 1 and type(actor.get('attempt')) is int and actor.get('model') == MODEL
            and actor.get('effort') == EFFORT and text(actor.get('auditor_id')) and actor['auditor_id'] not in forbidden_ids
            and text(actor.get('session_id')) and actor.get('descendant_cap') == 0,
            'Actual technical actor identity/model/cap is not independent')
    cp = actor.get('checkpoint', {})
    require(cp.get('attempt') == 1 and cp.get('session_id') == actor['session_id'], 'Actual one-turn checkpoint changed')
    native, final = reference(out, actor['native']), reference(out, actor['final'])
    require(final.read_bytes() == reference(out, final_document).read_bytes(), 'Actual final must be the exact opinion bytes')
    dispute.native_checkpoint(native, final, cp)
    sessions = refs(out, actor.get('rollouts')); nodes = h.rollout_own_streams(sessions)
    require(set(nodes) == {actor['session_id']} and nodes[actor['session_id']]['parent'] is None,
            'Technical stage permits one actual root and zero descendants')
    models, calls = [], []
    for line, event in nodes[actor['session_id']]['own']:
        payload = event.get('payload', {})
        if event['type'] == 'turn_context':
            models.append({'model': payload.get('model'), 'effort': payload.get('effort', payload.get('reasoning_effort'))})
        if event['type'] == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call', 'mcp_tool_call'):
            name = payload.get('name', '')
            require(isinstance(name, str) and not re.match(r'(?i)(?:functions\.)?(?:mcp__unity__|mcp\.unity\.|unity\.)', name)
                    and payload.get('server') != 'unity' and name not in ('spawn_agent', 'functions.spawn_agent'),
                    'Technical actor invoked Unity or a descendant')
            calls.append({'owner_session_id': actor['session_id'], 'line': line, 'event_sha256': h.canonical_item_sha256(event)})
    require(models and all(m == {'model': MODEL, 'effort': EFFORT} for m in models)
            and read(reference(out, actor['model_evidence'])) == {actor['session_id']: models}, 'Actual own model evidence mismatch')
    paired = h.rollout_pairs(sessions)
    require(all(v['terminal_event_present'] and not v['record_incomplete'] and not v['pending']
                and not v['background_active'] for v in paired.values()) and not h.native_pairs([native])['pending'],
            'Actual technical execution is not terminal')
    for event in h.json_events(native, allow_diagnostics=True):
        item = event.get('item', {})
        require(not (item.get('type') == 'mcp_tool_call' and item.get('server') == 'unity'), 'Technical native invoked Unity')
    require(all(p.is_relative_to(Path(out) / 'private' / ROLE) for p in sessions + [native, final]),
            'Technical actual source escaped dedicated role output')
    exported_files(out, actor.get('trace_files'), set(sessions) | {native, final})
    execution = actor.get('execution', {})
    require(execution.get('process_tree_stopped') is True and text(execution.get('started_utc'))
            and text(execution.get('ended_utc')) and type(execution.get('cli_pid')) is int
            and execution['cli_pid'] > 0, 'Actual stopped invocation evidence missing')
    census = read(reference(out, execution['generation_census_ref']))
    require(census.get('complete') is True
            and [r for r in census.get('processes', []) if r.get('ProcessId') == execution['cli_pid']] == [execution.get('generation_row')],
            'Actual live CLI full generation is not bound')
    return calls, set(paired)


def actual_review_source(out, peer, certificate_ref, forbidden_ids):
    """Trace an existing independent review; no new role and no recursive review."""
    _, h, _ = execution_modules()
    require(text(peer.get('auditor_id')) and peer['auditor_id'] not in forbidden_ids
            and text(peer.get('session_id')), 'Actual trace reviewer is root/source author or not distinct')
    nodes = h.rollout_own_streams(refs(out, peer.get('source_rollouts')))
    require(peer['session_id'] in nodes, 'Actual independent review owner source missing')
    certificate = read(reference(out, certificate_ref))
    contexts = [{'model': e['payload'].get('model'), 'effort': e['payload'].get('effort', e['payload'].get('reasoning_effort'))}
                for _,e in nodes[peer['session_id']]['own'] if e['type'] == 'turn_context']
    require(contexts and all(text(c['model']) and text(c['effort']) for c in contexts)
            and read(reference(out, peer['model_evidence'])) == {peer['session_id']: contexts},
            'Actual independent review own model evidence missing or changed')
    require(certificate.get('reviewer_id') == peer['auditor_id']
            and certificate.get('reviewer_session_id') == peer['session_id']
            and certificate.get('reviewer_model_evidence') == peer['model_evidence'], 'Actual review output identity/model binding missing')
    own = dict(nodes[peer['session_id']]['own']); selected = peer.get('review_event_refs')
    binding = peer.get('certificate_binding_event_refs')
    require(isinstance(selected, list) and selected and isinstance(binding, list) and binding,
            'Actual review and subsequent certificate-byte binding events required')
    require([r.get('line') for r in selected + binding] == sorted({r.get('line') for r in selected + binding}),
            'Actual review and subsequent certificate binding event order changed')
    seen, pending, review_outputs, binding_outputs = set(), {}, [], []
    for record in selected + binding:
        number = record.get('line'); require(type(number) is int and number in own and number not in seen,
                                             'Review event is duplicated, inherited or outside actual own stream')
        event = own[number]; seen.add(number)
        require(record.get('owner_session_id') == peer['session_id'] and record.get('event_sha256') == h.canonical_item_sha256(event),
                'Actual review source identity/bytes changed')
        payload = event.get('payload', {}); kind = payload.get('type')
        require(event.get('type') == 'response_item' and kind in ('function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output'),
                'Review source must be an actual tool call/result')
        cid = payload.get('call_id'); require(text(cid), 'Actual review call identity missing')
        if kind.endswith('_output'):
            require(cid in pending, 'Actual review output lacks preceding own call')
            pending.pop(cid)
            (review_outputs if record in selected else binding_outputs).extend(strict_output_objects(payload.get('output')))
        else:
            require(cid not in pending, 'Duplicate actual review call identity'); pending[cid] = payload
    expected = {k:v for k,v in certificate.items() if k != 'actual_review_event_refs'}
    expected['schema'] = 'b1-technical-adjudication-actual-review-result-v1'
    require(not pending and sum(value == expected for value in review_outputs) == 1,
            'Actual own completed structured review result must bind the entire scope and findings')
    binding_record = {'schema':'b1-technical-adjudication-certificate-binding-v1',
        'reviewer_id':peer['auditor_id'],'reviewer_session_id':peer['session_id'],
        'certificate':certificate,'certificate_ref':certificate_ref,
        'certificate_bytes_sha256':certificate_ref['sha256'],'certificate_canonical_sha256':digest(certificate)}
    require(sum(value == binding_record for value in binding_outputs) == 1,
            'Actual own completed output must bind full final certificate bytes/canonical mapping; hash echoes are insufficient')
    return peer['session_id']


def strict_output_objects(value):
    """Typed records inside real tool stdout/envelopes, with strict JSON keys."""
    def unique(items):
        result = {}
        for key,item in items:
            require(key not in result, 'Duplicate actual review output key'); result[key] = item
        return result
    def reject(_): raise ValueError('NonJSON actual review output')
    decoder = json.JSONDecoder(object_pairs_hook=unique,parse_constant=reject)
    if isinstance(value,dict):
        yield value
        for item in value.values(): yield from strict_output_objects(item)
    elif isinstance(value,list):
        for item in value: yield from strict_output_objects(item)
    elif isinstance(value,str):
        cursor = 0
        while cursor < len(value):
            positions = [i for i in (value.find('{',cursor),value.find('[',cursor)) if i >= 0]
            if not positions: break
            start = min(positions)
            try:
                parsed,end = decoder.raw_decode(value,start)
            except ValueError:
                cursor = start + 1; continue
            yield from strict_output_objects(parsed)
            cursor = end


def require_scope(result, check_ids, *, action):
    requested = names(list(check_ids), {f'{i:03}' for i in range(1, 101)}, 'Explicit canonical check scope required')
    require(action in ('independent_acceptance', 'measurement_dispute', 'full_report', 'consistency', 'horizontal'),
            'Technical receipt cannot complete or score an audit')
    if action in ('full_report', 'consistency', 'horizontal'):
        require(requested == {f'{i:03}' for i in range(1, 101)}, 'Full consumer requires all100 technical IDs')
    if action == 'measurement_dispute': require(requested == {'057'}, 'Only exact057 technical prerequisite supported')
    require(requested <= set(result['eligible_check_ids']), 'Requested technical scope is blocked or missing evidence')
    return result


def resolve(report_path, check_ids, *, action):
    """Read an existing trusted anchor. Never writes or creates acceptance."""
    report_path = safe(report_path); out = report_path.parent.parent
    judge = read(out / 'judge_manifest.json')
    anchor = judge.get(ANCHOR); require(anchor, 'No explicit technical adjudication acceptance anchor')
    receipt = read(reference(out, anchor))
    result = validate_acceptance(out, receipt)
    result['receipt_ref'] = anchor
    return require_scope(result, check_ids, action=action)


def validate_acceptance(out, receipt):
    """Pure pre-binding gate for an ACTUAL recorded disposition, never a writer."""
    a = api(); out = safe(out); report_path = out / 'blind/audit.json'
    current_report = read(report_path); blind = read(out / 'blind/manifest.json')
    require(receipt.get('schema') == 'b1-technical-measurement-adjudication-acceptance-v1'
            and receipt.get('source_pins') == source_pins(), 'Technical consumer/transitive source pins changed')
    authority = read(reference(out, receipt['authority'])); capsule = read(reference(out, authority['input_capsule']))
    validate_protocol(out, authority)
    validate_source_authority(out, authority)
    report = read(reference(out, capsule['original_report']))
    api().measurement_dispute_module().same_primary(report, current_report)
    require(authority.get('schema') == 'b1-technical-adjudication-authority-v1'
            and authority.get('merits_approved') is False and authority.get('one_initial_turn') is True
            and authority.get('automatic_retry') is False and authority.get('descendant_cap') == 0,
            'Technical authority is not a one-shot question')
    require(receipt.get('input_capsule_sha256') == authority['input_capsule']['sha256']
            and authority.get('source_pins') == receipt['source_pins'], 'Actual authority/capsule/source closure changed')
    require(authority.get('opaque_id') == report['opaque_id'] and authority.get('candidate_hash') == report['candidate_hash']
            and authority.get('report_sha256') == capsule['original_report']['sha256'], 'Technical original report/candidate changed')
    p = a.provenance_module()
    require(a.sha256(p.__file__) == blind['artifacts']['provenance_tool.py'], 'Original blind provenance validator changed')
    chain = p.validate_workspace(out / 'blind/candidate', out / 'blind/runtime', out / 'blind/evidence',
                                 blind['operational_provenance'], require_review=False)
    require(chain['journal_head'] == authority['journal_head'], 'Technical operation chain changed')
    reviewer, refusal_sha = canonical_refusal(out, blind['operational_provenance'], chain, capsule['original_reviews'])
    validate_capsule(out, capsule, authority, report, chain)
    require(capsule.get('operation_ids') == chain['operations'], 'Capsule omitted a recorded operation')
    for row in capsule['claim_dependencies']:
        require(row.get('original_check_sha256') == digest(next(c for c in report['checks'] if c['id'] == row['check_id'])),
                'Capsule original check digest changed')
    verdict_ref = receipt['verdict']; verdict = read(reference(out, verdict_ref))
    forbidden = set(authority.get('root_and_source_author_ids', [])) | {reviewer, report['auditor']['id']}
    require({'root', '/root/prepare_uniform_freeze'} <= forbidden, 'Root/source author exclusion registry missing')
    registry = read(reference(out, receipt['participant_registry']))
    role = participants(registry).get(ROLE)
    actor = receipt['adjudicator']
    require(registry.get('opaque_id') == report['opaque_id'] and role
            and role.get('auditor_id') == actor['auditor_id'] and role.get('session_id') == actor['session_id']
            and role.get('completed_turn_checkpoint') == actor['checkpoint']
            and role.get('authority_sha256') == receipt['authority']['sha256']
            and role.get('input_capsule_sha256') == authority['input_capsule']['sha256']
            and role.get('invocation_reserved') is True and role.get('stage_status') == 'completed'
            and role.get('execution_attempts') == [dict(actor['execution'], attempt=1)],
            'Actual adjunct registry/checkpoint/caps/execution are not bound')
    for name, count in (('primary', 4), ('provenance', 5)):
        old = capsule['original_participants'][name]; state = registry['roles'][name]
        require(state.get('attempts') == count and all(state.get(k) == old[k] for k in ('auditor_id', 'session_id')),
                'Original role cap/identity changed')
        forbidden.add(state['auditor_id'])
    forbidden.update(s.get('auditor_id') for s in registry['roles'].values())
    identity = verdict.get('actual_adjudicator', {})
    require(identity == {k: actor[k] for k in ('auditor_id', 'attempt', 'model', 'effort')}
            or identity == {k: actor[k] for k in ('auditor_id', 'session_id', 'attempt', 'model', 'effort')},
            'Actual adjudicator opinion identity mismatch')
    require(actor['session_id'] not in {v['session_id'] for v in capsule['original_participants'].values()},
            'New adjudicator reused an original session')
    forbidden_sessions = authority.get('root_and_source_author_session_ids')
    require(isinstance(forbidden_sessions, list) and len(forbidden_sessions) >= 2 and all(text(s) for s in forbidden_sessions)
            and actor['session_id'] not in forbidden_sessions, 'Root/source author actual session cannot adjudicate')
    calls, owners = actual_actor(out, actor, verdict_ref, forbidden)
    certificate_ref = receipt['actual_trace_review']; certificate = read(reference(out, certificate_ref))
    peer = receipt['actual_trace_reviewer']; peer_session = actual_review_source(out, peer, certificate_ref, forbidden | {actor['auditor_id']})
    require(peer_session not in set(forbidden_sessions) | {actor['session_id']} | {
        v['session_id'] for v in capsule['original_participants'].values()}, 'Actual independent review reused a forbidden session')
    require(certificate.get('schema') == 'b1-technical-adjudication-actual-trace-review-v1'
            and certificate.get('reviewer_id') == peer['auditor_id'] and certificate.get('technical_trace_verified') is True
            and certificate.get('verdict_ref') == verdict_ref and certificate.get('input_capsule') == authority['input_capsule']
            and certificate.get('adjudicator_checkpoint') == actor['checkpoint']
            and certificate.get('participant_registry') == receipt['participant_registry']
            and certificate.get('participants_annex') == receipt['participants_annex']
            and certificate.get('quiescence') == receipt['quiescence']
            and certificate.get('driver_source') == authority['driver_source']
            and certificate.get('merged_source_review') == authority['merged_source_review']
            and certificate.get('actual_review_event_refs') == peer['review_event_refs']
            and certificate.get('reviewed_calls') == calls and certificate.get('no_unity_including_generic_calls') is True
            and certificate.get('all_claim_components_and_influence_edges_reviewed') is True
            and certificate.get('old_evidence_U_R_saves_and_original_opinions_unchanged') is True,
            'Independent actual trace/source-effects review is incomplete')
    require(certificate.get('only_authorized_anonymous_input_read') is True
            and certificate.get('writes_only_dedicated_adjudication_output') is True,
            'Actual independent source review did not establish exact read/write scope')
    refs(out, certificate.get('source_effects_evidence'))
    # Fresh proofs are read-only replayed, not collected by this resolver.
    verify_quiescence(out, receipt, capsule, owners | {peer_session})
    eligible = validate_verdict(out, verdict, capsule, authority, report)
    require(eligible, 'Rejected/missing/empty technical disposition cannot create an acceptance receipt')
    require(receipt.get('eligible_check_ids') == sorted(eligible)
            and receipt.get('blocked_check_ids') == sorted({c['id'] for c in report['checks']} - eligible),
            'Acceptance scope differs from actual independent verdict')
    require(receipt.get('original_canonical_final_approved') is False
            and receipt.get('canonical_reviewer_id') == reviewer and receipt.get('canonical_review_sha256') == refusal_sha,
            'Acceptance impersonates original provenance approval')
    result = {**chain, 'review_approved': False, 'pending_final_review': True, 'reviewer_id': reviewer,
              'canonical_review_approved': False, 'canonical_reviewer_id': reviewer, 'canonical_review_sha256': refusal_sha,
              'technical_mode': MODE, 'technical_adjudicator_id': actor['auditor_id'],
              'eligible_check_ids': sorted(eligible), 'blocked_check_ids': receipt['blocked_check_ids'],
              'verdict_ref': verdict_ref, 'input_capsule_ref': authority['input_capsule'],
              'participants_annex_ref': receipt['participants_annex'],
              'evidence_refs': evidence_closure(out, receipt, authority, capsule, verdict, certificate)}
    return result


def evidence_closure(out, receipt, authority, capsule, verdict, certificate):
    """Explicit actual payload closure for downstream blinded source packaging."""
    values = [receipt['authority'], authority['input_capsule'], receipt['verdict'], receipt['actual_trace_review'],
              receipt['participant_registry'], receipt['participants_annex'], *receipt['quiescence'].values(),
              capsule['original_report'], capsule['writer_inventory'], capsule['protected_inventory'],
              authority['protocol'], authority['merged_source_review'], authority['driver_source'], authority['runtime_manifest'],
              *capsule['artifacts'], *capsule['original_reviews'], *certificate['source_effects_evidence'],
              *receipt['actual_trace_reviewer']['source_rollouts']]
    actor = receipt['adjudicator']
    values.extend([actor['native'],actor['final'],actor['model_evidence'],actor['execution']['generation_census_ref'],*actor['rollouts']])
    for entry in actor['trace_files']: values.extend(entry[k] for k in ('source','snapshot','export'))
    for record in receipt['quiescence'].values():
        proof = read(reference(out, record)); source = proof['collector_execution']
        values.extend([*proof['censuses'],*proof['scope_snapshots'],*source['source_refs'],source['typed_terminal_ref']])
    annex = read(reference(out, receipt['participants_annex']))
    for item in annex['appended_generations'] + annex['direct_actor_additions']: values.extend(item['raw_source_refs'])
    values.extend(direct_pid_reuse_refs(annex))
    for finding in verdict['defect_findings']: values.extend(finding['new_measurement_refs'])
    for row in verdict['check_eligibility']:
        for edge in row['dependency_paths']: values.extend(edge['evidence_refs'])
        for key in ('original_evidence_refs','independent_current_evidence_refs','integrity_refs'): values.extend(row.get(key, []))
    unique = {}
    for record in values:
        path = reference(out, record); normalized = {'path':path.relative_to(out).as_posix(),'sha256':record['sha256'],'bytes':record['bytes']}
        require(normalized['path'] not in unique or unique[normalized['path']] == normalized, 'Evidence closure has conflicting versions')
        unique[normalized['path']] = normalized
    return [unique[k] for k in sorted(unique)]


def generation_annex(out, receipt, capsule):
    """Separate append-only participants; never rewrite the old24 inventory."""
    _, _, generation = execution_modules()
    base = read(reference(out, capsule['writer_inventory']))
    annex = read(reference(out, receipt['participants_annex']))
    require(annex.get('schema') == 'b1-technical-adjudication-participants-annex-v1'
            and annex.get('base_writer_inventory') == capsule['writer_inventory']
            and annex.get('input_capsule_sha256') == receipt['input_capsule_sha256']
            and annex.get('original_primary_attempts') == 4 and annex.get('original_provenance_attempts') == 5,
            'Participant annex changed original inventory/caps/capsule')
    require(len(base.get('auxiliary_generations', [])) == 24, 'All24 original auxiliary anchors required')
    old_pids = [r['ProcessId'] for r in base['auxiliary_generations']]
    require(len(set(old_pids)) == 24, 'Original24 generation identities must remain distinct')
    rows = annex.get('appended_generations'); direct = annex.get('direct_actor_additions')
    require(isinstance(rows, list) and rows and isinstance(direct, list), 'Actual new participant generations required')
    seen = set()
    for item in rows + direct:
        require(item.get('first_phase') in ('before', 'after', 'acceptance') and text(item.get('role'))
                and text(item.get('session_id')), 'Actual participant phase/owner missing')
        refs(out, item.get('raw_source_refs'))
        pid = item.get('pid') if item in direct else item.get('row', {}).get('ProcessId')
        require(type(pid) is int and pid > 0 and pid not in seen and pid not in old_pids,
                'Actual participant PID duplicated/unknown or conflicts with an original24 generation')
        seen.add(pid)
        if item in rows:
            require(generation.archived_cim_rows(item['row']) == [item['row']], 'Full original CIM generation row required')
            generation.creation_ms(item['row'].get('CreationDate'))
            require(recorded_row(out, item['raw_source_refs'], item['row']), 'New generation does not occur in original raw source')
        else:
            require(isinstance(item.get('observed_row'), dict) and item['observed_row'].get('ProcessId') == pid,
                    'Original partial row must be preserved, never filled into a synthetic full generation')
            require(recorded_row(out, item['raw_source_refs'], item['observed_row']), 'Partial observation differs from original source')
    actor = receipt['adjudicator']
    require(any(r['role'] == ROLE and r['session_id'] == actor['session_id'] and r['first_phase'] == 'after'
                and r['row'] == actor['execution']['generation_row']
                and actor['execution']['generation_census_ref'] in r['raw_source_refs'] for r in rows),
            'Actual TA CLI generation omitted from annex')
    prov = capsule['original_participants']['provenance']
    executions = prov.get('execution_attempts', [])
    fifth = [e for e in executions if e.get('attempt') == 5]
    require(len(fifth) == 1 and type(fifth[0].get('cli_pid')) is int and fifth[0]['cli_pid'] > 0
            and fifth[0].get('process_tree_stopped') is True, 'Original actual provenance5 execution record required')
    execution_source = reference(out, prov['execution_source_ref'])
    require(recorded_row(out, [prov['execution_source_ref']], fifth[0]), 'Provenance5 execution differs from its original actual source')
    require(any(r['role'] == 'provenance' and r['session_id'] == prov['session_id'] and r['first_phase'] == 'before'
                and (r.get('pid') if r in direct else r['row']['ProcessId']) == fifth[0]['cli_pid']
                for r in rows + direct), 'Actual provenance5 new participant omitted')
    require(receipt['actual_trace_reviewer'].get('in_scope_writer_generations') == annex.get('actual_review_in_scope_writers'),
            'Actual review source writer set omitted or invented')
    require(all(r in rows + direct for r in annex['actual_review_in_scope_writers']), 'Review writer missing from closure')
    return base, rows, direct


def recorded_row(out, records, expected):
    """Match preserved original rows, including nested raw tool stdout JSON."""
    def unique(items):
        result = {}
        for key,value in items:
            require(key not in result, 'Duplicate raw process-source key'); result[key] = value
        return result
    def reject(_): raise ValueError('NonJSON process-source constant')
    def visit(value):
        if isinstance(value,dict): return value == expected or any(visit(v) for v in value.values())
        if isinstance(value,list): return any(visit(v) for v in value)
        if isinstance(value,str):
            try: return visit(json.loads(value,object_pairs_hook=unique,parse_constant=reject))
            except (ValueError,TypeError):
                for line in value.splitlines():
                    try:
                        if visit(json.loads(line,object_pairs_hook=unique,parse_constant=reject)): return True
                    except (ValueError,TypeError): continue
                return False
        return False
    for record in records:
        raw = reference(out,record).read_text(encoding='utf-8')
        try:
            if visit(json.loads(raw,object_pairs_hook=unique,parse_constant=reject)): return True
        except ValueError:
            for line in raw.splitlines():
                try:
                    if visit(json.loads(line,object_pairs_hook=unique,parse_constant=reject)): return True
                except ValueError: continue
    return False


def direct_pid_reuse_refs(annex):
    """Only the independently diagnosed original60256 evidence, never new anchors."""
    witness = annex.get('historical_pid60256_reuse_witness')
    if witness is None:
        return []
    require(isinstance(witness, dict) and set(witness) == {
        'diagnosis', 'current_observation', 'absence_observation', 'absence_censuses'}
        and isinstance(witness['absence_censuses'], list) and len(witness['absence_censuses']) == 2,
        'Exact historical60256 witness references required')
    return [witness['diagnosis'], witness['current_observation'], witness['absence_observation'],
            *witness['absence_censuses']]


def _direct_witness_ms(value):
    """Compare preserved ISO/Date timestamps locally; do not change the shared codec."""
    require(isinstance(value, str), 'Unknown historical60256 witness timestamp')
    match = re.fullmatch(r'/Date\(([0-9]+)\)/', value)
    if match:
        return int(match[1])
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Unknown historical60256 witness timestamp') from error
    require(parsed.tzinfo is not None, 'Historical60256 timestamp timezone missing')
    delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def effective_direct_actor_pids(out, capsule, annex, census, direct_actor_pids):
    """Pure per-census guard for the one immutable old60256 absence witness.

    Original inventory/raw rows remain intact. Callers must still check helper
    exclusions against ALL original/new anchors, and run the unchanged complete
    project/session/Unity guard and auxiliary-generation guard on this census.
    """
    base = read(reference(out, capsule['writer_inventory']))
    require(isinstance(direct_actor_pids, list) and all(type(p) is int and p > 0 for p in direct_actor_pids)
            and len(set(direct_actor_pids)) == len(direct_actor_pids)
            and set(base['direct_actor_pids']) <= set(direct_actor_pids),
            'Effective direct input omitted or duplicated an original writer PID')
    records = direct_pid_reuse_refs(annex)
    if not records:
        return list(direct_actor_pids)
    _, helper, generation = execution_modules()
    diagnosis, observation, old, *absence = [read(p) for p in refs(out, records)]
    require(records[0]['sha256'] == PID60256_DIAGNOSIS and records[1]['sha256'] == PID60256_OBSERVATION
            and capsule.get('opaque_id') == CASE and capsule.get('candidate_hash') == CANDIDATE
            and diagnosis.get('status') == 'ACTUAL_FRESH_BLOCK_DIAGNOSED_NOT_CLOSURE_APPROVAL'
            and diagnosis.get('reviewer_id') == '/root/review_dispute_tooling'
            and base['direct_actor_pids'].count(60256) == 1
            and len(base.get('auxiliary_generations', [])) == 24
            and len({r['ProcessId'] for r in base['auxiliary_generations']}) == 24
            and all(r['ProcessId'] != 60256 for r in base['auxiliary_generations']),
            'Only the independently diagnosed original60256 direct anchor may use this witness')
    original = diagnosis['absence_before_reuse_evidence']
    source_records = [diagnosis['current_actual_observation'], original['observation'], *original['complete_censuses']]
    require(len(source_records) == 4 and all(
        (actual['sha256'], actual['bytes']) == (expected['sha256'], expected['bytes'])
        for actual, expected in zip(records[1:], source_records)), 'Historical60256 original witness bytes differ')
    row = diagnosis['current_generation']
    require(set(row) == {'ProcessId', 'ParentProcessId', 'Name', 'CommandLine', 'ExecutablePath', 'CreationDate'}
            and row['ProcessId'] == 60256 and row['Name'] == 'conhost.exe'
            and generation.archived_cim_rows(row) == [row]
            and [r for r in observation.get('rows', []) if r.get('ProcessId') == 60256] == [row]
            and observation.get('direct_children') == [] and old.get('utc') == original['observation_utc']
            and absence == [old.get('census_before'), old.get('census_after')],
            'Historical60256 original full row/absence observations differ')
    absent_ms, born_ms = _direct_witness_ms(old['utc']), _direct_witness_ms(row['CreationDate'])
    require(born_ms > absent_ms, 'Historical60256 absence must strictly precede the proved new birth')
    for prior in absence:
        rows = prior.get('processes')
        require(prior.get('complete') is True and prior.get('collector_marker') == helper.COLLECTOR
                and isinstance(rows, list) and rows and generation.archived_cim_rows(rows) == rows
                and len({r['ProcessId'] for r in rows}) == len(rows)
                and not any(r['ProcessId'] == 60256 or r['ParentProcessId'] == 60256 for r in rows),
                'Old60256 actor or possible orphan present in complete absence witness')
    rows = census.get('processes')
    require(census.get('complete') is True and census.get('collector_marker') == helper.COLLECTOR
            and isinstance(rows, list) and rows and generation.archived_cim_rows(rows) == rows
            and len({r['ProcessId'] for r in rows}) == len(rows), 'Complete unique current60256 census required')
    require(not any(r['ParentProcessId'] == 60256 for r in rows),
            'Possible old/unknown/new60256 child blocks the narrow reuse witness')
    current = [r for r in rows if r['ProcessId'] == 60256]
    if not current:
        return list(direct_actor_pids)
    require(len(current) == 1 and set(current[0]) == set(row)
            and all(current[0][k] == row[k] for k in row if k != 'CreationDate')
            and _direct_witness_ms(current[0]['CreationDate']) == born_ms > absent_ms,
            'Current60256 is not the exact proved strictly later generation')
    return [pid for pid in direct_actor_pids if pid != 60256]


def verify_quiescence(out, receipt, capsule, session_ids):
    """Replay three ACTUAL phases, including their exact raw collector sources."""
    _, h, generation = execution_modules()
    base, appended, added_direct = generation_annex(out, receipt, capsule)
    annex = read(reference(out, receipt['participants_annex']))
    proofs = receipt.get('quiescence')
    require(isinstance(proofs, dict) and set(proofs) == {'before', 'after', 'acceptance'}, 'Three real fresh-proof phases required')
    order = {'before': 0, 'after': 1, 'acceptance': 2}
    for phase, record in proofs.items():
        proof = read(reference(out, record))
        current = [r for r in appended + added_direct if order[r['first_phase']] <= order[phase]]
        require(proof.get('schema') == 'b1-technical-adjudication-quiescence-v1'
                and proof.get('phase') == phase and proof.get('historical_writer_inventory_sha256') == capsule['writer_inventory']['sha256']
                and proof.get('participant_generations') == current
                and proof.get('historical_pid60256_reuse_witness') == annex.get('historical_pid60256_reuse_witness')
                and proof.get('source_pins') == receipt['source_pins'], 'Fresh proof identity/source/actual participants changed')
        snapshots = [read(path) for path in refs(out, proof.get('scope_snapshots'))]
        require(len(snapshots) == 3 and snapshots[0] == snapshots[1] == snapshots[2]
                and {'U', 'R', 'saves', 'logs', 'old_evidence'} <= set(snapshots[0]), 'Three actual stable scopes required per phase')
        require(snapshots[0]['U'] == api().inventory(Path(out) / 'blind/candidate')
                and snapshots[0]['R'] == api().provenance_module().inventory(Path(out) / 'blind/runtime'),
                'Actual original U/R changed across adjudication')
        saves = Path(os.environ['USERPROFILE']) / 'AppData/LocalLow/SJSBenchmark' / ('B1Audit-' + CASE)
        require(Path(capsule['saves_path']).resolve() == saves.resolve()
                and snapshots[0]['saves'] == h.save_inventory(saves), 'Actual external anonymous save state changed')
        censuses = [read(path) for path in refs(out, proof.get('censuses'))]
        require(len(censuses) == 2, 'Two complete original raw censuses required')
        source = proof.get('collector_execution', {})
        refs(out, source.get('source_refs')); terminal = read(reference(out, source['typed_terminal_ref']))
        authority = read(reference(out, receipt['authority']))
        require(all(ref in source['source_refs'] for ref in
                    (authority['driver_source'], authority['runtime_manifest'], authority['merged_source_review'])),
                'Actual fresh collector exact source/manifest/source-review tuple missing')
        require(terminal.get('type') == 'B1_TECHNICAL_FRESH_COLLECTOR_COMPLETED'
                and terminal.get('exit_code') == 0 and terminal.get('phase') == phase
                and terminal.get('censuses') == proof['censuses'] and terminal.get('scope_snapshots') == proof['scope_snapshots']
                and text(source.get('command')) and terminal.get('command') == source['command'],
                'Actual typed collector completion/command/raw output binding missing')
        generations = {r['ProcessId']: r for r in base['auxiliary_generations']}
        generations.update({r['row']['ProcessId']: r['row'] for r in appended if order[r['first_phase']] <= order[phase]})
        direct = list(base['direct_actor_pids']) + [r['pid'] for r in added_direct if order[r['first_phase']] <= order[phase]]
        for census in censuses:
            identity = source['helper_generation']; generation.creation_ms(identity.get('CreationDate'))
            require(not {identity['ProcessId'], census.get('collector_pid')}.intersection(set(generations) | set(direct)),
                    'Fresh helper cannot exclude a historical/new writer generation')
            effective_direct = effective_direct_actor_pids(out, capsule, annex, census, direct)
            verified = h.replay_census(census, Path(out) / 'blind/runtime', Path(out) / 'private', session_ids,
                base['known_instance_ids'], effective_direct, identity)
            generation.verify_auxiliary_absence(census, generations, set(verified['excluded_pids']))
    protected = read(reference(out, capsule['protected_inventory']))
    require(isinstance(protected, list) and protected, 'Original protected inventory missing')
    refs(out, protected)
