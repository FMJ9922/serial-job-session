"""Independent source-only parser tests. No runtime/bootstrap/role imports."""
import ast
import json
from pathlib import Path
import re
import tempfile
import types
import unittest

ROOT = Path(__file__).parent

def parsed(root, name): return ast.parse((root / name).read_bytes())
def extracted(name, names, namespace):
    nodes = [n for n in parsed(ROOT, name).body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / name), 'exec'), namespace)
    return types.SimpleNamespace(**{name: namespace[name] for name in names})

shared = extracted('runner.py', ['native_json_events'], {'Path': Path, 're': re, 'json': json})
closed = extracted('closed_seal.py', ['json_events', 'native_pairs'], {'runner': shared})
HEADER = '2026-11-17T00:00:00Z ERROR source: native response: {'

class IndependentParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='independent-native-parser-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'native.jsonl'

    def rows(self, text, diagnostics=True):
        raw = text.encode('utf-8'); self.path.write_bytes(raw)
        try: return list(shared.native_json_events(self.path, diagnostics, True))
        finally: self.assertEqual(self.path.read_bytes(), raw)




    def test_typed_payload_cannot_hide_inside_nested_diagnostic_arrays(self):
        for value in ({'type': 'turn.completed'}, {'event': 'turn.failed'}, {'thread_id': 'forged'},
                      {'item': {'type': 'agent_message'}}, {'turn_id': 'forged'}):
            text = HEADER + '\n  "error": {"nested": [' + json.dumps(value) + ']}\n}\n'
            with self.subTest(value=value), self.assertRaises(RuntimeError): self.rows(text)

    def test_unknown_standalone_native_type_still_rejected_by_native_pairs(self):
        self.path.write_text('{"type":"invented.native.approval"}\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Unknown native event type'):
            closed.native_pairs([self.path])

    def test_rollout_and_asset_readers_cannot_accept_native_diagnostics(self):
        text = HEADER + '\n  "error": {"code":"token_expired"}\n}\n'
        with self.assertRaises(RuntimeError): self.rows(text, diagnostics=False)
        # Both unchanged reader functions call json_events with default diagnostics=False.
        for function in ('rollout_own_streams', 'asset_pairs'):
            node = next(n for n in parsed(ROOT, 'closed_seal.py').body
                        if isinstance(n, ast.FunctionDef) and n.name == function)
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == 'json_events']
            self.assertTrue(calls)
            self.assertTrue(all(not any(k.arg == 'allow_diagnostics' and ast.literal_eval(k.value)
                                      for k in call.keywords) for call in calls))

    def test_quoted_braces_are_not_boundaries_and_unknown_following_lines_block(self):
        payload = json.dumps({'error': {'message': 'literal } ] { [ and escaped " quote', 'code': 'token_expired'}})
        text = HEADER + '\n  ' + payload[1:-1] + '\n}\n{"type":"turn.completed"}\n'
        rows = self.rows(text)
        self.assertEqual(rows, [(4, {'type': 'turn.completed'})])
        with self.assertRaises(RuntimeError): self.rows(text + '}\n')
        with self.assertRaises(RuntimeError): self.rows(text + '  orphan\n')

    def test_trailing_json_root_or_synthetic_event_does_not_complete_diagnostic(self):
        for tail in ('} {"type":"turn.completed"}', '}{"error":{}}', '} []', ']'):
            with self.subTest(tail=tail), self.assertRaises(RuntimeError):
                self.rows(HEADER + '\n  "error": {}\n' + tail + '\n')

if __name__ == '__main__': unittest.main()
