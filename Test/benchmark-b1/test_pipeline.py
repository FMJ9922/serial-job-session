import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import pipeline
import runner


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.admin = Path(self.temp.name)
        self.order = ['one', 'two']
        self.patch = patch.object(runner, 'manifest', return_value={'order': self.order})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.states('ready', 'ready')

    def states(self, *statuses):
        for key, status in zip(self.order, statuses):
            own = self.admin / 'groups' / key
            own.mkdir(parents=True, exist_ok=True)
            (own / 'state.json').write_text(json.dumps({'status': status}), encoding='utf-8')

    def test_ready_and_running_never_start_audits(self):
        for status in ('ready', 'running'):
            self.states('submitted', status)
            self.assertEqual(pipeline.inspect_development(self.admin)[0], 'development_running')

    def test_paused_invalid_and_stopped_require_judge_attention(self):
        for status in ('paused', 'invalid', 'stopped'):
            self.states('running', status)
            self.assertEqual(pipeline.inspect_development(self.admin)[0], 'attention_required')

    def test_terminal_but_unsealed_does_not_start_audits(self):
        self.states('submitted', 'timeout')
        self.assertEqual(pipeline.inspect_development(self.admin)[0], 'development_running')

    def test_all_sealed_can_start_audits_but_hash_mutation_cannot(self):
        self.states('submitted', 'timeout')
        for key in self.order:
            root = self.admin / 'sealed' / key
            (root / 'project/Assets').mkdir(parents=True)
            (root / 'project/Assets/source.cs').write_text('original')
            (root / 'seal.json').write_text(json.dumps({'source_files': runner.inventory(root / 'project')}))
        self.assertEqual(pipeline.inspect_development(self.admin)[0], 'ready_for_audit')
        (self.admin / 'sealed/one/project/Assets/source.cs').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'Sealed project changed'):
            pipeline.inspect_development(self.admin)

    def test_metrics_are_collected_before_audits_and_failure_does_not_start_auditor(self):
        runtime = self.admin / 'runtime'
        runtime.mkdir()
        (runtime / 'metrics.py').write_text('collector')
        for code in (0, 1):
            with patch.object(pipeline, 'verify_runtime'), \
                    patch.object(pipeline, 'inspect_development', return_value=('ready_for_audit', {})), \
                    patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=code)), \
                    patch.object(pipeline, 'audit_step') as audit_step:
                result = pipeline.watch(self.admin, runtime, Path('Unity.exe'), interval=0)
                self.assertEqual(result, 0 if code == 0 else 2)
                self.assertEqual(audit_step.call_count, 2 if code == 0 else 0)


if __name__ == '__main__':
    unittest.main()
