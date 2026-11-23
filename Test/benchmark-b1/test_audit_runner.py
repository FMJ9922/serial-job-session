"""Audit scheduler tests: synthetic deliveries, fake CLI, no Unity or API calls."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import audit_runner as ar


class AuditRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.admin = self.base / 'admin'
        frozen = self.admin / 'frozen'
        (frozen / 'unity-mcp-skill').mkdir(parents=True)
        (frozen / 'unity-mcp-skill/SKILL.md').write_text('Unity common skill')
        (frozen / 'unity_proxy.py').write_text('# frozen neutral proxy')
        self.auth = self.base / 'auth'
        self.auth.mkdir()
        (self.auth / 'auth.json').write_text('{"private":"fake"}')
        groups = {g['id']: g for g in ar.spec.GROUPS}
        for key in groups:
            entry = self.admin / 'sealed' / key
            project = entry / 'project'
            for folder in ('Assets', 'Packages', 'ProjectSettings'):
                (project / folder).mkdir(parents=True)
            (project / 'Assets/game.cs').write_text('// candidate code')
            (project / 'Packages/manifest.json').write_text('{}')
            bootstrap = project / 'Packages/com.coplaydev.unity-mcp/Editor/McpCiBoot.cs'
            bootstrap.parent.mkdir(parents=True)
            bootstrap.write_text('// Synthetic original MCP startup entry')
            (project / 'ProjectSettings/ProjectSettings.asset').write_text('PlayerSettings:\n  productName: AITest_' + key + '\n')
            (project / 'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 2022.3.62f2\n')
            ar.save(entry / 'seal.json', {'source_files': ar.runner.inventory(project)})
            ar.save(entry / 'state.json', {'status': 'submitted', 'process_tree_stopped': True,
                                          'requires_terminal_drain': True, 'terminal_drain': {'passed': True},
                                          'actual_model_verified': True, 'effective_seconds': 12})
        ar.save(self.admin / 'manifest.json', {'groups': groups, 'order': ar.spec.ORDER,
                                             'frozen_files': ar.runner.inventory(frozen), 'cli': 'fake-cli'})
        (self.admin / 'manifest.sha256').write_text(ar.runner.digest(self.admin / 'manifest.json'))
        self.editor = self.base / 'fake-editor.exe'
        self.editor.write_text('Never run in tests')
        self.root = self.base / 'audits'

    def init(self):
        return ar.initialize(self.admin, self.root, self.editor, self.auth, operational=False)

    def verified_report(self, out, auditor_id):
        report = ar.read(out / 'blind/audit.json')
        report['auditor']['id'] = auditor_id
        evidence = out / 'blind/evidence/fake.txt'
        evidence.write_text('Synthetic unit-test measurement')
        for check in report['checks']:
            check.update(status='verified', score=1, operation='fake operation', actual='synthetic pass',
                         evidence=[{'path': 'fake.txt', 'sha256': ar.audit.sha256(evidence)}])
        ar.save(out / 'blind/audit.json', report)

    def test_guard_all_six_before_any_write(self):
        (self.admin / 'sealed/Demo4/seal.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'All six'):
            self.init()
        self.assertFalse(self.root.exists())

    def test_sealed_source_changes_block_audit(self):
        (self.admin / 'sealed/Demo/project/Assets/game.cs').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.init()

    def test_all_six_formal_editors_require_archived_drain_evidence(self):
        state_path = self.admin / 'sealed/Demo4/state.json'
        state = ar.read(state_path)
        state.pop('requires_terminal_drain')
        state.pop('terminal_drain')
        ar.save(state_path, state)
        with self.assertRaisesRegex(RuntimeError, 'terminal drain was not confirmed'):
            self.init()
        self.assertFalse(self.root.exists())

    def test_anonymous_mapping_and_no_workflow_skill(self):
        control = self.init()
        self.assertEqual(len(control['groups']), 6)
        opaque_ids = {c['opaque_id'] for c in control['groups'].values()}
        self.assertEqual(len(opaque_ids), 6)
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        self.assertNotIn('Demo', str(out / 'blind'))
        home = out / 'private/primary/home'
        ar.setup_home(home, self.auth, self.admin / 'frozen/unity-mcp-skill')
        self.assertEqual({p.name for p in (home / 'skills').iterdir()}, {'unity-mcp-skill'})
        self.assertFalse((out / 'blind/auth.json').exists())

    def test_cli_exact_model_and_roles(self):
        cmd = ar.audit_command('codex', self.base / 'home', self.base / 'blind', self.base / 'proxy.py', self.base / 'log', self.base / 'final')
        self.assertEqual(cmd[:3], ['codex', '--no-daemon', 'exec'])
        self.assertEqual(cmd[cmd.index('--model') + 1], 'gpt-6.1-sol')
        self.assertIn('agents.max_concurrent_threads_per_session=2', cmd)
        self.assertIn('model_reasoning_effort="high"', cmd)
        self.assertIn('--ignore-user-config', cmd)
        self.assertFalse(any('assets.' in c for c in cmd))

    def test_candidate_business_guard_allows_only_adapters(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        adapter = out / 'blind/candidate/Assets/__B1Audit/Editor'
        adapter.mkdir(parents=True)
        (adapter / 'Test.cs').write_text('// adapter')
        ar.guard_candidate(out)
        (out / 'blind/candidate/Assets/game.cs').write_text('// repair forbidden')
        with self.assertRaisesRegex(RuntimeError, 'business files changed'):
            ar.guard_candidate(out)

    def test_unavailable_existing_editor_never_restarts(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        case['editor_launched'] = True
        out = self.root / 'cases' / case['opaque_id']
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.discover.side_effect = ConnectionError('unavailable')
        with mock.patch.object(ar, 'load_proxy', return_value=proxy), mock.patch.object(ar.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'no automatic repeated restart'):
                ar.ensure_editor(control, case, out, lambda: None)
            popen.assert_not_called()

    def test_windows_editor_launch_is_hidden_and_nonwindows_has_no_windows_options(self):
        startup = types.SimpleNamespace(dwFlags=8, wShowWindow=None)
        with mock.patch.object(ar.subprocess, 'STARTUPINFO', return_value=startup, create=True), \
             mock.patch.object(ar.subprocess, 'STARTF_USESHOWWINDOW', 1, create=True), \
             mock.patch.object(ar.subprocess, 'SW_HIDE', 0, create=True):
            options = ar.editor_launch_options('nt')
            self.assertIs(options['startupinfo'], startup)
            self.assertEqual(startup.dwFlags, 9)
            self.assertEqual(startup.wShowWindow, 0)
            self.assertEqual(ar.editor_launch_options('posix'), {})

    def derived_fixture(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        reference = out / 'blind/candidate'
        bootstrap = reference / 'Packages/com.synthetic/McpCiBoot.cs'
        bootstrap.parent.mkdir()
        bootstrap.write_text('// synthetic original bootstrap entry')
        manifest = ar.read(out / 'blind/manifest.json')
        manifest['audit_baseline'] = ar.audit.inventory(reference)
        shutil.copytree(reference, out / 'blind/runtime')
        manifest.update(workspace_mode='derived_operations_v1', runtime='runtime',
                        operational_provenance={'synthetic': True})
        ar.save(out / 'blind/manifest.json', manifest)
        return control, case, out

    def test_derived_guard_keeps_reference_strict_and_checks_runtime_journal(self):
        _, _, out = self.derived_fixture()
        module = mock.Mock()
        with mock.patch.object(ar, 'provenance_api', return_value=module):
            ar.guard_candidate(out)
            module.validate_workspace.assert_called_once_with(*ar.provenance_args(out), require_review=False)
            adapter = out / 'blind/candidate/Assets/__B1Audit'
            adapter.mkdir()
            (adapter / 'forbidden.cs').write_text('// U cannot be changed even by adapters')
            with self.assertRaisesRegex(RuntimeError, 'business files changed'):
                ar.guard_candidate(out)

    def test_derived_editor_opens_runtime_only_and_journals_startup(self):
        control, case, out = self.derived_fixture()
        module = mock.Mock()
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.discover.side_effect = [ConnectionError('not started'), {'id': 'runtime-only', 'unity_port': 1234}]
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = None
        with mock.patch.object(ar, 'provenance_api', return_value=module), \
             mock.patch.object(ar, 'load_proxy', return_value=proxy), \
             mock.patch.object(ar.subprocess, 'Popen', return_value=proc) as popen:
            ar.ensure_editor(control, case, out, lambda: None)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('-projectPath') + 1], str((out / 'blind/runtime').resolve()))
        module.begin_operation.assert_called_once()
        module.finish_operation.assert_called_once()
        self.assertEqual(module.begin_operation.call_args.args[:4], ar.provenance_args(out))
        self.assertEqual(module.begin_operation.call_args.args[4], 'editor_startup')
        proof = module.finish_operation.call_args.args[-1]
        self.assertTrue(proof['source_refs'][0]['path'].endswith('McpCiBoot.cs'))
        self.assertTrue(proof['evidence_refs'])

    def test_provenance_role_is_independent_and_blocks_without_valid_write_review(self):
        control, case, out = self.derived_fixture()
        module = mock.Mock()
        module.validate_workspace.side_effect = ValueError('unapproved actual path')
        seen = []
        def fake_invoke(control, case, role, prompt, out, persist):
            seen.append(role)
            state = case['roles'][role]
            state['attempts'] += 1
            state['session_id'] = 'same-provenance-session'
            self.assertNotEqual(state['auditor_id'], case['roles']['primary']['auditor_id'])
            self.assertIn('Do not run Unity', prompt)
            self.assertIn('every changed path', prompt.lower())
            self.assertIn('not functional zero scores', prompt)
        with mock.patch.object(ar, 'provenance_api', return_value=module), \
             mock.patch.object(ar, 'invoke', side_effect=fake_invoke), mock.patch.object(ar, 'guard_candidate'), \
             mock.patch.object(ar, 'verify_exported_tool_traces'):
            with self.assertRaisesRegex(RuntimeError, 'Provenance remains blocked'):
                ar.provenance_review(control, case, out, lambda: None)
        self.assertEqual(seen, ['provenance'] * 4)
        self.assertFalse((out / 'blind/score.json').exists())

    def test_default_operational_initialization_keeps_reference_and_binds_actual_primary(self):
        control = ar.initialize(self.admin, self.root, self.editor, self.auth)
        for case in control['groups'].values():
            out = self.root / 'cases' / case['opaque_id']
            manifest = ar.read(out / 'blind/manifest.json')
            self.assertEqual(manifest['workspace_mode'], 'derived_operations_v1')
            self.assertEqual(manifest['operational_provenance']['primary_id'], case['roles']['primary']['auditor_id'])
            self.assertEqual(ar.audit.inventory(out / 'blind/candidate'), ar.audit.inventory(out / 'blind/runtime'))
            self.assertEqual(ar.runtime_project(out), out / 'blind/runtime')
            ar.guard_candidate(out)
            self.assertIn('provenance_tool.py', manifest['artifacts'])

    def test_real_provenance_review_requires_actual_role_id_and_revalidates_after_new_operation(self):
        control = ar.initialize(self.admin, self.root, self.editor, self.auth)
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        module = ar.provenance_api()
        calls = []
        def fake_invoke(control, case, role, prompt, out, persist):
            calls.append(role)
            state = case['roles'][role]
            state['attempts'] += 1
            state['session_id'] = 'same-provenance-session'
            state['model_evidence'] = {'verified': True}
            current = module.validate_workspace(*ar.provenance_args(out))
            path = out / 'blind/evidence/reviewer-observations.txt'
            path.write_text('Synthetic independent journal/source/actual-operation review')
            refs = [{'path': path.name, 'sha256': ar.audit.sha256(path)}]
            manifest = ar.provenance_args(out)[-1]
            ar.save(out / 'blind/evidence/provenance-review.json', {
                'schema': 'b1-provenance-review-v1', 'manifest_sha256': manifest['manifest_sha256'],
                'candidate_hash': manifest['candidate_hash'], 'journal_head': current['journal_head'],
                'primary_id': case['roles']['primary']['auditor_id'], 'reviewer_id': state['auditor_id'], 'independent': True,
                'decisions': [], 'final': {'approved': True, 'runtime_digest': current['current_digest'],
                    'reference_digest': current['reference_digest'], 'basis': 'Synthetic no-data-change final review', 'evidence_refs': refs}})
        with mock.patch.object(ar, 'invoke', side_effect=fake_invoke), mock.patch.object(ar, 'verify_exported_tool_traces'):
            result = ar.provenance_review(control, case, out, lambda: None)
            ar.provenance_review(control, case, out, lambda: None)
            self.assertEqual(result['reviewer_id'], case['roles']['provenance']['auditor_id'])
            self.assertEqual(calls, ['provenance'])
            args = ar.provenance_args(out)
            evidence = out / 'blind/evidence/actual-controller-call.txt'
            evidence.write_text('synthetic normal stop exact invocation')
            provenance = {'actor_id': 'controller', 'invocation': {'kind': 'controller', 'command': 'normal stop', 'refs': ['call-1']},
                'source_refs': [{'path': 'Assets/game.cs', 'sha256': ar.audit.sha256(out / 'blind/candidate/Assets/game.cs')}],
                'evidence_refs': [{'path': evidence.name, 'sha256': ar.audit.sha256(evidence)}]}
            module.begin_operation(*args, 'normal_stop_play', 'stop-1', provenance)
            module.finish_operation(*args, 'stop-1')
            ar.provenance_review(control, case, out, lambda: None)
            self.assertEqual(calls, ['provenance', 'provenance'])

    def test_controller_exports_only_actual_anonymous_traces_with_exact_hashes(self):
        _, case, out = self.derived_fixture()
        private = out / 'private/primary'
        (private / 'home/sessions').mkdir(parents=True)
        for name, body in [('turn-1.jsonl', '{"type":"thread.started"}'),
                           ('home/sessions/root.jsonl', '{"type":"session_meta"}'),
                           ('home/sessions/child.jsonl', '{"type":"session_meta"}'),
                           ('unity.jsonl', '{"method":"tools/call"}'),
                           ('home/auth.json', '{"private":"not exportable"}'),
                           ('home/config.toml', 'private_config=true')]:
            (private / name).write_text(body)
        state = {'attempts': 1, 'session_id': 'anonymous-root', 'auditor_id': case['roles']['primary']['auditor_id']}
        manifest = ar.export_tool_traces(out, 'primary', state, private / 'turn-1')
        self.assertEqual(len(manifest['files']), 4)
        self.assertTrue(all(item['source_sha256'] == item['exported_sha256'] and not item['redacted'] for item in manifest['files']))
        destination = out / 'blind/evidence/tool-traces/primary/1'
        self.assertFalse((destination / 'home/auth.json').exists())
        self.assertFalse((destination / 'home/config.toml').exists())
        self.assertTrue((destination / 'home/sessions/child.jsonl').exists())

    def test_controller_trace_export_fails_closed_for_actual_auth_token(self):
        _, case, out = self.derived_fixture()
        private = out / 'private/primary'
        (private / 'home/sessions').mkdir(parents=True)
        secret = 'opaque_actual_test_credential_value'
        ar.save(private / 'home/auth.json', {'tokens': {'access_token': secret}})
        (private / 'turn-1.jsonl').write_text(json.dumps({'output': secret}))
        state = {'attempts': 1, 'session_id': 'anonymous-root', 'auditor_id': case['roles']['primary']['auditor_id']}
        with self.assertRaisesRegex(RuntimeError, 'credential'):
            ar.export_tool_traces(out, 'primary', state, private / 'turn-1')
        self.assertFalse((out / 'blind/evidence/tool-traces/primary/1').exists())
        self.assertTrue((private / 'turn-1.jsonl').exists())

    def opaque_trace_fixture(self, **payload_changes):
        blob = 'gAAAAA' + 'A' * 520 + '-sk-' + 'B' * 40 + '=='
        payload = {'type': 'reasoning', 'encrypted_content': blob, **payload_changes}
        return json.dumps({'type': 'response_item', 'payload': payload}, ensure_ascii=False).encode('utf-8'), blob

    def test_opaque_reasoning_span_retains_exact_bytes_and_accounts_for_utf8_crlf_offsets(self):
        line, blob = self.opaque_trace_fixture()
        prefix = json.dumps({'type': 'session_meta', 'payload': {'note': '中文'}}, ensure_ascii=False).encode('utf-8') + b'\r\n'
        original = prefix + line + b'\r\n'
        proof = ar.trace_credential_proof(original, 'home/sessions/root.jsonl')
        field = proof['opaque_fields'][0]
        self.assertEqual(field['line'], 2)
        self.assertEqual(original[field['start_byte']:field['end_byte']], blob.encode('ascii'))
        self.assertEqual(field['start_byte'], original.index(blob.encode('ascii')))
        self.assertEqual(field['sha256'], ar.hashlib.sha256(blob.encode()).hexdigest())
        self.assertTrue(proof['original_bytes_retained'])
        with self.assertRaisesRegex(RuntimeError, 'actual credential'):
            ar.trace_credential_proof(original, 'home/sessions/root.jsonl', [b'sk-' + b'B' * 40])

    def test_opaque_exception_does_not_cover_other_fields_tools_unknown_types_or_short_blobs(self):
        line, _ = self.opaque_trace_fixture()
        event = json.loads(line)
        token = 'sk-' + 'X' * 30
        cases = []
        other_field = json.loads(line)
        other_field['payload']['summary'] = token
        cases.append(json.dumps(other_field).encode())
        for kind in ('function_call_output', 'function_call', 'unknown'):
            altered = json.loads(line)
            altered['payload']['type'] = kind
            cases.append(json.dumps(altered).encode())
        unknown = json.loads(line)
        unknown['type'] = 'unknown'
        cases.append(json.dumps(unknown).encode())
        short, _ = self.opaque_trace_fixture(encrypted_content='gAAAAA-sk-' + 'X' * 30)
        cases.append(short)
        prefixless, _ = self.opaque_trace_fixture(encrypted_content='AAAAAA' + 'A' * 520 + '-sk-' + 'X' * 30)
        cases.append(prefixless)
        for data in cases:
            with self.subTest(data_sha=ar.hashlib.sha256(data).hexdigest()), self.assertRaisesRegex(RuntimeError, 'credential'):
                ar.trace_credential_proof(data, 'home/sessions/root.jsonl')
        with self.assertRaisesRegex(RuntimeError, 'credential'):
            ar.trace_credential_proof(line, 'turn-1.jsonl')

    def test_opaque_scan_rejects_duplicate_keys_ambiguous_fields_and_malformed_json(self):
        line, blob = self.opaque_trace_fixture()
        duplicate = line.replace(b'"type": "reasoning"', b'"type": "reasoning", "type": "reasoning"')
        ambiguous = json.loads(line)
        ambiguous['payload']['other'] = {'encrypted_content': blob}
        for data in (duplicate, line[:-1], json.dumps(ambiguous).encode()):
            with self.subTest(data_sha=ar.hashlib.sha256(data).hexdigest()), self.assertRaisesRegex(RuntimeError, 'Malformed|ambiguous|Ambiguous'):
                ar.trace_credential_proof(data, 'home/sessions/root.jsonl')

    def test_opaque_scan_rejects_nonstandard_json_constants(self):
        line, _ = self.opaque_trace_fixture()
        for value in (float('nan'), float('inf'), float('-inf')):
            event = json.loads(line)
            event['payload']['summary'] = value
            with self.subTest(value=str(value)), self.assertRaisesRegex(RuntimeError, 'Malformed'):
                ar.trace_credential_proof(json.dumps(event).encode(), 'home/sessions/root.jsonl')

    def test_legacy_export_without_scan_proof_accepts_only_ciphertext_needing_no_exception(self):
        _, case, out = self.derived_fixture()
        private = out / 'private/primary'
        (private / 'home/sessions').mkdir(parents=True)
        (private / 'turn-1.jsonl').write_text('{"type":"thread.started"}')
        source = private / 'home/sessions/root.jsonl'
        line, _ = self.opaque_trace_fixture(encrypted_content='gAAAAA' + 'A' * 600)
        source.write_bytes(line)
        state = case['roles']['primary']
        state.update(attempts=1, session_id='root')
        manifest = ar.export_tool_traces(out, 'primary', state, private / 'turn-1')
        for item in manifest['files']:
            self.assertEqual(item['credential_scan']['opaque_fields'], [])
            self.assertEqual(item['credential_scan']['ignored_generic_matches'], [])
            del item['credential_scan']
        state['exported_tool_trace_manifests'] = [manifest]
        ar.save(out / 'blind/evidence/tool-traces/primary/1/manifest.json', manifest)
        ar.verify_exported_tool_traces(out, case)
        # New exception usage cannot be silently added to an old unproved row.
        data, _ = self.opaque_trace_fixture()
        row = next(item for item in manifest['files'] if item['source_file'] == 'home/sessions/root.jsonl')
        for path in (private / row['source_snapshot_file'], out / 'blind/evidence/tool-traces/primary/1/home/sessions/root.jsonl'):
            path.write_bytes(data)
        row['source_sha256'] = row['exported_sha256'] = ar.hashlib.sha256(data).hexdigest()
        ar.save(out / 'blind/evidence/tool-traces/primary/1/manifest.json', manifest)
        with self.assertRaisesRegex(RuntimeError, 'lacks controller scan proof'):
            ar.verify_exported_tool_traces(out, case)

    def test_opaque_export_proof_recomputed_against_private_originals(self):
        _, case, out = self.derived_fixture()
        private = out / 'private/primary'
        (private / 'home/sessions').mkdir(parents=True)
        (private / 'turn-1.jsonl').write_text('{"type":"thread.started"}')
        data, _ = self.opaque_trace_fixture()
        source = private / 'home/sessions/root.jsonl'
        source.write_bytes(data)
        state = case['roles']['primary']
        state.update(attempts=1, session_id='root')
        manifest = ar.export_tool_traces(out, 'primary', state, private / 'turn-1')
        state['exported_tool_trace_manifests'] = [manifest]
        ar.verify_exported_tool_traces(out, case)
        exported = out / 'blind/evidence/tool-traces/primary/1/home/sessions/root.jsonl'
        self.assertEqual(exported.read_bytes(), data)
        row = next(item for item in manifest['files'] if item['source_file'] == 'home/sessions/root.jsonl')
        self.assertEqual(row['source_sha256'], row['exported_sha256'])
        self.assertFalse(row['redacted'])
        row['credential_scan']['opaque_fields'][0]['start_byte'] += 1
        ar.save(out / 'blind/evidence/tool-traces/primary/1/manifest.json', manifest)
        with self.assertRaisesRegex(RuntimeError, 'credential-scan proof'):
            ar.verify_exported_tool_traces(out, case)

    def test_trace_final_verification_uses_private_snapshots_and_controller_identity_anchor(self):
        _, case, out = self.derived_fixture()
        private = out / 'private/primary'
        (private / 'home/sessions').mkdir(parents=True)
        source = private / 'home/sessions/root.jsonl'
        source.write_text('{"type":"session_meta"}')
        (private / 'turn-1.jsonl').write_text('{"type":"thread.started"}')
        state = case['roles']['primary']
        state.update(attempts=1, session_id='anonymous-root')
        manifest = ar.export_tool_traces(out, 'primary', state, private / 'turn-1')
        state['exported_tool_trace_manifests'] = [manifest]
        ar.verify_exported_tool_traces(out, case)
        # A legitimate later session append must not invalidate an earlier
        # fixed private snapshot of the actual source bytes at export time.
        source.write_text(source.read_text() + '\n{"type":"turn_context"}')
        ar.verify_exported_tool_traces(out, case)
        destination = out / 'blind/evidence/tool-traces/primary/1'
        exported = destination / 'home/sessions/root.jsonl'
        exported.write_text('tampered')
        forged = ar.read(destination / 'manifest.json')
        row = next(item for item in forged['files'] if item['exported_file'] == 'home/sessions/root.jsonl')
        row['source_sha256'] = row['exported_sha256'] = ar.audit.sha256(exported)
        ar.save(destination / 'manifest.json', forged)
        with self.assertRaisesRegex(RuntimeError, 'private controller anchor'):
            ar.verify_exported_tool_traces(out, case)
        ar.save(destination / 'manifest.json', manifest)
        with self.assertRaisesRegex(RuntimeError, 'exported bytes changed'):
            ar.verify_exported_tool_traces(out, case)
        exported.write_bytes((private / 'trace-snapshots/1/home/sessions/root.jsonl').read_bytes())
        (private / 'trace-snapshots/1/home/sessions/root.jsonl').write_text('source snapshot changed')
        with self.assertRaisesRegex(RuntimeError, 'source snapshot'):
            ar.verify_exported_tool_traces(out, case)

    def test_started_turn_without_controller_export_is_not_reviewable(self):
        _, case, out = self.derived_fixture()
        case['roles']['primary'].update(attempts=1, session_id='actual-root')
        with self.assertRaisesRegex(RuntimeError, 'Missing controller-anchored'):
            ar.verify_exported_tool_traces(out, case)

    def test_startup_guard_failure_never_claims_success_or_finishes_journal(self):
        control, case, out = self.derived_fixture()
        module = mock.Mock()
        proxy = mock.Mock()
        proxy.discover.side_effect = [ConnectionError('not started'), {'id': 'runtime', 'unity_port': 1234}]
        failure = {'status': 'success', 'result': {'success': False,
                   'code': "'action' parameter is required.", 'error': "'action' parameter is required."}}
        proxy.bridge_request.return_value = failure
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = None
        with mock.patch.object(ar, 'provenance_api', return_value=module), \
             mock.patch.object(ar, 'load_proxy', return_value=proxy), \
             mock.patch.object(ar.subprocess, 'Popen', return_value=proc):
            with self.assertRaisesRegex(RuntimeError, 'actual SessionState value'):
                ar.ensure_editor(control, case, out, lambda: None)
        module.begin_operation.assert_called_once()
        module.finish_operation.assert_not_called()
        attempts = list((out / 'blind/evidence/tool-traces/controller').rglob('startup-guard-attempt-1.json'))
        self.assertEqual(len(attempts), 1)
        observation = ar.read(attempts[0])
        self.assertEqual(observation['decoded_response'], failure)
        self.assertTrue(observation['response_available'])
        self.assertEqual(observation['request']['params']['action'], 'execute')
        self.assertEqual(observation['request']['params']['compiler'], 'codedom')

    def test_startup_guard_actual_success_shape_and_unknown_values_fail_closed(self):
        actual = {'status': 'success', 'result': {'success': True, 'message': 'Code executed successfully.',
                  'data': {'result': True, 'compiler': 'codedom'}}}
        self.assertTrue(ar.startup_guard_verified(actual))
        for response in (None, {'status': 'error', 'result': actual['result']},
                         {'status': 'success', 'result': True}, {'status': 'success', 'result': {'success': True, 'data': None}},
                         {'status': 'success', 'result': {'success': True, 'data': {'result': 'true'}}},
                         {'status': 'success', 'result': {'success': True, 'data': {'result': 1}}},
                         {'success': True, 'data': {'result': True}}):
            with self.subTest(response=response):
                self.assertFalse(ar.startup_guard_verified(response))

    def test_startup_guard_transport_failure_is_recorded_without_response_invention(self):
        _, _, out = self.derived_fixture()
        reference = ar.save_startup_guard_attempt(out, 'synthetic-startup', 1,
            {'type': 'execute_code', 'params': {'action': 'execute'}}, error=ConnectionError('bridge disconnected'))
        observation = ar.read(out / 'blind/evidence' / reference['path'])
        self.assertIsNone(observation['decoded_response'])
        self.assertFalse(observation['response_available'])
        self.assertEqual(observation['transport_error']['type'], 'ConnectionError')
        with self.assertRaisesRegex(RuntimeError, 'overwrite'):
            ar.save_startup_guard_attempt(out, 'synthetic-startup', 1, {}, response={})

    def test_derived_invocation_pins_mcp_runtime_keeps_blind_cwd_and_verifies_model(self):
        control, case, out = self.derived_fixture()
        private = out / 'private/provenance'
        proc = mock.Mock()
        def communicate(*args, **kwargs):
            home = private / 'home'
            (home / 'sessions').mkdir()
            rows = [{'type': 'session_meta', 'payload': {'id': 'same-provenance'}},
                    {'type': 'turn_context', 'payload': {'model': ar.MODEL, 'effort': ar.EFFORT}}]
            (home / 'sessions/root.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
            (private / 'turn-1.final.txt').write_text('Synthetic review final')
            return (b'{"type":"thread.started","thread_id":"same-provenance"}\n', None)
        proc.communicate.side_effect = communicate
        with mock.patch.object(ar.subprocess, 'Popen', return_value=proc) as popen, \
             mock.patch.object(ar.runner, 'ProcessTree'):
            ar.invoke(control, case, 'provenance', 'Synthetic audit prompt', out, lambda: None)
        command = popen.call_args.args[0]
        encoded = next(arg.split('=', 1)[1] for arg in command if arg.startswith('mcp_servers.unity.args='))
        proxy_args = json.loads(encoded)
        self.assertEqual(proxy_args[proxy_args.index('--project') + 1], str(out / 'blind/runtime'))
        self.assertEqual(popen.call_args.kwargs['cwd'], out / 'blind')
        self.assertEqual(case['roles']['provenance']['model_evidence']['same-provenance'][0], {'model': ar.MODEL, 'effort': ar.EFFORT})

    def test_real_startup_journal_uses_source_entry_and_frozen_controller_result(self):
        control, case, out = self.derived_fixture()
        manifest = ar.read(out / 'blind/manifest.json')
        module = ar.provenance_api()
        manifest['operational_provenance'] = module.initialize(out / 'blind/candidate', out / 'blind/runtime',
            out / 'blind/evidence', manifest['candidate_hash'], primary_id=case['roles']['primary']['auditor_id'])
        ar.save(out / 'blind/manifest.json', manifest)
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.discover.side_effect = [ConnectionError('not started'), {'id': 'runtime', 'unity_port': 1234}]
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = None
        with mock.patch.object(ar, 'load_proxy', return_value=proxy), \
             mock.patch.object(ar.subprocess, 'Popen', return_value=proc):
            ar.ensure_editor(control, case, out, lambda: None)
        result = module.validate_workspace(*ar.provenance_args(out), require_review=False)
        self.assertEqual(len(result['operations']), 1)
        self.assertEqual(result['operation_actor_ids'], ['audit-controller'])
        self.assertFalse(result['review_approved'])
        with self.assertRaises(ValueError):
            module.validate_workspace(*ar.provenance_args(out), require_review=True)
        observations = [p for p in (out / 'blind/evidence/tool-traces/controller').rglob('result.json')
                        if 'result' in ar.read(p)]
        self.assertEqual(len(observations), 1)
        self.assertEqual(ar.read(observations[0])['result']['pid'], 12345)
        saved = ar.read(observations[0])['result']
        self.assertTrue(ar.startup_guard_verified(saved['startup_guard_result']))
        self.assertEqual(len(saved['startup_guard_evidence']), 1)
        attempt_path = out / 'blind/evidence' / saved['startup_guard_evidence'][0]['path']
        self.assertEqual(ar.audit.sha256(attempt_path), saved['startup_guard_evidence'][0]['sha256'])

    def test_derived_case_cannot_publish_until_terminal_drain_head_gets_independent_review(self):
        control = ar.initialize(self.admin, self.root, self.editor, self.auth)
        key = control['order'][0]
        case = control['groups'][key]
        out = self.root / 'cases' / case['opaque_id']
        module = ar.provenance_api()
        events = []
        def fake_invoke(control, case, role, prompt, out, persist):
            self.assertFalse((out / 'blind/score.json').exists())
            events.append(role)
            state = case['roles'][role]
            state['attempts'] += 1
            state['session_id'] = 'same-' + role
            state['model_evidence'] = {'synthetic_verified_model': True}
            if role == 'primary':
                self.verified_report(out, state['auditor_id'])
                return
            self.assertEqual(role, 'provenance')
            current = module.validate_workspace(*ar.provenance_args(out), require_review=False)
            notes = out / 'blind/evidence/provenance-notes.txt'
            notes.write_text('Synthetic independent final head and original-operation review')
            manifest = ar.provenance_args(out)[-1]
            ar.save(out / 'blind/evidence/provenance-review.json', {
                'schema': 'b1-provenance-review-v1', 'manifest_sha256': manifest['manifest_sha256'],
                'candidate_hash': manifest['candidate_hash'], 'journal_head': current['journal_head'],
                'primary_id': case['roles']['primary']['auditor_id'], 'reviewer_id': state['auditor_id'],
                'independent': True, 'decisions': [], 'final': {'approved': True,
                    'runtime_digest': current['current_digest'], 'reference_digest': current['reference_digest'],
                    'basis': 'Synthetic independent unchanged-data operation review',
                    'evidence_refs': [{'path': notes.name, 'sha256': ar.audit.sha256(notes)}]}})
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        def drain(project):
            self.assertEqual(project, out / 'blind/runtime')
            self.assertFalse((out / 'blind/score.json').exists())
            events.append('drain')
            return {'quiescent': True, 'editor_restarted': False}
        proxy.drain.side_effect = drain
        with mock.patch.object(ar, 'ensure_editor'), mock.patch.object(ar, 'invoke', side_effect=fake_invoke), \
             mock.patch.object(ar, 'load_proxy', return_value=proxy), mock.patch.object(ar, 'verify_exported_tool_traces'):
            ar.run_case(control, key, self.root)
        self.assertEqual(events, ['primary', 'provenance', 'drain', 'provenance'])
        self.assertEqual(case['status'], 'complete')
        self.assertTrue(module.validate_workspace(*ar.provenance_args(out), require_review=True)['review_approved'])
        self.assertTrue((out / 'blind/score.json').exists())

    def aa_fixture(self):
        control, case, out = self.derived_fixture()
        project = ar.runtime_project(out)
        folder = project / 'Library/PackageCache/com.unity.addressables@1.21.21'
        hashes = {}
        for relative in ar.AA_PREFLIGHT_SOURCES:
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('// synthetic original ' + relative)
            hashes[relative] = ar.audit.sha256(path)
        ar.save(folder / 'package.json', {'name': 'com.unity.addressables', 'version': '1.21.21'})
        config = project / 'Library/AddressablesConfig.dat'
        config.write_bytes(b'original\x00preference')
        return control, case, out, config, hashes

    def aa_package_identity(self, out):
        return {'assembly': 'Unity.Addressables.Editor', 'package': {'name': 'com.unity.addressables',
            'version': '1.21.21', 'resolvedPath': str((ar.runtime_project(out) /
                'Library/PackageCache/com.unity.addressables@1.21.21').resolve())}}

    def test_addressables_preflight_preserves_actual_config_bytes_request_and_package_sources(self):
        _, _, out, config, hashes = self.aa_fixture()
        proxy = mock.Mock()
        def respond(port, request):
            self.assertEqual(port, 1234)
            self.assertEqual(request['params']['action'], 'execute')
            self.assertEqual(request['params']['compiler'], 'codedom')
            self.assertIn('if (!nb) n.SetValue(null, true, null)', request['params']['code'])
            self.assertLess(request['params']['code'].index('PackageInfo.FindForAssembly'), request['params']['code'].index('n.SetValue'))
            self.assertLess(request['params']['code'].index('return new { blocked = true'), request['params']['code'].index('n.SetValue'))
            self.assertNotIn('g.SetValue', request['params']['code'])
            self.assertNotIn('BuildPlayerContent', request['params']['code'])
            config.write_bytes(b'new\x00notified')
            return {'status': 'success', 'result': {'success': True, 'data': {'result': {
                'generate_before': False, 'generate_after': False, 'notified_before': False,
                'notified_after': True, 'setter_called': True, **self.aa_package_identity(out)}}}}
        proxy.bridge_request.side_effect = respond
        with mock.patch.object(ar, 'AA_PREFLIGHT_SOURCES', hashes):
            result = ar.addressables_preflight(out, proxy, {'unity_port': 1234}, 'preflight')
        folder = out / 'blind/evidence/tool-traces/controller/preflight/addressables-preflight'
        self.assertEqual((folder / 'before/AddressablesConfig.dat').read_bytes(), b'original\x00preference')
        self.assertEqual((folder / 'after/AddressablesConfig.dat').read_bytes(), b'new\x00notified')
        self.assertEqual(len(ar.read(folder / 'result.json')['original_package_sources']), 2)
        self.assertEqual(result['status'], 'verified')
        for ref in result['evidence_refs']:
            self.assertEqual(ar.audit.sha256(out / 'blind/evidence' / ref['path']), ref['sha256'])

    def test_aa_variants_cache_119_and_embedded_121_bind_actual_version_path_and_archive_sources(self):
        _, _, out, _, hashes = self.aa_fixture()
        project = ar.runtime_project(out)
        location = project / 'Library/PackageCache/com.unity.addressables@1.21.21'
        for kind in ('cache119', 'embedded121'):
            with self.subTest(kind=kind):
                if kind == 'embedded121':
                    target = project / 'Packages/com.unity.addressables'
                    version = '1.21.21'
                else:
                    target = location.with_name('com.unity.addressables@1.21.19')
                    version = '1.21.19'
                location.rename(target)
                location = target
                ar.save(target / 'package.json', {'name': 'com.unity.addressables', 'version': version})
                proxy = mock.Mock()
                proxy.bridge_request.return_value = {'status': 'success', 'result': {'success': True, 'data': {'result': {
                    'generate_before': False, 'generate_after': False, 'notified_before': True, 'notified_after': True,
                    'setter_called': False, 'assembly': 'Unity.Addressables.Editor', 'package': {
                        'name': 'com.unity.addressables', 'version': version, 'resolvedPath': str(target.resolve())}}}}}
                with mock.patch.object(ar, 'AA_PREFLIGHT_SOURCES', hashes), mock.patch.object(ar, 'AA_PREFLIGHT_119_SOURCES', hashes):
                    result = ar.addressables_preflight(out, proxy, {'unity_port': 1}, kind)
                self.assertEqual(result['status'], 'verified')
                code = proxy.bridge_request.call_args.args[1]['params']['code']
                self.assertIn('p.version != ' + json.dumps(version), code)
                self.assertIn(json.dumps(str(target.resolve())), code)
                folder = out / f'blind/evidence/tool-traces/controller/{kind}/addressables-preflight'
                self.assertEqual(ar.read(folder / 'original-package/package.json')['version'], version)
                self.assertTrue((folder / 'original-package/Editor/Settings/ProjectConfigData.cs').is_file())

    def test_aa_variants_unknown_version_multiple_candidates_and_modified_source_block_before_rpc(self):
        _, _, out, _, hashes = self.aa_fixture()
        project = ar.runtime_project(out)
        folder = project / 'Library/PackageCache/com.unity.addressables@1.21.21'
        proxy = mock.Mock()
        ar.save(folder / 'package.json', {'name': 'com.unity.addressables', 'version': '9.9.9'})
        with self.assertRaisesRegex(RuntimeError, 'name/version'):
            ar.addressables_preflight(out, proxy, {}, 'unknown-version')
        ar.save(folder / 'package.json', {'name': 'com.unity.addressables', 'version': '1.21.21'})
        extra = project / 'Packages/com.unity.addressables'
        extra.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'entry shape'):
            ar.addressables_preflight(out, proxy, {}, 'ambiguous-packages')
        extra.rmdir()
        (folder / 'Editor/Settings/ProjectConfigData.cs').write_text('// modified')
        with mock.patch.object(ar, 'AA_PREFLIGHT_SOURCES', hashes), self.assertRaisesRegex(RuntimeError, 'original source differs'):
            ar.addressables_preflight(out, proxy, {}, 'modified-source')
        proxy.bridge_request.assert_not_called()

    def test_aa_variants_primary_prompt_clarifies_existing_record_and_packed_evidence_schema(self):
        _, _, out = self.derived_fixture()
        prompt = ar.primary_prompt(out, 'independent')
        self.assertIn('status="verified"', prompt)
        self.assertIn('integer score 0 or 1', prompt)
        self.assertIn('before any Play/FastMode operation can overwrite it', prompt)

    def test_addressables_preflight_rejects_changed_layout_or_already_notified_config_write(self):
        _, _, out, config, hashes = self.aa_fixture()
        proxy = mock.Mock()
        values = {'generate_before': False, 'generate_after': True, 'notified_before': False,
                  'notified_after': True, 'setter_called': True, **self.aa_package_identity(out)}
        proxy.bridge_request.return_value = {'status': 'success', 'result': {'success': True, 'data': {'result': values}}}
        with mock.patch.object(ar, 'AA_PREFLIGHT_SOURCES', hashes):
            with self.assertRaisesRegex(RuntimeError, 'unchanged report preference'):
                ar.addressables_preflight(out, proxy, {'unity_port': 1234}, 'layout-failure')
            values.update(generate_after=False, notified_before=True, setter_called=False)
            def mutate(*args):
                config.write_bytes(b'forbidden unexpected no-op write')
                return {'status': 'success', 'result': {'success': True, 'data': {'result': values}}}
            proxy.bridge_request.side_effect = mutate
            with self.assertRaisesRegex(RuntimeError, 'unchanged report preference'):
                ar.addressables_preflight(out, proxy, {'unity_port': 1234}, 'noop-failure')
        self.assertTrue((out / 'blind/evidence/tool-traces/controller/layout-failure/addressables-preflight/actual-response.json').exists())

    def test_addressables_absence_skips_but_unverified_package_rejects_before_rpc(self):
        _, _, out = self.derived_fixture()
        proxy = mock.Mock()
        result = ar.addressables_preflight(out, proxy, {}, 'absent')
        self.assertEqual(result['status'], 'skipped')
        proxy.bridge_request.assert_not_called()
        folder = ar.runtime_project(out) / 'Library/PackageCache/com.unity.addressables@1.21.21'
        folder.mkdir(parents=True)
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            ar.addressables_preflight(out, proxy, {}, 'invalid-source')
        proxy.bridge_request.assert_not_called()

    def test_addressables_loaded_assembly_package_binding_rejects_embedded_version_and_unknown(self):
        _, _, out, config, hashes = self.aa_fixture()
        original = config.read_bytes()
        proxy = mock.Mock()
        expected = self.aa_package_identity(out)
        variants = [None, {**expected['package'], 'name': 'other-package'},
                    {**expected['package'], 'version': '1.22.0'},
                    {**expected['package'], 'resolvedPath': str(ar.runtime_project(out) / 'Packages/com.unity.addressables')}]
        with mock.patch.object(ar, 'AA_PREFLIGHT_SOURCES', hashes):
            for index, package in enumerate(variants):
                # This matches the code's early return before any setter. Even
                # a wrongly reported success cannot make an unknown source pass.
                proxy.bridge_request.return_value = {'status': 'success', 'result': {'success': True,
                    'data': {'result': {'blocked': True, 'assembly': 'Unity.Addressables.Editor', 'package': package}}}}
                with self.subTest(package=package), self.assertRaisesRegex(RuntimeError, 'unchanged report preference'):
                    ar.addressables_preflight(out, proxy, {'unity_port': 1}, f'package-mismatch-{index}')
                self.assertEqual(config.read_bytes(), original)
                path = out / f'blind/evidence/tool-traces/controller/package-mismatch-{index}/addressables-preflight/actual-response.json'
                self.assertEqual(ar.read(path)['response']['result']['data']['result']['package'], package)

    def test_addressables_already_notified_is_noop_and_transport_failure_preserves_evidence(self):
        _, _, out, config, hashes = self.aa_fixture()
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {'status': 'success', 'result': {'success': True, 'data': {'result': {
            'generate_before': True, 'generate_after': True, 'notified_before': True,
            'notified_after': True, 'setter_called': False, **self.aa_package_identity(out)}}}}
        original = config.read_bytes()
        with mock.patch.object(ar, 'AA_PREFLIGHT_SOURCES', hashes):
            self.assertEqual(ar.addressables_preflight(out, proxy, {'unity_port': 1}, 'noop')['status'], 'verified')
            self.assertEqual(config.read_bytes(), original)
            proxy.bridge_request.side_effect = ConnectionError('synthetic network failure')
            with self.assertRaisesRegex(RuntimeError, 'unchanged report preference'):
                ar.addressables_preflight(out, proxy, {'unity_port': 1}, 'transport-failure')
        folder = out / 'blind/evidence/tool-traces/controller/transport-failure/addressables-preflight'
        actual = ar.read(folder / 'actual-response.json')
        self.assertIsNone(actual['response'])
        self.assertEqual(actual['transport_error']['type'], 'ConnectionError')
        self.assertEqual((folder / 'after/AddressablesConfig.dat').read_bytes(), original)

    def test_reused_editor_preflight_respects_active_operation_and_never_relaunches(self):
        control, case, out = self.derived_fixture()
        active = out / 'blind/evidence/provenance/active-operation.json'
        active.parent.mkdir(parents=True)
        ar.save(active, {'operation_id': 'someone-elses-active-build'})
        proxy = mock.Mock()
        proxy.discover.return_value = {'id': 'own', 'unity_port': 1234}
        with mock.patch.object(ar, 'load_proxy', return_value=proxy), mock.patch.object(ar.subprocess, 'Popen') as popen, \
             mock.patch.object(ar, 'addressables_preflight') as preflight:
            with self.assertRaisesRegex(RuntimeError, 'must finish'):
                ar.ensure_editor(control, case, out, lambda: None)
            preflight.assert_not_called()
            popen.assert_not_called()
            active.unlink()
            module = mock.Mock()
            preflight.return_value = {'status': 'verified', 'evidence_refs': []}
            with mock.patch.object(ar, 'provenance_api', return_value=module):
                ar.ensure_editor(control, case, out, lambda: None)
            preflight.assert_called_once()
            module.begin_operation.assert_called_once()
            module.finish_operation.assert_called_once()
            popen.assert_not_called()

    def test_initial_editor_uses_hidden_options_exact_arguments_and_single_launch(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.discover.side_effect = [ConnectionError('not started'), {'id': 'own', 'unity_port': 1234}, {'id': 'own', 'unity_port': 1234}]
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = None
        hidden = object()
        with mock.patch.object(ar, 'load_proxy', return_value=proxy), \
             mock.patch.object(ar, 'editor_launch_options', return_value={'startupinfo': hidden}), \
             mock.patch.object(ar.subprocess, 'Popen', return_value=proc) as popen:
            ar.ensure_editor(control, case, out, lambda: None)
            ar.ensure_editor(control, case, out, lambda: None)
        popen.assert_called_once()
        self.assertIs(popen.call_args.kwargs['startupinfo'], hidden)
        self.assertEqual(popen.call_args.args[0], [control['editor'], '-projectPath',
            str((out / 'blind/candidate').resolve()), '-logFile', str((out / 'editor.log').resolve()),
            '-job-worker-count', '2', '-executeMethod', 'MCPForUnity.Editor.McpCiBoot.StartStdioForCi'])
        self.assertTrue(case['editor_launched'])

    def test_uniform_derived_protocol_preserves_standard_lifecycle_with_write_provenance(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        prompt = ar.primary_prompt(out, 'judge')
        protocol = ar.measurement_restart_protocol()
        self.assertIn(protocol, prompt)
        for required in ('candidate/ is immutable reference U', 'runtime/ is disposable execution shadow R',
                         'provenance_tool.py begin/finish', 'normal Unity lifecycle', 'normal Editor exit/save behavior',
                         'one hidden reopen', 'not functional zero scores', 'Never repair business behavior',
                         'never hand-edit original .meta files', 'Original GUIDs in R are locked',
                         'unchanged GUID does not establish equivalence',
                         'Do not add crash-durability requirements', 'restart merely for refresh',
                         'Resources.FindObjectsOfTypeAll', 'focus=false', 'window.Show', 'window.Focus',
                         "normal internal TextField keyboard-focus logic", 'every descendant'):
            self.assertIn(required, protocol)
        self.assertNotIn('Do not use EditorApplication.Exit', protocol)

    def test_metadata_requires_every_child_exact_model_and_high(self):
        home = self.base / 'isolated'
        sessions = home / 'sessions'
        sessions.mkdir(parents=True)
        for sid, model in [('parent', ar.MODEL), ('child', 'gpt-6-luna')]:
            (sessions / (sid + '.jsonl')).write_text('\n'.join(json.dumps(e) for e in [
                {'type': 'session_meta', 'payload': {'id': sid}},
                {'type': 'turn_context', 'payload': {'model': model, 'effort': 'high'}}]))
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            ar.model_metadata(home)

    def test_model_metadata_missing_child_context_blocks_scoring(self):
        sessions = self.base / 'home/sessions'
        sessions.mkdir(parents=True)
        (sessions / 'child.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'child'}}))
        with self.assertRaisesRegex(RuntimeError, 'Missing actual'):
            ar.model_metadata(self.base / 'home')

    def full_history_fixture(self, child_own=True, wrong_model=False):
        home = self.base / 'full-history-home'
        sessions = home / 'sessions'
        sessions.mkdir(exist_ok=True, parents=True)
        def meta(sid, ordinal=0, parent=None, boundary=None):
            payload = {'id': sid, 'session_id': 'root', 'source': 'exec' if not parent else
                       {'subagent': {'thread_spawn': {'parent_thread_id': parent}}}}
            if parent:
                payload.update(parent_thread_id=parent, subagent_history_start_ordinal=boundary)
            return {'type': 'session_meta', 'ordinal': ordinal, 'payload': payload}
        def context(turn, ordinal, model=ar.MODEL):
            return {'type': 'turn_context', 'ordinal': ordinal,
                    'payload': {'turn_id': turn, 'model': model, 'effort': 'high'}}
        rows = {'root': [meta('root'), context('root-turn', 7)],
                'child': [meta('child', parent='root', boundary=9), meta('root', 1),
                          context('root-turn', 6, 'wrong' if wrong_model else ar.MODEL)],
                'grandchild': [meta('grandchild', parent='child', boundary=20), meta('child', 1), meta('root', 2),
                              context('root-turn', 6), context('child-turn', 13), context('grandchild-turn', 21)]}
        if child_own:
            rows['child'].append(context('child-turn', 13))
        else:
            rows.pop('grandchild')
        for sid, events in rows.items():
            (sessions / (sid + '.jsonl')).write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
        return home

    def test_full_history_identity_first_anchor_and_two_levels_exclude_parent_contexts(self):
        home = self.full_history_fixture()
        result = ar.model_metadata(home)
        self.assertEqual(set(result), {'root', 'child', 'grandchild'})
        self.assertEqual({sid: len(contexts) for sid, contexts in result.items()}, {'root': 1, 'child': 1, 'grandchild': 1})

    def test_child_only_inherited_context_is_not_actual_model_evidence(self):
        home = self.full_history_fixture(child_own=False)
        with self.assertRaisesRegex(RuntimeError, 'no own execution context'):
            ar.model_metadata(home)

    def test_boundaryless_child_needs_complete_ancestor_own_turn_id_set(self):
        home = self.full_history_fixture()
        (home / 'sessions/grandchild.jsonl').unlink()
        path = home / 'sessions/child.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]['payload'].pop('subagent_history_start_ordinal')
        path.write_text('\n'.join(json.dumps(e) for e in rows))
        self.assertEqual(len(ar.model_metadata(home)['child']), 1)
        root = home / 'sessions/root.jsonl'
        with root.open('a') as handle:
            handle.write('\n' + json.dumps({'type': 'turn_context', 'payload': {'model': ar.MODEL, 'effort': 'high'}}))
        with self.assertRaisesRegex(RuntimeError, 'turn identities unavailable'):
            ar.model_metadata(home)

    def test_wrong_model_inherited_or_owned_and_conflicting_boundary_are_rejected(self):
        home = self.full_history_fixture(wrong_model=True)
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            ar.model_metadata(home)
        path = home / 'sessions/child.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[2]['payload']['model'] = ar.MODEL
        rows[-1]['payload']['model'] = 'wrong-own-model'
        path.write_text('\n'.join(json.dumps(e) for e in rows))
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            ar.model_metadata(home)
        rows[-1]['payload']['model'] = ar.MODEL
        rows[2]['ordinal'] = 10  # Parent turn must not appear as child-owned.
        path.write_text('\n'.join(json.dumps(e) for e in rows))
        with self.assertRaisesRegex(RuntimeError, 'conflicts'):
            ar.model_metadata(home)

    def test_completed_primary_parser_recovery_does_not_invoke_primary_or_change_final(self):
        control = self.init()
        key = control['order'][0]
        case = control['groups'][key]
        out = self.root / 'cases' / case['opaque_id']
        state = case['roles']['primary']
        state.update(attempts=1, session_id='root')
        home = self.full_history_fixture()
        private = out / 'private/primary'
        private.mkdir(parents=True)
        shutil.copytree(home, private / 'home')
        answer = 'Original completed audit answer'
        native = [{'type': 'thread.started', 'thread_id': 'root'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': answer}},
                  {'type': 'turn.completed'}]
        (private / 'turn-1.jsonl').write_text('\n'.join(json.dumps(e) for e in native))
        final = private / 'turn-1.final.txt'
        final.write_text(answer)
        expected_hash = ar.audit.sha256(final)
        self.verified_report(out, state['auditor_id'])
        proxy = mock.Mock()
        proxy.drain.return_value = {'quiescent': True, 'editor_restarted': False}
        with mock.patch.object(ar, 'ensure_editor') as ensure, mock.patch.object(ar, 'invoke') as invoke, \
             mock.patch.object(ar, 'load_proxy', return_value=proxy):
            ar.run_case(control, key, self.root)
        invoke.assert_not_called()
        ensure.assert_not_called()
        self.assertEqual(state['attempts'], 1)
        self.assertEqual(ar.audit.sha256(final), expected_hash)
        self.assertEqual(state['completed_turn_checkpoint']['final_sha256'], expected_hash)
        self.assertEqual(set(state['model_evidence']), {'root', 'child', 'grandchild'})
        self.assertEqual(case['status'], 'complete')

    def closed_fixture(self):
        control, case, out = self.derived_fixture()
        private = out / 'private/primary'
        (private / 'home/sessions').mkdir(parents=True)
        state = case['roles']['primary']
        state.update(attempts=1, session_id='root')
        rows = [{'type': 'session_meta', 'payload': {'id': 'root'}},
                {'type': 'turn_context', 'payload': {'model': ar.MODEL, 'effort': ar.EFFORT}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete'}}]
        (private / 'home/sessions/root.jsonl').write_text('\n'.join(json.dumps(e) for e in rows))
        native = [{'type': 'thread.started', 'thread_id': 'root'},
                  {'type': 'item.completed', 'item': {'id': 'final', 'type': 'agent_message', 'text': 'Done'}},
                  {'type': 'turn.completed'}]
        (private / 'turn-1.jsonl').write_text('\n'.join(json.dumps(e) for e in native))
        (private / 'turn-1.final.txt').write_bytes(b'Done')
        state['exported_tool_trace_manifests'] = [ar.export_tool_traces(out, 'primary', state, private / 'turn-1')]
        state['model_evidence'] = ar.model_metadata(private / 'home')
        state['completed_turn_checkpoint'] = ar.completed_turn_checkpoint(out, 'primary', state)
        case.update(initial_editor_pid=12345, editor_identity='runtime@1234abcd')
        helper_path = Path(ar.__file__).with_name('closed_seal.py')
        helper = ar.closed_helper(ar.audit.sha256(helper_path))
        original = out / 'blind/evidence/normal-exit.json'
        ar.save(original, {'operation': 'synthetic original normal exit', 'journal_finished': True})
        source = {'path': str(original), 'sha256': ar.audit.sha256(original)}
        saves = self.base / 'anonymous-saves'
        saves.mkdir()
        (saves / 'save.json').write_text('{}')
        _, _, hashes = ar.closed_log_files(out, case)
        evidence = {'schema': 'b1-audit-closed-editor-evidence-v1', 'helper_sha256': ar.audit.sha256(helper_path),
            'opaque_id': case['opaque_id'], 'candidate_hash': ar.read(out / 'blind/manifest.json')['candidate_hash'],
            'runtime': str(ar.runtime_project(out)), 'roles': ar.closed_role_bindings(case),
            'known_actor_pids': [12345], 'known_instance_ids': ['runtime@1234abcd'],
            'saves_path': str(saves), 'lifecycle_evidence': [source], 'closure_basis': 'closed_cause_unconfirmed',
            'review': {'reviewer_id': 'independent-judge', 'independent': True, 'basis': 'exact actual exit records',
                       'evidence_refs': [source]}, 'log_hashes': hashes,
            'log_sizes': {name: (out / name).stat().st_size for name in hashes},
            'inventories': {'reference': ar.audit.inventory(out / 'blind/candidate'),
                            'runtime': ar.audit.inventory(ar.runtime_project(out)), 'saves': helper.save_inventory(saves)}}
        path = self.base / 'independent-closed-review.json'
        ar.save(path, evidence)
        claim = {'path': str(path), 'sha256': ar.audit.sha256(path)}
        request = {'out': str(out), 'case': case, 'evidence': claim}
        census = {'complete': True, 'collector_marker': helper.COLLECTOR, 'collector_pid': 77777,
            'processes': [{'ProcessId': 0, 'Name': 'System Idle Process', 'CommandLine': None},
                          {'ProcessId': os.getpid(), 'Name': 'python.exe', 'CommandLine': 'python audit_runner.py --closed_seal-proof'},
                          {'ProcessId': 77777, 'Name': 'powershell.exe', 'CommandLine': helper.COLLECTOR}]}
        return control, case, out, helper, request, evidence, path, census

    def test_closed_proof_reuses_real_pairs_census_models_checkpoints_and_stable_inputs(self):
        _, _, out, helper, request, _, _, census = self.closed_fixture()
        with mock.patch.object(ar, 'closed_helper', return_value=helper), \
             mock.patch.object(helper, 'collect_census', side_effect=[census, census]) as collect, \
             mock.patch.object(ar, 'guard_candidate'), mock.patch.object(ar.subprocess, 'Popen') as popen:
            result = ar.closed_proof_check(request)
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(result['no_known_writers'])
        self.assertEqual(result['mode'], 'closed_quiescent')
        self.assertFalse(result['editor_restarted'])
        self.assertEqual(result['tool_pairs']['sessions']['root']['pending'], [])
        popen.assert_not_called()

    def test_static_closed_facts_allow_later_real_stopped_turn_with_same_session_prefix(self):
        _, case, out, helper, request, _, path, census = self.closed_fixture()
        private = out / 'private/primary'
        state = case['roles']['primary']
        session = private / 'home/sessions/root.jsonl'
        with session.open('ab') as stream:
            stream.write(b'\n' + json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete'}}).encode())
        (private / 'turn-2.jsonl').write_bytes((private / 'turn-1.jsonl').read_bytes())
        (private / 'turn-2.final.txt').write_bytes(b'Done')
        state['attempts'] = 2
        state['execution_attempts'] = [{'attempt': 2, 'cli_pid': 23456, 'process_tree_stopped': True,
                                        'started_utc': 'start', 'ended_utc': 'end'}]
        state['exported_tool_trace_manifests'].append(ar.export_tool_traces(out, 'primary', state, private / 'turn-2'))
        state['completed_turn_checkpoint'] = ar.completed_turn_checkpoint(out, 'primary', state)
        state['model_evidence'] = ar.model_metadata(private / 'home')
        original_review_sha = ar.audit.sha256(path)
        with mock.patch.object(ar, 'closed_helper', return_value=helper), mock.patch.object(ar, 'guard_candidate'), \
             mock.patch.object(helper, 'collect_census', side_effect=[census, census]):
            proof = ar.closed_proof_check(request)
        self.assertEqual(ar.audit.sha256(path), original_review_sha)
        self.assertEqual(proof['actual_current_roles']['primary']['attempts'], 2)
        self.assertIn(23456, proof['verification_after']['known_pid_fields_present'])
        state['execution_attempts'][0]['process_tree_stopped'] = False
        with mock.patch.object(ar, 'closed_helper', return_value=helper), self.assertRaisesRegex(RuntimeError, 'stopped controller'):
            ar.closed_proof_check(request)

    def test_static_closed_facts_allow_new_independent_role_but_reject_live_new_actor(self):
        _, case, out, helper, request, _, _, census = self.closed_fixture()
        private = out / 'private/provenance'
        (private / 'home/sessions').mkdir(parents=True)
        state = {'attempts': 1, 'session_id': 'provenance-root', 'auditor_id': 'new-independent-role',
                 'execution_attempts': [{'attempt': 1, 'cli_pid': 34567, 'started_utc': 'start', 'ended_utc': 'end',
                                         'process_tree_stopped': True}]}
        case['roles']['provenance'] = state
        (private / 'home/sessions/new.jsonl').write_text('\n'.join(json.dumps(e) for e in [
            {'type': 'session_meta', 'payload': {'id': 'provenance-root'}},
            {'type': 'turn_context', 'payload': {'model': ar.MODEL, 'effort': ar.EFFORT}}]))
        data = (out / 'private/primary/turn-1.jsonl').read_bytes().replace(b'"root"', b'"provenance-root"')
        (private / 'turn-1.jsonl').write_bytes(data)
        (private / 'turn-1.final.txt').write_bytes(b'Done')
        state['exported_tool_trace_manifests'] = [ar.export_tool_traces(out, 'provenance', state, private / 'turn-1')]
        state['completed_turn_checkpoint'] = ar.completed_turn_checkpoint(out, 'provenance', state)
        state['model_evidence'] = ar.model_metadata(private / 'home')
        with mock.patch.object(ar, 'closed_helper', return_value=helper), mock.patch.object(ar, 'guard_candidate'):
            with mock.patch.object(helper, 'collect_census', side_effect=[census, census]):
                self.assertTrue(ar.closed_proof_check(request)['quiescent'])
            live = {**census, 'processes': census['processes'] + [
                {'ProcessId': 34567, 'Name': 'codex.exe', 'CommandLine': 'codex exec'}]}
            with mock.patch.object(helper, 'collect_census', return_value=live), self.assertRaisesRegex(RuntimeError, 'Live assigned'):
                ar.closed_proof_check(request)

    def test_closed_proof_rejects_unknown_editor_live_actor_and_stability_changes(self):
        _, _, out, helper, request, _, _, census = self.closed_fixture()
        with mock.patch.object(ar, 'closed_helper', return_value=helper), mock.patch.object(ar, 'guard_candidate'):
            for row in ({'ProcessId': 999, 'Name': 'Unity.exe', 'CommandLine': None},
                        {'ProcessId': 12345, 'Name': 'Unity.exe', 'CommandLine': 'Unity.exe -projectPath assigned'}):
                bad = {**census, 'processes': census['processes'] + [row]}
                with self.subTest(row=row), mock.patch.object(helper, 'collect_census', return_value=bad), self.assertRaises(RuntimeError):
                    ar.closed_proof_check(request)
            def mutate():
                (ar.runtime_project(out) / 'Assets/game.cs').write_text('// unexpected write')
                return census
            with mock.patch.object(helper, 'collect_census', side_effect=mutate), self.assertRaisesRegex(RuntimeError, 'changed from reviewed'):
                ar.closed_proof_check(request)

    def test_closed_proof_missing_pending_active_and_wrong_review_remain_blocked(self):
        control, case, out, helper, request, evidence, path, census = self.closed_fixture()
        with self.assertRaisesRegex(RuntimeError, 'independently reviewed'):
            ar.verify_closed_editor(control, case, out)
        active = out / 'blind/evidence/provenance/active-operation.json'
        active.parent.mkdir(parents=True)
        active.write_text('{}')
        with mock.patch.object(ar, 'closed_helper', return_value=helper), self.assertRaisesRegex(RuntimeError, 'active operation'):
            ar.closed_proof_check(request)
        active.unlink()
        evidence['review']['reviewer_id'] = case['roles']['primary']['auditor_id']
        ar.save(path, evidence)
        request['evidence']['sha256'] = ar.audit.sha256(path)
        with mock.patch.object(ar, 'closed_helper', return_value=helper), self.assertRaisesRegex(RuntimeError, 'Independent'):
            ar.closed_proof_check(request)

    def test_closed_proof_does_not_excuse_pending_native_rpc_just_because_editor_is_gone(self):
        _, case, out, helper, request, evidence, path, census = self.closed_fixture()
        private = out / 'private/primary'
        native = private / 'turn-1.jsonl'
        data = native.read_bytes() + b'\n' + json.dumps({'type': 'item.started', 'item': {
            'id': 'unfinished-unity-request', 'type': 'mcp_tool_call', 'server': 'unity'}}).encode()
        native.write_bytes(data)
        state = case['roles']['primary']
        manifest = state['exported_tool_trace_manifests'][0]
        row = next(item for item in manifest['files'] if item['source_file'] == 'turn-1.jsonl')
        row['source_sha256'] = row['exported_sha256'] = ar.hashlib.sha256(data).hexdigest()
        (private / row['source_snapshot_file']).write_bytes(data)
        (out / 'blind/evidence/tool-traces/primary/1/turn-1.jsonl').write_bytes(data)
        ar.save(out / 'blind/evidence/tool-traces/primary/1/manifest.json', manifest)
        state['completed_turn_checkpoint'] = ar.completed_turn_checkpoint(out, 'primary', state)
        _, _, evidence['log_hashes'] = ar.closed_log_files(out, case)
        evidence['log_sizes'] = {name: (out / name).stat().st_size for name in evidence['log_hashes']}
        ar.save(path, evidence)
        request['evidence']['sha256'] = ar.audit.sha256(path)
        with mock.patch.object(ar, 'closed_helper', return_value=helper), mock.patch.object(ar, 'guard_candidate'), \
             mock.patch.object(helper, 'collect_census', return_value=census) as collect:
            with self.assertRaisesRegex(RuntimeError, 'Pending native item'):
                ar.closed_proof_check(request)
        collect.assert_not_called()

    def test_no_editor_finalize_requires_closed_proof_without_opening_operation_or_editor(self):
        control, case, out = self.derived_fixture()
        proxy = mock.Mock()
        proxy.discover.side_effect = ConnectionError('closed')
        with mock.patch.object(ar, 'guard_candidate'), mock.patch.object(ar, 'load_proxy', return_value=proxy), \
             mock.patch.object(ar, 'begin_controller_operation') as begin, mock.patch.object(ar.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'drain failed'):
                ar.finalize_case(control, case, out, lambda: None)
        begin.assert_not_called()
        popen.assert_not_called()
        proxy.drain.assert_not_called()
        self.assertFalse(case['terminal_drain']['passed'])

    def test_closed_finalize_rechecks_after_provenance_before_publish(self):
        control, case, out = self.derived_fixture()
        proxy = mock.Mock()
        proxy.discover.side_effect = ConnectionError('closed')
        events = []
        proof = {'mode': 'closed_quiescent', 'quiescent': True, 'editor_restarted': False}
        def verify(*args):
            events.append('closed-proof')
            return proof
        with mock.patch.object(ar, 'guard_candidate'), mock.patch.object(ar, 'load_proxy', return_value=proxy), \
             mock.patch.object(ar, 'verify_closed_editor', side_effect=verify), \
             mock.patch.object(ar, 'provenance_review', side_effect=lambda *args: events.append('provenance')), \
             mock.patch.object(ar, 'verify_exported_tool_traces'), \
             mock.patch.object(ar.audit, 'save_score', side_effect=lambda *args: events.append('score') or {'score': 0}):
            ar.finalize_case(control, case, out, lambda: None)
        self.assertEqual(events, ['closed-proof', 'provenance', 'closed-proof', 'score'])

    def test_primary_recovery_without_prior_checkpoint_rejects_matching_rewritten_native_and_final(self):
        _, case, out = self.derived_fixture()
        private = out / 'private/primary'
        (private / 'home/sessions').mkdir(parents=True)
        state = case['roles']['primary']
        state.update(attempts=1, session_id='root')
        native = private / 'turn-1.jsonl'
        final = private / 'turn-1.final.txt'
        def write(answer):
            events = [{'type': 'thread.started', 'thread_id': 'root'},
                      {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': answer}},
                      {'type': 'turn.completed'}]
            native.write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
            final.write_bytes(answer.encode('utf-8'))
        write('Original answer')
        state['exported_tool_trace_manifests'] = [ar.export_tool_traces(out, 'primary', state, private / 'turn-1')]
        self.assertNotIn('completed_turn_checkpoint', state)
        ar.verify_exported_tool_traces(out, case)
        self.assertIsNotNone(ar.completed_turn_checkpoint(out, 'primary', state))
        write('Coherently rewritten answer')
        # Fixed snapshots and their exports still verify. Recovery must also
        # bind the actual completed turn to that original native source SHA.
        ar.verify_exported_tool_traces(out, case)
        with mock.patch.object(ar, 'model_metadata') as metadata:
            with self.assertRaisesRegex(RuntimeError, 'controller-anchored original trace'):
                ar.recover_primary_postprocessing(case, out, lambda: None)
        metadata.assert_not_called()
        self.assertNotIn('completed_turn_checkpoint', state)

    def test_completed_checkpoint_requires_exact_final_bytes_string_message_and_terminal_order(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        private = out / 'private/primary'
        private.mkdir(parents=True)
        final = private / 'turn-1.final.txt'
        state = {'attempts': 1, 'session_id': 'root'}
        first = {'type': 'thread.started', 'thread_id': 'root'}
        message = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Final'}}
        complete = {'type': 'turn.completed'}
        def write(events, answer=b'Final'):
            (private / 'turn-1.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            final.write_bytes(answer)
        write([first, message, complete])
        self.assertIsNotNone(ar.completed_turn_checkpoint(out, 'primary', state))
        for answer in (b' Final', b'Final ', b'Final\n', b'Final\r\n'):
            write([first, message, complete], answer)
            with self.subTest(answer=answer):
                self.assertIsNone(ar.completed_turn_checkpoint(out, 'primary', state))
        for events in ([first, complete, message], [first, message, complete, {'type': 'turn.started'}],
                       [first, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 42}}, complete]):
            write(events)
            with self.subTest(events=events):
                self.assertIsNone(ar.completed_turn_checkpoint(out, 'primary', state))

    def test_completed_checkpoint_multiline_diagnostics_are_adjacent_and_json_first(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        private = out / 'private/primary'; private.mkdir(parents=True)
        native = private / 'turn-1.jsonl'; final = private / 'turn-1.final.txt'
        final.write_bytes(b'Final')
        state = {'attempts': 1, 'session_id': 'root'}
        first = json.dumps({'type': 'thread.started', 'thread_id': 'root'})
        message = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Final'}})
        complete = json.dumps({'type': 'turn.completed'})
        warning = '2026-11-17T01:02:03.456Z ERROR codex_core::tools::router: apply_patch verification failed'
        def check(lines):
            native.write_text('\n'.join(lines), encoding='utf-8')
            before = native.read_bytes()
            result = ar.completed_turn_checkpoint(out, 'primary', state)
            self.assertEqual(native.read_bytes(), before)
            self.assertEqual(final.read_bytes(), b'Final')
            return result
        self.assertIsNotNone(check([first, warning, '  }', '  object ClonePiece', message, complete]))
        self.assertIsNotNone(check([first, warning, '\tcontinuation', warning, '  next', '  ' + message, complete]))
        # A valid indented JSON event is parsed, never swallowed as diagnostic.
        self.assertIsNone(check([first, message, warning, '  ' + complete, '  ' + json.dumps({'type': 'turn.started'})]))
        self.assertIsNone(check([first, warning, '  ' + json.dumps({'type': 'thread.started', 'thread_id': 'other'}), message, complete]))
        for middle in (['  orphan'], [warning, '', '  orphan'], [warning, 'unindented unknown'],
                       [warning, message, '  orphan'], [warning, '{malformed JSON}']):
            with self.subTest(middle=middle), self.assertRaisesRegex(RuntimeError, 'Malformed completed-turn native'):
                check([first, *middle, message, complete])

    def test_fake_cli_invocation_records_session_no_live_calls(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        private = out / 'private/primary'
        script = self.base / 'fake_cli.py'
        script.write_text('import json,os,pathlib,sys,time\n'
                          'sys.stdin.read()\n'
                          'h=pathlib.Path(os.environ["CODEX_HOME"]); (h/"sessions").mkdir()\n'
                          '(h/"sessions/fake.jsonl").write_text("\\n".join(json.dumps(e) for e in '
                          '[{"type":"session_meta","payload":{"id":"fake-session"}},'
                          '{"type":"turn_context","payload":{"model":"gpt-6.1-sol","effort":"high"}}]))\n'
                          '(h.parent/"turn-1.final.txt").write_text("Audit turn finished")\n'
                          'print(json.dumps({"type":"thread.started","thread_id":"fake-session"}),flush=True)\n'
                          'time.sleep(.2)\n')
        with mock.patch.object(ar, 'audit_command', return_value=[sys.executable, str(script)]):
            ar.invoke(control, case, 'primary', 'neutral fake prompt', out, lambda: None)
        self.assertEqual(case['roles']['primary']['session_id'], 'fake-session')
        self.assertEqual(case['roles']['primary']['attempts'], 1)
        self.assertTrue((private / 'turn-1.jsonl').exists())

    def test_incomplete_audit_never_summary_zero(self):
        control = self.init()
        with self.assertRaisesRegex(RuntimeError, 'must be complete'):
            ar.summarize(control, self.root)
        self.assertFalse((self.admin / 'results').exists())

    def test_audit_turn_limit_is_fail_closed(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        case['roles']['primary'] = {'attempts': 4, 'session_id': 'same', 'auditor_id': 'auditor'}
        with self.assertRaisesRegex(RuntimeError, 'exhausted'):
            ar.invoke(control, case, 'primary', 'no call', self.root / 'cases' / case['opaque_id'], lambda: None)

    def test_primary_corrects_same_audit_without_editor_restart(self):
        control = self.init()
        key = control['order'][0]
        case = control['groups'][key]
        out = self.root / 'cases' / case['opaque_id']
        prompts = []
        def fake_invoke(control, case, role, prompt, out, persist):
            state = case['roles'][role]
            state['attempts'] += 1
            state['session_id'] = 'same-primary'
            prompts.append(prompt)
            report = ar.read(out / 'blind/audit.json')
            report['auditor']['id'] = state['auditor_id']
            if state['attempts'] > 1:
                evidence = out / 'blind/evidence/fake.txt'
                evidence.write_text('Synthetic unit-test measurement')
                for check in report['checks']:
                    check.update(status='verified', score=1, operation='fake operation', actual='synthetic pass',
                                 evidence=[{'path': 'fake.txt', 'sha256': ar.audit.sha256(evidence)}])
            ar.save(out / 'blind/audit.json', report)
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.drain.return_value = {'quiescent': True, 'editor_restarted': False}
        with mock.patch.object(ar, 'ensure_editor') as ensure, mock.patch.object(ar, 'invoke', side_effect=fake_invoke), mock.patch.object(ar, 'load_proxy', return_value=proxy):
            ar.run_case(control, key, self.root)
        self.assertEqual(ensure.call_count, 2)  # Each real measurement turn verifies its own editor.
        self.assertEqual(case['status'], 'complete')
        self.assertEqual(case['score'], 100)
        self.assertEqual(len(prompts), 2)
        self.assertIn('same independent audit', prompts[1])

    def test_anonymous_case_drains_before_next_case_editor_starts(self):
        control = self.init()
        events = []
        def fake_invoke(control, case, role, prompt, out, persist):
            case['roles'][role]['attempts'] += 1
            events.append(('audit', case['opaque_id']))
            self.verified_report(out, case['roles'][role]['auditor_id'])
        def drain(project):
            events.append(('drain', project.parents[1].name))
            return {'quiescent': True, 'editor_restarted': False}
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.drain.side_effect = drain
        keys = control['order'][:2]
        with mock.patch.object(ar, 'ensure_editor', side_effect=lambda c, case, out, persist: events.append(('editor', case['opaque_id']))), mock.patch.object(ar, 'invoke', side_effect=fake_invoke), mock.patch.object(ar, 'load_proxy', return_value=proxy):
            for key in keys:
                ar.run_case(control, key, self.root)
        ids = [control['groups'][key]['opaque_id'] for key in keys]
        self.assertEqual(events, [('editor', ids[0]), ('audit', ids[0]), ('drain', ids[0]),
                                  ('editor', ids[1]), ('audit', ids[1]), ('drain', ids[1])])

    def test_failed_drain_pauses_then_retries_without_new_audit_or_restart(self):
        control = self.init()
        key = control['order'][0]
        case = control['groups'][key]
        out = self.root / 'cases' / case['opaque_id']
        def fake_invoke(control, case, role, prompt, out, persist):
            case['roles'][role]['attempts'] += 1
            self.verified_report(out, case['roles'][role]['auditor_id'])
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.drain.side_effect = [TimeoutError('still playing'), {'quiescent': True, 'editor_restarted': False}]
        with mock.patch.object(ar, 'ensure_editor') as ensure, mock.patch.object(ar, 'invoke', side_effect=fake_invoke) as invoke, mock.patch.object(ar, 'load_proxy', return_value=proxy):
            with self.assertRaisesRegex(RuntimeError, 'drain failed'):
                ar.run_case(control, key, self.root)
            self.assertEqual(case['status'], 'paused')
            self.assertEqual(case['phase'], 'finalizing')
            self.assertFalse(case['terminal_drain']['passed'])
            self.assertFalse((out / 'blind/score.json').exists())
            self.assertFalse(ar.read(out / 'terminal-drain.json')['passed'])
            ar.run_case(control, key, self.root)
            ensure.assert_called_once()
            invoke.assert_called_once()
        self.assertEqual(case['status'], 'complete')
        self.assertEqual(case['score'], 100)
        self.assertTrue(case['terminal_drain']['passed'])

    def test_legacy_complete_case_acquires_drain_without_reauditing(self):
        control = self.init()
        key = control['order'][0]
        case = control['groups'][key]
        out = self.root / 'cases' / case['opaque_id']
        self.verified_report(out, 'existing-primary')
        case['status'] = 'complete'
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.drain.return_value = {'quiescent': True, 'editor_restarted': False}
        with mock.patch.object(ar, 'ensure_editor') as ensure, mock.patch.object(ar, 'invoke') as invoke, mock.patch.object(ar, 'load_proxy', return_value=proxy):
            ar.run_case(control, key, self.root)
            ar.run_case(control, key, self.root)
            ensure.assert_not_called()
            invoke.assert_not_called()
            proxy.drain.assert_called_once()
        self.assertTrue(case['terminal_drain']['passed'])

    def test_business_guard_runs_after_terminal_drain(self):
        control = self.init()
        key = control['order'][0]
        case = control['groups'][key]
        out = self.root / 'cases' / case['opaque_id']
        self.verified_report(out, 'existing-primary')
        case['status'] = 'complete'
        def bad_drain(project):
            (project / 'Assets/game.cs').write_text('forbidden mutation during stop')
            return {'quiescent': True, 'editor_restarted': False}
        proxy = mock.Mock()
        proxy.bridge_request.return_value = {"status": "success", "result": {"success": True, "message": "Code executed successfully.", "data": {"result": True, "compiler": "codedom"}}}
        proxy.drain.side_effect = bad_drain
        with mock.patch.object(ar, 'load_proxy', return_value=proxy):
            with self.assertRaisesRegex(RuntimeError, 'business files changed'):
                ar.run_case(control, key, self.root)
        self.assertEqual(case['status'], 'paused')
        self.assertFalse((out / 'blind/score.json').exists())

    def test_independent_review_only_merges_flagged_ids(self):
        control = self.init()
        case = next(iter(control['groups'].values()))
        out = self.root / 'cases' / case['opaque_id']
        report, _ = self.complete_measurement_report(out, 'original-primary')
        report['checks'][0]['review_required'] = True
        ar.save(out / 'blind/audit.json', report)
        def fake_review(control, case, role, prompt, out, persist):
            self.assertEqual(role, 'review')
            self.assertIn(ar.measurement_restart_protocol(), prompt)
            state = case['roles']['review']
            self.assertNotEqual(state['auditor_id'], 'original-primary')
            state['attempts'] += 1
            evidence = out / 'blind/evidence/review.txt'
            evidence.write_text('Synthetic independent measurement')
            ar.save(out / 'blind/review-decisions.json', {'decisions': [{'id': report['checks'][0]['id'],
                'auditor_id': state['auditor_id'], 'score': 0, 'operation': 'review operation', 'expected': 'required behavior',
                'actual': 'observed missing behavior', 'evidence': [{'path': 'review.txt', 'sha256': ar.audit.sha256(evidence)}]}]})
        with mock.patch.object(ar, 'invoke', side_effect=fake_review):
            ar.review(control, case, out, lambda: None)
        final = ar.read(out / 'blind/audit.json')
        self.assertEqual(final['auditor']['id'], 'original-primary')
        self.assertEqual(final['checks'][0]['review']['score'], 0)
        self.assertIsNone(final['checks'][1]['review'])


    def complete_measurement_report(self, out, auditor_id=None):
        report = ar.read(out / 'blind/audit.json')
        if auditor_id:
            report['auditor']['id'] = auditor_id
        evidence = out / 'blind/evidence/new-measurement.json'
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"actual":"synthetic player flow"}')
        ref = {'path': 'new-measurement.json', 'sha256': ar.audit.sha256(evidence)}
        for check in report['checks']:
            check.update(status='verified', score=1, operation='actual synthetic measurement', expected='required',
                         actual='observed', evidence=[ref])
        ar.save(out / 'blind/audit.json', report)
        return report, ref

    def business_supplement_fixture(self):
        control, case, out, _, _, module = self.supplemental_fixture()
        reviewer = case['roles'].pop('provenance')
        case['roles']['review'] = reviewer
        reviewer['completed_turn_checkpoint']['native_sha256'] = 'd' * 64
        primary = case['roles']['primary']
        primary['completed_turn_checkpoint']['native_sha256'] = 'e' * 64
        report, ref = self.complete_measurement_report(out, primary['auditor_id'])
        report['checks'][0]['review_required'] = True
        report['checks'][0]['review_reason'] = 'actual evidence dispute'
        ar.save(out / 'blind/audit.json', report)
        old = out / 'blind/review-decisions.json'
        ar.save(old, {'auditor_id': reviewer['auditor_id'], 'decisions': []})
        operation = out / 'blind/evidence/provenance/operations/packed.json'
        ar.save(operation, {'operation_id': 'packed', 'operation_sha256': '3' * 64, 'kind': 'normal_run',
                           'provenance': {'actor_id': primary['auditor_id'], 'invocation': {'refs': ['actual-call']},
                                          'evidence_refs': [ref]}})
        authority = {'schema': 'b1-supplemental-business-review-v1', 'basis': 'actual new original-primary player flow',
                     'opaque_id': case['opaque_id'], 'candidate_hash': 'c' * 64,
                     'same_review_session': reviewer['session_id'], 'reviewer_id': reviewer['auditor_id'],
                     'prior_attempts': 4, 'reviewed_primary_attempts': 2,
                     'new_primary_final_sha256': 'a' * 64, 'new_primary_native_sha256': 'e' * 64,
                     'new_primary_export_manifest_sha256': ar.hashlib.sha256(json.dumps(primary['exported_tool_trace_manifests'][-1], sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                     'old_review_sha256': ar.audit.sha256(old), 'old_journal_head': '2' * 64,
                     'new_journal_head': '3' * 64, 'new_measurement_operation_ids': ['packed'],
                     'new_measurement_evidence': [ref], 'requirements_models_skills_scoring_changes': False,
                     'reviewer_must_decide_independently': True}
        path = self.base / 'business-authority.json'
        ar.save(path, authority)
        claim = {'path': str(path), 'sha256': ar.audit.sha256(path)}
        return control, case, out, authority, claim, module

    def test_incomplete_primary_routes_before_any_independent_review(self):
        control = self.init()
        key = next(iter(control['groups']))
        case = control['groups'][key]
        out = self.root / 'cases' / case['opaque_id']
        primary = case['roles']['primary']
        primary.update(attempts=4, session_id='same-primary')
        report, _ = self.complete_measurement_report(out, primary['auditor_id'])
        report['checks'][24].update(status='not_run', score=None, review_required=True)
        ar.save(out / 'blind/audit.json', report)
        with mock.patch.object(ar, 'recover_primary_postprocessing', return_value=True), mock.patch.object(ar, 'guard_candidate'), \
             mock.patch.object(ar, 'review') as review, mock.patch.object(ar, 'provenance_review') as provenance, \
             mock.patch.object(ar, 'ensure_editor') as editor, mock.patch.object(ar, 'invoke') as invoke:
            with self.assertRaisesRegex(RuntimeError, 'corrective audit turns exhausted'):
                ar.run_case(control, key, self.root)
        self.assertEqual(case['phase'], 'primary_correction')
        self.assertIn('025', case['validation_error'])
        review.assert_not_called(); provenance.assert_not_called(); editor.assert_not_called(); invoke.assert_not_called()

    def test_business_supplement_actual_new_measurements_once_per_head(self):
        _, case, out, _, claim, module = self.business_supplement_fixture()
        with self.supplemental_patches(case, module):
            record = ar.authorize_supplemental_business_review(case, out, claim)
            self.assertEqual(record['reserved_attempt'], 5)
            self.assertEqual(record['session_id'], 'same-reviewer')
            case['roles']['review']['supplemental_business_authorizations'] = [record]
            with self.assertRaisesRegex(RuntimeError, 'consumed'):
                ar.authorize_supplemental_business_review(case, out, claim)

    def test_business_supplement_rejects_stale_or_wrong_identity_anchors(self):
        _, case, out, authority, claim, module = self.business_supplement_fixture()
        path = Path(claim['path'])
        for key, wrong in [('schema', 'other'), ('same_review_session', 'fresh'), ('prior_attempts', 0),
                           ('reviewer_id', case['roles']['primary']['auditor_id']), ('reviewed_primary_attempts', 3),
                           ('new_primary_native_sha256', '0'*64), ('new_primary_export_manifest_sha256', '0'*64),
                           ('old_review_sha256', '0'*64), ('old_journal_head', '3'*64),
                           ('new_journal_head', '4'*64), ('new_measurement_operation_ids', [])]:
            with self.subTest(field=key):
                bad = dict(authority); bad[key] = wrong; ar.save(path, bad)
                with self.supplemental_patches(case, module), self.assertRaises((ValueError, RuntimeError)):
                    ar.authorize_supplemental_business_review(case, out, {'path': str(path), 'sha256': ar.audit.sha256(path)})

    def test_business_supplement_missing_measurement_active_or_nonprimary_operation_block(self):
        _, case, out, _, claim, module = self.business_supplement_fixture()
        with self.supplemental_patches(case, module):
            active = out / 'blind/evidence/provenance/active-operation.json'; active.write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'inactive'):
                ar.authorize_supplemental_business_review(case, out, claim)
            active.unlink()
            op = out / 'blind/evidence/provenance/operations/packed.json'; value = ar.read(op)
            value['provenance']['actor_id'] = 'controller'; ar.save(op, value)
            with self.assertRaisesRegex(RuntimeError, 'original-primary'):
                ar.authorize_supplemental_business_review(case, out, claim)
            report = ar.read(out / 'blind/audit.json'); report['checks'][0]['score'] = None
            ar.save(out / 'blind/audit.json', report)
            with self.assertRaisesRegex(ValueError, 'incomplete IDs'):
                ar.authorize_supplemental_business_review(case, out, claim)

    def test_business_supplement_reserves_before_launch_failure_and_other_roles_cannot_use(self):
        control, case, out, _, claim, module = self.business_supplement_fixture()
        with self.assertRaisesRegex(RuntimeError, 'only to review'):
            ar.invoke(control, case, 'primary', 'no', out, lambda: None, supplemental_business_review=claim)
        with self.supplemental_patches(case, module), mock.patch.object(ar, 'setup_home', side_effect=OSError('no launch')):
            with self.assertRaises(OSError):
                ar.invoke(control, case, 'review', 'one', out, lambda: None, supplemental_business_review=claim)
        state = case['roles']['review']
        self.assertEqual(state['attempts'], 4)
        record=state['supplemental_business_authorizations'][0]
        self.assertEqual(record['reserved_attempt'], 5)
        self.assertEqual(ar.audit.sha256(out / record['old_review_archive']),record['old_review_sha256'])
        with self.supplemental_patches(case, module), self.assertRaisesRegex(RuntimeError, 'consumed'):
            ar.authorize_supplemental_business_review(case, out, claim)

    def test_uniform_tooling_discovery_is_bounded_and_not_business_scoring(self):
        protocol = ar.measurement_restart_protocol()
        self.assertIn('If evidence/judge-tooling-availability.json exists, read it', protocol)
        self.assertIn('referenced calibration/adapter guide BEFORE choosing scripted input', protocol)
        self.assertIn('bounded, actually calibrated capabilities', protocol)
        self.assertIn('infrastructure blockers, never grounds for a game score of zero', protocol)
        self.assertIn('does not change any business acceptance criterion', protocol)

    def test_business_supplement_and_exhausted_cap_never_start_runtime(self):
        control, case, out, _, claim, module = self.business_supplement_fixture()
        case['review_runtime_required'] = True
        with mock.patch.object(ar, 'ensure_editor') as editor, mock.patch.object(ar, 'invoke') as invoke:
            with self.assertRaisesRegex(RuntimeError, 'exhausted'):
                ar.review(control, case, out, lambda: None)
            for authority in (claim, {'path': 'missing', 'sha256': '0'*64}):
                with self.assertRaisesRegex(RuntimeError, 'evidence-only'):
                    ar.review(control, case, out, lambda: None, supplemental_business_review=authority)
        editor.assert_not_called(); invoke.assert_not_called()

    def test_business_supplement_one_turn_and_no_default_cap_lift(self):
        control, case, out, _, claim, module = self.business_supplement_fixture()
        old = ar.audit.sha256(out / 'blind/review-decisions.json')
        with self.assertRaisesRegex(RuntimeError, 'exhausted'):
            ar.review(control, case, out, lambda: None)
        self.assertEqual(ar.audit.sha256(out / 'blind/review-decisions.json'), old)
        def incomplete(*args, **kwargs):
            self.assertEqual(kwargs['supplemental_business_review'], claim)
            case['roles']['review']['attempts'] += 1
        with mock.patch.object(ar, 'guard_candidate'), mock.patch.object(ar, 'invoke', side_effect=incomplete) as invoke:
            with self.assertRaisesRegex(RuntimeError, 'remains incomplete'):
                ar.review(control, case, out, lambda: None, supplemental_business_review=claim)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(case['roles']['review']['attempts'], 5)

    def supplemental_fixture(self):
        control, case, out = self.derived_fixture()
        primary = case['roles']['primary']
        primary.update(attempts=3, session_id='same-primary', model_evidence={'p': [{'model': ar.MODEL, 'effort': ar.EFFORT}]},
                       completed_turn_checkpoint={'attempt': 3, 'session_id': 'same-primary', 'final_sha256': 'a' * 64},
                       exported_tool_trace_manifests=[{'turn': 3, 'source': 'actual-frozen-export'}])
        reviewer = {'attempts': 4, 'session_id': 'same-reviewer', 'auditor_id': 'independent-original-reviewer',
                    'model_evidence': {'r': [{'model': ar.MODEL, 'effort': ar.EFFORT}]},
                    'completed_turn_checkpoint': {'attempt': 4, 'session_id': 'same-reviewer', 'final_sha256': 'b' * 64}}
        case['roles']['provenance'] = reviewer
        canonical = {'schema': 'b1-provenance-review-v1', 'independent': True,
                     'reviewer_id': reviewer['auditor_id'], 'primary_id': primary['auditor_id'],
                     'candidate_hash': 'c' * 64, 'manifest_sha256': '1' * 64, 'journal_head': '2' * 64}
        ar.save(out / 'blind/evidence/provenance-review.json', canonical)
        folder = out / 'blind/evidence/provenance/operations'
        folder.mkdir(parents=True)
        ar.save(folder / 'old.json', {'operation_id': 'old', 'operation_sha256': '2' * 64})
        ar.save(folder / 'packed.json', {'operation_id': 'packed', 'operation_sha256': '3' * 64, 'kind': 'addressables_build'})
        authority = {'basis': 'New original-primary packed measurement after original review',
                     'same_provenance_session': 'same-reviewer', 'prior_attempts': 4,
                     'new_primary_checkpoint_sha256': 'a' * 64, 'requirements_models_skills_scoring_changes': False,
                     'reviewer_must_decide_independently': True}
        path = self.base / 'supplemental-authority.json'
        ar.save(path, authority)
        claim = {'path': str(path), 'sha256': ar.audit.sha256(path), 'kind': 'primary_remeasurement', 'reviewed_primary_attempts': 2}
        module = mock.Mock()
        module.validate_workspace.return_value = {'candidate_hash': 'c' * 64, 'manifest_sha256': '1' * 64,
                                                  'journal_head': '3' * 64, 'operations': ['old', 'packed']}
        return control, case, out, authority, claim, module

    def supplemental_patches(self, case, module):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(mock.patch.object(ar, 'guard_candidate'))
        stack.enter_context(mock.patch.object(ar, 'verify_exported_tool_traces'))
        stack.enter_context(mock.patch.object(ar, 'provenance_api', return_value=module))
        stack.enter_context(mock.patch.object(ar, 'model_metadata', side_effect=lambda home: case['roles'][home.parent.name]['model_evidence']))
        stack.enter_context(mock.patch.object(ar, 'completed_turn_checkpoint', side_effect=lambda out, role, state: state.get('completed_turn_checkpoint')))
        return stack

    def test_supplemental_provenance_binds_new_evidence_and_consumes_each_head_once(self):
        _, case, out, _, claim, module = self.supplemental_fixture()
        with self.supplemental_patches(case, module):
            record = ar.authorize_supplemental_provenance(case, out, claim)
            self.assertEqual(record['new_operations'], ['packed'])
            self.assertEqual((record['prior_attempts'], record['reserved_attempt']), (4, 5))
            self.assertEqual(record['primary_checkpoint']['attempt'], 3)
            self.assertEqual(record['session_id'], 'same-reviewer')
            case['roles']['provenance']['supplemental_authorizations'] = [record]
            with self.assertRaisesRegex(RuntimeError, 'already consumed'):
                ar.authorize_supplemental_provenance(case, out, claim)

    def test_supplemental_provenance_default_cap_other_roles_old_head_and_wrong_reviewer_block(self):
        control, case, out, _, claim, module = self.supplemental_fixture()
        with self.assertRaisesRegex(RuntimeError, 'exhausted'):
            ar.invoke(control, case, 'provenance', 'no authority', out, lambda: None)
        with self.assertRaisesRegex(RuntimeError, 'only to provenance'):
            ar.invoke(control, case, 'primary', 'not permitted', out, lambda: None, supplemental_provenance=claim)
        with self.supplemental_patches(case, module):
            module.validate_workspace.return_value['journal_head'] = '2' * 64
            with self.assertRaisesRegex(RuntimeError, 'genuinely newer'):
                ar.authorize_supplemental_provenance(case, out, claim)
            module.validate_workspace.return_value['journal_head'] = '3' * 64
            path = out / 'blind/evidence/provenance-review.json'
            old = ar.read(path)
            old['reviewer_id'] = 'different-reviewer'
            ar.save(path, old)
            with self.assertRaisesRegex(RuntimeError, 'same canonical reviewer'):
                ar.authorize_supplemental_provenance(case, out, claim)

    def test_supplemental_provenance_missing_new_primary_active_and_bad_authority_block(self):
        _, case, out, _, claim, module = self.supplemental_fixture()
        with self.supplemental_patches(case, module):
            claim['reviewed_primary_attempts'] = 3
            with self.assertRaisesRegex(RuntimeError, 'older primary attempt'):
                ar.authorize_supplemental_provenance(case, out, claim)
            claim['reviewed_primary_attempts'] = 2
            with mock.patch.object(ar, 'completed_turn_checkpoint', return_value=None), self.assertRaisesRegex(RuntimeError, 'actual model/export'):
                ar.authorize_supplemental_provenance(case, out, claim)
            active = out / 'blind/evidence/provenance/active-operation.json'
            active.write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'inactive operation'):
                ar.authorize_supplemental_provenance(case, out, claim)
            active.unlink()
            Path(claim['path']).write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'authority bytes changed'):
                ar.authorize_supplemental_provenance(case, out, claim)

    def test_supplemental_controller_drain_needs_actual_controller_operation_and_result(self):
        _, case, out, _, claim, module = self.supplemental_fixture()
        claim['kind'] = 'controller_terminal_drain'
        del claim['reviewed_primary_attempts']
        with self.supplemental_patches(case, module):
            with self.assertRaisesRegex(RuntimeError, 'actual new terminal-drain'):
                ar.authorize_supplemental_provenance(case, out, claim)
            result = out / 'blind/evidence/tool-traces/controller/drain/result.json'
            result.parent.mkdir(parents=True)
            ar.save(result, {'result': {'quiescent': True, 'editor_restarted': False}})
            ar.save(out / 'blind/evidence/provenance/operations/packed.json', {
                'operation_id': 'packed', 'operation_sha256': '3' * 64, 'kind': 'normal_stop_play',
                'provenance': {'actor_id': 'audit-controller', 'invocation': {'kind': 'controller', 'command': json.dumps({
                    'tool': 'frozen unity_proxy.drain', 'project': str(ar.runtime_project(out))})},
                    'evidence_refs': [{'path': 'tool-traces/controller/drain/result.json', 'sha256': ar.audit.sha256(result)}]}})
            self.assertEqual(ar.authorize_supplemental_provenance(case, out, claim)['kind'], 'controller_terminal_drain')

    def test_supplemental_review_is_one_attempt_and_approved_returns_without_retry(self):
        control, case, out, _, claim, module = self.supplemental_fixture()
        module.validate_workspace.side_effect = [ValueError('old final'), {'reviewer_id': case['roles']['provenance']['auditor_id'], 'review_approved': True}]
        def invoke(control, case, role, prompt, out, persist, **kwargs):
            self.assertEqual(kwargs['supplemental_provenance'], claim)
            case['roles'][role]['attempts'] += 1
        with mock.patch.object(ar, 'verify_exported_tool_traces'), mock.patch.object(ar, 'guard_candidate'), \
             mock.patch.object(ar, 'provenance_api', return_value=module), mock.patch.object(ar, 'invoke', side_effect=invoke) as called:
            self.assertTrue(ar.provenance_review(control, case, out, lambda: None, supplemental_provenance=claim)['review_approved'])
        called.assert_called_once()
        self.assertEqual(case['roles']['provenance']['attempts'], 5)

    def test_supplemental_invoke_keeps_cumulative_attempt_and_consumes_before_launch(self):
        control, case, out, _, claim, module = self.supplemental_fixture()
        private = out / 'private/provenance'
        private.mkdir(parents=True)
        (private / 'turn-5.final.txt').write_text('Synthetic fifth final')
        proc = mock.Mock(pid=98765)
        proc.communicate.return_value = (b'{"type":"thread.started","thread_id":"same-reviewer"}\n', None)
        with self.supplemental_patches(case, module), mock.patch.object(ar, 'setup_home'), \
             mock.patch.object(ar, 'audit_command', return_value=['fake-cli']), \
             mock.patch.object(ar.subprocess, 'Popen', return_value=proc) as popen, \
             mock.patch.object(ar.runner, 'ProcessTree'), mock.patch.object(ar, 'export_tool_traces', return_value={'turn': 5}):
            ar.invoke(control, case, 'provenance', 'Independent supplementary review', out, lambda: None,
                      supplemental_provenance=claim)
        state = case['roles']['provenance']
        self.assertEqual(state['attempts'], 5)
        self.assertEqual(state['session_id'], 'same-reviewer')
        self.assertEqual(state['supplemental_authorizations'][0]['reserved_attempt'], 5)
        self.assertTrue(state['execution_attempts'][0]['process_tree_stopped'])
        popen.assert_called_once()

    def test_supplemental_unapproved_single_turn_does_not_retry_automatically(self):
        control, case, out, _, claim, module = self.supplemental_fixture()
        module.validate_workspace.side_effect = ValueError('Still missing actual evidence')
        def invoke(control, case, role, prompt, out, persist, **kwargs):
            case['roles'][role]['attempts'] += 1
        with mock.patch.object(ar, 'verify_exported_tool_traces'), mock.patch.object(ar, 'guard_candidate'), \
             mock.patch.object(ar, 'provenance_api', return_value=module), mock.patch.object(ar, 'invoke', side_effect=invoke) as called:
            with self.assertRaisesRegex(RuntimeError, 'Provenance remains blocked'):
                ar.provenance_review(control, case, out, lambda: None, supplemental_provenance=claim)
        called.assert_called_once()
        self.assertEqual(case['roles']['provenance']['attempts'], 5)


if __name__ == '__main__':
    unittest.main()
