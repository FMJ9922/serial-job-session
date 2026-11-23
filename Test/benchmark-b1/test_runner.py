"""Local controller tests; fake subprocesses only, never invoke a model."""
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest import mock

try:
    from . import runner
except ImportError:
    import runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.admin = self.root / 'admin'
        self.admin.mkdir()
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'example.cs').write_text('original', encoding='utf-8')
        (self.admin / 'frozen').mkdir()
        (self.admin / 'frozen/unity_proxy.py').write_text('# frozen', encoding='utf-8')
        self.own = self.admin / 'groups/Demo'
        (self.own / 'home').mkdir(parents=True)
        (self.own / 'prompt.txt').write_text('fake task', encoding='utf-8')
        runner.write_json(self.own / 'state.json', {'status': 'ready', 'effective_seconds': 0, 'attempts': []})
        self.data = {'groups': {'Demo': {'id': 'Demo', 'model': 'test-exact', 'project': str(self.project), 'use_skill': False}},
                     'order': ['Demo'], 'cli': 'unused', 'limit_seconds': 60,
                     'baselines': {'Demo': {'commit': 'abc', 'files': runner.inventory(self.project)}},
                     'prompt_hashes': {'Demo': runner.digest(self.own / 'prompt.txt')},
                     'frozen_files': runner.inventory(self.admin / 'frozen')}
        self.save_manifest()

    def save_manifest(self):
        runner.write_json(self.admin / 'manifest.json', self.data)
        (self.admin / 'manifest.sha256').write_text(runner.digest(self.admin / 'manifest.json'))

    def fake(self, model='test-exact', final=True, sleep=.4):
        script = self.root / 'fake.py'
        script.write_text('import json,time,sys\n'
                          'sys.stdin.read()\n'
                          'print(json.dumps({"type":"thread.started","thread_id":"fake-id"}),flush=True)\n'
                          f'print(json.dumps({{"type":"turn_context","payload":{{"model":{model!r}}}}}),flush=True)\n'
                          + ('print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"done"}}),flush=True)\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":1}}),flush=True)\n' if final else '')
                          + f'time.sleep({sleep})\n', encoding='utf-8')
        return [sys.executable, str(script)]

    def test_exact_cli_and_agent_limits(self):
        cmd = runner.cli_command('codex', self.data['groups']['Demo'], self.root / 'proxy.py', self.root / 'out')
        self.assertEqual(cmd[cmd.index('--model') + 1], 'test-exact')
        self.assertIn('agents.max_concurrent_threads_per_session=2', cmd)
        self.assertIn('agents.default_subagent_reasoning_effort="high"', cmd)
        self.assertIn('--ignore-user-config', cmd)
        self.assertEqual(cmd[1:3], ['--no-daemon', 'exec'])

    def test_model_catalog_never_substitutes(self):
        p = self.root / 'cache.json'
        p.write_text(json.dumps({'models': [{'slug': 'other', 'supported_reasoning_levels': [{'effort': 'high'}]}]}))
        with self.assertRaisesRegex(RuntimeError, 'no substitution'):
            runner.validate_catalog(p, ['requested'])

    def test_readiness_requires_hashes_and_exact_models(self):
        p = self.root / 'readiness.json'
        report = {'ready': True, 'models': ['test-exact'], 'calibration_passed': True,
                  'image_access_passed': True, 'evidence_files': {str(self.project / 'example.cs'): runner.digest(self.project / 'example.cs')}}
        runner.write_json(p, report)
        runner.verify_readiness(p, ['test-exact'])
        with self.assertRaisesRegex(RuntimeError, 'exact three'):
            runner.verify_readiness(p, ['wrong'])
        (self.project / 'example.cs').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            runner.verify_readiness(p, ['test-exact'])

    def test_controller_lock_disallows_second_controller(self):
        with runner.controller_lock(self.admin):
            with self.assertRaises(FileExistsError):
                with runner.controller_lock(self.admin):
                    self.fail('second controller acquired lock')
        self.assertFalse((self.admin / 'CONTROLLER.lock').exists())

    def test_frozen_file_tampering_rejected(self):
        (self.admin / 'frozen/unity_proxy.py').write_text('modified')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            runner.manifest(self.admin)

    def test_dirty_initial_files_rejected_before_process(self):
        (self.project / 'example.cs').write_text('dirty')
        with self.assertRaisesRegex(RuntimeError, 'baseline'):
            runner.run_group(self.admin, 'Demo', command_override=['never-execute'])

    def test_final_response_seals_and_cannot_rerun(self):
        result = runner.run_group(self.admin, 'Demo', command_override=self.fake())
        self.assertEqual(result['status'], 'submitted')
        self.assertTrue(result['process_tree_stopped'])
        self.assertEqual((self.admin / 'sealed/Demo/project/example.cs').read_text(), 'original')
        self.assertFalse((self.admin / 'sealed/Demo/auth.json').exists())
        with self.assertRaises(RuntimeError):
            runner.run_group(self.admin, 'Demo', command_override=self.fake())

    def test_wrong_model_invalid(self):
        result = runner.run_group(self.admin, 'Demo', command_override=self.fake(model='wrong'))
        self.assertEqual(result['status'], 'invalid')
        self.assertTrue((self.admin / 'sealed/Demo/seal.json').exists())

    def test_timeout_stops_tree_then_seals(self):
        result = runner.run_group(self.admin, 'Demo', limit=.15, command_override=self.fake(final=False, sleep=10))
        self.assertEqual(result['status'], 'timeout')
        self.assertTrue(result['process_tree_stopped'])
        self.assertLess(result['effective_seconds'], 5)

    def test_no_final_pauses_no_snapshot_no_fresh_restart(self):
        result = runner.run_group(self.admin, 'Demo', command_override=self.fake(final=False))
        self.assertEqual(result['status'], 'paused')
        self.assertFalse((self.admin / 'sealed/Demo').exists())
        with self.assertRaises(RuntimeError):
            runner.run_group(self.admin, 'Demo', command_override=self.fake())

    def test_resume_requires_infrastructure_proof_and_bounds_deduction(self):
        result = runner.run_group(self.admin, 'Demo', command_override=self.fake(final=False))
        before = result['effective_seconds']
        with self.assertRaisesRegex(RuntimeError, 'adjudication'):
            runner.run_group(self.admin, 'Demo', resume=True, command_override=self.fake())
        evidence = self.root / 'outage-proof.txt'
        evidence.write_text('Synthetic confirmed transport outage')
        with self.assertRaisesRegex(RuntimeError, 'bounded'):
            runner.approve_infrastructure(self.admin, 'Demo', before + 10, evidence)
        reviewed = runner.approve_infrastructure(self.admin, 'Demo', .1, evidence)
        self.assertAlmostEqual(reviewed['effective_seconds'], before - .1)
        self.assertFalse(reviewed['needs_infrastructure_review'])
        with self.assertRaisesRegex(RuntimeError, 'already'):
            runner.approve_infrastructure(self.admin, 'Demo', .1, evidence)
        resumed = runner.run_group(self.admin, 'Demo', resume=True, command_override=self.fake())
        self.assertEqual(resumed['status'], 'submitted')
        self.assertTrue((self.admin / 'sealed/Demo/infrastructure/1/review.json').exists())

    def test_nested_resource_named_logs_is_not_excluded(self):
        nested = self.project / 'Assets/Logs'
        nested.mkdir(parents=True)
        (nested / 'texture.bin').write_bytes(b'image')
        cache = self.project / 'Library'
        cache.mkdir()
        (cache / 'generated.bin').write_bytes(b'cache')
        files = runner.inventory(self.project)
        self.assertIn('Assets/Logs/texture.bin', files)
        self.assertNotIn('Library/generated.bin', files)

    def policy_fixture(self):
        policy = self.root / 'operation-policy.txt'
        policy.write_text('用户约束：不操作真实鼠标和键盘，仅通过脚本验证。', encoding='utf-8')
        sha = runner.digest(policy)
        state = json.loads((self.own / 'state.json').read_text())
        state.update(status='paused', effective_seconds=3047.86, session_id='original-session',
                     process_tree_stopped=True, actual_model_verified=True,
                     observations={'actual_models': ['test-exact'], 'actual_efforts': ['high']},
                     attempts=[{'number': 1, 'wall_seconds': 3047.86, 'ended_utc': 'synthetic'}],
                     needs_infrastructure_review=True)
        runner.write_json(self.own / 'state.json', state)
        sessions = self.own / 'home/sessions'
        sessions.mkdir(exist_ok=True)
        rows = [{'type': 'session_meta', 'payload': {'id': 'original-session'}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                 'phase': 'commentary', 'content': [{'type': 'output_text', 'text': '仍在返工'}]}}]
        (sessions / 'root.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        evidence = self.root / 'policy-approval.json'
        document = {'version': 1, 'group': 'Demo', 'attempt': 1, 'session_id': 'original-session',
                    'model': 'test-exact', 'policy_sha256': sha, 'reviewed': True, 'final_absent': True,
                    'decision': 'user_policy_resume_approved', 'reviewer': 'synthetic judge'}
        runner.write_json(evidence, document)
        return policy, sha, evidence, document

    def test_operation_policy_hash_is_required_and_frozen_prompt_unchanged(self):
        policy = self.root / 'permissions.txt'
        policy.write_text('script only', encoding='utf-8')
        sha = runner.digest(policy)
        command = self.fake()
        script = Path(command[1])
        script.write_text(script.read_text().replace('sys.stdin.read()',
            'open("received.txt","w",encoding="utf-8").write(sys.stdin.buffer.read().decode("utf-8"))'), encoding='utf-8')
        runner.run_group(self.admin, 'Demo', command_override=command, policy_path=policy, policy_sha256=sha)
        self.assertEqual((self.project / 'received.txt').read_text(), 'fake task\n\nscript only')
        self.assertEqual(runner.digest(self.own / 'prompt.txt'), self.data['prompt_hashes']['Demo'])
        self.assertEqual((self.admin / 'sealed/Demo/user-policy/input-1/policy.txt').read_text(), 'script only')
        policy.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'hash changed'):
            runner.operation_policy(policy, sha)
        with self.assertRaisesRegex(RuntimeError, 'SHA256'):
            runner.operation_policy(policy, None)

    def test_policy_resume_preserves_time_session_and_never_claims_infrastructure(self):
        policy, sha, evidence, _ = self.policy_fixture()
        reviewed = runner.approve_user_policy(self.admin, 'Demo', policy, sha, evidence)
        self.assertEqual(reviewed['effective_seconds'], 3047.86)
        self.assertNotIn('infrastructure_approved_attempt', reviewed)
        command = self.fake()
        script = Path(command[1])
        script.write_text(script.read_text().replace('sys.stdin.read()',
            'open("received.txt","w",encoding="utf-8").write(sys.stdin.buffer.read().decode("utf-8"))'), encoding='utf-8')
        expected = runner.cli_command('codex', self.data['groups']['Demo'], self.root / 'proxy', self.root / 'final',
                                      reviewed['session_id'])
        self.assertEqual(expected[expected.index('resume') + 1], 'original-session')
        resumed = runner.run_group(self.admin, 'Demo', resume=True, command_override=command, limit=4000,
                                   policy_path=policy, policy_sha256=sha)
        self.assertGreaterEqual(resumed['effective_seconds'], 3047.86)
        self.assertEqual(resumed['attempts'][-1]['resume_basis'], 'user_policy_resume_approval')
        text = (self.project / 'received.txt').read_text(encoding='utf-8')
        self.assertIn(policy.read_text(encoding='utf-8'), text)
        self.assertNotIn('基础设施已恢复', text)
        self.assertTrue((self.admin / 'sealed/Demo/user-policy/approval-1/evidence.json').exists())

    def test_policy_review_rejects_wrong_binding_and_terminal_or_running_candidates(self):
        policy, sha, evidence, document = self.policy_fixture()
        for change in ({'session_id': 'new-session'}, {'attempt': 2}, {'group': 'Demo1'},
                       {'policy_sha256': '0' * 64}, {'final_absent': False}, {'model': 'wrong'}):
            runner.write_json(evidence, {**document, **change})
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'bind exact'):
                runner.approve_user_policy(self.admin, 'Demo', policy, sha, evidence)
        runner.write_json(evidence, document)
        state = json.loads((self.own / 'state.json').read_text())
        for change in ({'status': 'submitted'}, {'process_tree_stopped': False},
                       {'actual_model_verified': False}, {'session_id': None},
                       {'observations': {'final_text': 'delivered'}}):
            runner.write_json(self.own / 'state.json', {**state, **change})
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                runner.approve_user_policy(self.admin, 'Demo', policy, sha, evidence)

    def test_policy_approval_cannot_be_reused_for_a_new_attempt_or_missing_policy(self):
        policy, sha, evidence, _ = self.policy_fixture()
        runner.approve_user_policy(self.admin, 'Demo', policy, sha, evidence)
        with self.assertRaisesRegex(RuntimeError, 'adjudication'):
            runner.run_group(self.admin, 'Demo', resume=True, command_override=['never'])
        state = json.loads((self.own / 'state.json').read_text())
        state['attempts'].append({'number': 2})
        runner.write_json(self.own / 'state.json', state)
        with self.assertRaisesRegex(RuntimeError, 'adjudication'):
            runner.run_group(self.admin, 'Demo', resume=True, command_override=['never'],
                             policy_path=policy, policy_sha256=sha)

    def test_commentary_and_child_final_do_not_count_as_primary_delivery(self):
        policy, sha, evidence, _ = self.policy_fixture()
        state = json.loads((self.own / 'state.json').read_text())
        state['observations']['final_text'] = 'Still working commentary'
        runner.write_json(self.own / 'state.json', state)
        child = [{'type': 'session_meta', 'payload': {'id': 'child-session'}},
                 {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                  'phase': 'final_answer', 'content': [{'text': 'child done'}]}}]
        (self.own / 'home/sessions/child.jsonl').write_text('\n'.join(json.dumps(r) for r in child), encoding='utf-8')
        result = runner.approve_user_policy(self.admin, 'Demo', policy, sha, evidence)
        self.assertFalse(result['user_policy_resume_approval']['no_final_delivery_proof']['final_delivery_present'])

    def test_persisted_root_final_unknown_and_malformed_records_fail_closed(self):
        _, _, _, _ = self.policy_fixture()
        state = json.loads((self.own / 'state.json').read_text())
        path = self.own / 'home/sessions/root.jsonl'
        original = path.read_text(encoding='utf-8')
        records = [
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': []}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'channel': 'final', 'content': []}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'unknown', 'content': []}},
            {'type': 'unknown', 'payload': {}}, {'type': 'response_item', 'payload': None}]
        for record in records:
            path.write_text(original + '\n' + json.dumps(record), encoding='utf-8')
            with self.subTest(record=record), self.assertRaises(RuntimeError):
                runner.no_final_delivery_proof(self.own, state)
        path.write_text(original + '\n{bad', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            runner.no_final_delivery_proof(self.own, state)
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, 'persisted original root'):
            runner.no_final_delivery_proof(self.own, state)

    def test_native_terminal_marker_completed_answer_and_final_file_block_resume(self):
        self.policy_fixture()
        state = json.loads((self.own / 'state.json').read_text())
        for observed in ({'final_text': 'B1_FINAL_STATUS: submitted'},
                         {'final_text': 'done', 'turn_completed': True}):
            with self.subTest(observed=observed), self.assertRaisesRegex(RuntimeError, 'True final delivery'):
                runner.no_final_delivery_proof(self.own, {**state, 'observations': observed})
        (self.own / 'attempt-001.final.txt').write_text('Final answer', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'True final delivery'):
            runner.no_final_delivery_proof(self.own, state)

    def test_terminal_drain_failure_blocks_snapshot_and_retry_keeps_terminal(self):
        state = json.loads((self.own / 'state.json').read_text())
        state.update(status='submitted', process_tree_stopped=True, requires_terminal_drain=True)
        runner.write_json(self.own / 'state.json', state)
        with mock.patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'busy')):
            with self.assertRaisesRegex(RuntimeError, 'drain failed'):
                runner.seal(self.admin, 'Demo')
        self.assertFalse((self.admin / 'sealed/Demo').exists())
        failed = json.loads((self.own / 'state.json').read_text())
        self.assertEqual(failed['status'], 'submitted')
        self.assertTrue(failed['needs_seal_review'])
        with mock.patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"idle":true}', b'')):
            runner.seal(self.admin, 'Demo')
        self.assertTrue((self.admin / 'sealed/Demo/seal.json').exists())

    def test_windows_reparse_attribute_is_rejected_without_is_junction_api(self):
        fake_stat = types.SimpleNamespace(st_file_attributes=0x400)
        with mock.patch.object(Path, 'lstat', return_value=fake_stat):
            with self.assertRaisesRegex(RuntimeError, 'linked/reparse'):
                runner.check_links(self.project)

    def test_stop_flag_terminates(self):
        (self.own / 'STOP').touch()
        result = runner.run_group(self.admin, 'Demo', command_override=self.fake(final=False, sleep=10))
        self.assertEqual(result['status'], 'stopped')

    def test_snapshot_never_follows_link(self):
        try:
            (self.project / 'escape').symlink_to(self.root / 'outside')
        except (OSError, NotImplementedError):
            self.skipTest('Host does not permit symlinks')
        with self.assertRaisesRegex(RuntimeError, 'linked'):
            runner.inventory(self.project)


if __name__ == '__main__':
    unittest.main()
