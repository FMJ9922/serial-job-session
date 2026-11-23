"""Pure AST-only native parser/terminal tests; no runtime, role, CIM or Unity."""
import ast
import hashlib
import json
from pathlib import Path
import re
import tempfile
import types
import unittest

ROOT=Path(__file__).parent

def functions(name,names,env):
    tree=ast.parse((ROOT/name).read_bytes())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes}==set(names)
    scope=dict(env);exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/name),'exec'),scope)
    return types.SimpleNamespace(**{name:scope[name] for name in names})

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def require(ok,message):
    if not ok:raise ValueError(message)

shared=functions('runner.py',['native_json_events'],dict(Path=Path,re=re,json=json))
closed=functions('closed_seal.py',['json_events','native_pairs'],dict(Path=Path,runner=shared))
ar=functions('audit_runner.py',['completed_turn_checkpoint'],dict(runner=shared,json=json,re=re,audit=types.SimpleNamespace(sha256=sha),derived_workspace=lambda _:False))
md=functions('measurement_dispute.py',['native_checkpoint'],dict(json=json,require=require,api=lambda:types.SimpleNamespace(sha256=sha),execution_helper=lambda:closed))

HEADER='2026-11-17T07:32:05.617396Z  WARN codex_analytics::client: events failed with status 401 Unauthorized: {'
BODY=['  "error": {','    "message": "token expired; brace } and \\\"quoted\\\"",','    "type": null,','    "code": "token_expired",','    "param": null','  },','  "status": 401,','  "detail": {"code":"token_expired"}','}']

def event(kind,**values):return json.dumps(dict(type=kind,**values))

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
        self.native=self.out/'private/provenance/turn-5.jsonl';self.native.parent.mkdir(parents=True)
        self.final=self.native.with_suffix('.final.txt');self.final.write_bytes(b'WITHHELD')
        self.state={'attempts':5,'session_id':'original-session'}
        self.start=[event('thread.started',thread_id='original-session'),event('turn.started')]
        self.end=[event('item.completed',item={'id':'final-item','type':'agent_message','text':'WITHHELD'}),event('turn.completed')]

    def put(self,middle,ending=None):
        self.native.write_text('\n'.join(self.start+middle+(self.end if ending is None else ending))+'\n',encoding='utf-8')
        return self.native.read_bytes()

    def parse(self,middle):
        self.put(middle);return list(closed.json_events(self.native,allow_diagnostics=True,with_line=True))

    def assert_block(self,middle):
        raw=self.put(middle)
        with self.assertRaises(RuntimeError):list(closed.json_events(self.native,allow_diagnostics=True))
        with self.assertRaises(RuntimeError):ar.completed_turn_checkpoint(self.out,'provenance',self.state)
        self.assertEqual(raw,self.native.read_bytes())

    def test_complete_nested_error_body_preserves_all_original_line_numbers_and_bytes(self):
        raw=self.put([HEADER,*BODY]);rows=list(closed.json_events(self.native,True,True))
        self.assertEqual([line for line,_ in rows],[1,2,13,14])
        cp=ar.completed_turn_checkpoint(self.out,'provenance',self.state)
        self.assertEqual(cp,dict(attempt=5,session_id='original-session',native_sha256=sha(self.native),final_sha256=sha(self.final),business_or_measurement_turn_reopened=False))
        md.native_checkpoint(self.native,self.final,cp)
        self.assertEqual(closed.native_pairs([self.native])['pending'],[])
        self.assertEqual(raw,self.native.read_bytes());self.assertEqual(self.final.read_bytes(),b'WITHHELD')

    def test_diagnostics_disabled_blocks_same_body(self):
        self.put([HEADER,*BODY])
        with self.assertRaises(RuntimeError):list(closed.json_events(self.native))

    def test_truncated_body_and_body_at_eof_block(self):
        self.assert_block([HEADER,*BODY[:-1]])
        self.put([HEADER,*BODY[:-1]],ending=[])
        with self.assertRaisesRegex(RuntimeError,'incomplete diagnostic'):list(closed.json_events(self.native,True))

    def test_unmatched_and_extra_closers_block(self):
        for rows in ([HEADER,*BODY[:-1],']'],[HEADER,*BODY,'}'],['}'],[HEADER,'}']):
            with self.subTest(rows=rows):self.assert_block(rows)

    def test_blank_timestamp_unknown_plaintext_and_new_event_cannot_bridge_body(self):
        for line in ('','2026-11-17T00:00:00Z ERROR source: next','arbitrary',event('turn.completed')):
            with self.subTest(line=line):self.assert_block([HEADER,'  "error": {',line,'  }','}'])

    def test_invalid_comma_duplicate_keys_nonjson_constants_and_trailing_text_block(self):
        for rows in ([HEADER,'  "error": {},','}'],[HEADER,'  "error": {},"error": {}','}'],
                     [HEADER,'  "error": {"code":NaN}','}'],[HEADER,*BODY[:-1],'} unknown']):
            with self.subTest(rows=rows):self.assert_block(rows)

    def test_typed_events_inside_complete_body_block_including_nested_unknown_events(self):
        for item in ({'type':'turn.completed'},{'event':'turn.failed'},{'item':{}},{'thread_id':'forged'},{'turn_id':'forged'}):
            with self.subTest(item=item):self.assert_block([HEADER,'  "error": '+json.dumps(item),'}'])
        self.assert_block([HEADER,'  "type": "turn.completed"','}'])

    def test_unknown_error_root_and_nonheader_body_block(self):
        self.assert_block([HEADER,'  "unknown": {}','}'])
        self.assert_block(['{',*BODY])
        self.assert_block(['2026-11-17T00:00:00Z ERROR source: plain','{',*BODY])

    def test_existing_plaintext_continuations_and_json_first_remain_supported(self):
        warning='2026-11-17T00:00:00Z ERROR source: failed source patch'
        rows=self.parse([warning,'  }','\tcontinuation']);self.assertEqual(len(rows),4)
        self.put([warning,'  '+event('turn.completed')],ending=[event('turn.started')])
        self.assertIsNone(ar.completed_turn_checkpoint(self.out,'provenance',self.state))

    def test_orphan_continuations_after_blank_or_event_block(self):
        warning='2026-11-17T00:00:00Z ERROR source: failed source patch'
        for middle in (['  orphan'],[warning,'','  orphan'],[warning,event('turn.started'),'  orphan'],[HEADER,*BODY,'  orphan']):
            with self.subTest(middle=middle):self.assert_block(middle)

    def test_no_diagnostic_body_may_synthesize_or_fix_terminal_final_or_identity(self):
        self.put([HEADER,*BODY],ending=[event('item.completed',item={'type':'agent_message','text':'WITHHELD'})])
        self.assertIsNone(ar.completed_turn_checkpoint(self.out,'provenance',self.state))
        self.put([HEADER,*BODY],ending=[*self.end,event('turn.failed')]);self.assertIsNone(ar.completed_turn_checkpoint(self.out,'provenance',self.state))
        self.put([HEADER,*BODY]);self.final.write_bytes(b'APPROVED');self.assertIsNone(ar.completed_turn_checkpoint(self.out,'provenance',self.state))
        self.final.write_bytes(b'WITHHELD');wrong={**self.state,'session_id':'wrong'};self.assertIsNone(ar.completed_turn_checkpoint(self.out,'provenance',wrong))

    def test_exported_native_anchor_still_mandatory_and_exact(self):
        self.put([HEADER,*BODY]);globals_=ar.completed_turn_checkpoint.__globals__;before=globals_['derived_workspace'];globals_['derived_workspace']=lambda _:True
        try:
            with self.assertRaisesRegex(RuntimeError,'controller-anchored'):ar.completed_turn_checkpoint(self.out,'provenance',self.state)
            record={'turn':5,'role':'provenance','auditor_id':'original-auditor','session_id':'original-session','files':[{'source_file':'turn-5.jsonl','source_sha256':sha(self.native),'exported_sha256':sha(self.native)}]}
            state={**self.state,'auditor_id':'original-auditor','exported_tool_trace_manifests':[record]}
            self.assertIsNotNone(ar.completed_turn_checkpoint(self.out,'provenance',state))
            record['files'][0]['exported_sha256']='0'*64
            with self.assertRaisesRegex(RuntimeError,'native bytes differ'):ar.completed_turn_checkpoint(self.out,'provenance',state)
        finally:globals_['derived_workspace']=before

    def test_dispute_checkpoint_rejects_bad_body_and_final_mismatch(self):
        self.put([HEADER,*BODY]);cp={'session_id':'original-session','native_sha256':sha(self.native),'final_sha256':sha(self.final)}
        md.native_checkpoint(self.native,self.final,cp)
        self.put([HEADER,'  "error": {"type":"turn.completed"}','}']);cp['native_sha256']=sha(self.native)
        with self.assertRaisesRegex(ValueError,'Malformed dispute'):md.native_checkpoint(self.native,self.final,cp)
        cp['native_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):md.native_checkpoint(self.native,self.final,cp)

if __name__=='__main__':unittest.main()
