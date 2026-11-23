import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.admin = Path(self.temp.name)
        self.now = dt.datetime(2026, 11, 16, 6, 0, tzinfo=dt.timezone.utc)

    def test_early_invocation_never_reads_manifest_or_participants(self):
        (self.admin / 'monitor-policy.json').write_text(json.dumps({'last_poll_utc': self.now.isoformat()}))
        with patch.object(monitor.runner, 'manifest') as manifest, patch.object(monitor, 'process_alive') as probe:
            result = monitor.poll(self.admin, self.now + dt.timedelta(seconds=599))
            self.assertEqual(result['wait_seconds'], 1)
            manifest.assert_not_called()
            probe.assert_not_called()

    def test_observation_checks_actual_pid_and_persists_cadence_across_invocations(self):
        own = self.admin / 'groups/Demo1'
        own.mkdir(parents=True)
        (own / 'state.json').write_text(json.dumps({'status': 'running', 'pid': 123, 'attempts': []}))
        with patch.object(monitor.runner, 'manifest', return_value={'order': ['Demo1']}) as manifest, \
                patch.object(monitor, 'process_alive', return_value=True):
            first = monitor.poll(self.admin, self.now)
            self.assertTrue(first['groups']['Demo1']['process_alive'])
            self.assertEqual(monitor.poll(self.admin, self.now + dt.timedelta(seconds=60))['status'], 'not_due')
            self.assertEqual(monitor.poll(self.admin, self.now + dt.timedelta(seconds=600))['status'], 'observed')
            self.assertEqual(manifest.call_count, 2)

    def test_inaccessible_process_remains_unknown_instead_of_claiming_stopped(self):
        own = self.admin / 'groups/Demo1'
        own.mkdir(parents=True)
        (own / 'state.json').write_text(json.dumps({'status': 'running', 'pid': 123, 'attempts': []}))
        with patch.object(monitor.runner, 'manifest', return_value={'order': ['Demo1']}), \
                patch.object(monitor, 'process_alive', return_value=None):
            self.assertIsNone(monitor.poll(self.admin, self.now)['groups']['Demo1']['process_alive'])

    def test_current_process_is_verified_live(self):
        self.assertIs(monitor.process_alive(os.getpid()), True)


if __name__ == '__main__':
    unittest.main()
