"""Synthetic evidence-only tests; no formal audits, models or Unity are read/run."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import horizontal_review as hr


class HorizontalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.out = self.root / 'horizontal'
        self.bundle = self.root / 'judge-consistency-002'
        self.bundle.mkdir()
        for name in hr.BUNDLE_FILES:
            (self.bundle / name).write_text('# synthetic frozen validator')
        self.hashes = {name: hr.sha(self.bundle / name) for name in hr.BUNDLE_FILES}
        self.reports = []
        self.candidates = []
        self.index = []
        for number in range(6):
            opaque = f'anonymous-{number}'
            blind = self.root / opaque
            evidence = blind / 'evidence/actual.txt'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('Actual synthetic measurement ' + str(number))
            digest = hr.sha(evidence)
            report = blind / 'audit.json'
            hr.write(report, {'opaque_id': opaque, 'checks': [{'id': f'{n:03}', 'evidence': [{'path': 'actual.txt', 'sha256': digest}]} for n in range(1, 101)]})
            self.reports.append(report)
            self.candidates.append({'opaque_id': opaque, 'workspace_mode': 'derived_operations_v1'})
            self.index.append({'id': opaque + ':' + digest, 'sha256': digest, 'bytes': evidence.stat().st_size})
        self.pack = {'candidates': self.candidates, 'evidence_index': self.index,
                     'rows': [{'id': f'{n:03}', 'requirement': 'Original requirement', 'candidates': [
                         {'opaque_id': c['opaque_id'], 'effective_score': 1, 'operation': 'original observation',
                          'evidence_refs': [self.index[i]['id']], 'review': None, 'adjudication': None}
                         for i, c in enumerate(self.candidates)]} for n in range(1, 101)]}

    def fake_build(self, bundle, hashes, reports, target):
        hr.check_bundle(bundle, hashes)
        target.mkdir()
        hr.write(target / 'consistency.json', self.pack)
        return self.pack

    def test_complete_transitive_bundle_is_pinned_before_subprocess(self):
        self.assertEqual(set(hr.BUNDLE_FILES), {'consistency.py', 'audit.py', 'operation_provenance.py', 'spec.py',
                                              'measurement_dispute.py', 'closed_seal.py', 'process_generation.py', 'runner.py', 'technical_adjudication.py'})
        for omitted in ('measurement_dispute.py', 'process_generation.py', 'runner.py', 'technical_adjudication.py'):
            bad = {k: v for k, v in self.hashes.items() if k != omitted}
            with mock.patch.object(hr.subprocess, 'run') as run:
                with self.assertRaises(ValueError): hr.build_pack(self.bundle, bad, self.reports, self.root / 'not-created')
                run.assert_not_called()
        (self.bundle / 'process_generation.py').write_text('# changed dependency')
        with mock.patch.object(hr.subprocess, 'run') as run:
            with self.assertRaises(ValueError): hr.build_pack(self.bundle, self.hashes, self.reports, self.root / 'not-created')
            run.assert_not_called()

    def test_overlay_actual_evidence_and_decision_bytes_are_collected(self):
        report_path = self.reports[0]; report = hr.read(report_path)
        check = next(c for c in report['checks'] if c['id'] == '057')
        check.update(status='review_required', score=None); hr.write(report_path, report)
        root = report_path.parent / 'evidence'; sidecar = root / 'measurement-dispute.json'; sidecar.write_text('{"synthetic":"independent decision"}')
        detail = root / 'independent-observation.txt'; detail.write_text('Synthetic actual independent observation')
        extra = []
        for p in (sidecar, detail):
            key = 'anonymous-0:' + hr.sha(p)
            self.index.append({'id': key, 'sha256': hr.sha(p), 'bytes': p.stat().st_size}); extra.append(key)
        row = next(r for r in self.pack['rows'] if r['id'] == '057')
        row['candidates'][0]['evidence_refs'] += extra
        accepted = {'overlay_sha256': hr.sha(sidecar), 'decision': {'evidence': [{'path': detail.name, 'sha256': hr.sha(detail)}]}}
        with mock.patch.object(hr.ar.audit.measurement_dispute_module(), 'resolve', return_value=accepted):
            self.prepare()
        manifest = hr.read(self.out / 'private/manifest.json')
        artifacts = {a['evidence_id']: a for a in manifest['artifacts']}
        self.assertTrue(set(extra) <= set(artifacts))
        for key in extra:
            self.assertEqual(hr.sha(self.out / artifacts[key]['original_file']), key.rsplit(':', 1)[1])

    def prepare(self):
        with mock.patch.object(hr, 'build_pack', side_effect=self.fake_build):
            return hr.prepare(self.reports, self.bundle, self.hashes, self.out)

    def stage(self):
        self.prepare()
        template = hr.read(self.out / 'private/blinding-template.json')
        receipt = self.out / 'private/independent-review.txt'
        receipt.write_text('Synthetic independently recorded per-artifact review')
        ref = {'path': 'private/independent-review.txt', 'sha256': hr.sha(receipt)}
        template.update(root_authorized=True, independent=True, reviewer_id='separate-blinding-reviewer',
                        basis='Synthetic explicit root approval', actual_review_evidence=[ref],
                        reviewed_pack_file='private/consistency-002/consistency.json',
                        reviewed_pack_sha256=hr.sha(self.out / 'private/consistency-002/consistency.json'))
        for item in template['artifacts']:
            item.update(approved=True, provided_file=item['original_file'], provided_sha256=item['original_sha256'],
                        redaction_basis='Actual synthetic fixture contains no private identity', actual_review_evidence=[ref])
        approval = self.root / 'root-approval.json'
        hr.write(approval, template)
        hr.stage(self.out, approval, hr.sha(approval))
        return approval

    def result(self):
        capsule = hr.check_capsule(self.out)
        evidence = {x['evidence_id']: x for x in capsule['artifacts']}
        report = {'schema': 'b1-horizontal-result-v1', 'reviewer_id': capsule['reviewer_id'],
                  'pack_sha256': capsule['pack_sha256'], 'checks': []}
        for row in self.pack['rows']:
            values = []
            for candidate in row['candidates']:
                artifact = evidence[candidate['evidence_refs'][0]]
                values.append({'opaque_id': candidate['opaque_id'], 'actual_evidence_observation': 'Observed original fixture',
                    'evidence_examined': [{'artifact_id': artifact['artifact_id'], 'provided_sha256': artifact['provided_sha256'], 'observation_locator': 'line 1'}]})
            report['checks'].append({'id': row['id'], 'status': 'consistent', 'basis': 'Same original standard', 'candidates': values})
        path = self.out / 'capsule/horizontal-result.json'
        hr.write(path, report)
        return path

    def test_incomplete_six_inputs_or_frozen_bundle_failure_produce_no_output(self):
        with self.assertRaises(ValueError):
            hr.prepare(self.reports[:5], self.bundle, self.hashes, self.out)
        with mock.patch.object(hr, 'build_pack', side_effect=ValueError('provenance incomplete')):
            with self.assertRaisesRegex(ValueError, 'provenance incomplete'):
                hr.prepare(self.reports, self.bundle, self.hashes, self.out)
        self.assertFalse(self.out.exists())
        (self.bundle / 'audit.py').write_text('# changed')
        with self.assertRaisesRegex(ValueError, 'frozen'):
            hr.check_bundle(self.bundle, self.hashes)

    def test_prepare_copies_actual_bytes_but_never_releases_raw_evidence(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'blocked_pending_blinding')
        self.assertFalse((self.out / 'capsule').exists())
        manifest = hr.check_preparation(self.out)
        self.assertEqual(len(manifest['artifacts']), 6)
        for artifact in manifest['artifacts']:
            self.assertEqual(hr.sha(self.out / artifact['original_file']), artifact['original_sha256'])
        with self.assertRaises(ValueError):
            hr.stage(self.out, self.out / 'private/blinding-template.json', hr.sha(self.out / 'private/blinding-template.json'))

    def test_stage_requires_each_actual_artifact_and_locks_capsule_bytes(self):
        approval = self.stage()
        capsule = hr.check_capsule(self.out)
        self.assertEqual(len(capsule['artifacts']), 6)
        self.assertNotIn(str(self.root), json.dumps(capsule))
        (self.out / 'capsule' / capsule['artifacts'][0]['path']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'released evidence changed'):
            hr.check_capsule(self.out)

    def test_structural_result_requires_100_times_six_actual_refs_and_never_claims_substantive_completion(self):
        self.stage()
        path = self.result()
        result = hr.validate_result(self.out, path)
        self.assertEqual(result['candidate_examinations'], 600)
        self.assertEqual(result['status'], 'schema_valid_pending_actual_trace_review')
        self.assertFalse(result['scores_changed'])
        original = hr.read(path)
        for change in ('missing_id', 'duplicate_id', 'missing_candidate', 'index_only', 'wrong_evidence'):
            report = copy.deepcopy(original)
            if change == 'missing_id':
                report['checks'].pop()
            elif change == 'duplicate_id':
                report['checks'][-1]['id'] = '001'
            elif change == 'missing_candidate':
                report['checks'][0]['candidates'].pop()
            elif change == 'index_only':
                report['checks'][0]['candidates'][0]['evidence_examined'] = []
            else:
                report['checks'][0]['candidates'][0]['evidence_examined'][0]['provided_sha256'] = '0' * 64
            hr.write(path, report)
            with self.subTest(change=change), self.assertRaises(ValueError):
                hr.validate_result(self.out, path)

    def test_no_approval_cannot_launch_any_model(self):
        self.prepare()
        with mock.patch.object(hr.subprocess, 'Popen') as popen, self.assertRaises(OSError):
            hr.run(self.out, Path('not-a-real-cli'), self.root)
        popen.assert_not_called()

    def test_real_fake_cli_tracks_exact_model_session_checkpoint_and_stops_tree(self):
        self.stage()
        self.result()
        script = self.root / 'fake_cli.py'
        script.write_text('import json,os,pathlib,sys\n'
            'sys.stdin.read()\n'
            'h=pathlib.Path(os.environ["CODEX_HOME"]); (h/"sessions").mkdir()\n'
            '(h/"sessions/root.jsonl").write_text("\\n".join(json.dumps(x) for x in '
            '[{"type":"session_meta","payload":{"id":"horizontal-root"}},'
            '{"type":"turn_context","payload":{"model":"gpt-6.1-sol","effort":"high"}},'
            '{"type":"event_msg","payload":{"type":"task_complete"}}]))\n'
            'f=pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]); f.write_bytes(b"Finished")\n'
            'for x in [{"type":"thread.started","thread_id":"horizontal-root"},'
            '{"type":"item.completed","item":{"id":"final","type":"agent_message","text":"Finished"}},'
            '{"type":"turn.completed"}]: print(json.dumps(x),flush=True)\n', encoding='utf-8')
        original_popen = hr.subprocess.Popen
        def fake_popen(command, **kwargs):
            self.assertEqual(command[command.index('--model') + 1], 'gpt-6.1-sol')
            self.assertIn('model_reasoning_effort="high"', command)
            self.assertFalse(any('mcp_servers.unity' in x for x in command))
            self.assertNotIn('CODEX_THREAD_ID', kwargs['env'])
            return original_popen([sys.executable, str(script), *command[1:]], **kwargs)
        with mock.patch.object(hr, 'build_pack', side_effect=self.fake_build), mock.patch.object(hr.subprocess, 'Popen', side_effect=fake_popen):
            result = hr.run(self.out, Path('synthetic-cli'), self.root)
        self.assertEqual(result['status'], 'pending_independent_actual_trace_review')
        self.assertEqual(result['session_id'], 'horizontal-root')
        state = hr.read(self.out / 'private/run-state.json')
        self.assertTrue(state['process_tree_stopped'])
        self.assertEqual(state['model_evidence']['horizontal-root'][0], {'model': 'gpt-6.1-sol', 'effort': 'high'})
        self.assertIn('native_sha256', state['completed_turn_checkpoint'])
        receipt = state['trace_manifests'][0]
        self.assertEqual(hr.sha(self.out / receipt['path']), receipt['sha256'])
        for entry in receipt['receipt']['files']:
            self.assertEqual(hr.sha(self.out / entry['snapshot_relative']), entry['sha256'])
        self.assertTrue(any(entry['source_relative'].endswith('horizontal-result.json') for entry in receipt['receipt']['files']))
        hr.verify_trace_history(self.out, state)
        native = self.out / 'private/turn-1.jsonl'
        data = native.read_bytes()
        native.unlink()
        with self.assertRaises(OSError):
            hr.verify_trace_history(self.out, state)
        native.write_bytes(data + b'\n')
        with self.assertRaisesRegex(ValueError, 'Historical native/final'):
            hr.verify_trace_history(self.out, state)

    def test_trajectory_binds_actual_root_all_children_and_all_native_attempts(self):
        self.prepare()
        private = self.out / 'private'
        home = private / 'home'
        (home / 'sessions').mkdir(parents=True)
        state = {'session_id': 'root', 'attempts': 1}
        (private / 'turn-1.jsonl').write_text('{"type":"turn.completed"}')
        good = {'record_incomplete': False, 'terminal_event_present': True, 'attribution': {'parent': None}}
        helper = mock.Mock()
        with mock.patch.object(hr.ar, 'model_metadata', return_value={'other': []}), self.assertRaisesRegex(ValueError, 'root session owner'):
            hr.verify_trajectory(self.out, home, state, helper)
        helper.rollout_pairs.return_value = {'root': good, 'child': {'record_incomplete': True, 'terminal_event_present': False, 'attribution': {'parent': 'root'}}}
        with mock.patch.object(hr.ar, 'model_metadata', return_value={'root': [], 'child': []}), self.assertRaisesRegex(ValueError, 'terminal event'):
            hr.verify_trajectory(self.out, home, state, helper)
        for ownership in ({'root': good, 'other': good}, {'root': {**good, 'attribution': {'parent': 'parent'}}, 'parent': good},
                          {'root': good, 'child': {**good, 'attribution': {'parent': 'missing'}}}):
            helper.rollout_pairs.return_value = ownership
            with self.subTest(owners=list(ownership)), mock.patch.object(hr.ar, 'model_metadata', return_value={key: [] for key in ownership}), self.assertRaisesRegex(ValueError, 'root|descendant'):
                hr.verify_trajectory(self.out, home, state, helper)
        (private / 'turn-1.jsonl').unlink()
        with mock.patch.object(hr.ar, 'model_metadata', return_value={'root': []}), self.assertRaisesRegex(ValueError, 'Every actual horizontal native'):
            hr.verify_trajectory(self.out, home, state, helper)
        # A prior turn's unresolved native request survives a same-session resume.
        hr.write(private / 'dummy.json', {})
        (private / 'turn-1.jsonl').write_text(json.dumps({'type': 'item.started', 'item': {'id': 'pending', 'type': 'mcp_tool_call', 'server': 'unknown'}}))
        (private / 'turn-2.jsonl').write_text('{"type":"turn.completed"}')
        state['attempts'] = 2
        real = hr.ar.closed_helper(hr.local_sources()['closed_seal.py'])
        with mock.patch.object(hr.ar, 'model_metadata', return_value={'root': []}), self.assertRaisesRegex(RuntimeError, 'Pending native'):
            hr.verify_trajectory(self.out, home, state, real)

    def test_result_must_cover_every_original_and_independent_evidence_reference(self):
        # Add a distinct second actual artifact for one requirement/candidate.
        evidence = self.reports[0].parent / 'evidence/second.txt'
        evidence.write_text('Distinct actual second observation')
        report = hr.read(self.reports[0])
        digest = hr.sha(evidence)
        report['checks'][0]['evidence'].append({'path': 'second.txt', 'sha256': digest})
        hr.write(self.reports[0], report)
        key = self.candidates[0]['opaque_id'] + ':' + digest
        self.pack['evidence_index'].append({'id': key, 'sha256': digest, 'bytes': evidence.stat().st_size})
        self.pack['rows'][0]['candidates'][0]['evidence_refs'].append(key)
        self.stage()
        path = self.result()  # This fixture intentionally cites only the first artifact.
        with self.assertRaisesRegex(ValueError, 'Every original/review/adjudication'):
            hr.validate_result(self.out, path)


if __name__ == '__main__':
    unittest.main()
