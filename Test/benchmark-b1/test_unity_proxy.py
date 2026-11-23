import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import unity_proxy as proxy


class NativePeer:
    def __init__(self):
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'tools/list':
            return {'tools': [{'name': 'manage_scene'}, {'name': 'set_active_instance'}]}
        return {'content': [{'type': 'text', 'text': 'ok'}]}


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'Demo/AITest'
        self.other = self.root / 'Demo1/AITest'
        self.project.mkdir(parents=True)
        self.other.mkdir(parents=True)
        self.live = {'id': 'AITest@ownhash', 'unity_port': 6409, 'project_name': 'AITest'}
        self.proxy = proxy.UnityProxy(self.project, self.root / 'logs/tools.jsonl')
        self.proxy.peer = NativePeer()
        self.proxy.tool_catalog = [{'name': 'set_active_instance'}]
        self.patcher = patch.object(proxy, 'discover', return_value=self.live)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_native_tool_targets_only_assigned_instance(self):
        self.proxy.dispatch('tools/call', {'name': 'manage_scene', 'arguments': {'action': 'get_active'}})
        method, params = self.proxy.peer.calls[-1]
        self.assertEqual(params['arguments']['unity_instance'], 'AITest@ownhash')
        self.assertEqual(params['arguments']['action'], 'get_active')

    def test_foreign_instance_and_path_are_rejected_before_native_call(self):
        for args in ({'unity_instance': 'Other@foreign'}, {'commands': [{'params': {'path': str(self.other / 'Assets/Secret.cs')}}]}):
            with self.assertRaises(PermissionError):
                self.proxy.dispatch('tools/call', {'name': 'batch_execute', 'arguments': args})
        self.assertEqual(self.proxy.peer.calls, [])

    def test_instance_resource_never_exposes_other_project(self):
        result = self.proxy.dispatch('resources/read', {'uri': 'mcpforunity://instances'})
        text = result['contents'][0]['text']
        self.assertEqual(len(json.loads(text)['instances']), 1)
        self.assertNotIn(str(self.other), text)
        self.assertIn('ownhash', text)

    def test_global_selector_hidden_from_model_tools(self):
        tools = self.proxy.dispatch('tools/list', {})['tools']
        self.assertEqual([t['name'] for t in tools], ['manage_scene'])

    def test_live_identity_failure_prevents_mutation(self):
        with patch.object(proxy, 'discover', side_effect=PermissionError('identity mismatch')):
            with self.assertRaises(PermissionError):
                self.proxy.dispatch('tools/call', {'name': 'execute_code', 'arguments': {'code': 'mutate'}})
        self.assertEqual(self.proxy.peer.calls, [])


if __name__ == '__main__':
    unittest.main()
