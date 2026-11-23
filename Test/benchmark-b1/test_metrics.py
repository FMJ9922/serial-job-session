import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import metrics
import runner


def meta(session='root'):
    return {'timestamp': '01', 'type': 'session_meta', 'payload': {'id': session, 'source': 'exec'}}


def usage(timestamp, amount):
    return {'timestamp': timestamp, 'type': 'event_msg', 'payload': {
        'type': 'token_count', 'info': {'total_token_usage': {
            'input_tokens': amount, 'cached_input_tokens': amount // 2,
            'output_tokens': 10, 'reasoning_output_tokens': 2, 'total_tokens': amount + 10}}}}


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, records):
        (self.root / name).write_text('\n'.join(json.dumps(record) for record in records), encoding='utf-8')

    def test_uses_last_cumulative_not_sum_of_snapshots_or_subsets(self):
        self.write('root.jsonl', [meta(), usage('02', 100), usage('03', 200)])
        result = metrics.session_metrics(self.root)
        self.assertEqual(result['reported_token_totals']['total_tokens'], 210)
        self.assertEqual(result['reported_token_totals']['cached_input_tokens'], 100)
        self.assertIsNone(result['reported_token_totals']['cache_write_input_tokens'])

    def full_history(self, owner, parent_rows, own_events, parent='root'):
        first = meta(owner)
        first['payload'].update(parent_thread_id=parent, subagent_history_start_ordinal=len(parent_rows) + 1,
            source={'subagent': {'thread_spawn': {'parent_thread_id': parent}}})
        rows = [first, *copy.deepcopy(parent_rows), *copy.deepcopy(own_events)]
        for n, event in enumerate(rows): event['ordinal'] = n
        return rows

    def test_two_level_full_history_counts_only_actual_owners_and_compactions(self):
        context = lambda sid: {'timestamp': '02', 'type': 'turn_context', 'payload': {'turn_id': sid, 'model': sid}}
        compact = {'timestamp': '03', 'type': 'compacted', 'payload': {}}
        root = [meta(), context('root'), usage('04', 100), compact]
        for n, event in enumerate(root): event['ordinal'] = n
        child = self.full_history('child', root, [context('child'), usage('05', 50)])
        grand = self.full_history('grand', child, [context('grand'), usage('06', 20)], parent='child')
        for name, rows in [('root', root), ('child', child), ('grand', grand)]: self.write(name+'.jsonl', rows)
        report = metrics.session_metrics(self.root)
        self.assertEqual(len(report['sessions']), 3)
        self.assertEqual(report['reported_token_totals']['total_tokens'], 200)
        self.assertEqual(report['recorded_compaction_events'], 1)
        for session in report['sessions']:
            self.assertEqual(session['models'], [session['session_id']])
            self.assertEqual(session['recorded_turn_contexts'], 1)
            self.assertTrue(session['own_execution_attribution_verified'])
        self.assertEqual(next(s for s in report['sessions'] if s['session_id']=='child')['source'],
            {'subagent': {'thread_spawn': {'parent_thread_id': 'root'}}})
        self.assertEqual(report['reported_token_totals']['cached_input_tokens'], 85)
        self.assertEqual(report['reported_token_totals']['reasoning_output_tokens'], 6)

    def test_unknown_full_history_boundary_or_ancestor_withholds_values(self):
        root = [meta(), usage('02', 100)]
        for n, event in enumerate(root): event['ordinal']=n
        child = self.full_history('child', root, [usage('03', 50)])
        self.write('root.jsonl', root)
        for change in ('missing_boundary', 'wrong_projection', 'missing_ancestor', 'ordinal_gap'):
            rows = copy.deepcopy(child)
            if change=='missing_boundary': del rows[0]['payload']['subagent_history_start_ordinal']
            if change=='wrong_projection': rows[2]['payload']['info']['total_token_usage']['input_tokens']=999
            if change=='missing_ancestor': rows[0]['payload'].update(parent_thread_id='absent', source='exec')
            if change=='ordinal_gap': rows[-1]['ordinal']+=1
            self.write('child.jsonl', rows)
            result = metrics.session_metrics(self.root)
            with self.subTest(change=change):
                self.assertIsNone(result['reported_token_totals']['total_tokens'])
                self.assertIsNone(result['recorded_compaction_events'])
                self.assertFalse(result['own_execution_attribution_complete'])
                self.assertTrue(result['warnings'])

    def test_full_history_same_owner_resume_deduplicates_own_events(self):
        root=[meta(),usage('02',100)]
        for n,event in enumerate(root):event['ordinal']=n
        child=self.full_history('child',root,[usage('03',50),{'timestamp':'04','type':'compacted','payload':{}}])
        resumed=copy.deepcopy(child)+[{'timestamp':'05','type':'event_msg','ordinal':len(child),'payload':usage('05',80)['payload']}]
        self.write('root.jsonl',root);self.write('child-first.jsonl',child);self.write('child-resume.jsonl',resumed)
        result=metrics.session_metrics(self.root)
        self.assertEqual(len(result['sessions']),2)
        self.assertEqual(result['reported_token_totals']['total_tokens'],200)
        self.assertEqual(result['recorded_compaction_events'],1)

    def test_same_owner_resume_conflicting_parent_or_boundary_is_unknown(self):
        root = [meta(), usage('02', 100), {'timestamp': '03', 'type': 'compacted', 'payload': {}}]
        other = [meta('other'), usage('02', 200)]
        for rows in (root, other):
            for n, event in enumerate(rows): event['ordinal'] = n
        self.write('root.jsonl', root); self.write('other.jsonl', other)
        first = self.full_history('child', root[:2], [usage('04', 50)])
        self.write('child-first.jsonl', first)
        for variant in (self.full_history('child', other, [usage('05', 80)], parent='other'),
                        self.full_history('child', root, [usage('05', 80)])):
            self.write('child-resume.jsonl', variant)
            result = metrics.session_metrics(self.root)
            with self.subTest(parent=variant[0]['payload']['parent_thread_id'], boundary=len(variant)-1):
                self.assertFalse(result['own_execution_attribution_complete'])
                self.assertIsNone(result['reported_token_totals']['total_tokens'])
                self.assertIsNone(result['recorded_compaction_events'])
                self.assertTrue(any('Conflicting parent or execution boundary' in w for w in result['warnings']))

    @unittest.skipUnless(os.environ.get('B1_READONLY_METRICS_RECHECK'), 'explicit metadata-only sealed recheck opt-in')
    def test_opt_in_seventeen_sealed_owner_metadata_and_counters_are_read_only(self):
        path = Path(os.environ['B1_READONLY_METRICS_RECHECK'])
        doc = json.loads(path.read_bytes())
        count = 0
        for group in doc['groups'].values():
            owners = group['owners']; files = [Path(owner['file']) for owner in owners]
            before = {p:runner.digest(p) for p in files}
            self.assertEqual(list(before.values()), [owner['file_sha256'] for owner in owners])
            result = metrics.session_metrics(self.root,files=files)
            self.assertEqual({s['session_id'] for s in result['sessions']},{o['owner_id'] for o in owners})
            self.assertTrue(result['own_execution_attribution_complete'])
            self.assertTrue(all(s['own_execution_attribution_verified'] for s in result['sessions']))
            self.assertEqual(before,{p:runner.digest(p) for p in files})
            count += len(owners)
        self.assertEqual(count,17)

    def test_duplicate_resumed_session_is_counted_once_and_children_are_added(self):
        self.write('first.jsonl', [meta(), usage('02', 100)])
        self.write('resumed.jsonl', [meta(), usage('02', 100), usage('03', 200)])
        self.write('child.jsonl', [meta('child'), usage('02', 50)])
        result = metrics.session_metrics(self.root)
        self.assertEqual(len(result['sessions']), 2)
        self.assertEqual(result['reported_token_totals']['total_tokens'], 270)

    def test_missing_child_usage_makes_aggregate_unknown(self):
        self.write('root.jsonl', [meta(), usage('02', 100)])
        self.write('child.jsonl', [meta('child')])
        self.assertIsNone(metrics.session_metrics(self.root)['reported_token_totals']['total_tokens'])

    def test_counter_reset_remains_unknown_rather_than_inventing_billable_usage(self):
        self.write('root.jsonl', [meta(), usage('02', 100), usage('03', 20)])
        result = metrics.session_metrics(self.root)
        self.assertIsNone(result['reported_token_totals']['total_tokens'])
        self.assertTrue(result['warnings'])

    def test_compaction_dual_records_are_not_double_counted(self):
        self.write('root.jsonl', [meta(), usage('02', 100),
            {'timestamp': '03', 'type': 'compacted', 'payload': {}},
            {'timestamp': '04', 'type': 'event_msg', 'payload': {'type': 'context_compacted'}}])
        self.assertEqual(metrics.session_metrics(self.root)['recorded_compaction_events'], 1)

    def test_corrupt_record_makes_aggregate_unknown(self):
        self.write('root.jsonl', [meta(), usage('02', 100)])
        with (self.root / 'root.jsonl').open('a') as handle:
            handle.write('\n{incomplete')
        self.assertIsNone(metrics.session_metrics(self.root)['reported_token_totals']['total_tokens'])

    def test_latest_missing_field_does_not_reuse_stale_counter(self):
        first, second = usage('02', 100), usage('03', 200)
        del second['payload']['info']['total_token_usage']['cached_input_tokens']
        self.write('root.jsonl', [meta(), first, second])
        self.assertIsNone(metrics.session_metrics(self.root)['reported_token_totals']['cached_input_tokens'])

    def test_delivery_requires_sealed_unchanged_logs_and_preserves_missing_cost(self):
        sessions = self.root / 'sessions'
        sessions.mkdir()
        (sessions / 'root.jsonl').write_text('\n'.join(json.dumps(v) for v in [meta(), usage('02', 100)]))
        state = {'group': 'Demo1', 'status': 'submitted', 'process_tree_stopped': True,
                 'effective_seconds': 30, 'observations': {'child_session_count': 0},
                 'infrastructure_reviews': [{'outage_seconds': 12}]}
        (self.root / 'state.json').write_text(json.dumps(state))
        (self.root / 'seal.json').write_text(json.dumps({'all_files': runner.inventory(self.root)}))
        result = metrics.collect_delivery(self.root)
        self.assertEqual(result['reported_token_totals']['total_tokens'], 110)
        self.assertEqual(result['excluded_outage_seconds'], 12)
        self.assertIsNone(result['actual_cost'])
        extra = sessions / 'unsealed.jsonl'
        extra.write_text(json.dumps(meta('injected')))
        with self.assertRaisesRegex(ValueError, 'Sealed evidence changed'):
            metrics.collect_delivery(self.root)
        extra.unlink()
        (sessions / 'root.jsonl').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Sealed evidence changed'):
            metrics.collect_delivery(self.root)

    def test_user_policy_resume_is_separate_and_does_not_exclude_its_runtime(self):
        state = {'effective_seconds': 3047.86,
                 'infrastructure_reviews': [{'attempt': 1, 'outage_seconds': 12}],
                 'user_policy_resume_approval': {'attempt': 2, 'effective_seconds_unchanged': 3047.86},
                 'attempts': [{'number': 1}, {'number': 2, 'resume_basis': 'infrastructure_approved_attempt'},
                              {'number': 3, 'resume_basis': 'user_policy_resume_approval'}]}
        result = metrics.resume_metrics(state)
        self.assertEqual(result['infrastructure_resume_count'], 1)
        self.assertEqual(result['user_policy_resume_count'], 1)
        self.assertEqual(result['infrastructure_review_count'], 1)
        self.assertEqual(result['user_policy_approval_count'], 1)
        self.assertEqual(result['excluded_outage_seconds'], 12)
        self.assertEqual(state['effective_seconds'], 3047.86)

    def test_unused_approval_is_not_counted_as_started_resume(self):
        state = {'attempts': [{'number': 1}],
                 'infrastructure_reviews': [{'attempt': 1, 'outage_seconds': 5}],
                 'user_policy_resume_approval': {'attempt': 1}}
        result = metrics.resume_metrics(state)
        self.assertEqual(result['infrastructure_resume_count'], 0)
        self.assertEqual(result['user_policy_resume_count'], 0)
        self.assertEqual(result['user_policy_approval_count'], 1)

    def test_legacy_resumes_require_matching_review_and_missing_history_stays_unknown(self):
        state = {'infrastructure_reviews': [{'attempt': 1, 'outage_seconds': 8}],
                 'attempts': [{'number': 1}, {'number': 2}, {'number': 3}]}
        result = metrics.resume_metrics(state)
        self.assertEqual(result['infrastructure_resume_count'], 1)
        self.assertEqual(result['legacy_infrastructure_resumes_inferred'], 1)
        self.assertEqual(result['unclassified_resume_count'], 1)
        missing = metrics.resume_metrics({'infrastructure_reviews': state['infrastructure_reviews']})
        self.assertEqual(missing['infrastructure_review_count'], 1)
        self.assertIsNone(missing['infrastructure_resume_count'])
        self.assertIsNone(missing['user_policy_resume_count'])

    def test_explicit_policy_basis_wins_over_earlier_infrastructure_review(self):
        result = metrics.resume_metrics({'infrastructure_reviews': [{'attempt': 1, 'outage_seconds': 0}],
            'attempts': [{'number': 1}, {'number': 2, 'resume_basis': 'user_policy_resume_approval'}]})
        self.assertEqual(result['infrastructure_resume_count'], 0)
        self.assertEqual(result['user_policy_resume_count'], 1)

    def test_cli_creates_default_results_parent_after_all_deliveries_are_sealed(self):
        (self.root / 'frozen').mkdir()
        groups = ['Demo1', 'Demo2', 'Demo5', 'Demo', 'Demo3', 'Demo4']
        manifest = {'order': groups, 'frozen_files': {}}
        runner.write_json(self.root / 'manifest.json', manifest)
        (self.root / 'manifest.sha256').write_text(runner.digest(self.root / 'manifest.json'))
        for group in groups:
            sealed = self.root / 'sealed' / group
            sealed.mkdir(parents=True)
            runner.write_json(sealed / 'state.json', {'group': group, 'status': 'submitted',
                'process_tree_stopped': True, 'effective_seconds': 30, 'attempts': [{'number': 1}]})
            runner.write_json(sealed / 'seal.json', {'all_files': runner.inventory(sealed)})
        output = self.root / 'results/ancillary-metrics.json'
        self.assertFalse(output.parent.exists())
        command = [sys.executable, str(Path(metrics.__file__).resolve()), '--admin', str(self.root)]
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(output))
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(set(report['groups']), set(groups))
            self.assertFalse(report['affects_main_score'])


if __name__ == '__main__':
    unittest.main()
