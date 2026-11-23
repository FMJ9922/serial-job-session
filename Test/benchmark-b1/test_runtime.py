import json
from pathlib import Path
import types
import unittest
import tempfile
import hashlib

import runtime
import runner
import audit_runner


class RuntimeTests(unittest.TestCase):
    def test_absent_operation_policy_preserves_legacy_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(runtime.audit_operation_policy(directory))

    def test_audit_policy_appends_without_replacing_frozen_requirements(self):
        spec = types.SimpleNamespace(audit_prompt=lambda *args: 'Original 100 requirements')
        module = types.SimpleNamespace(audit=types.SimpleNamespace(spec=spec))
        runtime.patch_audit_policy(module, {'sha256': 'a' * 64, 'text': 'No physical desktop input.'})
        prompt = spec.audit_prompt('candidate', 'evidence')
        self.assertTrue(prompt.startswith('Original 100 requirements'))
        self.assertIn('No physical desktop input.', prompt)
        self.assertIn('a' * 64, prompt)

    def test_audit_policy_hash_and_runtime_scope_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / 'policy.txt'
            policy.write_text('No physical desktop input.', encoding='utf-8')
            digest = hashlib.sha256(policy.read_bytes()).hexdigest()
            config = root / 'operation-policy.json'
            config.write_text(json.dumps({'version': 1, 'path': 'policy.txt', 'sha256': digest}), encoding='utf-8')
            self.assertEqual(runtime.audit_operation_policy(root)['sha256'], digest)
            policy.write_text('changed', encoding='utf-8')
            with self.assertRaises((RuntimeError, ValueError)):
                runtime.audit_operation_policy(root)
            config.write_text(json.dumps({'version': 1, 'path': '../outside.txt', 'sha256': digest}), encoding='utf-8')
            with self.assertRaises(RuntimeError):
                runtime.audit_operation_policy(root)

    def test_development_changes_only_explicit_unity_environment_for_every_group(self):
        import spec
        for group in spec.GROUPS:
            base = runner.overrides(group, Path('frozen/unity_proxy.py'))
            module = types.SimpleNamespace(overrides=runner.overrides)
            runtime.patch_development(module)
            adjusted = module.overrides(group, Path('frozen/unity_proxy.py'))
            self.assertEqual(adjusted, {**base, **runtime.unity_environment()})
            self.assertEqual(adjusted['model_reasoning_effort'], 'high')

    def test_audit_adds_same_server_environment_without_changing_existing_arguments(self):
        args = ('codex', Path('home'), Path('project'), Path('proxy.py'), Path('tools.jsonl'), Path('final.txt'))
        base = audit_runner.audit_command(*args)
        module = types.SimpleNamespace(audit_command=audit_runner.audit_command)
        runtime.patch_audit(module)
        adjusted = module.audit_command(*args)
        expected = []
        for name, value in runtime.unity_environment().items():
            expected += ['-c', name + '=' + json.dumps(value)]
        self.assertEqual(adjusted, base[:-1] + expected + ['-'])


if __name__ == '__main__':
    unittest.main()
