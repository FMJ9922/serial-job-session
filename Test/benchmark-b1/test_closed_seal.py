"""Synthetic closed-editor sealing tests; no CIM, Unity or model operations."""
import json
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import closed_seal as cs


class FullHistoryRolloutTests(unittest.TestCase):
    """Owner attribution fixtures only; no private logs, model or Unity calls."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def save(self, name, rows):
        path = self.root / (name + '.jsonl')
        path.write_text('\n'.join(json.dumps(x) for x in rows) + '\n', encoding='utf8')
        return path

    def meta(self, sid, parent=None, boundary=None):
        payload = {'id': sid, 'session_id': 'root', 'source': 'exec'}
        if parent:
            payload.update(parent_thread_id=parent, forked_from_id=parent,
                           source={'subagent': {'thread_spawn': {'parent_thread_id': parent}}})
        if boundary is not None:
            payload['subagent_history_start_ordinal'] = boundary
        return {'type': 'session_meta', 'payload': payload}

    def event(self, typ, **fields):
        return {'type': 'event_msg', 'payload': {'type': typ, **fields}}

    def context(self, sid):
        return {'type': 'turn_context', 'payload': {'turn_id': sid + '-turn', 'model': 'gpt-6.1-sol', 'effort': 'high'}}

    def pair(self, cid, custom=False, output='done', name='read', arguments='{}'):
        typ = 'custom_tool_call' if custom else 'function_call'
        field = 'input' if custom else 'arguments'
        return [{'type': 'response_item', 'payload': {'type': typ, 'call_id': cid, 'name': name, field: arguments}},
                {'type': 'response_item', 'payload': {'type': typ + '_output', 'call_id': cid, 'output': output}}]

    def numbered(self, rows):
        result = copy.deepcopy(rows)
        for i, event in enumerate(result):
            event.update(ordinal=i, timestamp='synthetic-envelope-time')
        return result

    def parent(self):
        return self.numbered([self.meta('root'), self.event('task_started'), self.context('root'),
                              *self.pair('root-call'), self.event('task_complete')])

    def child(self, sid, parent, prefix, own=None):
        if own is None:
            own = [self.event('thread_settings_applied'), self.event('task_started'), self.context(sid),
                   *self.pair(sid + '-call', custom=True), self.event('task_complete')]
        return self.numbered([self.meta(sid, parent, 1 + len(prefix)), *prefix, *own])

    def test_two_level_full_history_anchors_first_id_and_counts_only_actual_own_calls(self):
        root = self.parent()
        # The fork snapshot contains a parent call whose output appears only
        # later in the parent's file. It must not become child pending state.
        child = self.child('child', 'root', root[:4])
        grandchild = self.child('grandchild', 'child', child)
        paths = [self.save(sid, rows) for sid, rows in [('grandchild', grandchild), ('child', child), ('root', root)]]
        result = cs.rollout_pairs(paths)
        self.assertEqual(set(result), {'root', 'child', 'grandchild'})
        self.assertEqual({sid: r['completed_calls'] for sid, r in result.items()}, {'root': 1, 'child': 1, 'grandchild': 1})
        self.assertTrue(all(r['terminal_event_present'] and not r['record_incomplete'] for r in result.values()))
        self.assertEqual(result['child']['attribution']['first_metadata_session_id'], 'root')
        self.assertEqual(result['child']['attribution']['first_metadata_id'], 'child')

    def test_inherited_only_child_terminal_never_proves_child_completion(self):
        root = self.parent()
        child = self.child('child', 'root', root, own=[])
        result = cs.rollout_pairs([self.save('root', root), self.save('child', child)])
        self.assertEqual(result['child']['completed_calls'], 0)
        self.assertIsNone(result['child']['last_task'])
        self.assertFalse(result['child']['terminal_event_present'])
        self.assertTrue(result['child']['record_incomplete'])
        child = self.child('child', 'root', root, own=[self.event('task_started'), self.context('child')])
        result = cs.rollout_pairs([self.save('root', root), self.save('child', child)])
        self.assertEqual(result['child']['last_task'], 'task_started')
        self.assertTrue(result['child']['record_incomplete'])

    def test_actual_child_pending_call_cannot_be_closed_by_inherited_parent_output(self):
        root = self.parent()
        own = [self.event('task_started'), self.context('child'), self.pair('root-call')[0], self.event('task_complete')]
        child = self.child('child', 'root', root, own)
        with self.assertRaisesRegex(RuntimeError, 'Unanswered'):
            cs.rollout_pairs([self.save('root', root), self.save('child', child)])

    def test_unknown_missing_and_discontinuous_boundaries_fail_closed(self):
        root = self.parent()
        good = self.child('child', 'root', root[:3])
        variants = []
        missing = copy.deepcopy(good)
        missing[0]['payload'].pop('subagent_history_start_ordinal')
        variants.append(missing)
        for value in (True, -1, 0, 999, '4'):
            bad = copy.deepcopy(good)
            bad[0]['payload']['subagent_history_start_ordinal'] = value
            variants.append(bad)
        for index in (2, len(good) - 1):
            bad = copy.deepcopy(good)
            bad[index]['ordinal'] += 1
            variants.append(bad)
        for rows in variants:
            with self.subTest(rows=rows[0]['payload']), self.assertRaisesRegex(RuntimeError, 'boundary'):
                cs.rollout_pairs([self.save('root', root), self.save('child', rows)])

    def test_mismatched_parent_prefix_and_unknown_executable_projection_fail_closed(self):
        root = self.parent()
        good = self.child('child', 'root', root[:4])
        bad = copy.deepcopy(good)
        bad[3]['payload']['turn_id'] = 'invented-parent-turn'
        with self.assertRaisesRegex(RuntimeError, 'ordered original parent'):
            cs.rollout_pairs([self.save('root', root), self.save('child', bad)])
        bad = copy.deepcopy(good)
        bad[4]['payload']['name'] = 'changed-inherited-tool'
        with self.assertRaisesRegex(RuntimeError, 'ordered original parent'):
            cs.rollout_pairs([self.save('root', root), self.save('child', bad)])

    def test_full_history_projection_is_ordered_subset_with_only_known_context_rewrites(self):
        user = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'id': 'input-1', 'content': [{'type': 'input_text', 'text': 'synthetic inherited context'}]}}
        state = {'type': 'world_state', 'payload': {'full': True, 'state': {'multi_agent_usage_hint': 'parent only', 'stable': 2}}}
        root = self.numbered([self.meta('root'), self.event('task_started'), user, state, self.context('root'),
                              *self.pair('root-call'), self.event('task_complete')])
        projected = copy.deepcopy([root[i] for i in (0, 1, 2, 3, 4, 7)])
        projected[2]['metadata'] = {'client_authored': True, 'inherited_user_message': True, 'user_input_order': 7}
        projected[3]['payload']['state'].pop('multi_agent_usage_hint')
        dispatch = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer', 'id': 'new-dispatch',
                    'content': [{'type': 'input_text', 'text': 'synthetic child task envelope'}], 'internal_chat_message_metadata_passthrough': {}}}
        child = self.child('child', 'root', [*projected, dispatch])
        result = cs.rollout_pairs([self.save('root', root), self.save('child', child)])
        attribution = result['child']['attribution']
        self.assertEqual(attribution['matched_parent_projection_records'], 6)
        self.assertEqual(len(attribution['unmatched_dispatch_contexts']), 1)
        self.assertEqual(attribution['unmatched_dispatch_contexts'][0]['classification'], 'unmatched_dispatch_context_excluded_from_execution_proof')
        self.assertEqual(result['child']['completed_calls'], 1)
        # Unknown content rewrite or state rewrite is not made equivalent.
        for index, field in ((3, 'text'), (4, 'state')):
            bad = copy.deepcopy(child)
            if field == 'text':
                bad[index]['payload']['content'][0]['text'] = 'not the parent message'
            else:
                bad[index]['payload']['state']['stable'] = 999
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'ordered original parent'):
                cs.rollout_pairs([self.save('root', root), self.save('child', bad)])

    def test_actual_own_region_cannot_change_owner_or_reuse_ancestor_turn(self):
        root = self.parent()
        for injected in (self.meta('root'), self.context('root')):
            rows = self.child('child', 'root', root[:3], own=[injected, self.event('task_complete')])
            with self.subTest(injected=injected), self.assertRaisesRegex(RuntimeError, 'actual.*(stream|boundary)'):
                cs.rollout_pairs([self.save('root', root), self.save('child', rows)])

    def test_repeated_same_owner_metadata_does_not_reset_real_pending_or_terminal(self):
        rows = self.numbered([self.meta('root'), self.event('task_started'), self.pair('actual')[0],
                              self.meta('root'), self.pair('actual')[1], self.event('task_complete')])
        result = cs.rollout_pairs([self.save('root', rows)])
        self.assertEqual(result['root']['completed_calls'], 1)
        self.assertTrue(result['root']['terminal_event_present'])

    def test_parent_missing_or_cyclic_ancestry_cannot_establish_inherited_origin(self):
        root = self.parent()
        child = self.child('child', 'root', root[:3])
        with self.assertRaisesRegex(RuntimeError, 'Missing parent'):
            cs.rollout_pairs([self.save('child', child)])
        a = self.numbered([self.meta('a', 'b', 1), self.event('task_complete')])
        b = self.numbered([self.meta('b', 'a', 1), self.event('task_complete')])
        with self.assertRaisesRegex(RuntimeError, 'Cyclic'):
            cs.rollout_pairs([self.save('a', a), self.save('b', b)])

    def test_actual_background_cannot_borrow_other_owners_terminal_wait(self):
        root = self.numbered([self.meta('root'), self.event('task_started'), self.context('root'),
            *self.pair('start-bg', custom=True, output='Script running with cell ID 20', name='exec'), self.event('task_complete')])
        child = self.child('child', 'root', root, own=[self.event('task_started'), self.context('child'),
            *self.pair('other-wait', custom=True, output='Script completed', name='wait', arguments='{"cell_id":"20"}'), self.event('task_complete')])
        with self.assertRaisesRegex(RuntimeError, 'background tool handles'):
            cs.rollout_pairs([self.save('root', root), self.save('child', child)])

    def test_child_own_background_without_terminal_is_not_closed_by_parent_context(self):
        root = self.parent()
        child = self.child('child', 'root', root, own=[self.event('task_started'), self.context('child'),
            *self.pair('bg', custom=True, output='Script running with cell ID 77', name='exec'), self.event('task_complete')])
        with self.assertRaisesRegex(RuntimeError, 'child:cell:77'):
            cs.rollout_pairs([self.save('root', root), self.save('child', child)])

    def background_rows(self, operations):
        return self.numbered([self.meta('root'), self.event('task_started'),
            *[row for operation in operations for row in operation], self.event('task_complete')])

    def test_background_terminal_before_start_and_old_terminal_before_reuse_are_rejected(self):
        launch = lambda cid: self.pair(cid, custom=True, name='exec', output='Script running with cell ID 77')
        finish = lambda cid: self.pair(cid, custom=True, name='wait', arguments='{"cell_id":"77"}', output='Script completed')
        for operations in ([finish('early'), launch('later')],
                           [launch('first'), finish('first-done'), launch('second')]):
            with self.subTest(operations=operations), self.assertRaisesRegex(RuntimeError, 'background tool handles'):
                cs.rollout_pairs([self.save('root', self.background_rows(operations))])

    def test_background_running_poll_and_ordered_reuse_keep_generation_identity(self):
        operations = [self.pair('first', custom=True, name='exec', output='Script running with cell ID 77'),
            self.pair('poll', custom=True, name='wait', arguments='{"cell_id":"77"}', output='Script running with cell ID 77'),
            self.pair('first-done', custom=True, name='wait', arguments='{"cell_id":"77"}', output='Script completed'),
            self.pair('second', custom=True, name='exec', output='Script running with cell ID 77'),
            self.pair('second-done', custom=True, name='wait', arguments='{"cell_id":"77"}', output='Script terminated')]
        record = cs.rollout_pairs([self.save('root', self.background_rows(operations))])['root']
        self.assertEqual(record['background_active'], [])
        self.assertEqual([x['started_by_call_ids'] for x in record['background_terminal_events']],
                         [{'cell:77': 'first'}, {'cell:77': 'second'}])

    def test_background_active_handle_relaunch_and_unknown_running_poll_are_blocked(self):
        launch = self.pair('first', custom=True, name='exec', output='Script running with cell ID 77')
        for operations in ([launch, self.pair('duplicate-launch', custom=True, name='exec', output='Script running with cell ID 77')],
                           [self.pair('unknown-poll', custom=True, name='wait', arguments='{"cell_id":"77"}', output='Script running with cell ID 77')]):
            with self.subTest(operations=operations), self.assertRaisesRegex(RuntimeError, 'background tool handle'):
                cs.rollout_pairs([self.save('root', self.background_rows(operations))])

    def test_synchronous_exec_result_is_not_background_terminal_evidence(self):
        sync = self.pair('sync', name='exec_command', output='{"session_id":42,"exit_code":0,"output":"direct result"}')
        record = cs.rollout_pairs([self.save('root', self.background_rows([sync]))])['root']
        self.assertEqual(record['background_finished'], [])
        later = self.pair('later', name='exec_command', output='{"session_id":42,"exit_code":null,"output":"running"}')
        with self.assertRaisesRegex(RuntimeError, 'background tool handles'):
            cs.rollout_pairs([self.save('root', self.background_rows([sync, later]))])
        with self.assertRaisesRegex(RuntimeError, 'Synchronous result cannot close'):
            cs.rollout_pairs([self.save('root', self.background_rows([later, sync]))])


class ClosedSealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.admin = self.root / 'admin'
        self.own = self.admin / 'groups/Demo1'
        self.own.mkdir(parents=True)
        self.project = self.root / 'Demo1/AITest'
        self.project.mkdir(parents=True)
        (self.project / 'example.cs').write_text('synthetic business source')
        frozen = self.admin / 'frozen'
        frozen.mkdir()
        (frozen / 'unity_proxy.py').write_text('# must never be run')
        data = {'groups': {'Demo1': {'id': 'Demo1', 'project': str(self.project), 'model': 'gpt-6.1-sol'}},
                'frozen_files': cs.runner.inventory(frozen), 'baselines': {'Demo1': {'commit': 'fake-baseline'}}}
        cs.runner.write_json(self.admin / 'manifest.json', data)
        (self.admin / 'manifest.sha256').write_text(cs.runner.digest(self.admin / 'manifest.json'))
        self.state = {'status': 'submitted', 'process_tree_stopped': True, 'actual_model_verified': True,
                      'session_id': 'root-session', 'attempts': [], 'pid': None,
                      'requires_terminal_drain': True, 'terminal_drain': {'passed': False, 'error': 'no bridge'},
                      'needs_seal_review': True}
        cs.runner.write_json(self.own / 'state.json', self.state)
        (self.own / 'terminal-drain-report.json').write_text('{"passed":false,"error":"no bridge"}')
        self.native = self.own / 'attempt-001.jsonl'
        self.events(self.native, [{'type': 'thread.started', 'thread_id': 'root-session'},
            {'type': 'item.started', 'item': {'id': 'item1', 'type': 'command_execution'}},
            {'type': 'item.completed', 'item': {'id': 'item1', 'type': 'command_execution', 'status': 'completed'}},
            {'type': 'turn.completed', 'usage': {}}])
        sessions = self.own / 'home/sessions'
        sessions.mkdir(parents=True)
        self.session = sessions / 'root.jsonl'
        self.session_events = [{'type': 'session_meta', 'payload': {'id': 'root-session'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started'}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': 'f1', 'name': 'read'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'f1', 'output': 'done'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'c1', 'name': 'exec'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'c1', 'output': 'done'}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete'}}]
        self.events(self.session, self.session_events)
        self.profile = self.root / 'profile'
        self.env = mock.patch.dict(os.environ, {'USERPROFILE': str(self.profile)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def events(self, path, rows):
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')

    def census(self, extra=()):
        return {'complete': True, 'collector_pid': 2147483000, 'collector_marker': cs.COLLECTOR,
                'processes': [{'ProcessId': os.getpid(), 'ParentProcessId': 1, 'Name': 'python.exe',
                               'CommandLine': 'python closed_seal.py --group Demo1'},
                              {'ProcessId': 2147483000, 'ParentProcessId': os.getpid(), 'Name': 'powershell.exe',
                               'CommandLine': cs.COLLECTOR}, *extra]}

    def verify(self, census):
        return cs.verify_census(census, self.project, self.own, self.state, ['root-session'])

    def test_bridge_missing_alone_is_insufficient(self):
        with self.assertRaisesRegex(RuntimeError, 'complete attributable'):
            self.verify({'complete': False, 'processes': []})

    def test_legitimate_system_idle_pid_zero_is_allowed(self):
        result = self.verify(self.census([{'ProcessId': 0, 'ParentProcessId': 0,
                                          'Name': 'System Idle Process', 'CommandLine': None}]))
        self.assertTrue(result['no_known_writers'])

    def test_duplicate_or_malformed_process_ids_remain_rejected(self):
        for rows in ([{'ProcessId': -1, 'Name': 'python.exe'}],
                     [{'ProcessId': 0, 'Name': 'python.exe', 'CommandLine': 'writer.py'}],
                     [{'ProcessId': 0, 'Name': 'System Idle Process', 'CommandLine': None},
                      {'ProcessId': 0, 'Name': 'System Idle Process', 'CommandLine': None}],
                     [{'ProcessId': '123', 'Name': 'python.exe'}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, 'Malformed'):
                self.verify(self.census(rows))

    def test_unknown_unity_commandline_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Unknown/null Unity'):
            self.verify(self.census([{'ProcessId': 2222, 'Name': 'Unity.exe', 'CommandLine': None}]))

    def test_assigned_editor_and_scoped_writer_rejected(self):
        for name in ('Unity.exe', 'python.exe', 'UnityShaderCompiler.exe'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Live assigned'):
                self.verify(self.census([{'ProcessId': 2222, 'Name': name, 'CommandLine': f'{name} -projectPath "{self.project}"'}]))

    def test_other_instance_exact_path_does_not_block(self):
        other = self.root / 'Demo10/AITest'
        result = self.verify(self.census([{'ProcessId': 2222, 'Name': 'Unity.exe', 'CommandLine': f'Unity.exe -projectPath "{other}"'}]))
        self.assertTrue(result['no_assigned_editor'])

    def test_known_pid_and_session_uuid_rejected(self):
        self.state['root_pid'] = 2222
        with self.assertRaisesRegex(RuntimeError, 'Live assigned'):
            self.verify(self.census([{'ProcessId': 2222, 'Name': 'codex.exe', 'CommandLine': 'codex exec'}]))
        self.state.pop('root_pid')
        with self.assertRaisesRegex(RuntimeError, 'Live assigned'):
            self.verify(self.census([{'ProcessId': 3333, 'Name': 'codex.exe', 'CommandLine': 'codex exec resume root-session'}]))

    def test_pending_native_request_rejected(self):
        self.events(self.native, [{'type': 'item.started', 'item': {'id': 'pending', 'type': 'command_execution'}}])
        with self.assertRaisesRegex(RuntimeError, 'Pending native'):
            cs.logs_proof(self.own)

    def test_pending_function_and_custom_requests_rejected(self):
        for kind in ('function_call', 'custom_tool_call'):
            self.events(self.session, [{'type': 'session_meta', 'payload': {'id': 'root-session'}},
                {'type': 'response_item', 'payload': {'type': kind, 'call_id': 'pending'}}])
            with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, 'Unanswered'):
                cs.logs_proof(self.own)

    def test_pending_asset_request_rejected(self):
        self.events(self.own / 'asset-tools.jsonl', [{'event': 'request', 'call_id': 'asset-1'}])
        with self.assertRaisesRegex(RuntimeError, 'Pending asset'):
            cs.logs_proof(self.own)

    def test_unrecognized_native_format_rejected(self):
        self.events(self.native, [{'type': 'something_new'}])
        with self.assertRaisesRegex(RuntimeError, 'Unknown native'):
            cs.logs_proof(self.own)

    def test_indented_multiline_native_diagnostic_is_allowed(self):
        self.native.write_text('2026-11-16T06:00:00Z ERROR codex_core::tools::router: error=apply_patch verification failed:\n'
                               '    { expected source continuation }\n\tsecond indented continuation\n'
                               '{"type":"turn.completed","usage":{}}\n', encoding='utf-8')
        self.assertEqual(cs.native_pairs([self.native])['pending'], [])

    def test_diagnostic_continuations_require_contiguous_origin(self):
        diagnostic = '2026-11-16T06:00:00Z ERROR codex_core::tools::router: failure\n'
        for body in ('    orphan source line\n', diagnostic + 'unindented arbitrary line\n',
                     diagnostic + '{"type":"turn.completed"}\n    orphan after JSON\n',
                     diagnostic + '\n    orphan after blank\n', diagnostic + '{broken JSON\n'):
            with self.subTest(body=body):
                self.native.write_text(body, encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'Unrecognized event'):
                    cs.native_pairs([self.native])

    def test_confirmed_native_rollout_metadata_is_supported(self):
        metadata = [{'type': 'world_state', 'payload': {'full': True, 'state': {}}},
                    {'type': 'token_usage_record', 'payload': {'tokens': 1}},
                    {'type': 'inter_agent_communication_metadata', 'payload': {'agent_id': 'child'}},
                    {'type': 'response_item', 'payload': {'type': 'agent_message', 'author': 'child', 'recipient': 'root', 'content': []}}]
        self.events(self.session, self.session_events[:-1] + metadata + self.session_events[-1:])
        self.assertEqual(cs.logs_proof(self.own)['sessions']['root-session']['pending'], [])
        self.events(self.session, self.session_events[:-1] + [{'type': 'world_state', 'payload': {'unexpected': 1}}])
        with self.assertRaisesRegex(RuntimeError, 'world_state metadata'):
            cs.logs_proof(self.own)

    def test_historical_missing_terminal_event_recorded_without_fabrication(self):
        self.events(self.session, self.session_events[:-1])
        result = cs.logs_proof(self.own)
        self.assertTrue(result['sessions']['root-session']['record_incomplete'])
        self.assertFalse(result['sessions']['root-session']['terminal_event_present'])
        self.assertEqual(result['sessions']['root-session']['last_task'], 'task_started')

    def test_background_exec_session_rejected_even_with_call_output(self):
        rows = self.session_events[:-1] + [
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command', 'call_id': 'bg', 'arguments': '{}'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'bg', 'output': '{"session_id":42,"output":"running","exit_code":null}'}},
            self.session_events[-1]]
        self.events(self.session, rows)
        with self.assertRaisesRegex(RuntimeError, 'background tool handles'):
            cs.logs_proof(self.own)

    def test_completed_background_handles_are_not_pending(self):
        pending, done = cs.background_handles({'name': 'write_stdin', 'arguments': '{"session_id":42}'},
            {'output': '{"exit_code":0,"output":"done"}'})
        self.assertEqual(done, {'exec:42'})
        started, _ = cs.background_handles({'name': 'exec'}, {'output': 'Script running with cell ID abc-123'})
        self.assertEqual(started, {'cell:abc-123'})
        _, done = cs.background_handles({'name': 'wait', 'arguments': '{"cell_id":"abc-123"}'}, {'output': 'Script completed'})
        self.assertEqual(done, {'cell:abc-123'})

    def test_quoted_transcript_stdout_cannot_create_phantom_cell_or_exec_handles(self):
        call = {'name': 'exec', 'input': 'text(await tools.exec_command({cmd:"python readonly_print.py"}))'}
        stdout = 'Script running with cell ID 58\\r\\nWall time 31.0 seconds\\r\\n'
        stdout += '\nProcess running with session ID 123\n' + json.dumps({'session_id': 999, 'exit_code': None, 'output': 'old transcript'})
        output = {'output': [{'type': 'input_text', 'text': 'Script completed\nWall time 0.4 seconds\n'},
            {'type': 'input_text', 'text': json.dumps({'chunk_id': 'actual', 'wall_time_seconds': 0.4,
                'exit_code': 0, 'output': stdout})}]}
        self.assertEqual(cs.background_handles(call, output), (set(), set()))
        rows = self.session_events[:-1] + FullHistoryRolloutTests.pair(self, 'quoted', custom=True, name='exec',
            arguments=call['input'], output=output['output']) + [self.session_events[-1]]
        self.events(self.session, rows)
        record = cs.rollout_pairs([self.session])
        self.assertEqual(next(iter(record.values()))['background_active'], [])

    def test_authoritative_wrappers_preserve_genuine_background_and_wait_terminal(self):
        call = {'name': 'exec', 'input': 'text(await tools.exec_command({cmd:"real-command"}))'}
        output = {'output': [{'type': 'input_text', 'text': 'Script completed\nWall time 0.4 seconds\n'},
            {'type': 'input_text', 'text': json.dumps({'session_id': 42, 'exit_code': None, 'output': 'Script failed'})}]}
        self.assertEqual(cs.background_handles(call, output), ({'exec:42'}, set()))
        array = {'output': 'Script completed\r\nOutput:\r\n' + json.dumps([{'session_id': 43, 'exit_code': None, 'output': 'data'}])}
        self.assertEqual(cs.background_handles(call, array), ({'exec:43'}, set()))
        wait = {'name': 'wait', 'arguments': '{"cell_id":58}'}
        terminal = {'output': [{'type': 'input_text', 'text': 'Script failed\nWall time 0.1 seconds\n'},
            {'type': 'input_text', 'text': json.dumps({'exit_code': 0, 'output': 'Script running with cell ID 58'})}]}
        self.assertEqual(cs.background_handles(wait, terminal), (set(), {'cell:58'}))

    def test_printed_terminal_stdout_cannot_cancel_actual_pending_handle(self):
        call = {'name': 'exec', 'input': 'text(await tools.exec_command({cmd:"print transcript"}))'}
        output = {'output': [{'type': 'input_text', 'text': 'Script completed'},
            {'type': 'input_text', 'text': json.dumps({'exit_code': 0, 'output': 'Script completed\nProcess exited with code 0\n'})}]}
        self.assertEqual(cs.background_handles(call, output), (set(), set()))
        launch = FullHistoryRolloutTests.pair(self, 'real-launch', custom=True, name='exec', output='Script running with cell ID 58')
        printed = FullHistoryRolloutTests.pair(self, 'print-old-terminal', custom=True, name='exec', arguments=call['input'], output=output['output'])
        rows = self.session_events[:-1] + launch + printed + [self.session_events[-1]]
        self.events(self.session, rows)
        with self.assertRaisesRegex(RuntimeError, 'background tool handles'):
            cs.rollout_pairs([self.session])

    def test_unknown_authoritative_status_wrappers_fail_closed(self):
        call = {'name': 'exec_command', 'arguments': '{}'}
        for output in ({'output': '{"unexpected":{"session_id":42,"exit_code":null}}'},
                       {'output': '{"session_id":"unknown","exit_code":null,"output":"running"}'},
                       {'output': 'Process running with session ID unknown\nOutput:\nanything'}):
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, 'Unrecognized'):
                cs.background_handles(call, output)

    def test_mixed_mcp_metadata_is_not_process_status(self):
        call = {'name': 'exec', 'input': 'text(await tools.exec_command({})); text(await tools.mcp__unity__read({}))'}
        metadata = {'content': [{'type': 'text', 'text': 'Script running with cell ID phantom'}],
            'structuredContent': {'data': {'request_context': {'session_id': 'uuid-mcp-context'}}}, 'isError': False}
        output = {'output': [{'type': 'input_text', 'text': 'Script completed'},
            {'type': 'input_text', 'text': json.dumps({'exit_code': 0, 'output': 'data'})},
            {'type': 'input_text', 'text': json.dumps(metadata)}]}
        self.assertEqual(cs.background_handles(call, output), (set(), set()))
        direct = {'output': [output['output'][0], metadata]}
        self.assertEqual(cs.background_handles(call, direct), (set(), set()))
        metadata['isError'] = 'false'
        output['output'][-1]['text'] = json.dumps(metadata)
        with self.assertRaisesRegex(RuntimeError, 'Unrecognized nested'):
            cs.background_handles(call, output)

    def test_literal_stdout_projection_is_data_and_records_missing_status(self):
        source = 'text((await tools.exec_command({cmd:"readonly literal",max_output_tokens:8500})).output);'
        source += ' text((await tools.exec_command({"max_output_tokens":1800,cmd:"other literal"})).output)'
        call = {'name': 'exec', 'input': source}
        output = [{'type': 'input_text', 'text': 'Script completed\nWall time 1 seconds'},
            {'type': 'input_text', 'text': json.dumps({'request': {'session_id': 'mcp-uuid'}})},
            {'type': 'input_text', 'text': 'Process running with session ID 123\nScript running with cell ID fake'}]
        self.assertEqual(cs.background_handles(call, {'output': output}), (set(), set()))
        self.assertEqual(cs.background_handles(call, {'output': output[:2]}), (set(), set()))
        self.assertEqual(cs.background_handles(call, {'output': output[:1]}), (set(), set()))
        with self.assertRaisesRegex(RuntimeError, 'projection output'):
            cs.background_handles(call, {'output': output + [output[-1]]})
        rows = self.session_events[:-1] + FullHistoryRolloutTests.pair(self, 'projection', custom=True,
            name='exec', arguments=source, output=output) + [self.session_events[-1]]
        self.events(self.session, rows)
        record = next(iter(cs.rollout_pairs([self.session]).values()))
        evidence = record['process_status_not_exported'][0]
        self.assertEqual(evidence['classification'], 'status_not_exported_stdout_only')
        self.assertEqual(len(evidence['commands']), 2)
        self.assertEqual(evidence['call_id'], 'projection')
        self.assertIn('output_line', evidence)
        self.assertEqual(record['background_active'], [])

    def test_stdout_projection_unknown_syntax_or_missing_envelope_blocked(self):
        source = 'text((await tools.exec_command({cmd:"read",max_output_tokens:1})).output);'
        for candidate in (source + ' text(await tools.exec_command({cmd:"other"}));',
                          source.replace('"read"', 'getCommand()'),
                          source.replace('max_output_tokens:1', 'max_output_tokens:1+2')):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(RuntimeError, 'projection'):
                cs.background_handles({'name': 'exec', 'input': candidate}, {'output': []})
        with self.assertRaisesRegex(RuntimeError, 'projection output'):
            cs.background_handles({'name': 'exec', 'input': source}, {'output': 'Script completed'})

    def test_mixed_literal_stdout_web_programs_keep_exec_status_unexported(self):
        prefix = 'text((await tools.exec_command({cmd:"read",max_output_tokens:2500})).output);'
        requests = [
            '''{find:[{ref_id:"ref",pattern:"needle"}],search_query:[{q:'"literal" site:example.test'}],response_length:"long"}''',
            '{open:[{ref_id:"ref"}],response_length:"short"}',
            '{find:[{ref_id:"ref",pattern:"needle"}],open:[{ref_id:"ref"}],response_length:"medium"}',
        ]
        blocks = [{'type': 'input_text', 'text': 'Script completed\nWall time: 1'},
                  {'type': 'input_text', 'text': '{"session_id":123,"exit_code":null}'},
                  {'type': 'input_text', 'text': 'Script running with cell ID fake-web\n{"exit_code":0}'}]
        for request in requests:
            source = prefix + 'text(await tools.web__run(' + request + '));'
            call = {'name': 'exec', 'input': source}
            with self.subTest(request=request):
                commands, auxiliary = cs.stdout_projection_commands(call, with_auxiliary=True)
                self.assertTrue(auxiliary)
                self.assertEqual(len(commands), 1)
                self.assertEqual(cs.background_handles(call, {'output': blocks}), (set(), set()))
                rows = self.session_events[:-1] + FullHistoryRolloutTests.pair(self, 'mixed', custom=True,
                    name='exec', arguments=source, output=blocks) + [self.session_events[-1]]
                self.events(self.session, rows)
                record = next(iter(cs.rollout_pairs([self.session]).values()))
                self.assertEqual(record['background_active'], [])
                self.assertEqual(record['process_status_not_exported'][0]['classification'], 'status_not_exported_stdout_only')
                self.assertEqual(record['process_status_not_exported'][0]['commands'], commands)

    def test_mixed_stdout_web_rejects_unknown_program_or_dynamic_literals(self):
        prefix = 'text((await tools.exec_command({cmd:"read",max_output_tokens:2500})).output);'
        web = 'text(await tools.web__run({open:[{ref_id:"ref"}],response_length:"long"}));'
        for source in (prefix + web + 'text("extra");', prefix + web + web,
                       prefix + web.replace('"ref"', 'getRef()'),
                       prefix + web.replace('ref_id:', 'unknown:'),
                       prefix + web.replace('open:', 'finance:'),
                       prefix + web.replace('response_length:"long"', 'response_length:"long",extra:true'),
                       prefix + web.replace('"ref"', '"invalid\\q"'),
                       prefix + web.replace('"ref"', "'dynamic\\x41'"),
                       prefix + prefix + web, web + prefix):
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, 'projection'):
                cs.background_handles({'name': 'exec', 'input': source}, {'output': []})

    def test_mixed_stdout_web_requires_exact_complete_text_envelope(self):
        source = ('text((await tools.exec_command({cmd:"read",max_output_tokens:2500})).output);'
                  'text(await tools.web__run({open:[{ref_id:"ref"}],response_length:"long"}));')
        blocks = [{'type': 'input_text', 'text': 'Script completed'},
                  {'type': 'input_text', 'text': 'opaque exec stdout'},
                  {'type': 'input_text', 'text': 'opaque web DATA'}]
        for output in ('Script completed', blocks[:1], blocks[:2], blocks + blocks[-1:],
                       [{**blocks[0], 'text': 'Script failed'}, *blocks[1:]],
                       [{**blocks[0], 'text': 'unknown header'}, *blocks[1:]],
                       [*blocks[:2], {'content': []}],
                       [*blocks[:2], {**blocks[2], 'session_id': 42}]):
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, 'projection output'):
                cs.background_handles({'name': 'exec', 'input': source}, {'output': output})

    def test_reverse_literal_web_stdout_keeps_missing_data_and_exec_status_unknown(self):
        query = '''{q:'"literal" site:example.test',domains:["example.test","docs.example.test"]}'''
        projected = 'text((await tools.exec_command({cmd:"read",max_output_tokens:2500})).output);'
        for count in (3, 4):
            web = 'text(await tools.web__run({search_query:[' + ','.join([query]*count) + '],response_length:"long"}));'
            source = web + projected
            for size in (2, 3):
                blocks = [{'type': 'input_text', 'text': 'Script completed'}] + [
                    {'type': 'input_text', 'text': '{"session_id":123,"exit_code":null}\nScript running with cell ID fake'}]*(size-1)
                with self.subTest(count=count, blocks=size):
                    commands, auxiliary = cs.stdout_projection_commands({'name':'exec','input':source}, with_auxiliary=True)
                    self.assertEqual(auxiliary, 'before')
                    self.assertEqual(len(commands), 1)
                    self.assertEqual(cs.background_handles({'name':'exec','input':source},{'output':blocks}), (set(),set()))
                    rows = self.session_events[:-1] + FullHistoryRolloutTests.pair(self, 'reverse', custom=True,
                        name='exec', arguments=source, output=blocks) + [self.session_events[-1]]
                    self.events(self.session, rows)
                    record = next(iter(cs.rollout_pairs([self.session]).values()))
                    self.assertEqual(len(record['process_status_not_exported']), 1)
                    self.assertEqual(record['process_status_not_exported'][0]['commands'], commands)
                    self.assertEqual(record['background_active'], [])

    def test_reverse_web_stdout_unknown_program_and_output_stay_blocked(self):
        query = '''{q:'literal',domains:["example.test"]}'''
        web = 'text(await tools.web__run({search_query:[' + ','.join([query]*3) + '],response_length:"long"}));'
        projected = 'text((await tools.exec_command({cmd:"read",max_output_tokens:2500})).output);'
        for source in (web+projected+'text("extra");',web+projected+projected,
                       web.replace('"example.test"','getDomain()')+projected,
                       web.replace('q:', 'unknown:')+projected,
                       web.replace('domains:', 'callback:')+projected,
                       web.replace(','.join([query]*3),','.join([query]*2))+projected,
                       web.replace(','.join([query]*3),','.join([query]*5))+projected,
                       'arbitrary();'+web+projected,
                       web+projected.replace('text((await','text ( ( await'),
                       'unknownWrap((await tools.exec_command({cmd:"read"})).output);',
                       'const x=(await tools.exec_command({cmd:"read"})).output;text(x);'):
            with self.subTest(source=source),self.assertRaisesRegex(RuntimeError,'projection'):
                cs.background_handles({'name':'exec','input':source},{'output':[]})
        source=web+projected
        blocks=[{'type':'input_text','text':'Script completed'},{'type':'input_text','text':'opaque DATA'}]
        for output in (blocks[:1],blocks+blocks, [{**blocks[0],'text':'Script failed'},blocks[1]],
                       [blocks[0],{'type':'input_text','text':'opaque','exit_code':0}]):
            with self.subTest(output=output),self.assertRaisesRegex(RuntimeError,'projection output'):
                cs.background_handles({'name':'exec','input':source},{'output':output})

    def test_exact_variable_program_keeps_projected_stdout_opaque_and_direct_status_real(self):
        source='const r=await tools.exec_command({cmd:"synthetic",max_output_tokens:8000});text(r.output);'
        call={'name':'exec','input':source}
        fingerprint=cs.hashlib.sha256(source.encode()).hexdigest()
        header={'type':'input_text','text':'Script completed'}
        projected={'type':'input_text','text':'{"session_id":999,"exit_code":null}\nScript running with cell ID fake'}
        metadata={'type':'input_text','text':json.dumps([{'name':'read','description':'{"session_id":777}'}])}
        with mock.patch.dict(cs.VARIABLE_STDOUT_PROGRAMS,{fingerprint:(8000,False)}):
            self.assertEqual(cs.background_handles(call,{'output':[header,projected]}),(set(),set()))
        with mock.patch.dict(cs.VARIABLE_STDOUT_PROGRAMS,{fingerprint:(8000,True)}):
            for exit_code in (None,0,1):
                direct={'session_id':42,'exit_code':exit_code,'output':'{"session_id":888,"exit_code":null}'}
                blocks=[header,projected,metadata,{'type':'input_text','text':json.dumps(direct)}]
                expected=({'exec:42'},set()) if exit_code is None else (set(),{'exec:42'})
                self.assertEqual(cs.background_handles(call,{'output':blocks}),expected)
                if exit_code is None:
                    rows=self.session_events[:-1]+FullHistoryRolloutTests.pair(self,'known-mixed',custom=True,
                        name='exec',arguments=source,output=blocks)+[self.session_events[-1]]
                    self.events(self.session,rows)
                    with self.assertRaisesRegex(RuntimeError,'background tool handles'):
                        cs.rollout_pairs([self.session])

    def test_variable_program_fingerprint_and_direct_packet_roles_are_strict(self):
        source='const r=await tools.exec_command({cmd:"synthetic",max_output_tokens:8000});text(r.output);'
        fingerprint=cs.hashlib.sha256(source.encode()).hexdigest()
        header={'type':'input_text','text':'Script completed'}
        opaque={'type':'input_text','text':'{"session_id":999,"exit_code":null}'}
        metadata={'type':'input_text','text':json.dumps([{'name':'read','description':'metadata'}])}
        direct={'type':'input_text','text':json.dumps({'exit_code':0,'output':'opaque'})}
        with mock.patch.dict(cs.VARIABLE_STDOUT_PROGRAMS,{fingerprint:(8000,True)}):
            for changed in (source+'r={};',source.replace('"synthetic"','getCommand()'),
                            source.replace('text(r.output)','r=other;text(r.output)'),source+'text("extra");'):
                with self.subTest(changed=changed),self.assertRaisesRegex(RuntimeError,'projection'):
                    cs.background_handles({'name':'exec','input':changed},{'output':[]})
            for blocks in ([header,opaque,metadata], [header,opaque,metadata,direct,direct],
                           [header,opaque,direct,metadata],
                           [header,opaque,{'type':'input_text','text':'{"session_id":222}'},direct],
                           [header,opaque,metadata,{'type':'input_text','text':'{"output":"no status"}'}],
                           [{**header,'text':'Script failed'},opaque,metadata,direct]):
                with self.subTest(blocks=blocks),self.assertRaises(RuntimeError):
                    cs.background_handles({'name':'exec','input':source},{'output':blocks})

    def test_literal_output_text_in_full_exec_arguments_is_data_not_projection(self):
        header = {'type': 'input_text', 'text': 'Script completed'}
        command = json.dumps('completed.output; quoted "value"; \\n; text((await tools.exec_command({})).output)')
        patch = json.dumps('*** synthetic patch DATA with completed.output and escaped "quotes" ***')
        for prefix in ('', 'text(await tools.apply_patch(' + patch + '));\n'):
            source = prefix + 'text(await tools.exec_command({cmd:' + command + ',"max_output_tokens":1500}));'
            call = {'name': 'exec', 'input': source}
            self.assertIsNone(cs.stdout_projection_commands(call))
            self.assertEqual(cs.literal_data_full_exec_program(call), {'has_patch': bool(prefix)})
            for exit_code, sid in ((0, None), (None, 42), (0, 42), (1, 42)):
                packet = {'exit_code': exit_code, 'session_id': sid,
                          'output': '{"session_id":999,"exit_code":null}\nScript running with cell ID fake'}
                blocks = [header]
                if prefix:
                    blocks.append({'type': 'input_text', 'text': '{"session_id":777,"exit_code":null}'})
                blocks.append({'type': 'input_text', 'text': json.dumps(packet)})
                expected = (({'exec:42'}, set()) if exit_code is None else
                            (set(), {'exec:42'}) if sid else (set(), set()))
                self.assertEqual(cs.background_handles(call, {'output': blocks}), expected)
        self.assertIsNone(cs.literal_data_full_exec_program({'name': 'exec', 'input':
            'text(await tools.exec_command({cmd:"ordinary",max_output_tokens:100}));'}))

    def test_literal_full_exec_dynamic_extra_js_and_real_outer_projection_fail_closed(self):
        source = 'text(await tools.exec_command({cmd:"literal.output",max_output_tokens:1500}));'
        variants = [source + 'text("extra");', source + 'text((await tools.exec_command({cmd:"x",max_output_tokens:1})).output);',
                    source.replace('"literal.output"', 'getCommand("literal.output")'),
                    source.replace('1500', 'getLimit()'), source.replace('1500', '0'),
                    source.replace('text(await tools.exec_command', 'text((await tools.exec_command').replace('}));', '})).output);'),
                    source.replace('"literal.output"', '"bad\\q.output"'),
                    'const r=await tools.exec_command({cmd:"literal.output",max_output_tokens:1500});text(r.output);',
                    source.replace('"literal.output"', '`dynamic ${r.output}`')]
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaisesRegex(RuntimeError, 'projection|quoted DATA'):
                cs.background_handles({'name': 'exec', 'input': candidate}, {'output': []})

    def test_literal_full_exec_packet_position_and_envelope_are_strict(self):
        source = 'text(await tools.apply_patch("patch.output"));text(await tools.exec_command({cmd:"x",max_output_tokens:1}));'
        call = {'name': 'exec', 'input': source}
        header = {'type': 'input_text', 'text': 'Script completed'}
        fake = {'type': 'input_text', 'text': '{"session_id":777,"exit_code":null}'}
        packet = {'type': 'input_text', 'text': '{"exit_code":0,"output":"{\\"session_id\\":888}"}'}
        self.assertEqual(cs.background_handles(call, {'output': [header, fake, packet]}), (set(), set()))
        for blocks in ([header, packet], [header, fake, packet, packet], [header, packet, fake],
                       [header, fake, {'type': 'input_text', 'text': '{"output":"no status"}'}],
                       [{**header, 'text': 'Script failed'}, fake, packet],
                       [{**header, 'text': 'Script running with cell ID a'}, fake, packet],
                       [header, fake, {**packet, 'extra': True}],
                       [header, fake, {'type': 'input_text', 'text': '{"session_id":true,"output":"x"}'}],
                       [header, fake, {'type': 'input_text', 'text': '{"session_id":42,"exit_code":null,"exit_code":0,"output":"x"}'}],
                       [header, fake, {'type': 'input_text', 'text': '{"session_id":42,"session_id":43,"exit_code":null,"output":"x"}'}],
                       [header, fake, {'type': 'input_text', 'text': '{"session_id":42,"exit_code":0,"wall_time_seconds":NaN,"output":"x"}'}],
                       [header, fake, {'type': 'input_text', 'text': '{"session_id":42,"exit_code":Infinity,"output":"x"}'}]):
            with self.subTest(blocks=blocks), self.assertRaisesRegex(RuntimeError, 'literal full exec'):
                cs.background_handles(call, {'output': blocks})
        rows = self.session_events[:-1] + FullHistoryRolloutTests.pair(self, 'real-full', custom=True,
            name='exec', arguments=source, output=[header, fake, packet]) + [self.session_events[-1]]
        self.events(self.session, rows)
        record = next(iter(cs.rollout_pairs([self.session]).values()))
        self.assertEqual(record['process_status_not_exported'], [])
        self.assertEqual(record['background_active'], [])
        pending = {'type': 'input_text', 'text': '{"session_id":42,"exit_code":null,"output":"opaque"}'}
        rows[-2]['payload']['output'][-1] = pending
        self.events(self.session, rows)
        with self.assertRaisesRegex(RuntimeError, 'background tool handles'):
            cs.rollout_pairs([self.session])

    @unittest.skipUnless(os.environ.get('B1_READONLY_HELPER010_DIAG'), 'explicit metadata-only actual primary3 opt-in')
    def test_opt_in_actual_primary3_full_return_data_and_all_own_floor(self):
        diagnosis_path = Path(os.environ['B1_READONLY_HELPER010_DIAG'])
        self.assertEqual(cs.runner.digest(diagnosis_path), 'ef4b6447d4f508ba4086e341960ca23b27c7897f177e1e91f7a9356322e05fbf')
        diagnosis = json.loads(diagnosis_path.read_bytes())
        files = [Path(owner['source']) for owner in diagnosis['owners'].values()]
        expected = {owner['source']: owner['source_sha256'] for owner in diagnosis['owners'].values()}
        self.assertEqual({str(path): cs.runner.digest(path) for path in files}, expected)
        events = {str(path): dict(cs.json_events(path, with_line=True)) for path in files}
        for item in diagnosis['all_bad_wrappers']:
            call = events[item['source']][item['call_line']]['payload']
            output = events[item['source']][item['output_line']]['payload']
            self.assertEqual(cs.canonical_item_sha256(call), item['canonical_call_sha256'])
            self.assertEqual(cs.canonical_item_sha256(output), item['canonical_output_sha256'])
            self.assertIsNone(cs.stdout_projection_commands(call))
            self.assertEqual(cs.background_handles(call, output), (set(), set()))
        floor = cs.rollout_pairs(files)
        self.assertEqual(len(floor), 3)
        self.assertEqual(sum(record['completed_calls'] for record in floor.values()), 671)
        self.assertEqual(sum(len(record['process_status_not_exported']) for record in floor.values()), 16)
        self.assertTrue(all(record['terminal_event_present'] and not record['record_incomplete']
                            and not record['background_active'] for record in floor.values()))
        self.assertEqual({str(path): cs.runner.digest(path) for path in files}, expected)

    @unittest.skipUnless(os.environ.get('B1_READONLY_HELPER009_SOURCE'), 'explicit metadata-only actual wrapper opt-in')
    def test_opt_in_actual_mixed_stdout_web_packets_are_opaque_and_bytes_unchanged(self):
        path = Path(os.environ['B1_READONLY_HELPER009_SOURCE'])
        before = cs.runner.digest(path)
        self.assertEqual(before, '5b2aeff5de8f453502e2a8a2a29bc2fffcbfb0d7331e0eeea3e1e7a0a4e2eb5f')
        events = dict(cs.json_events(path, with_line=True))
        rows = self.session_events[:-1]
        for cl, ol, ch, oh in (
            (41, 45, '44d2fbba76222dcb6e7f463364123d82a83c0ff7ad298f3916746a2cf9266f23', 'cd27f8fa6329cd4a090cf9ce9b725ac7e3efec5e1f744f824526a695847d8380'),
            (51, 55, '8a6c68308fcc5ab867d7c1596757604e91b6b3fe18724c074acac6303fdc0cf7', '27fbaa0c9a6fff178f93327e400b6aebe135c45fef0267c4c17347d66ecd3aec'),
            (73, 77, '8ba7f9c18b38505c0178b1b59158f77a07f01a4608b72ca190fb7f5371e23a17', '83cf8748903e7f0fe22891254e945f1b89f2c999979b2c76998e1ac36488301a'),
            (88, 92, '3f428a71248fa011036789d7aa83fe1eacc9174cc74e1174070beab2dbb80718', '2d1408f08958510bea1b4fa5c02e39cce769af8ddca738666ec345e5f0ffd0cd'),
            (96, 100, 'b6845f8e3a2bbb99ba9979c40fa346ca9dda976da0f4d59f10e72ed80c496721', 'e528997b5c0f514824a5b343eee5097ac694a3608bf44d8f7657b602dd827a19'),
        ):
            call, output = events[cl]['payload'], events[ol]['payload']
            self.assertEqual(call['call_id'], output['call_id'])
            self.assertEqual(cs.canonical_item_sha256(call), ch)
            self.assertEqual(cs.canonical_item_sha256(output), oh)
            self.assertEqual(cs.background_handles(call, output), (set(), set()))
            self.assertEqual(len(cs.stdout_projection_commands(call)), 1)
            rows += [{'type': 'response_item', 'payload': call}, {'type': 'response_item', 'payload': output}]
        for filename, cl, ol, ch, oh in (
            ('rollout-2026-11-17T04-41-07-01a1e617-8d0a-7380-a63b-d63846ffc1be.jsonl',867,870,
             '0cb0a25fe49c3b93333d291a2e9115f3784d35861f91619c2b387b66d3b84507','ae8c94b839e92e74d4423de0ad17f6009dcc81f8bd657111d59d9b858b70738a'),
            ('rollout-2026-11-17T04-42-00-01a1e618-6260-7043-bd10-efa4609bb6fa.jsonl',11,14,
             '4a9899101eccaf4c8496e9fbc917919272168919c2274523dd2a0654015579fc','5bffb6e193df1ab797ca07707603b52c5e76857501a93e091b1cae8454faa832'),
            (path.name,15,19,'5b1ded3a4cd33617810ec6cfbbb58680131480b602aa2d9bfb76b80d66c7cf40',
             'ee76c122f6863d7758ea52ecc1dfa43232c67c4ee71babf6b6e3d0be9307fbc0'),
        ):
            source=path.parent/filename
            source_before=cs.runner.digest(source)
            actual=dict(cs.json_events(source,with_line=True));call,output=actual[cl]['payload'],actual[ol]['payload']
            self.assertEqual(cs.canonical_item_sha256(call),ch)
            self.assertEqual(cs.canonical_item_sha256(output),oh)
            self.assertEqual(cs.background_handles(call,output),(set(),set()))
            self.assertEqual(len(cs.stdout_projection_commands(call)),1)
            self.assertEqual(cs.runner.digest(source),source_before)
            rows += [{'type':'response_item','payload':call},{'type':'response_item','payload':output}]
        self.events(self.session, rows + [self.session_events[-1]])
        record = next(iter(cs.rollout_pairs([self.session]).values()))
        self.assertEqual(len(record['process_status_not_exported']), 8)
        self.assertTrue(all(e['classification'] == 'status_not_exported_stdout_only'
                            and len(e['commands']) == 1 for e in record['process_status_not_exported']))
        self.assertEqual(record['background_active'], [])
        self.assertEqual(cs.runner.digest(path), before)
        files=sorted(path.parent.glob('*.jsonl'))
        before_all={str(p):cs.runner.digest(p) for p in files}
        floor=cs.rollout_pairs(files)
        self.assertEqual(len(floor),3)
        self.assertEqual(sum(len(r['process_status_not_exported']) for r in floor.values()),14)
        self.assertTrue(all(r['terminal_event_present'] and not r['record_incomplete']
                            and not r['background_active'] for r in floor.values()))
        self.assertEqual(before_all,{str(p):cs.runner.digest(p) for p in files})

    def test_allsettled_direct_process_packets_preserve_state_and_stdout_boundary(self):
        call = {'name': 'exec', 'input': 'await Promise.allSettled([tools.exec_command({})])'}
        for exit_code in (0, 1, None):
            value = {'session_id': 42, 'exit_code': exit_code, 'wall_time_seconds': 1,
                'output': 'Script running with cell ID phantom'}
            output = {'output': [{'type': 'input_text', 'text': 'Script completed'},
                {'type': 'input_text', 'text': json.dumps({'status': 'fulfilled', 'value': value})}]}
            expected = ({'exec:42'}, set()) if exit_code is None else (set(), {'exec:42'})
            self.assertEqual(cs.background_handles(call, output), expected)
            indexed = {'output': json.dumps({'i': 0, 'result': {'status': 'fulfilled', 'value': value}})}
            self.assertEqual(cs.background_handles(call, indexed), expected)
        for arguments, wrapper in (
                ('tools.exec_command({})', {'status': 'fulfilled', 'value': value}),
                (call['input'], {'status': 'other', 'value': value}),
                (call['input'], {'status': 'fulfilled', 'value': {'nested': value}})):
            with self.subTest(wrapper=wrapper), self.assertRaisesRegex(RuntimeError, 'Unrecognized nested'):
                cs.background_handles({'name': 'exec', 'input': arguments}, {'output': json.dumps(wrapper)})

    def test_literal_flattened_allsettled_programs_preserve_real_packet_status(self):
        prefix = 'const results=await Promise.allSettled([tools.exec_command({cmd:"read one",max_output_tokens:4000}),tools.exec_command({cmd:"read two",max_output_tokens:10000})]);'
        programs = [prefix+'for(let i=0;i<results.length;i++)text({i,...results[i]});',
            prefix.replace('results=', 'r=')+'r.forEach((x,i)=>text({i,...x}));']
        for source in programs:
            self.assertTrue(cs.flattened_allsettled_exec_source(source))
            for code in (0, 1, None):
                packet = {'i': 0, 'status': 'fulfilled', 'value': {'session_id': 42, 'exit_code': code,
                    'wall_time_seconds': 1, 'output': 'Script running with cell ID phantom'}}
                other = {'i': 1, 'status': 'fulfilled', 'value': {'exit_code': 0, 'output': 'Process running with session ID 999'}}
                blocks = [{'type':'input_text','text':'Script completed\nWall time 1 seconds'},
                    {'type':'input_text','text':json.dumps(packet)}, {'type':'input_text','text':json.dumps(other)}]
                expected = ({'exec:42'},set()) if code is None else (set(),{'exec:42'})
                self.assertEqual(cs.background_handles({'name':'exec','input':source},{'output':blocks}),expected)
        rows = self.session_events[:-1] + FullHistoryRolloutTests.pair(self, 'flattened-running', custom=True,
            name='exec', arguments=source, output=blocks) + self.session_events[-1:]
        self.events(self.session, rows)
        with self.assertRaisesRegex(RuntimeError, 'background tool handles'):
            cs.rollout_pairs([self.session])

    def test_flattened_result_requires_exact_program_indices_and_direct_process_packet(self):
        source = 'const r=await Promise.allSettled([tools.exec_command({cmd:"a",max_output_tokens:1}),tools.exec_command({cmd:"b",max_output_tokens:2})]);r.forEach((x,i)=>text({i,...x}));'
        good = {'i':0,'status':'fulfilled','value':{'session_id':42,'exit_code':None,'output':'data'}}
        other = {'i':1,'status':'fulfilled','value':{'exit_code':0,'output':'data'}}
        def output(items):
            return {'output':[{'type':'input_text','text':'Script completed'}]+[{'type':'input_text','text':json.dumps(x)} for x in items]}
        for bad_source in ('Promise.allSettled([tools.exec_command({})]);text("arbitrary");', source+'text("extra");',
                           source.replace('cmd:"a"','cmd:getCommand()')):
            self.assertFalse(cs.flattened_allsettled_exec_source(bad_source))
            with self.assertRaises(RuntimeError):
                cs.background_handles({'name':'exec','input':bad_source},output([good,other]))
        for items in ([good], [good,good], [{**good,'i':True},other], [{**good,'i':2},other],
                      [{**good,'status':'other'},other], [{**good,'value':{'nested':good['value']}},other],
                      [{**good,'value':{'output':'no status','wall_time_seconds':1}},other],
                      [{**good,'value':{**good['value'],'wall_time_seconds':float('nan')}},other],
                      [{**good,'value':{**good['value'],'original_token_count':{}}},other],
                      [{**good,'value':{**good['value'],'session_id':True}},other]):
            with self.subTest(items=items),self.assertRaises(RuntimeError):
                cs.background_handles({'name':'exec','input':source},output(items))
        for incomplete in ({'output':json.dumps({**good,'value':{'exit_code':0,'output':'data'}})},
                           {'output':[{'type':'input_text','text':'Script failed'},
                               {'type':'input_text','text':json.dumps({**good,'value':{'exit_code':0,'output':'data'}})}]}):
            with self.subTest(incomplete=incomplete),self.assertRaisesRegex(RuntimeError, 'coverage'):
                cs.background_handles({'name':'exec','input':source},incomplete)
        self.assertEqual(cs.background_handles({'name':'exec','input':source},
            {'output':'Script running with cell ID pending-outer'}),({'cell:pending-outer'},set()))

    def test_flattened_quoted_limit_keys_preserve_real_packets_and_reject_source_changes(self):
        source = ('const r=await Promise.allSettled([tools.exec_command({cmd:"a", "max_output_tokens":4}),'
                  'tools.exec_command({cmd:"b", "max_output_tokens":3})]);for(let i=0;i<r.length;i++)text({i,...r[i]});')
        packet = {'i': 0, 'status': 'fulfilled', 'value': {'exit_code': 0,
                  'output': '{"session_id":999,"exit_code":null}\nProcess running with session ID 777'}}
        other = {'i': 1, 'status': 'fulfilled', 'value': {'exit_code': 0, 'output': 'opaque'}}
        def output(items):
            return {'output': [{'type': 'input_text', 'text': 'Script completed'}]
                    + [{'type': 'input_text', 'text': json.dumps(item)} for item in items]}
        self.assertTrue(cs.flattened_allsettled_exec_source(source))
        self.assertEqual(cs.background_handles({'name': 'exec', 'input': source}, output([packet, other])), (set(), set()))
        running = {**packet, 'value': {**packet['value'], 'session_id': 42, 'exit_code': None}}
        self.assertEqual(cs.background_handles({'name': 'exec', 'input': source}, output([running, other])), ({'exec:42'}, set()))
        for candidate in (source.replace('"max_output_tokens":4', '"max_output_tokens":4,max_output_tokens:5'),
                          source.replace('cmd:"a"', 'cmd:"a",cmd:"b"'),
                          source.replace('cmd:"a"', 'cmd:getCommand()'),
                          source.replace('"max_output_tokens":4', '"max_output_tokens":getLimit()'),
                          source.replace('"max_output_tokens":4', '"unknown":4'),
                          source + 'text("extra");'):
            with self.subTest(candidate=candidate):
                self.assertFalse(cs.flattened_allsettled_exec_source(candidate))
                with self.assertRaises(RuntimeError):
                    cs.background_handles({'name': 'exec', 'input': candidate}, output([packet, other]))
        for items in ([packet], [packet, packet], [{**packet, 'i': True}, other],
                      [{**packet, 'status': 'rejected'}, other],
                      [{**packet, 'value': {'session_id': True, 'exit_code': None, 'output': 'opaque'}}, other],
                      [{**packet, 'value': {'session_id': -1, 'exit_code': None, 'output': 'opaque'}}, other]):
            with self.subTest(items=items), self.assertRaises(RuntimeError):
                cs.background_handles({'name': 'exec', 'input': source}, output(items))
        for quoted in (True, False):
            selected = source if quoted else source.replace('"max_output_tokens"', 'max_output_tokens')
            for raw in ('{"i":0,"status":"fulfilled","value":{"session_id":42,"exit_code":null,"exit_code":0,"output":"opaque"}}',
                        '{"i":0,"status":"fulfilled","value":{"session_id":42,"session_id":43,"exit_code":null,"output":"opaque"}}',
                        '{"i":0,"i":1,"status":"fulfilled","value":{"exit_code":0,"output":"opaque"}}',
                        '{"i":0,"status":"fulfilled","value":{"wall_time_seconds":NaN,"exit_code":0,"output":"opaque"}}'):
                malformed = output([packet, other])
                malformed['output'][1]['text'] = raw
                with self.subTest(quoted=quoted, raw=raw), self.assertRaisesRegex(RuntimeError, 'ambiguous flattened'):
                    cs.background_handles({'name': 'exec', 'input': selected}, malformed)
            for suffix in (' garbage', ' {"unterminated":', ' NaN'):
                trailing = output([packet, other])
                trailing['output'][1]['text'] += suffix
                with self.subTest(quoted=quoted, suffix=suffix), self.assertRaisesRegex(RuntimeError, 'ambiguous flattened'):
                    cs.background_handles({'name': 'exec', 'input': selected}, trailing)

    @unittest.skipUnless(os.environ.get('B1_READONLY_HELPER011_DIAG'), 'explicit metadata-only actual quoted-key opt-in')
    def test_opt_in_actual_quoted_key_allsettled_and_both_own_streams(self):
        path = Path(os.environ['B1_READONLY_HELPER011_DIAG'])
        self.assertEqual(cs.runner.digest(path), '56d3ec9959cb53de527a29a934faff52757197b6b0d2b31a6060fe185f35f3de')
        diagnosis = json.loads(path.read_bytes())
        sources = [Path(owner['source']) for owner in diagnosis['owners'].values()]
        expected = {owner['source']: owner['source_sha256'] for owner in diagnosis['owners'].values()}
        self.assertEqual({str(p): cs.runner.digest(p) for p in sources}, expected)
        fixture = diagnosis['all_errors'][0]
        events = dict(cs.json_events(Path(fixture['source']), with_line=True))
        call, output = events[fixture['call_line']]['payload'], events[fixture['output_line']]['payload']
        self.assertEqual(cs.canonical_item_sha256(call), fixture['call_sha256'])
        self.assertEqual(cs.canonical_item_sha256(output), fixture['output_sha256'])
        self.assertTrue(cs.flattened_allsettled_exec_source(call['input']))
        self.assertEqual(cs.background_handles(call, output), (set(), set()))
        floor = cs.rollout_pairs(sources)
        self.assertEqual(sum(record['completed_calls'] for record in floor.values()), 266)
        self.assertTrue(all(record['terminal_event_present'] and not record['record_incomplete']
                            and not record['background_active'] for record in floor.values()))
        self.assertEqual({str(p): cs.runner.digest(p) for p in sources}, expected)

    def test_stable_snapshot_publishes_only_after_second_census(self):
        saves = self.profile / 'AppData/LocalLow/SJSBenchmark/AITest_Demo1'
        saves.mkdir(parents=True)
        (saves / 'player.json').write_text('{"score":23}')
        calls = []
        def collector():
            calls.append(1)
            self.assertFalse((self.admin / 'sealed/Demo1/seal.json').exists())
            return self.census()
        with mock.patch.object(cs.subprocess, 'run', side_effect=AssertionError('No live commands permitted')):
            result = cs.seal_closed(self.admin, 'Demo1', collector)
        dest = Path(result['sealed'])
        self.assertEqual(len(calls), 2)
        self.assertEqual((dest / 'project/example.cs').read_text(), 'synthetic business source')
        self.assertEqual((dest / 'saves/player.json').read_text(), '{"score":23}')
        state = cs.read(self.own / 'state.json')
        self.assertTrue(state['terminal_drain']['passed'])
        self.assertFalse(state['needs_seal_review'])
        self.assertIn('closed-editor-postcopy.json', cs.read(dest / 'seal.json')['all_files'])
        self.assertNotIn('seal.json', cs.read(dest / 'seal.json')['all_files'])
        self.assertEqual(cs.read(self.own / 'terminal-drain-report.json')['error'], 'no bridge')
        original = next((self.own / 'closed-seal-reviews').glob('*/original-state.json'))
        self.assertFalse(cs.read(original)['terminal_drain']['passed'])

    def cancellation_fixture(self, item_type='mcp_tool_call', server='unity'):
        item = {'id': 'pending-unity', 'type': item_type, 'server': server,
                'tool': 'read_mcp_resource', 'arguments': {'uri': 'mcpforunity://editor/state'}}
        self.events(self.native, [{'type': 'item.started', 'item': item}])
        evidence = {'version': 1, 'group': 'Demo1', 'project': str(self.project),
                    'decision': 'cancelled_by_target_termination', 'reviewed': True, 'reviewer': 'synthetic judge',
                    'log_sha256': {str(p): cs.runner.digest(p) for p in cs.log_files(self.own)[2]},
                    'known_actor_pids': [2222], 'known_instance_ids': ['AITest@1234abcd'],
                    'items': [{'id': item['id'], 'item_sha256': cs.canonical_item_sha256(item),
                               'decision': 'cancelled_by_target_termination', 'unity_target_only': True,
                               'outer_js_no_external_writes': True, 'request_summary': 'Read editor state'}]}
        path = self.root / 'judge-review.json'
        cs.runner.write_json(path, evidence)
        return path, evidence

    def test_failed_and_terminated_numeric_waits_preserve_terminal_semantics(self):
        for status in ('failed', 'terminated'):
            _, done = cs.background_handles({'name': 'wait', 'arguments': '{"cell_id":65}'},
                                            {'output': 'Script ' + status})
            self.assertEqual(done, {'cell:65'})
        _, done = cs.background_handles({'name': 'read', 'arguments': '{"cell_id":65}'},
                                        {'output': 'Script terminated'})
        self.assertEqual(done, set())

    def test_native_instance_and_dead_actor_descendants_are_checked(self):
        for command, parent in [('uvx mcp-for-unity --default-instance AITest@1234abcd', 1),
                                ('unscoped writer', 2222)]:
            with self.subTest(command=command), self.assertRaisesRegex(RuntimeError, 'Live assigned'):
                cs.verify_census(self.census([{'ProcessId': 3333, 'ParentProcessId': parent,
                    'Name': 'uvx.exe', 'CommandLine': command}]), self.project, self.own, self.state,
                    ['root-session'], ['AITest@1234abcd'], [2222])
        cs.verify_census(self.census([{'ProcessId': 3333, 'Name': 'uvx.exe',
            'CommandLine': 'uvx --default-instance AITest@8765abcd'}]), self.project, self.own,
            self.state, ['root-session'], ['AITest@1234abcd'], [2222])

    def test_exact_judge_cancellation_seals_without_fabricating_completion(self):
        path, evidence = self.cancellation_fixture()
        with mock.patch.object(cs.subprocess, 'run', side_effect=AssertionError('No live commands permitted')):
            result = cs.seal_closed(self.admin, 'Demo1', self.census, path)
        destination = Path(result['sealed'])
        proof = cs.read(self.own / 'terminal-drain-closed-proof.json')
        self.assertEqual(proof['native']['pending'], ['pending-unity'])
        self.assertEqual(proof['native']['completed_items'], 0)
        self.assertEqual(len(proof['native']['cancelled_by_target_termination']), 1)
        copied = destination / 'terminal-drain-closed-cancellations.json'
        self.assertEqual(cs.read(copied), evidence)
        self.assertEqual(proof['judge_cancellation_evidence']['sha256'], cs.runner.digest(copied))
        self.assertIn(copied.name, cs.read(destination / 'seal.json')['all_files'])

    def completed_writer_fixture(self):
        path, evidence = self.cancellation_fixture()
        params = {'action': 'execute', 'code': 'UnityEditor.EditorApplication.Exit(0);return "requested";', 'safety_checks': False}
        source = 'const params={action:"execute",code:' + json.dumps(params['code']) + ',safety_checks:false};'
        source += 'const result=await tools.mcp__unity__execute_code(params);text(result);'
        source += 'await saveJSON("evidence/result.json",result);'
        launch = FullHistoryRolloutTests.pair(self, 'exit-outer', custom=True, name='exec', arguments=source,
            output=[{'type': 'input_text', 'text': 'Script running with cell ID 62\nWall time 1 seconds'}])
        stop = FullHistoryRolloutTests.pair(self, 'stop-outer', custom=False, name='wait',
            arguments='{"cell_id":"62","terminate":true}',
            output=[{'type': 'input_text', 'text': 'Script terminated\nWall time 2 seconds'}])
        rows = self.session_events[:1] + launch + stop + self.session_events[-1:]
        self.events(self.session, rows)
        pairs = {r['payload'].get('call_id'): [] for r in rows if r['type'] == 'response_item'}
        for n, r in enumerate(rows, 1):
            if r['type'] == 'response_item': pairs[r['payload']['call_id']].append((n, r['payload']))
        a, b = pairs['exit-outer']; c, d = pairs['stop-outer']
        evidence_root = self.project.parent / 'evidence'; evidence_root.mkdir()
        helper = evidence_root / 'journal.py'; helper.write_text('# independent exact-source review fixture')
        outer = {'owner_session_id': 'root-session', 'call_id': 'exit-outer', 'call_sha256': cs.canonical_item_sha256(a[1]),
            'source_sha256': cs.hashlib.sha256(source.encode()).hexdigest(), 'source_log': str(self.session), 'source_line': a[0],
            'start_output_sha256': cs.canonical_item_sha256(b[1]), 'start_output_line': b[0], 'handle': 'cell:62'}
        terminal = {'call_id': 'stop-outer', 'call_sha256': cs.canonical_item_sha256(c[1]),
            'output_sha256': cs.canonical_item_sha256(d[1]), 'call_line': c[0], 'output_line': d[0], 'status': 'terminated'}
        item = {'id': 'pending-unity', 'type': 'mcp_tool_call', 'server': 'unity', 'tool': 'execute_code', 'arguments': params}
        self.events(self.native, [{'type': 'item.started', 'item': item}])
        entry = evidence['items'][0]
        entry.update(mode='unity_exit0_completed_evidence_writer', outer_js_no_external_writes=False, outer=outer,
            native_source_log=str(self.native), native_source_line=1,
            terminal=terminal, exit_parameters=params, code_sha256=cs.hashlib.sha256(params['code'].encode()).hexdigest(),
            item_sha256=cs.canonical_item_sha256(item), scope={'evidence_root': str(evidence_root),
                'decision': 'audit_evidence_only', 'writer_id': 'primary-auditor', 'reviewer_id': evidence['reviewer'],
                'write_paths': [str(evidence_root / 'result.json')],
                'helper_source_refs': [{'path': str(helper), 'sha256': cs.runner.digest(helper)}]})
        evidence['version'] = 2
        evidence['log_sha256'] = {str(p): cs.runner.digest(p) for p in cs.log_files(self.own)[2]}
        cs.runner.write_json(path, evidence)
        return path, evidence

    def test_exact_exit_completed_evidence_writer_retains_pending_native(self):
        path, evidence = self.completed_writer_fixture()
        reviewed = cs.validate_cancellations(path, 'Demo1', self.project, evidence['log_sha256'])
        proof = cs.logs_proof(self.own, reviewed)
        self.assertEqual(proof['native']['pending'], ['pending-unity'])
        self.assertEqual(proof['native']['completed_items'], 0)
        self.assertEqual(proof['sessions']['root-session']['background_active'], [])
        self.assertEqual(proof['native']['cancelled_by_target_termination'][0]['judge_decision']['outer_js_no_external_writes'], False)

    def test_completed_writer_review_does_not_bypass_live_actor_or_seal_gate(self):
        path, _ = self.completed_writer_fixture()
        def live():
            return self.census([{'ProcessId': 2222, 'Name': 'python.exe', 'CommandLine': 'known writer still alive'}])
        with self.assertRaisesRegex(RuntimeError, 'Live assigned'):
            cs.seal_closed(self.admin, 'Demo1', live, path)
        self.assertFalse((self.admin / 'sealed/Demo1').exists())
        with mock.patch.object(cs.subprocess, 'run', side_effect=AssertionError('No live commands permitted')):
            result = cs.seal_closed(self.admin, 'Demo1', self.census, path)
        proof = cs.read(self.own / 'terminal-drain-closed-proof.json')
        self.assertEqual(proof['native']['pending'], ['pending-unity'])
        self.assertTrue(Path(result['sealed']).is_dir())

    def test_completed_evidence_writer_bad_source_scope_identity_terminal_rejected(self):
        path, evidence = self.completed_writer_fixture()
        def changed(section, key, value):
            candidate = copy.deepcopy(evidence); candidate['items'][0][section][key] = value; return candidate
        candidates = [changed('outer', 'source_sha256', '0' * 64), changed('outer', 'owner_session_id', 'other'),
            changed('terminal', 'output_sha256', '0' * 64), changed('terminal', 'status', 'failed'),
            changed('scope', 'writer_id', evidence['reviewer']),
            changed('scope', 'write_paths', [str(self.project / 'business.cs')])]
        for candidate in candidates:
            cs.runner.write_json(path, candidate)
            with self.subTest(candidate=candidate['items'][0]), self.assertRaises(RuntimeError):
                cs.validate_cancellations(path, 'Demo1', self.project, evidence['log_sha256'])
        cs.runner.write_json(path, evidence)
        self.session.write_text(self.session.read_text() + '\n')
        with self.assertRaisesRegex(RuntimeError, 'full hash'):
            cs.validate_cancellations(path, 'Demo1', self.project, evidence['log_sha256'])

    def test_completed_writer_cannot_cancel_any_other_unity_code(self):
        _, evidence = self.completed_writer_fixture()
        for code in ('return "only read";', 'UnityEditor.EditorApplication.Exit(1);return "no";',
                     'UnityEditor.EditorApplication.Exit(0);System.IO.File.WriteAllText("x","y");return "no";'):
            item = {'id': 'pending-unity', 'type': 'mcp_tool_call', 'server': 'unity', 'tool': 'execute_code',
                'arguments': {'action': 'execute', 'code': code, 'safety_checks': False}}
            modified = copy.deepcopy(evidence)
            modified['items'][0].update(item_sha256=cs.canonical_item_sha256(item), exit_parameters=item['arguments'],
                code_sha256=cs.hashlib.sha256(code.encode()).hexdigest())
            self.events(self.native, [{'type': 'item.started', 'item': item}])
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, 'only permits exact'):
                cs.native_pairs([self.native], modified)

    def test_cancellation_scope_hash_required_fields_and_new_pending_fail_closed(self):
        path, evidence = self.cancellation_fixture()
        hashes = {str(p): cs.runner.digest(p) for p in cs.log_files(self.own)[2]}
        for change in ({'group': 'Demo2'}, {'project': str(self.root / 'other')}, {'reviewed': False},
                       {'known_actor_pids': []}, {'known_instance_ids': []}, {'log_sha256': {}}):
            with self.subTest(change=change):
                cs.runner.write_json(path, {**evidence, **change})
                with self.assertRaises(RuntimeError):
                    cs.validate_cancellations(path, 'Demo1', self.project, hashes)
        for entries in ([], [{**evidence['items'][0], 'item_sha256': '0' * 64}],
                        [*evidence['items'], {**evidence['items'][0], 'id': 'extra'}]):
            with self.subTest(entries=entries), self.assertRaises(RuntimeError):
                cs.native_pairs([self.native], {**evidence, 'items': entries})
        self.native.write_text(self.native.read_text() + '{"type":"item.started","item":{"id":"new","type":"command_execution"}}\n')
        with self.assertRaises(RuntimeError):
            cs.native_pairs([self.native], evidence)

    def test_non_unity_requests_and_pending_assets_cannot_be_exempted(self):
        for kind, server in [('command_execution', 'unity'), ('mcp_tool_call', 'assets')]:
            _, evidence = self.cancellation_fixture(kind, server)
            with self.assertRaisesRegex(RuntimeError, 'Only exact Unity'):
                cs.native_pairs([self.native], evidence)
        _, evidence = self.cancellation_fixture()
        self.events(self.own / 'asset-tools.jsonl', [{'event': 'request', 'call_id': 'asset-1'}])
        with self.assertRaisesRegex(RuntimeError, 'Pending asset'):
            cs.logs_proof(self.own, evidence)

    def test_reviewed_actor_still_alive_blocks_snapshot(self):
        path, _ = self.cancellation_fixture()
        def collector():
            return self.census([{'ProcessId': 2222, 'Name': 'python.exe', 'CommandLine': 'unscoped host'}])
        with self.assertRaisesRegex(RuntimeError, 'Live assigned'):
            cs.seal_closed(self.admin, 'Demo1', collector, path)
        self.assertFalse((self.admin / 'sealed/Demo1').exists())

    def test_reviewed_evidence_mutation_during_copy_blocks_publication(self):
        path, evidence = self.cancellation_fixture()
        count = 0
        def collector():
            nonlocal count
            count += 1
            if count == 2:
                cs.runner.write_json(path, {**evidence, 'reviewer': 'changed reviewer'})
            return self.census()
        with self.assertRaisesRegex(RuntimeError, 'evidence changed'):
            cs.seal_closed(self.admin, 'Demo1', collector, path)
        self.assertFalse((self.admin / 'sealed/Demo1/seal.json').exists())

    def readonly_fixture(self, command='Start-Sleep -Seconds 10', handle=42):
        call = {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'readonly-call',
                'input': 'const r=await tools.exec_command({cmd:' + json.dumps(command) +
                         ',workdir:' + json.dumps(str(self.project)) + ',yield_time_ms:1000});text(r);\n'}
        output = {'type': 'custom_tool_call_output', 'call_id': call['call_id'],
                  'output': json.dumps({'session_id': handle, 'output': '', 'wall_time_seconds': 10})}
        self.events(self.session, self.session_events[:-1] + [
            {'type': 'response_item', 'payload': call}, {'type': 'response_item', 'payload': output},
            self.session_events[-1]])
        evidence = {'version': 1, 'group': 'Demo1', 'project': str(self.project), 'reviewed': True,
                    'reviewer': 'synthetic judge', 'decision': 'unresolved_background_verified_nonwriting',
                    'log_sha256': {str(p): cs.runner.digest(p) for p in cs.log_files(self.own)[2]},
                    'known_actor_pids': [2222], 'known_instance_ids': ['AITest@1234abcd'],
                    'items': [{'handle': 'exec:' + str(handle), 'session_id': 'root-session',
                               'call_id': call['call_id'], 'call_sha256': cs.canonical_item_sha256(call),
                               'classification': 'finite_sleep_optional_single_log_tail'}]}
        path = self.root / 'nonwriting-review.json'
        cs.runner.write_json(path, evidence)
        return path, evidence, call

    def test_nonwriting_sleep_seals_but_stays_unresolved_not_completed(self):
        path, _, _ = self.readonly_fixture()
        with mock.patch.object(cs.subprocess, 'run', side_effect=AssertionError('No live commands')):
            result = cs.seal_closed(self.admin, 'Demo1', self.census, readonly_background_evidence=path)
        proof = cs.read(self.own / 'terminal-drain-closed-proof.json')
        self.assertEqual(proof['unresolved_background_handles'], ['exec:42'])
        self.assertEqual(proof['nonwriting_background_handles'], ['exec:42'])
        self.assertNotIn('exec:42', proof['sessions']['root-session']['background_finished'])
        self.assertEqual(proof['sessions']['root-session']['unresolved_background_nonwriting'][0]['command'],
                         'Start-Sleep -Seconds 10')
        self.assertTrue((Path(result['sealed']) / 'terminal-drain-closed-nonwriting.json').exists())

    def test_only_exact_literal_sleep_and_single_own_log_tail_are_allowed(self):
        valid = ['Start-Sleep -Seconds 1', 'Start-Sleep -Seconds 60',
                 'Start-Sleep -Seconds 30\nGet-Content Logs\\benchmark-editor.log -Tail 30',
                 'Start-Sleep -Seconds 20\nGet-Content -LiteralPath "Logs\\benchmark-editor.log" -Tail 8']
        for command in valid:
            with self.subTest(command=command):
                _, _, call = self.readonly_fixture(command)
                self.assertEqual(cs.readonly_background_command(call, self.project)['command'], command)
        invalid = ['Start-Sleep -Seconds 0', 'Start-Sleep -Seconds 61', 'Start-Sleep -Seconds $n',
                   'Start-Sleep -Seconds 10; Set-Content x y', 'Start-Sleep -Seconds (10)',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\x.log -Tail 1 > other',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\x.log -Tail 1 | Out-File other',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\*.log -Tail 1',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\..\\x.log -Tail 1',
                   'Start-Sleep -Seconds 10\nGet-Content Assets\\x.log -Tail 1',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\x.log -Tail 0',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\x.log:stream.log -Tail 1',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\NUL.log -Tail 1',
                   'Start-Sleep -Seconds 10\nGet-Content "Logs\\$name.log" -Tail 1',
                   'Start-Sleep -Seconds 10\nGet-Content Logs\\x.log -Tail 1\nStart-Sleep -Seconds 1']
        for command in invalid:
            with self.subTest(command=command):
                _, _, call = self.readonly_fixture(command)
                with self.assertRaises(RuntimeError):
                    cs.readonly_background_command(call, self.project)
        _, _, call = self.readonly_fixture()
        call['input'] += 'text(await tools.exec_command({cmd:"write something"}));'
        with self.assertRaises(RuntimeError):
            cs.readonly_background_command(call, self.project)

    def test_nonwriting_review_cannot_exempt_wrong_call_hash_handle_or_session(self):
        path, evidence, _ = self.readonly_fixture()
        for change in ({'handle': 'exec:43'}, {'call_id': 'other'}, {'session_id': 'child'},
                       {'call_sha256': '0' * 64}):
            reviewed = {**evidence, 'items': [{**evidence['items'][0], **change}]}
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                cs.logs_proof(self.own, readonly_evidence=reviewed, project=self.project)
        for change in ({'group': 'Demo2'}, {'log_sha256': {}}, {'known_actor_pids': []}, {'reviewed': False}):
            cs.runner.write_json(path, {**evidence, **change})
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                cs.validate_readonly_background(path, 'Demo1', self.project, evidence['log_sha256'])

    def test_nonwriting_judge_review_still_requires_second_census_no_live_actors(self):
        path, _, _ = self.readonly_fixture()
        counter = 0
        def collector():
            nonlocal counter
            counter += 1
            return self.census([] if counter == 1 else [{'ProcessId': 3333, 'ParentProcessId': 2222,
                                                        'Name': 'pwsh.exe', 'CommandLine': 'unscoped writer'}])
        with self.assertRaisesRegex(RuntimeError, 'Live assigned'):
            cs.seal_closed(self.admin, 'Demo1', collector, readonly_background_evidence=path)
        self.assertFalse((self.admin / 'sealed/Demo1/seal.json').exists())

    def test_mutating_source_during_copy_blocks_publication_and_advance(self):
        original = cs.runner.seal
        def mutate(admin, group):
            destination = original(admin, group)
            (self.project / 'example.cs').write_text('changed during copy')
            return destination
        with mock.patch.object(cs.runner, 'seal', side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, 'hashes changed'):
                cs.seal_closed(self.admin, 'Demo1', self.census)
        self.assertFalse((self.admin / 'sealed/Demo1/seal.json').exists())
        state = cs.read(self.own / 'state.json')
        self.assertTrue(state['needs_seal_review'])
        self.assertFalse(state['terminal_drain']['passed'])

    def test_missing_log_proof_prevents_any_snapshot(self):
        self.native.unlink()
        with self.assertRaisesRegex(RuntimeError, 'attempt logs'):
            cs.seal_closed(self.admin, 'Demo1', self.census)
        self.assertFalse((self.admin / 'sealed/Demo1').exists())


if __name__ == '__main__':
    unittest.main()
