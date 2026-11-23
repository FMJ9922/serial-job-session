"""Synthetic protocol fixtures only; no model, Unity or formal case access."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import audit
import audit_runner
import consistency
import measurement_dispute as md


class DisputeTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.root = Path(t.name); source = self.root / 'sealed'
        for folder in audit.BUSINESS_DIRS: (source / folder).mkdir(parents=True)
        (source / 'Assets/Game.cs').write_text('// synthetic unchanged source')
        (source / 'ProjectSettings/ProjectSettings.asset').write_text('PlayerSettings:\n  productName: Synthetic\n')
        self.opaque = getattr(self, '_opaque', 'opaque')
        self.out = self.root / 'case'; audit.init_audit(source, self.out, self.opaque, operational=True, primary_id='primary')
        self.blind = self.out / 'blind'; self.evidence = self.blind / 'evidence'; self.path = self.blind / 'audit.json'
        self.doc = audit.read_json(self.path)
        self.doc['auditor']['id'] = 'primary'
        log = self.evidence / 'measured.txt'; log.write_text('Synthetic recorded operation only; no game or model ran.')
        self.ev = [{'path': log.name, 'sha256': audit.sha256(log)}]
        for check in self.doc['checks']:
            check.update(status='verified', score=1, operation='synthetic operation', actual='synthetic observation', evidence=self.ev)
        self.check = next(c for c in self.doc['checks'] if c['id'] == '057')
        self.check.update(status='review_required', score=None)
        audit.write_json(self.path, self.doc)
        self.proof_root = self.out / 'private/measurement-dispute'; self.proof_root.mkdir(parents=True)
        self.manifest = audit.read_json(self.blind / 'manifest.json'); p = self.manifest['operational_provenance']
        self.p_args = (self.blind / 'candidate', self.blind / 'runtime', self.evidence, p)
        prov = {'actor_id': 'primary', 'invocation': {'kind': 'adapter', 'command': 'synthetic operation', 'refs': ['synthetic-call']},
                'source_refs': [{'path': 'Assets/Game.cs', 'sha256': self.manifest['audit_baseline']['Assets/Game.cs']}], 'evidence_refs': self.ev}
        pm = audit.provenance_module(); pm.begin_operation(*self.p_args, 'normal_run', 'measured-op', prov)
        pm.finish_operation(*self.p_args, 'measured-op', prov)
        self.chain = pm.validate_workspace(*self.p_args, require_review=False)
        audit.write_json(self.evidence / 'provenance-review.json', {
            'schema': 'b1-provenance-review-v1', 'manifest_sha256': p['manifest_sha256'], 'candidate_hash': p['candidate_hash'],
            'journal_head': self.chain['journal_head'], 'primary_id': 'primary', 'reviewer_id': 'provenance', 'independent': True,
            'decisions': [], 'final': {'approved': True, 'runtime_digest': self.chain['current_digest'],
              'reference_digest': self.chain['reference_digest'], 'basis': 'Synthetic protocol fixture only', 'evidence_refs': self.ev}})
        self.protected = md.evidence_inventory(self.evidence)
        audit.write_json(self.proof_root / 'protected-evidence.json', self.protected)
        for name in ('provenance-review.json', 'provenance/journal-head.json'):
            target = self.proof_root / 'protected-originals' / name
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((self.evidence / name).read_bytes())
        self.cp = self.checkpoint('primary', 4, 'primary-sid', 'primary final')
        self.rcp = self.checkpoint('review', 1, 'review-sid', 'review final')
        classification = self.proof_root / 'classification.json'; audit.write_json(classification, {
            'schema': 'b1-measurement-dispute-classification-v1', 'check_id': '057', 'actual_measured_dispute': True,
            'report_sha256': audit.sha256(self.path), 'original_check_sha256': md.digest(self.check),
            'primary_checkpoint': self.cp, 'reviewer_id': 'classification-reviewer'})
        effects = self.proof_root / 'writer-effects.json'; audit.write_json(effects, {'synthetic': 'independent writer/source-effects fixture'})
        writers = self.proof_root / 'writer-inventory.json'; audit.write_json(writers, {
            'schema': 'b1-dispute-known-writers-v1', 'report_sha256': audit.sha256(self.path), 'opaque_id': self.opaque,
            'primary_checkpoint': self.cp, 'direct_actor_pids': [9000], 'auxiliary_generations': [],
            'known_instance_ids': ['Synthetic@12345678'], 'source_effects_review': self.ref(effects)})
        self.authority = {'schema': 'b1-measurement-dispute-authority-v1', 'check_id': '057', 'report_sha256': audit.sha256(self.path),
            'candidate_hash': self.doc['candidate_hash'], 'opaque_id': self.opaque, 'primary_checkpoint': self.cp,
            'journal_head': self.chain['journal_head'], 'actual_measured_dispute': True, 'basis': 'Synthetic classified measured ambiguity',
            'original_check_sha256': md.digest(self.check), 'original_evidence': self.ev, 'classification_review': self.ref(classification),
            'writer_inventory': self.ref(writers)}
        authority = self.proof_root / 'authority.json'; audit.write_json(authority, self.authority)
        original = self.proof_root / 'report-original.json'; original.write_bytes(self.path.read_bytes())
        self.overlay = {'schema': 'b1-measurement-dispute-decision-v1', 'check_id': '057', 'candidate_hash': self.doc['candidate_hash'],
            'opaque_id': self.opaque, 'report_sha256': audit.sha256(self.path), 'original_check_sha256': md.digest(self.check),
            'journal_head': self.chain['journal_head'], 'decision': {'auditor_id': 'reviewer', 'status': 'verified', 'score': 0,
                'operation': 'Independent synthetic evidence inspection', 'expected': self.check['expected'],
                'actual': 'Synthetic independent decision, not a benchmark result', 'evidence': self.ev}}
        audit.write_json(self.evidence / md.OVERLAY, self.overlay)
        self.model = {'review-sid': [{'model': 'gpt-6.1-sol', 'effort': 'high'}]}
        model_path = self.proof_root / 'models.json'; audit.write_json(model_path, self.model)
        native = self.out / 'private/review/turn-1.jsonl'; final = self.out / 'private/review/turn-1.final.txt'
        rollout_name = 'home/sessions/synthetic.jsonl'; rollout = self.out / 'private/review' / rollout_name
        rollout.parent.mkdir(parents=True)
        rows = [{'type': 'session_meta', 'payload': {'id': 'review-sid', 'source': 'cli'}},
                {'type': 'turn_context', 'payload': {'turn_id': 'review-turn', 'model': 'gpt-6.1-sol', 'effort': 'high'}},
                {'type': 'event_msg', 'payload': {'type': 'task_started'}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete'}}]
        rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        entries, actual_files = [], []
        for source_file, source_path in [('turn-1.jsonl', native), (rollout_name, rollout)]:
            snap = self.out / 'private/review/trace-snapshots/1' / source_file
            exported = self.evidence / 'tool-traces/review/1' / source_file
            for dest in (snap, exported): dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(source_path.read_bytes())
            entries.append({'source_file': source_file, 'source_snapshot_file': 'trace-snapshots/1/' + source_file,
                            'exported_file': source_file, 'source_sha256': audit.sha256(source_path),
                            'exported_sha256': audit.sha256(source_path), 'redacted': False})
            actual_files.extend([self.ref(snap), self.ref(exported)])
        actual_files.extend([self.ref(native), self.ref(final)])
        self.tm = {'role': 'review', 'auditor_id': 'reviewer', 'session_id': 'review-sid', 'turn': 1, 'files': entries}
        trace_manifest = self.proof_root / 'export-manifest.json'; audit.write_json(trace_manifest, self.tm)
        trace_review = self.proof_root / 'actual-review.json'
        self.actual_review = {'schema': 'b1-measurement-dispute-actual-review-v1', 'approved': True, 'reviewer_id': 'trace-reviewer',
            'report_sha256': audit.sha256(self.path), 'decision_sha256': audit.sha256(self.evidence / md.OVERLAY),
            'independent_checkpoint': self.rcp, 'trace_files': actual_files, 'fresh_quiescence_verified': True,
            'writer_inventory_sha256': audit.sha256(writers), 'no_runtime_or_save_writes': True,
            'no_unity_calls_all_owners': True}
        self.actual_review.update(protected_evidence_sha256=audit.sha256(self.proof_root / 'protected-evidence.json'),
                                  old_evidence_and_journal_unchanged=True)
        audit.write_json(trace_review, self.actual_review)
        quiescence = self.proof_root / 'quiescence.json'; audit.write_json(quiescence, {
            'schema': 'b1-dispute-quiescence-v1', 'module_sha256': audit.sha256(Path(md.__file__)),
            'writer_inventory_sha256': audit.sha256(writers), 'censuses': [{'complete': True}, {'complete': True}],
            'verified': [{'no_assigned_editor': True, 'no_known_writers': True}] * 2,
            'auxiliary_generation_checks': [{'no_live_historical_auxiliary_generations': True}] * 2})
        self.receipt = {'schema': 'b1-measurement-dispute-acceptance-v1', 'module_sha256': audit.sha256(Path(md.__file__)),
            'execution_helper_sha256': audit.sha256(Path(md.execution_helper().__file__)),
            'process_generation_sha256': audit.sha256(Path(md.generation_module().__file__)),
            'report_sha256': audit.sha256(self.path), 'original_report': self.ref(original),
            'original_check_sha256': md.digest(self.check), 'candidate_hash': self.doc['candidate_hash'], 'check_id': '057',
            'authority': self.ref(authority), 'primary_checkpoint': self.cp,
            'primary_native': self.ref(self.out / 'private/primary/turn-4.jsonl'), 'primary_final': self.ref(self.out / 'private/primary/turn-4.final.txt'),
            'journal_head': self.chain['journal_head'], 'journal_operations': self.chain['operations'], 'provenance_reviewer_id': 'provenance',
            'independent': {'auditor_id': 'reviewer', 'session_id': 'review-sid', 'attempt': 1, 'model': 'gpt-6.1-sol', 'effort': 'high',
                            'checkpoint': self.rcp, 'model_evidence': self.ref(model_path), 'trace_manifest': self.ref(trace_manifest),
                            'native': self.ref(native), 'final': self.ref(final)},
            'actual_trace_files': actual_files, 'actual_trace_review': self.ref(trace_review),
            'acceptance_quiescence': self.ref(quiescence),
            'protected_evidence': self.ref(self.proof_root / 'protected-evidence.json'),
            'protected_originals': {name: self.ref(self.proof_root / 'protected-originals' / name)
                                   for name in ('provenance-review.json', 'provenance/journal-head.json')},
            'overlay': {'path': md.OVERLAY, 'sha256': audit.sha256(self.evidence / md.OVERLAY)}}
        self.publish()

    def ref(self, path): return {'path': path.relative_to(self.out).as_posix(), 'sha256': audit.sha256(path)}

    def checkpoint(self, role, attempt, sid, text):
        root = self.out / 'private' / role; root.mkdir(parents=True, exist_ok=True)
        native, final = root / f'turn-{attempt}.jsonl', root / f'turn-{attempt}.final.txt'
        native.write_text('\n'.join(json.dumps(x) for x in [{'type': 'thread.started', 'thread_id': sid}, {'type': 'turn.started'},
             {'type': 'item.completed', 'item': {'id': 'message1', 'type': 'agent_message', 'text': text}}, {'type': 'turn.completed'}]) + '\n')
        final.write_bytes(text.encode())
        return {'attempt': attempt, 'session_id': sid, 'native_sha256': audit.sha256(native), 'final_sha256': audit.sha256(final),
                'business_or_measurement_turn_reopened': False}

    def publish(self):
        audit.write_json(self.out / md.RECEIPT, self.receipt)
        judge = audit.read_json(self.out / 'judge_manifest.json'); judge['measurement_dispute_acceptance'] = self.ref(self.out / md.RECEIPT)
        audit.write_json(self.out / 'judge_manifest.json', judge)

    def test_accepted_independent_decision_supplies_effective_only(self):
        before = self.path.read_bytes(); result = audit.validate_report(self.path)
        c = next(c for c in result['checks'] if c['id'] == '057')
        self.assertEqual(c['score'], 0); self.assertIsNone(c['measurement_dispute']['primary_score'])
        self.assertEqual(before, self.path.read_bytes()); self.assertEqual(result['score'], 99)
        audit_runner.require_primary_measurements(self.doc, self.out)

    def test_missing_receipt_or_anchor_or_actual_review_blocks(self):
        for key in ('actual_trace_review', 'authority', 'primary_checkpoint'):
            old = copy.deepcopy(self.receipt); self.receipt.pop(key); self.publish()
            with self.subTest(key=key), self.assertRaises((ValueError, KeyError)): audit.validate_report(self.path)
            self.receipt = old
        self.publish(); (self.out / md.RECEIPT).unlink()
        with self.assertRaises((ValueError, OSError)): audit.validate_report(self.path)

    def test_wrong_model_sid_checkpoint_head_and_reference_rejected(self):
        changes = [lambda r: r['independent'].update(model='gpt-5.6-luna'),
                   lambda r: r['independent'].update(session_id='other'),
                   lambda r: r['independent']['checkpoint'].update(final_sha256='0'*64),
                   lambda r: r.update(journal_head='0'*64),
                   lambda r: r['overlay'].update(sha256='0'*64),
                   lambda r: r['primary_checkpoint'].update(session_id='wrong'),
                   lambda r: r['actual_trace_files'].pop()]
        original = copy.deepcopy(self.receipt)
        for change in changes:
            self.receipt = copy.deepcopy(original); change(self.receipt); self.publish()
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)): audit.validate_report(self.path)

    def test_not_run_blank_or_other_null_never_exempted(self):
        for index, updates in [(56, {'status': 'not_run'}), (56, {'operation': ''}), (55, {'score': None})]:
            value = copy.deepcopy(self.doc); value['checks'][index].update(updates); audit.write_json(self.path, value)
            with self.subTest(updates=updates), self.assertRaises(ValueError): audit.validate_report(self.path)

    def test_no_default_zero_when_independent_decision_incomplete(self):
        self.overlay['decision']['score'] = None; audit.write_json(self.evidence / md.OVERLAY, self.overlay)
        self.receipt['overlay']['sha256'] = audit.sha256(self.evidence / md.OVERLAY)
        # Updating root hash alone cannot provide new independent actual review.
        self.publish()
        with self.assertRaises(ValueError): audit.validate_report(self.path)

    def test_real_rollout_context_model_tamper_blocks_even_if_claim_unchanged(self):
        snap = self.out / 'private/review/trace-snapshots/1/home/sessions/synthetic.jsonl'
        snap.write_text(snap.read_text().replace('gpt-6.1-sol', 'gpt-5.6-luna'))
        with self.assertRaises(ValueError): audit.validate_report(self.path)

    def test_legal_future067_review_append_preserves057_but_primary_tamper_fails(self):
        initial = copy.deepcopy(self.doc)
        next(c for c in initial['checks'] if c['id'] == '067').update(review_required=True, review_reason='Synthetic genuine dispute067')
        self.reanchor(initial)
        value = copy.deepcopy(self.doc)
        c = next(c for c in value['checks'] if c['id'] == '067')
        c['review'] = {'auditor_id': 'reviewer', 'score': 1, 'operation': 'Independent067 evidence', 'expected': c['expected'],
                       'actual': 'Synthetic corroboration', 'evidence': self.ev}
        audit.write_json(self.path, value); self.assertEqual(audit.validate_report(self.path)['score'], 99)
        for field, content in [('score', 0), ('expected', 'changed'), ('review_required', True)]:
            broken = copy.deepcopy(value); broken['checks'][65][field] = content; audit.write_json(self.path, broken)
            with self.subTest(field=field), self.assertRaises(ValueError): audit.validate_report(self.path)

    def reanchor(self, document):
        """Build a NEW synthetic trusted fixture baseline, never a formal recovery."""
        self.doc = document; audit.write_json(self.path, document); raw_hash = audit.sha256(self.path)
        (self.proof_root / 'report-original.json').write_bytes(self.path.read_bytes())
        for name in ('classification.json', 'writer-inventory.json'):
            p = self.proof_root / name; value = audit.read_json(p); value['report_sha256'] = raw_hash; audit.write_json(p, value)
        self.authority.update(report_sha256=raw_hash, classification_review=self.ref(self.proof_root / 'classification.json'),
                              writer_inventory=self.ref(self.proof_root / 'writer-inventory.json'))
        audit.write_json(self.proof_root / 'authority.json', self.authority)
        self.overlay['report_sha256'] = raw_hash; audit.write_json(self.evidence / md.OVERLAY, self.overlay)
        self.actual_review.update(report_sha256=raw_hash, decision_sha256=audit.sha256(self.evidence / md.OVERLAY),
                                 writer_inventory_sha256=self.authority['writer_inventory']['sha256'])
        audit.write_json(self.proof_root / 'actual-review.json', self.actual_review)
        q = self.proof_root / 'quiescence.json'; value = audit.read_json(q)
        value['writer_inventory_sha256'] = self.authority['writer_inventory']['sha256']; audit.write_json(q, value)
        self.receipt.update(report_sha256=raw_hash, original_report=self.ref(self.proof_root / 'report-original.json'),
                            authority=self.ref(self.proof_root / 'authority.json'),
                            actual_trace_review=self.ref(self.proof_root / 'actual-review.json'), acceptance_quiescence=self.ref(q))
        self.receipt['overlay']['sha256'] = audit.sha256(self.evidence / md.OVERLAY); self.publish()

    def test_unflagged_independent_append_is_rejected(self):
        value = copy.deepcopy(self.doc); value['checks'][65]['review'] = {'auditor_id': 'reviewer', 'score': 1,
            'operation': 'unexpected', 'expected': value['checks'][65]['expected'], 'actual': 'unassigned', 'evidence': self.ev}
        audit.write_json(self.path, value)
        with self.assertRaises(ValueError): audit.validate_report(self.path)

    def test_flagged_item_other_than067_cannot_append_independent_decision(self):
        before = copy.deepcopy(self.doc)
        before['checks'][65].update(review_required=True, review_reason='Synthetic unrelated pending review')
        after = copy.deepcopy(before); after['checks'][65]['review'] = {'auditor_id': 'reviewer', 'score': 1}
        with self.assertRaises(ValueError): md.same_primary(before, after)

    def test_missing_original_provenance_snapshot_is_rejected(self):
        self.receipt['protected_originals'] = {}; self.publish()
        with self.assertRaisesRegex(ValueError, 'Both original'): audit.validate_report(self.path)

    def test_old_unreferenced_evidence_and_journal_cannot_be_overwritten(self):
        extra = self.evidence / 'historical-unreferenced.txt'; extra.write_text('original immutable bytes')
        inventory = md.evidence_inventory(self.evidence)
        extra.write_text('overwritten bytes')
        with self.assertRaises(ValueError): md.preserve_existing(self.evidence, inventory)
        with self.assertRaises(ValueError):
            md.preserve_existing(self.evidence, {**inventory, 'deleted-historical.txt': '0'*64})

    def test_new_dispute_turn_fourth_owner_is_blocked_not_scored(self):
        h = md.execution_helper(); entry = self.tm['files'][1]
        models = {'review-sid': [{'model': 'gpt-6.1-sol', 'effort': 'high'}]}
        nodes = {'review-sid': {'parent': None}}
        nodes.update({f'child-{i}': {'parent': 'review-sid'} for i in range(3)})
        with patch.object(h, 'rollout_own_streams', return_value=nodes):
            with self.assertRaisesRegex(ValueError, 'two descendants TOTAL|two descendants total'):
                md.verify_trace_execution(self.out, self.receipt['independent'], [entry], models)

    def test_child_own_unity_calls_are_rejected_and_exec_needs_actual_review(self):
        h = md.execution_helper(); context = {'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'high'}}
        for kind, name in [('function_call', 'mcp__unity__read_console'),
                           ('custom_tool_call', 'functions.mcp__unity__execute_code'),
                           ('function_call', 'mcp.unity.read_console')]:
            nodes = {'review-sid': {'parent': None, 'own': [(1, context)]},
                     'child': {'parent': 'review-sid', 'own': [(1, context), (2, {'type': 'response_item',
                         'payload': {'type': kind, 'name': name}})]}}
            models = {sid: [{'model': 'gpt-6.1-sol', 'effort': 'high'}] for sid in nodes}
            with self.subTest(kind=kind, name=name), patch.object(h, 'rollout_own_streams', return_value=nodes):
                with self.assertRaisesRegex(ValueError, 'owner invoked Unity'):
                    md.verify_trace_execution(self.out, self.receipt['independent'], [self.tm['files'][1]], models)
        self.actual_review.pop('no_unity_calls_all_owners')
        review = self.proof_root / 'actual-review.json'; audit.write_json(review, self.actual_review)
        self.receipt['actual_trace_review'] = self.ref(review); self.publish()
        with self.assertRaisesRegex(ValueError, 'actual source/trace/effects review'): audit.validate_report(self.path)

    def test_aux_generation_rules_do_not_seed_stale_numeric_parent(self):
        def row(pid, parent, creation):
            return dict(ProcessId=pid, ParentProcessId=parent, Name='synthetic.exe', CommandLine=None,
                        ExecutablePath=None, CreationDate=creation)
        generations = {38312: row(38312, 1, '/Date(200)/')}
        census = {'processes': [row(43128, 38312, '/Date(100)/')]}
        self.assertTrue(md.generation_module().verify_auxiliary_absence(census, generations, set())['no_live_historical_auxiliary_generations'])
        # Later exact creation now proves only the old PID generation ended;
        # no name/global nonwriter exemption or closure claim follows.
        reused = md.generation_module().verify_auxiliary_absence(
            {'processes': [row(38312, 1, '/Date(300)/')]}, generations, set())
        self.assertEqual(reused['excluded_reused_pid_generations'][0]['historical_creation_ms'], 200)
        self.assertEqual(reused['excluded_reused_pid_generations'][0]['current_creation_ms'], 300)
        for rows in ([row(38312, 1, '/Date(200)/')], [row(38312, 1, '/Date(100)/')],
                     [row(38312, 1, None)], [row(1, 38312, '/Date(300)/')], [row(1, 38312, None)],
                     [row(38312, 1, '/Date(300)/'), row(1, 38312, '/Date(200)/')],
                     [row(38312, 1, '/Date(300)/'), row(1, 38312, '/Date(300)/')]):
            with self.assertRaises((ValueError, RuntimeError)):
                md.generation_module().verify_auxiliary_absence({'processes': rows}, generations, set())

    def test_bootstrap_omitted_dependency_cannot_import_unverified_code(self):
        directory = self.root / 'bootstrap'; directory.mkdir()
        entry = directory / 'measurement_dispute.py'; entry.write_text('# isolated synthetic entry')
        marker = directory / 'marker'
        (directory / 'audit_runner.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("UNVERIFIED")\n')
        manifest = directory / 'manifest.json'
        audit.write_json(manifest, {'files': {'measurement_dispute.py': {'sha256': audit.sha256(entry), 'bytes': entry.stat().st_size}}})
        with patch.object(md, '__file__', str(entry)), patch.object(md, 'api', side_effect=AssertionError('import before verification')):
            with self.assertRaisesRegex(ValueError, 'omits'): md.load_runtime(audit.sha256(manifest))
        self.assertFalse(marker.exists())

    def test_bootstrap_payload_mismatch_blocks_before_any_runtime_import(self):
        directory = self.root / 'bootstrap-full'; directory.mkdir()
        names = ['measurement_dispute.py', 'audit.py', 'audit_runner.py', 'runner.py', 'closed_seal.py',
                 'operation_provenance.py', 'spec.py', 'runtime.py', 'process_generation.py', 'technical_adjudication.py', 'consistency.py', 'operation-policy.json']
        files = {}
        for name in names:
            p = directory / name; p.write_text('# synthetic unexecuted fixture')
            files[name] = {'sha256': audit.sha256(p), 'bytes': p.stat().st_size}
        manifest = directory / 'manifest.json'; audit.write_json(manifest, {'files': files})
        (directory / 'audit.py').write_text('raise RuntimeError("UNVERIFIED")')
        with patch.object(md, '__file__', str(directory / 'measurement_dispute.py')), patch.object(md, 'api', side_effect=AssertionError('import before verification')):
            with self.assertRaisesRegex(ValueError, 'payload changed'): md.load_runtime(audit.sha256(manifest))

    def bootstrap_bundle(self, name):
        directory = self.root / name; directory.mkdir()
        (directory / 'measurement_dispute.py').write_bytes(Path(md.__file__).read_bytes())
        for filename in ('spec.py', 'runner.py', 'operation_provenance.py', 'process_generation.py', 'closed_seal.py', 'technical_adjudication.py', 'consistency.py'):
            (directory / filename).write_text('# inert synthetic bootstrap dependency\n')
        (directory / 'audit.py').write_text('SOURCE_ONLY = True\n')
        (directory / 'audit_runner.py').write_text('import audit\nimport measurement_dispute\nSOURCE_ONLY = audit.SOURCE_ONLY\n')
        (directory / 'runtime.py').write_text('def configure_environment(): pass\ndef patch_audit(ar): pass\ndef audit_operation_policy(here): return {"sha256": "synthetic", "text": "synthetic"}\n')
        policy = directory / 'policy.md'; policy.write_text('Synthetic policy fixture only')
        audit.write_json(directory / 'operation-policy.json', {'version': 1, 'path': policy.name, 'sha256': audit.sha256(policy)})
        return directory

    def bootstrap_manifest(self, directory):
        files = {p.name: {'sha256': audit.sha256(p), 'bytes': p.stat().st_size}
                 for p in directory.iterdir() if p.is_file() and p.name != 'manifest.json'}
        manifest = directory / 'manifest.json'
        audit.write_json(manifest, {'schema': 'b1-infrastructure-freeze-v1', 'files': files})
        return manifest

    def test_bootstrap_unlisted_policy_target_blocks_before_import(self):
        directory = self.bootstrap_bundle('bootstrap-policy')
        manifest = self.bootstrap_manifest(directory); value = audit.read_json(manifest)
        value['files'].pop('policy.md'); audit.write_json(manifest, value)
        with patch.object(md, '__file__', str(directory / 'measurement_dispute.py')):
            with self.assertRaisesRegex(ValueError, 'policy target omitted'): md.load_runtime(audit.sha256(manifest))

    def test_bootstrap_uses_verified_sources_even_with_valid_unlisted_pyc(self):
        import importlib.util
        from importlib._bootstrap_external import _code_to_timestamp_pyc
        directory = self.bootstrap_bundle('bootstrap-pyc'); source = directory / 'audit.py'
        marker = directory / 'unverified-pyc-executed'
        bad = compile('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("UNVERIFIED")\nSOURCE_ONLY=False\n', str(source), 'exec')
        pyc = Path(importlib.util.cache_from_source(str(source))); pyc.parent.mkdir()
        pyc.write_bytes(_code_to_timestamp_pyc(bad, int(source.stat().st_mtime), source.stat().st_size))
        manifest = self.bootstrap_manifest(directory)
        code = ('import measurement_dispute as md\nimport sys\n'
                + 'ar=md.load_runtime(' + repr(audit.sha256(manifest)) + ')\n'
                + 'assert ar.SOURCE_ONLY is True\nassert sys.modules["measurement_dispute"] is md\n')
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=directory, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertFalse(marker.exists())

    def test_real_source_bundle_loads_without_running_a_case(self):
        directory = self.root / 'real-source-bootstrap'; directory.mkdir()
        source = Path(md.__file__).parent
        for name in ('measurement_dispute.py', 'audit.py', 'audit_runner.py', 'runner.py', 'closed_seal.py',
                     'operation_provenance.py', 'spec.py', 'runtime.py', 'process_generation.py', 'technical_adjudication.py', 'consistency.py'):
            (directory / name).write_bytes((source / name).read_bytes())
        policy = directory / 'policy.md'; policy.write_text('Synthetic no-execution policy fixture')
        audit.write_json(directory / 'operation-policy.json', {'version': 1, 'path': policy.name, 'sha256': audit.sha256(policy)})
        manifest = self.bootstrap_manifest(directory)
        code = ('import measurement_dispute as md\nimport sys\n'
                + 'ar=md.load_runtime(' + repr(audit.sha256(manifest)) + ')\n'
                + 'assert ar.audit.spec.SPEC_VERSION == "B1"\n'
                + 'assert ar.audit.measurement_dispute_module() is md\n'
                + 'assert ar._measurement_dispute_operation_policy["sha256"] == ' + repr(audit.sha256(policy)) + '\n')
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=directory, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_original_existing_review_cannot_be_replaced(self):
        before = copy.deepcopy(self.doc); before['checks'][66]['review'] = {'old': 'immutable'}
        after = copy.deepcopy(before); after['checks'][66]['review'] = {'replacement': True}
        with self.assertRaises(ValueError): md.same_primary(before, after)

    def test_classification_is_not_technical_score_approval(self):
        md.eligible(self.path, self.authority, self.cp, self.chain['journal_head'])
        authority = copy.deepcopy(self.authority); authority['actual_measured_dispute'] = False
        with self.assertRaises(ValueError): md.eligible(self.path, authority, self.cp, self.chain['journal_head'])
        authority = copy.deepcopy(self.authority); authority['original_evidence'] = []
        with self.assertRaises(ValueError): md.eligible(self.path, authority, self.cp, self.chain['journal_head'])

    def test_legacy_no_overlay_result_retains_original_shape_and_score(self):
        judge = audit.read_json(self.out / 'judge_manifest.json'); judge.pop('measurement_dispute_acceptance')
        audit.write_json(self.out / 'judge_manifest.json', judge)
        self.check.update(status='verified', score=1); audit.write_json(self.path, self.doc)
        result = audit.validate_report(self.path)
        self.assertEqual(result['score'], 100); self.assertTrue(all('measurement_dispute' not in c for c in result['checks']))

    def test_primary_cannot_replace_accepted_null_with_self_score(self):
        self.check.update(status='verified', score=1); audit.write_json(self.path, self.doc)
        with self.assertRaises(ValueError): audit.validate_report(self.path)

    def test_consistency_pack_exposes_primary_null_overlay_and_actual_evidence(self):
        # Six fully independent synthetic protocol fixtures; no real audits.
        paths = [self.path]
        for i in range(1, 6):
            other = DisputeTests('test_accepted_independent_decision_supplies_effective_only')
            other._opaque = 'opaque-' + str(i); other.setUp(); self.addCleanup(other.doCleanups)
            paths.append(other.path)
        pack = consistency.build_pack(paths, self.root / 'pack')
        row = next(r for r in pack['rows'] if r['id'] == '057')
        self.assertEqual(len(row['candidates']), 6)
        for c in row['candidates']:
            self.assertIsNone(c['original_score']); self.assertEqual(c['effective_score'], 0)
            self.assertIsNone(c['measurement_dispute']['primary_score'])
            self.assertEqual(c['measurement_dispute']['decision']['score'], 0)
            self.assertTrue(c['measurement_dispute']['evidence_refs'])
        self.assertEqual(pack['validator']['measurement_dispute_sha256'], audit.sha256(Path(md.__file__)))


if __name__ == '__main__': unittest.main()
