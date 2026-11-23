"""Pure synthetic tests: no CIM, Unity, formal roles or current case execution."""
import copy
import unittest

import process_generation as g


def row(pid, parent, born, scoped=False):
    return {'ProcessId': pid, 'ParentProcessId': parent, 'CreationDate': '/Date(' + str(born) + ')/',
            'Name': 'any-name.exe', 'ExecutablePath': 'arbitrary', 'CommandLine': 'assigned' if scoped else 'other'}


class ProcessGenerationTests(unittest.TestCase):
    def test_old_numeric_edge_does_not_attach_older_service(self):
        selected, rejected = g.actual_generation_tree(
            [row(10, 1, 100), row(20, 10, 110), row(30, 20, 50)], {10}, lambda r: False)
        self.assertEqual([r['ProcessId'] for r in selected], [10, 20])
        self.assertEqual([r['pid'] for r in rejected], [30])

    def test_direct_scope_still_selects_old_process(self):
        selected, _ = g.actual_generation_tree([row(10, 1, 100), row(20, 10, 50, True)],
            {10}, lambda r: isinstance(r['CommandLine'], str) and r['CommandLine'] == 'assigned')
        self.assertEqual([r['ProcessId'] for r in selected], [10, 20])

    def test_true_descendant_with_equal_creation_is_preserved(self):
        selected, rejected = g.actual_generation_tree(
            [row(10, 1, 100), row(20, 10, 100), row(30, 20, 111)], {10}, lambda r: False)
        self.assertEqual([r['ProcessId'] for r in selected], [10, 20, 30])
        self.assertFalse(rejected)

    def test_nullable_intermediate_and_orphan_remain_anchored(self):
        parent, orphan = row(33124, 61552, 110), row(78100, 33124, 120)
        for item in (parent, orphan):
            item['CommandLine'] = item['ExecutablePath'] = None
        archived = g.archived_cim_rows({'rows': [row(61552, 1, 100, True), parent, orphan]})
        selected, _ = g.actual_generation_tree(archived, {61552}, lambda r: False)
        self.assertEqual([r['ProcessId'] for r in selected], [33124, 61552, 78100])
        with self.assertRaises(RuntimeError):
            g.verify_auxiliary_absence({'processes': [orphan]}, {33124: parent, 78100: orphan}, set())

    def test_missing_and_malformed_creation_on_scoped_descendant_block(self):
        for born in (None, '2026-11-17', 100, '/Date(NaN)/'):
            child = row(20, 10, 110)
            child['CreationDate'] = born
            with self.assertRaises(RuntimeError):
                g.actual_generation_tree([row(10, 1, 100), child], {10}, lambda r: False)
        child.pop('CreationDate')
        child['CommandLine'] = child['ExecutablePath'] = None
        archived = g.archived_cim_rows([row(10, 1, 100), child])
        self.assertEqual(len(archived), 2)
        with self.assertRaises(RuntimeError):
            g.actual_generation_tree(archived, {10}, lambda r: False)

    def test_malformed_pid_ppid_or_nullable_columns_are_not_discarded(self):
        for key in ('ProcessId', 'ParentProcessId', 'CommandLine', 'ExecutablePath'):
            child = row(20, 10, 110)
            child[key] = {'fake': 'value'}
            with self.assertRaises(RuntimeError):
                g.archived_cim_rows([child])
        for key in ('ProcessId', 'ParentProcessId'):
            child = row(20, 10, 110)
            child[key] = None
            with self.assertRaises(RuntimeError):
                g.archived_cim_rows([child])

    def test_unrelated_unknown_creation_is_retained_without_false_lineage(self):
        unknown = row(20, 99, 110)
        unknown['CreationDate'] = None
        rows = g.archived_cim_rows([unknown])
        selected, rejected = g.actual_generation_tree(rows, {10}, lambda r: False)
        self.assertFalse(selected or rejected)
        self.assertEqual(len(rows), 1)

    def test_duplicate_conflicting_pid_generation_is_rejected(self):
        with self.assertRaises(RuntimeError):
            g.actual_generation_tree([row(10, 1, 100), row(10, 1, 101)], {10}, lambda r: False)

    def test_pid_zero_requires_exact_idle_identity(self):
        idle = row(0, 0, 0)
        idle.update(Name='System Idle Process', CommandLine=None, ExecutablePath=None, CreationDate=None)
        self.assertEqual(len(g.archived_cim_rows([idle])), 1)
        idle['Name'] = 'arbitrary.exe'
        with self.assertRaises(RuntimeError):
            g.archived_cim_rows([idle])

    def test_no_mutation_and_only_temporally_impossible_edge_excluded(self):
        census = {'processes': [row(30, 20, 50)]}
        old = copy.deepcopy(census)
        result = g.verify_auxiliary_absence(census, {20: row(20, 10, 110)}, set())
        self.assertEqual(len(result['excluded_impossible_numeric_edges']), 1)
        self.assertEqual(census, old)

    def test_same_or_earlier_present_auxiliary_generation_blocks(self):
        for born in (110, 50):
            with self.assertRaises(RuntimeError):
                g.verify_auxiliary_absence({'processes': [row(20, 10, born)]}, {20: row(20, 10, 110)}, set())

    def test_strictly_later_reuse_keeps_both_exact_generations_and_inputs(self):
        anchor, current = row(20, 10, 110), row(20, 999, 200)
        current.update(Name='unrelated.exe', CommandLine=None, ExecutablePath=None)
        census={'processes':[current]};anchors={20:anchor};before=copy.deepcopy((census,anchors))
        result=g.verify_auxiliary_absence(census,anchors,set())
        self.assertTrue(result['no_live_historical_auxiliary_generations'])
        proof=result['excluded_reused_pid_generations'][0]
        self.assertEqual(proof['historical_generation'],anchor);self.assertEqual(proof['current_generation'],current)
        self.assertEqual(proof['historical_creation_ms'],110);self.assertEqual(proof['current_creation_ms'],200)
        self.assertEqual((census,anchors),before)

    def test_reused_pid_missing_malformed_or_same_ms_creation_still_blocks(self):
        for born in (None, 201, '2026-11-17', '/Date(200+0800)/', '/Date(110)/'):
            current=row(20,999,200);current['CreationDate']=born
            with self.subTest(born=born),self.assertRaises(RuntimeError):
                g.verify_auxiliary_absence({'processes':[current]},{20:row(20,10,110)},set())

    def test_reused_pid_possible_old_orphan_and_both_time_boundaries_block(self):
        for born in (110,111,150,199,200,None):
            child=row(30,20,150)
            child['CreationDate']=None if born is None else '/Date('+str(born)+')/'
            child['CommandLine']=child['ExecutablePath']=None
            with self.subTest(born=born),self.assertRaisesRegex(RuntimeError,'descendant|creation generation'):
                g.verify_auxiliary_absence({'processes':[row(20,999,200),child]},
                                            {20:row(20,10,110)},set())

    def test_reused_pid_only_strictly_newer_child_edge_excluded_not_current_scope(self):
        anchor,current,child=row(20,10,110),row(20,999,200),row(30,20,201,True)
        child.update(Name='Unity.exe',ExecutablePath=None)
        census={'processes':[current,child]};before=copy.deepcopy(census)
        result=g.verify_auxiliary_absence(census,{20:anchor},set())
        proof=result['excluded_newer_generation_edges'][0]
        self.assertEqual(proof['child_generation'],child)
        self.assertTrue(proof['current_tree_still_requires_unmodified_full_scope_guard'])
        self.assertTrue(result['direct_scope_and_unknown_unity_checked_by_unmodified_frozen_helper'])
        # The raw current scoped Unity child remains present for the caller's
        # unmodified guard; this helper never labels it a global nonwriter.
        self.assertEqual(census,before)

    def test_reused_pid_older_service_edge_preserves_original_exclusion(self):
        result=g.verify_auxiliary_absence({'processes':[row(20,999,200),row(30,20,109)]},
                                          {20:row(20,10,110)},set())
        self.assertEqual([p['pid'] for p in result['excluded_impossible_numeric_edges']],[30])
        self.assertFalse(result['excluded_newer_generation_edges'])

    def test_newer_parent_never_excuses_separately_anchored_old_child(self):
        with self.assertRaises(RuntimeError):
            g.verify_auxiliary_absence({'processes':[row(20,999,200),row(30,20,150)]},
                                      {20:row(20,10,110),30:row(30,20,150)},set())

    def test_reused_pid_duplicate_current_rows_still_block(self):
        current=row(20,999,200)
        for extra in (current,row(20,888,201)):
            with self.subTest(extra=extra),self.assertRaisesRegex(RuntimeError,'Duplicate raw census PID'):
                g.verify_auxiliary_absence({'processes':[current,extra]},{20:row(20,10,110)},set())

    def test_live_or_unknown_descendant_blocks_without_name_exemption(self):
        for born in (110, 111, None):
            child = row(30, 20, 120)
            child['CreationDate'] = None if born is None else '/Date(' + str(born) + ')/'
            child['CommandLine'] = child['ExecutablePath'] = None
            with self.assertRaises(RuntimeError):
                g.verify_auxiliary_absence({'processes': [child]}, {20: row(20, 10, 110)}, set())

    def test_unknown_anchor_and_identity_mismatch_block_even_without_live_rows(self):
        anchor = row(20, 10, 110)
        anchor['CreationDate'] = None
        with self.assertRaises(RuntimeError):
            g.verify_auxiliary_absence({'processes': []}, {20: anchor}, set())
        with self.assertRaises(RuntimeError):
            g.verify_auxiliary_absence({'processes': []}, {21: row(20, 10, 110)}, set())

    def test_duplicate_fresh_census_pid_is_ambiguous(self):
        with self.assertRaises(RuntimeError):
            g.verify_auxiliary_absence({'processes': [row(30, 20, 50), row(30, 20, 50)]}, {}, set())

    def test_only_explicit_frozen_helper_exclusions_apply(self):
        result = g.verify_auxiliary_absence({'processes': [row(30, 20, 120)]}, {20: row(20, 10, 110)}, {30})
        self.assertTrue(result['no_live_historical_auxiliary_generations'])


if __name__ == '__main__':
    unittest.main()
