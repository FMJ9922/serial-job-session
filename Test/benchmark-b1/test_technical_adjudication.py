"""Synthetic protocol tests only. No role, CIM, Unity or formal case execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import os

import audit
import audit_runner as ar
import closed_seal as cs
import technical_adjudication as ta


class TechnicalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ev = self.write('synthetic-evidence.txt', b'Synthetic evidence fixture; no measurement occurred.')

    def write(self, name, value):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else ta.canonical(value))
        return {'path': name, 'sha256': audit.sha256(path), 'bytes': path.stat().st_size}

    def graph(self):
        ids = [f'{n:03}' for n in range(1,101)]
        self.report = {'checks': [{'id': i, 'score': None if i == '057' else 1} for i in ids]}
        self.authority = {k: 'synthetic-' + k for k in ('opaque_id','candidate_hash','report_sha256','journal_head')}
        self.authority.update(primary_checkpoint={'attempt':4}, requested_check_ids=['057'], input_capsule=self.ev)
        defects = [{'defect_id': d, 'historical_fact_ref': self.ev,
                    'original_call_source_result_refs': [self.ev], 'immutable_fact': 'Preserved synthetic historical failure'}
                   for d in sorted(ta.DEFECTS)]
        graph = [{'check_id': i, 'components': ['measurement', 'prior-settings-integrity'],
                  'defect_ids': sorted(ta.DEFECTS), 'original_check_sha256': ta.digest(c),
                  'edges': [{'edge_id': d, 'defect_id': d, 'necessary': True, 'from': 'historical-state',
                             'to': 'current-claim', 'source_refs':[self.ev]} for d in sorted(ta.DEFECTS)]}
                 for i,c in zip(ids,self.report['checks'])]
        self.capsule = {'original_reviews':[self.ev,self.ev], 'defects':defects,
                        'claim_dependencies':graph, 'operation_ids':['actual-synthetic-operation']}
        self.verdict = {'schema':'b1-technical-measurement-adjudication-v1','outcome':'supported_scope_only',
            **{k:self.authority[k] for k in ('opaque_id','candidate_hash','report_sha256','journal_head','primary_checkpoint')},
            'input_capsule_manifest_ref':self.ev, 'both_original_reviews_refs':[self.ev,self.ev],
            'original_canonical_final_approved':False,
            'defect_findings':[{**d,'status':'supported_independent_remeasurement','basis':'Independent synthetic evidence',
                                'preserved_limits':'Old failure is unchanged','new_measurement_refs':[self.ev],
                                'affected_claim_ids':ids} for d in defects],
            'check_eligibility':[]}
        for c,row in zip(self.report['checks'],graph):
            self.verdict['check_eligibility'].append({'check_id':c['id'], 'original_check_sha256':ta.digest(c),
                'scope':'eligible_for_independent_acceptance' if c['id']=='057' else 'not_in_scope',
                'all_relevant_defect_ids':sorted(ta.DEFECTS), 'basis':'Actual synthetic test only',
                'limitations':'No business decision or old cure', 'supported_components':row['components'],
                'dependency_paths':[{'edge_id':e['edge_id'],'status':'supported','basis':'Synthetic independent bounded edge',
                                     'evidence_refs':[self.ev]} for e in row['edges']],
                'actual_measurement_operation_ids':['actual-synthetic-operation'],
                'original_evidence_refs':[self.ev], 'independent_current_evidence_refs':[self.ev], 'integrity_refs':[self.ev]})
        return next(r for r in self.verdict['check_eligibility'] if r['check_id']=='057')

    def validate(self):
        return ta.validate_verdict(self.root,self.verdict,self.capsule,self.authority,self.report)

    def test_supported_scope_preserves_entire_blocked_complement_and_never_scores(self):
        self.graph(); self.assertEqual(self.validate(), {'057'})
        value={'eligible_check_ids':['057'],'blocked_check_ids':[f'{i:03}' for i in range(1,101) if i!=57],
               'review_approved':False,'canonical_review_approved':False}
        self.assertIs(ta.require_scope(value,{'057'},action='measurement_dispute'),value)
        for action in ('full_report','consistency','horizontal'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError,'blocked'):
                ta.require_scope(value,{f'{i:03}' for i in range(1,101)},action=action)

    def test_unresolved_necessary_external_settings_contamination_blocks(self):
        row=self.graph(); row['dependency_paths'][0]['status']='missing_evidence'
        with self.assertRaisesRegex(ValueError,'Unresolved necessary'): self.validate()

    def test_missing_claim_component_blocks(self):
        row=self.graph(); row['supported_components']=['measurement']
        with self.assertRaisesRegex(ValueError,'Unresolved necessary'): self.validate()

    def test_unresolved_historical_defect_cannot_be_retroactively_cured(self):
        self.graph(); self.verdict['defect_findings'][0]['status']='still_dependent_blocked'
        with self.assertRaisesRegex(ValueError,'Unresolved necessary'): self.validate()

    def test_rejected_and_missing_are_terminal_not_supported(self):
        self.graph()
        for outcome in ('rejected','missing_evidence'):
            self.verdict['outcome']=outcome
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError,'Unresolved necessary'): self.validate()

    def test_complement_scope_leak_and_incomplete100_rejected(self):
        self.graph(); self.verdict['check_eligibility'][0]['scope']='eligible_for_independent_acceptance'
        with self.assertRaisesRegex(ValueError,'complement'): self.validate()
        self.graph(); self.verdict['check_eligibility'].pop()
        with self.assertRaisesRegex(ValueError,'complement'): self.validate()

    def test_original_digest_unknown_operation_and_missing_edge_rejected(self):
        for field,value in [('original_check_sha256','fake'),('actual_measurement_operation_ids',['unknown']),('dependency_paths',[])]:
            row=self.graph(); row[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError): self.validate()

    def test_global_approval_score_and_original_canonical_true_rejected(self):
        for field in ('approved','score','pass','historical_defect_cured'):
            self.graph(); self.verdict[field]=True
            with self.subTest(field=field),self.assertRaises(ValueError): self.validate()
        self.graph(); self.verdict['original_canonical_final_approved']=True
        with self.assertRaises(ValueError): self.validate()

    def case(self):
        return {'opaque_id':'opaque','roles':{'primary':{'auditor_id':'p','attempts':4},
            'provenance':{'auditor_id':'v','attempts':5},'review':{'auditor_id':'b','attempts':0}}}

    def authority(self):
        return {'schema':'b1-technical-adjudication-authority-v1','opaque_id':'opaque','one_initial_turn':True,
                'automatic_retry':False,'descendant_cap':0,'question':'Exact synthetic technical question',
                'merits_approved':False,'input_capsule':self.ev}

    def test_one_shot_adjunct_does_not_reset_or_replace_original_roles(self):
        case=self.case(); original=copy.deepcopy(case['roles'])
        state=ta.new_participant(case,self.authority(),auditor_id='new',authority_sha256='a'*64)
        case['adjunct_participants']={ta.ROLE:state}
        self.assertEqual(case['roles'],original); self.assertEqual(ta.participants(case)[ta.ROLE]['attempts'],1)
        state['stage_status']='failed'
        with self.assertRaisesRegex(ValueError,'no retry'):
            ta.new_participant(case,self.authority(),auditor_id='another',authority_sha256='b'*64)

    def test_original_cap_reset_self_identity_unknown_adjunct_and_descendant_cap_rejected(self):
        case=self.case(); case['roles']['primary']['attempts']=0
        with self.assertRaises(ValueError): ta.new_participant(case,self.authority(),auditor_id='new',authority_sha256='a'*64)
        with self.assertRaises(ValueError): ta.new_participant(self.case(),self.authority(),auditor_id='p',authority_sha256='a'*64)
        case=self.case(); case['adjunct_participants']={'hidden':{}}
        with self.assertRaises(ValueError): ta.participants(case)
        state=ta.new_participant(self.case(),self.authority(),auditor_id='new',authority_sha256='a'*64)
        state['descendant_cap']=1; case=self.case(); case['adjunct_participants']={ta.ROLE:state}
        with self.assertRaises(ValueError): ar.actual_participants(case)

    def test_no_authority_cannot_reach_any_process_or_cim(self):
        case=self.case(); state=ta.new_participant(case,self.authority(),auditor_id='new',authority_sha256='a'*64)
        case['adjunct_participants']={ta.ROLE:state}
        with patch.object(ar.subprocess,'Popen') as proc,patch.object(cs,'collect_census') as census:
            with self.assertRaises(ValueError): ar.invoke({},case,ta.ROLE,'test',self.root,lambda:None)
            proc.assert_not_called(); census.assert_not_called()

    def test_read_only_command_has_no_resume_unity_or_descendant_permission(self):
        cmd=ta.command('cli','home','anonymous-input','final')
        self.assertIn('read-only',cmd); self.assertNotIn('resume',cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',cmd)
        self.assertFalse(any('unity' in value.lower() for value in cmd))
        self.assertIn('agents.max_depth=0',cmd)

    def test_nine_actual_validation_dependencies_are_hashed_and_colocated(self):
        pins=ta.source_pins()
        self.assertEqual(len(pins),9)
        self.assertIn('technical_adjudication.py',pins)
        self.assertEqual(pins['operation_provenance.py'],audit.sha256(audit.provenance_module().__file__))

    def test_refs_strict_length_sha_json_and_no_escape(self):
        for bad in (dict(self.ev,bytes=True),dict(self.ev,sha256='0'*64),dict(self.ev,path='../x'),dict(self.ev,path='C:/x')):
            with self.subTest(bad=bad),self.assertRaises(ValueError): ta.reference(self.root,bad)
        for value in (b'{"a":1,"a":2}',b'{"a":NaN}'):
            ref=self.write('bad.json',value)
            with self.assertRaises(ValueError): ta.read(self.root/ref['path'])

    def actor(self):
        sid='new-session'; prefix='private/technical_adjudication/'
        final=self.write(prefix+'turn-1.final.txt',b'{"synthetic":"opinion"}')
        native=self.write(prefix+'turn-1.jsonl', b'\n'.join(ta.canonical(e) for e in [
            {'type':'thread.started','thread_id':sid},{'type':'turn.started'},
            {'type':'item.completed','item':{'id':'answer','type':'agent_message','text':'{"synthetic":"opinion"}'}},{'type':'turn.completed'}])+b'\n')
        events=[{'type':'session_meta','payload':{'id':sid}},
            {'type':'turn_context','payload':{'model':ta.MODEL,'effort':ta.EFFORT}},
            {'type':'event_msg','payload':{'type':'task_complete'}}]
        rollout=self.write(prefix+'home/sessions/own.jsonl',b'\n'.join(ta.canonical(e) for e in events)+b'\n')
        model=self.write(prefix+'models.json',{sid:[{'model':ta.MODEL,'effort':ta.EFFORT}]})
        row=self.process(900,1,'codex.exe','cli --cd anonymous','/Date(10)/')
        census=self.write(prefix+'launch-census.json',{'complete':True,'processes':[row]})
        exports=[]
        for number,ref in enumerate((native,final,rollout)):
            raw=(self.root/ref['path']).read_bytes()
            exports.append({'source':ref,'snapshot':self.write(prefix+f'snapshots/{number}',raw),
                            'export':self.write(f'blind/evidence/tool-traces/technical_adjudication/{number}',raw)})
        actor={'attempt':1,'auditor_id':'new','session_id':sid,'model':ta.MODEL,'effort':ta.EFFORT,'descendant_cap':0,
            'checkpoint':{'attempt':1,'session_id':sid,'native_sha256':native['sha256'],'final_sha256':final['sha256']},
            'native':native,'final':final,'rollouts':[rollout],'model_evidence':model,'trace_files':exports,
            'execution':{'cli_pid':900,'started_utc':'before','ended_utc':'after','process_tree_stopped':True,
                         'generation_row':row,'generation_census_ref':census}}
        return actor,final,events

    @staticmethod
    def process(pid,parent,name,command,birth):
        return {'ProcessId':pid,'ParentProcessId':parent,'Name':name,'CommandLine':command,'ExecutablePath':None,'CreationDate':birth}

    def test_actual_one_owner_model_terminal_and_exports_positive(self):
        actor,final,_=self.actor()
        self.assertEqual(ta.actual_actor(self.root,actor,final,{'root'}),([] ,{'new-session'}))

    def test_wrong_model_identity_terminal_and_export_cannot_be_asserted(self):
        for field,value in [('model','wrong'),('auditor_id','root'),('session_id','another'),('descendant_cap',1),('attempt',2)]:
            actor,final,_=self.actor(); actor[field]=value
            with self.subTest(field=field),self.assertRaises((ValueError,RuntimeError)):
                ta.actual_actor(self.root,actor,final,{'root'})
        actor,final,_=self.actor(); actor['trace_files'].pop()
        with self.assertRaisesRegex(ValueError,'coverage'): ta.actual_actor(self.root,actor,final,{'root'})

    def test_real_own_descendant_stream_blocks_zero_desc_even_if_caller_claims_zero(self):
        actor,final,events=self.actor()
        child=self.write('private/technical_adjudication/home/sessions/child.jsonl',
            ta.canonical({'type':'session_meta','payload':{'id':'child','parent_thread_id':'new-session'}})+b'\n'+
            ta.canonical({'type':'event_msg','payload':{'type':'task_complete'}})+b'\n')
        actor['rollouts'].append(child)
        with self.assertRaisesRegex(ValueError,'zero descendants'): ta.actual_actor(self.root,actor,final,{'root'})

    def test_archived_census_replay_never_excludes_an_actual_writer(self):
        helper=self.process(100,1,'python.exe','python closed_seal-technical-proof','/Date(1)/')
        collector=self.process(101,100,'pwsh.exe',cs.COLLECTOR,'/Date(2)/')
        census={'complete':True,'collector_marker':cs.COLLECTOR,'collector_pid':101,'processes':[helper,collector]}
        result=cs.replay_census(census,self.root/'runtime',self.root/'private',[],[],[900],helper)
        self.assertEqual(result['excluded_pids'],[100,101]); self.assertEqual(result['mode'],'archived_raw_proof_replay')
        with self.assertRaises(RuntimeError): cs.replay_census(census,self.root,self.root,[],[],[100],helper)
        census['processes'].append(self.process(900,1,'codex.exe',None,'/Date(3)/'))
        with self.assertRaises(RuntimeError): cs.replay_census(census,self.root,self.root,[],[],[900],helper)

    def test_unknown_unity_or_unfinished_actual_tool_stays_blocked(self):
        helper=self.process(100,1,'python.exe','closed_seal','/Date(1)/')
        census={'complete':True,'collector_marker':cs.COLLECTOR,'collector_pid':101,'processes':[
            helper,self.process(101,100,'pwsh.exe',cs.COLLECTOR,'/Date(2)/'),self.process(3,1,'Unity.exe',None,'/Date(3)/')]}
        with self.assertRaisesRegex(RuntimeError,'Unknown/null Unity'):
            cs.replay_census(census,self.root,self.root,[],[],[],helper)

    def test_original_require_review_true_stays_false_and_default_consumer_cannot_fallback(self):
        from test_measurement_dispute import DisputeTests
        fixture=DisputeTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        path=fixture.evidence/'provenance-review.json'
        doc=audit.read_json(path); doc['final']['approved']=False
        doc['final']['basis']='Synthetic preserved refusal: independent measurement coverage is unsupported.'
        audit.write_json(path,doc)
        old=fixture.out/'private/original-refusal.json'; old.write_bytes(path.read_bytes())
        refs=[{'path':p.relative_to(fixture.out).as_posix(),'sha256':audit.sha256(p),'bytes':p.stat().st_size} for p in (old,path)]
        reviewer,sha=ta.canonical_refusal(fixture.out,fixture.manifest['operational_provenance'],fixture.chain,refs)
        self.assertEqual((reviewer,sha),('provenance',audit.sha256(path)))
        with self.assertRaisesRegex(ValueError,'Final independent'):
            audit.provenance_module().validate_workspace(*fixture.p_args,require_review=True)
        with patch.object(audit,'technical_adjudication_module',side_effect=AssertionError('implicit bypass')):
            with self.assertRaisesRegex(ValueError,'Final independent'): audit.validate_report(fixture.path)

    def test_missing_actual_acceptance_anchor_never_uses_structural_validity_as_approval(self):
        (self.root/'blind').mkdir()
        report=self.root/'blind/audit.json'; report.write_text('{}')
        self.write('judge_manifest.json',{})
        with self.assertRaisesRegex(ValueError,'No explicit'):
            ta.resolve(report,{'057'},action='measurement_dispute')

    def reviewer(self):
        sid='independent-existing-session'
        models=self.write('peer-models.json',{sid:[{'model':'gpt-6.1-sol','effort':'high'}]})
        core={'schema':'b1-technical-adjudication-actual-trace-review-v1','reviewer_id':'independent-peer',
              'reviewer_session_id':sid,'reviewer_model_evidence':models,'technical_trace_verified':True,
              'input_capsule':self.ev,'verdict_ref':self.ev,'reviewed_calls':[],'basis':'Synthetic complete evidence review result'}
        result={**core,'schema':'b1-technical-adjudication-actual-review-result-v1'}
        events=[{'type':'session_meta','payload':{'id':sid}},
            {'type':'turn_context','payload':{'model':'gpt-6.1-sol','effort':'high'}},
            {'type':'response_item','payload':{'type':'function_call','call_id':'review','name':'read','arguments':'original inputs'}},
            {'type':'response_item','payload':{'type':'function_call_output','call_id':'review','output':ta.canonical(result).decode()}}]
        def event_ref(n): return {'owner_session_id':sid,'line':n,'event_sha256':cs.canonical_item_sha256(events[n-1])}
        selected=[event_ref(3),event_ref(4)]
        certificate={**core,'actual_review_event_refs':selected}; certref=self.write('peer-certificate.json',certificate)
        binding={'schema':'b1-technical-adjudication-certificate-binding-v1','reviewer_id':'independent-peer',
            'reviewer_session_id':sid,'certificate':certificate,'certificate_ref':certref,
            'certificate_bytes_sha256':certref['sha256'],'certificate_canonical_sha256':ta.digest(certificate)}
        events.extend([{'type':'response_item','payload':{'type':'function_call','call_id':'binding','name':'read','arguments':'full final certificate'}},
            {'type':'response_item','payload':{'type':'function_call_output','call_id':'binding','output':ta.canonical(binding).decode()}}])
        source=self.write('peer-own.jsonl',b'\n'.join(ta.canonical(e) for e in events)+b'\n')
        peer={'auditor_id':'independent-peer','session_id':sid,'source_rollouts':[source],'model_evidence':models,
              'review_event_refs':selected,'certificate_binding_event_refs':[event_ref(5),event_ref(6)]}
        return peer,certref,events

    def test_existing_reviewer_actual_own_call_results_bind_certificate_without_second_role(self):
        peer,certificate,_=self.reviewer()
        self.assertEqual(ta.actual_review_source(self.root,peer,certificate,{'root','author'}),peer['session_id'])
        peer['certificate_binding_event_refs']=peer['review_event_refs']
        with self.assertRaises(ValueError): ta.actual_review_source(self.root,peer,certificate,{'root','author'})

    def test_hash_echoes_with_real_model_context_are_still_not_an_actual_review(self):
        peer,certificate,events=self.reviewer()
        for n in (4,6):
            events[n-1]['payload']['output']=certificate['sha256']
        source=self.write('peer-own.jsonl',b'\n'.join(ta.canonical(e) for e in events)+b'\n')
        peer['source_rollouts']=[source]
        for field in ('review_event_refs','certificate_binding_event_refs'):
            for record in peer[field]: record['event_sha256']=cs.canonical_item_sha256(events[record['line']-1])
        with self.assertRaisesRegex(ValueError,'structured review result'):
            ta.actual_review_source(self.root,peer,certificate,{'root','author'})

    def test_structured_review_result_cannot_change_input_scope(self):
        peer,certificate,events=self.reviewer()
        result=json.loads(events[3]['payload']['output']);result['input_capsule']['sha256']='0'*64
        events[3]['payload']['output']=ta.canonical(result).decode()
        peer['source_rollouts']=[self.write('peer-own.jsonl',b'\n'.join(ta.canonical(e) for e in events)+b'\n')]
        peer['review_event_refs'][1]['event_sha256']=cs.canonical_item_sha256(events[3])
        with self.assertRaisesRegex(ValueError,'structured review result'):
            ta.actual_review_source(self.root,peer,certificate,{'root','author'})

    def test_actual_peer_sid_model_or_certificate_raw_changes_block(self):
        peer,certificate,_=self.reviewer();peer['session_id']='another'
        with self.assertRaises(ValueError): ta.actual_review_source(self.root,peer,certificate,{'root','author'})
        peer,certificate,_=self.reviewer()
        peer['model_evidence']=self.write('peer-models.json',{peer['session_id']:[{'model':'wrong','effort':'low'}]})
        with self.assertRaisesRegex(ValueError,'model evidence'): ta.actual_review_source(self.root,peer,certificate,{'root','author'})
        peer,certificate,_=self.reviewer();(self.root/certificate['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,'bytes changed'): ta.actual_review_source(self.root,peer,certificate,{'root','author'})
        peer,certificate,_=self.reviewer()
        bad=self.write('peer-certificate.json',b'{"reviewer_id":"first","reviewer_id":"second"}')
        with self.assertRaisesRegex(ValueError,'Duplicate'): ta.actual_review_source(self.root,peer,bad,{'root','author'})

    def annex(self, *, collision=False, wrong_provenance=False):
        base={'auxiliary_generations':[self.process(700+i,1,'python.exe',None,'/Date(10)/') for i in range(24)],
              'direct_actor_pids':[9000]}
        base_ref=self.write('base-writers.json',base)
        row=self.process(700 if collision else 800,1,'codex.exe',None,'/Date(30)/')
        launch=self.write('launch.json',{'complete':True,'processes':[row]})
        execution={'attempt':5,'cli_pid':83208,'process_tree_stopped':True}
        original=self.write('original-execution.json',{'execution_attempts':[execution]})
        observed={'ProcessId':1234 if wrong_provenance else 83208,'Name':'codex.exe','CreationDate':'2026-11-17T15:29:33.095795+08:00'}
        raw=self.write('partial-observation.json',{'process':observed})
        ann={'schema':'b1-technical-adjudication-participants-annex-v1','base_writer_inventory':base_ref,
             'input_capsule_sha256':'capsule','original_primary_attempts':4,'original_provenance_attempts':5,
             'appended_generations':[{'role':ta.ROLE,'session_id':'ta-session','first_phase':'after','row':row,'raw_source_refs':[launch]}],
             'direct_actor_additions':[{'role':'provenance','session_id':'prov-session','first_phase':'before','pid':observed['ProcessId'],
                'observed_row':observed,'raw_source_refs':[raw]}],'actual_review_in_scope_writers':[]}
        receipt={'participants_annex':self.write('annex.json',ann),'input_capsule_sha256':'capsule',
            'adjudicator':{'session_id':'ta-session','execution':{'generation_row':row,'generation_census_ref':launch}},
            'actual_trace_reviewer':{'in_scope_writer_generations':[]}}
        capsule={'writer_inventory':base_ref,'original_participants':{'provenance':{
            'session_id':'prov-session','execution_attempts':[execution],'execution_source_ref':original}}}
        return receipt,capsule

    def test_annex_keeps_base24_and_exact_original_provenance5_actual_pid(self):
        receipt,capsule=self.annex()
        base,rows,direct=ta.generation_annex(self.root,receipt,capsule)
        self.assertEqual(len(base['auxiliary_generations']),24);self.assertEqual(direct[0]['pid'],83208)
        self.assertNotIn('ParentProcessId',direct[0]['observed_row'])
        receipt,capsule=self.annex(wrong_provenance=True)
        with self.assertRaisesRegex(ValueError,'provenance5 new participant omitted'):
            ta.generation_annex(self.root,receipt,capsule)

    def test_new_annex_cannot_overwrite_old_birth_or_drop_old_interval_orphan(self):
        receipt,capsule=self.annex(collision=True)
        with self.assertRaisesRegex(ValueError,'original24 generation'):ta.generation_annex(self.root,receipt,capsule)
        import process_generation as gen
        with self.assertRaisesRegex(RuntimeError,'Live or ambiguous descendant'):
            gen.verify_auxiliary_absence({'processes':[self.process(999,700,'python.exe',None,'/Date(20)/')]},
                {700:self.process(700,1,'python.exe',None,'/Date(10)/')},set())

    def test_nested_multiline_original_partial_source_is_read_without_filling_a_full_row(self):
        partial={'ProcessId':83208,'Name':'codex.exe','CreationDate':'2026-11-17T15:29:33.095795+08:00'}
        original=self.write('original-call-result.jsonl',ta.canonical({'output':'{"id":null}\n'+json.dumps(partial)+'\nother unrelated diagnostic'})+b'\n')
        self.assertTrue(ta.recorded_row(self.root,[original],partial))
        self.assertFalse(ta.recorded_row(self.root,[original],dict(partial,ParentProcessId=0)))

    def test_immutable_primary_snapshot_allows_only_flagged067_review_append(self):
        import measurement_dispute as md
        original={'candidate_hash':'original','auditor':{'id':'primary'},'checks':[
            {'id':f'{i:03}','score':None if i==57 else 1,'status':'review_required' if i==57 else 'verified',
             'expected':'frozen','actual':'original','operation':'original','candidate_hash':'original','evidence':[self.ev],
             'review':None,'adjudication':None,'review_required':i==67,'review_reason':'original flag' if i==67 else ''}
            for i in range(1,101)]}
        current=copy.deepcopy(original)
        current['checks'][66]['review']={'auditor_id':'business-reviewer','score':1,'operation':'review',
            'expected':'frozen','actual':'independent evidence review','evidence':[self.ev]}
        self.assertNotEqual(ta.digest(current),ta.digest(original));md.same_primary(original,current)
        current['checks'][66]['adjudication']={**current['checks'][66]['review'],'auditor_id':'another-reviewer'}
        md.same_primary(original,current)
        for field in ('score','status','expected','actual','operation','candidate_hash','evidence'):
            changed=copy.deepcopy(current);changed['checks'][66][field]='altered'
            with self.subTest(field=field),self.assertRaises(ValueError):md.same_primary(original,changed)
        changed=copy.deepcopy(current);changed['checks'][0]['review']={'invented':True}
        with self.assertRaises(ValueError):md.same_primary(original,changed)

    def reuse_fixture(self, *, old_rows=None, expected_birth='/Date(30)/', old_complete=True, old_marker=cs.COLLECTOR):
        base={'direct_actor_pids':[60256,9000], 'known_instance_ids':[],
              'auxiliary_generations':[self.process(700+i,1,'python.exe',None,'/Date(10)/') for i in range(24)]}
        base_ref=self.write('reuse/base.json',base)
        helper=self.process(100,1,'python.exe','python closed_seal-technical-proof','/Date(1)/')
        collector=self.process(101,100,'pwsh.exe',cs.COLLECTOR,'/Date(2)/')
        old={'complete':old_complete,'collector_marker':old_marker,'collector_pid':101,
             'processes':old_rows if old_rows is not None else [helper,collector]}
        absence=[self.write('reuse/old-0.json',old),self.write('reuse/old-1.json',old)]
        old_observation=self.write('reuse/old-observation.json',{'utc':'1970-01-01T00:00:00.010000+00:00',
            'census_before':old,'census_after':old})
        current=self.process(60256,53260,'conhost.exe','conhost unrelated','/Date(30)/')
        expected=dict(current,CreationDate=expected_birth)
        observation=self.write('reuse/current-observation.json',{'rows':[expected],'direct_children':[]})
        diagnosis=self.write('reuse/diagnosis.json',{
            'status':'ACTUAL_FRESH_BLOCK_DIAGNOSED_NOT_CLOSURE_APPROVAL','reviewer_id':'/root/review_dispute_tooling',
            'current_actual_observation':observation,'current_generation':expected,
            'absence_before_reuse_evidence':{'observation':old_observation,
                'observation_utc':'1970-01-01T00:00:00.010000+00:00','complete_censuses':absence}})
        witness={'diagnosis':diagnosis,'current_observation':observation,
                 'absence_observation':old_observation,'absence_censuses':absence}
        capsule={'opaque_id':ta.CASE,'candidate_hash':ta.CANDIDATE,'writer_inventory':base_ref}
        annex={'historical_pid60256_reuse_witness':witness,'appended_generations':[],'direct_actor_additions':[]}
        census={**old,'complete':True,'collector_marker':cs.COLLECTOR,'processes':[helper,collector,current]}
        return base,capsule,annex,census

    def reuse_guard(self, fixture, census=None):
        base,capsule,annex,current=fixture
        witness=annex['historical_pid60256_reuse_witness']
        with patch.object(ta,'PID60256_DIAGNOSIS',witness['diagnosis']['sha256']), \
             patch.object(ta,'PID60256_OBSERVATION',witness['current_observation']['sha256']):
            return ta.effective_direct_actor_pids(self.root,capsule,annex,census or current,base['direct_actor_pids'])

    def test_direct_reuse_exact_later_generation_or_absence_keeps_original_bytes(self):
        fixture=self.reuse_fixture(expected_birth='1970-01-01T00:00:00.030500+00:00')
        base,capsule,annex,census=fixture; original=copy.deepcopy(fixture)
        old_bytes=(self.root/capsule['writer_inventory']['path']).read_bytes()
        with self.assertRaisesRegex(RuntimeError,'60256'):
            cs.replay_census(census,self.root/'runtime',self.root/'private',[],[],base['direct_actor_pids'],census['processes'][0])
        effective=self.reuse_guard(fixture);self.assertEqual(effective,[9000])
        cs.replay_census(census,self.root/'runtime',self.root/'private',[],[],effective,census['processes'][0])
        absent={**census,'processes':census['processes'][:2]}
        self.assertEqual(self.reuse_guard(fixture,absent),base['direct_actor_pids'])
        self.assertEqual(fixture,original);self.assertEqual((self.root/capsule['writer_inventory']['path']).read_bytes(),old_bytes)
        self.assertEqual(len(base['auxiliary_generations']),24)

    def test_direct_reuse_without_exact_trusted_witness_retains_original_block(self):
        fixture=self.reuse_fixture();base,capsule,annex,census=fixture
        effective=ta.effective_direct_actor_pids(self.root,capsule,{},census,base['direct_actor_pids'])
        self.assertEqual(effective,base['direct_actor_pids'])
        with self.assertRaises(RuntimeError):cs.replay_census(census,self.root,self.root,[],[],effective,census['processes'][0])
        with self.assertRaisesRegex(ValueError,'independently diagnosed'):
            ta.effective_direct_actor_pids(self.root,capsule,annex,census,base['direct_actor_pids'])
        with self.assertRaisesRegex(ValueError,'omitted'):
            ta.effective_direct_actor_pids(self.root,capsule,annex,census,[9000])
        capsule['opaque_id']='another-case'
        with self.assertRaisesRegex(ValueError,'independently diagnosed'):self.reuse_guard(fixture)

    def test_direct_reuse_changed_refs_and_incomplete_duplicate_or_present_old_rows_block(self):
        for field in ('sha256','bytes'):
            fixture=self.reuse_fixture();r=fixture[2]['historical_pid60256_reuse_witness']['absence_censuses'][0]
            r[field]='0'*64 if field=='sha256' else r[field]+1
            with self.subTest(field=field),self.assertRaises(ValueError):self.reuse_guard(fixture)
        for rows in ([self.process(10,1,'python',None,'/Date(1)/')]*2,
                     [self.process(60256,1,'Unity.exe',None,None)],
                     [self.process(10,60256,'orphan',None,None)]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):self.reuse_guard(self.reuse_fixture(old_rows=rows))
        for options in ({'old_complete':False},{'old_marker':'unattributed'}):
            with self.subTest(options=options),self.assertRaises(ValueError):self.reuse_guard(self.reuse_fixture(**options))
        fixture=self.reuse_fixture();r=fixture[2]['historical_pid60256_reuse_witness']['absence_censuses'][0]
        bad=json.loads((self.root/r['path']).read_bytes());bad['complete']=False
        changed=self.write(r['path'],bad);fixture[2]['historical_pid60256_reuse_witness']['absence_censuses'][0]=changed
        with self.assertRaises(ValueError):self.reuse_guard(fixture)

    def test_direct_reuse_same_earlier_unknown_new_birth_or_changed_identity_block(self):
        for birth in ('/Date(10)/','/Date(9)/',None,'unknown','1970-01-01T00:00:00.030000'):
            with self.subTest(witness_birth=birth),self.assertRaises(ValueError):
                self.reuse_guard(self.reuse_fixture(expected_birth=birth))
        for field,value in [('CreationDate','/Date(10)/'),('CreationDate','/Date(31)/'),('CreationDate',None),
                            ('Name','Unity.exe'),('ParentProcessId',123),('CommandLine',None),('ExecutablePath','other')]:
            fixture=self.reuse_fixture();fixture[3]['processes'][-1][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.reuse_guard(fixture)

    def test_direct_reuse_any_current_child_including_absent_parent_and_excluded_collector_blocks(self):
        for birth in ('/Date(1)/','/Date(30)/','/Date(31)/',None):
            for present in (True,False):
                fixture=self.reuse_fixture();c=fixture[3]
                if not present:c['processes'].pop()
                c['processes'].append(self.process(555,60256,'child',None,birth))
                with self.subTest(birth=birth,present=present),self.assertRaisesRegex(ValueError,'child'):
                    self.reuse_guard(fixture)
        fixture=self.reuse_fixture();fixture[3]['processes'][1]['ParentProcessId']=60256
        with self.assertRaisesRegex(ValueError,'child'):self.reuse_guard(fixture)

    def test_direct_reuse_never_bypasses_scoped_session_unity_or_other_original_writer(self):
        for row in (self.process(200,1,'python.exe',str(self.root/'runtime'),'/Date(40)/'),
                    self.process(200,1,'codex.exe','resume original-session','/Date(40)/'),
                    self.process(200,1,'python.exe','--instance synthetic@aabbccdd','/Date(40)/'),
                    self.process(200,1,'Unity.exe',None,'/Date(40)/'),
                    self.process(9000,1,'python.exe',None,'/Date(40)/')):
            fixture=self.reuse_fixture();fixture[3]['processes'].append(row);effective=self.reuse_guard(fixture)
            with self.subTest(row=row),self.assertRaises(RuntimeError):
                cs.replay_census(fixture[3],self.root/'runtime',self.root/'private',['original-session'],['synthetic@aabbccdd'],effective,fixture[3]['processes'][0])
        fixture=self.reuse_fixture();fixture[3]['processes'].append(self.process(600,700,'orphan',None,'/Date(20)/'))
        effective=self.reuse_guard(fixture)
        verified=cs.replay_census(fixture[3],self.root/'runtime',self.root/'private',[],[],effective,fixture[3]['processes'][0])
        import process_generation as gen
        with self.assertRaisesRegex(RuntimeError,'Live or ambiguous descendant'):
            gen.verify_auxiliary_absence(fixture[3],{r['ProcessId']:r for r in fixture[0]['auxiliary_generations']},set(verified['excluded_pids']))
        for change in ('complete','duplicate','missing_column'):
            fixture=self.reuse_fixture();c=fixture[3]
            if change=='complete':c['complete']=False
            elif change=='duplicate':c['processes'].append(copy.deepcopy(c['processes'][-1]))
            else:del c['processes'][-1]['ParentProcessId']
            with self.subTest(change=change),self.assertRaises((ValueError,RuntimeError)):self.reuse_guard(fixture)

    def quiescence_reuse_fixture(self):
        fixture=self.reuse_fixture();base,capsule,annex,census=fixture
        capsule.update(saves_path=str(Path(os.environ['USERPROFILE'])/'AppData/LocalLow/SJSBenchmark'/('B1Audit-'+ta.CASE)),
                       protected_inventory=self.write('protected.json',[self.ev]))
        authority=self.write('authority.json',{k:self.ev for k in ('driver_source','runtime_manifest','merged_source_review')})
        receipt={'authority':authority,'participants_annex':self.write('reuse/annex.json',annex),
                 'source_pins':{'synthetic':'source'},'quiescence':{}}
        for phase in ('before','after','acceptance'):
            censuses=[self.write(f'{phase}/census-{i}.json',census) for i in range(2)]
            snapshots=[self.write(f'{phase}/scope-{i}.json',{k:{} for k in ('U','R','saves','logs','old_evidence')}) for i in range(3)]
            terminal=self.write(f'{phase}/terminal.json',{'type':'B1_TECHNICAL_FRESH_COLLECTOR_COMPLETED','exit_code':0,
                'phase':phase,'command':'synthetic pure collector','censuses':censuses,'scope_snapshots':snapshots})
            proof={'schema':'b1-technical-adjudication-quiescence-v1','phase':phase,
                'historical_writer_inventory_sha256':capsule['writer_inventory']['sha256'],'participant_generations':[],
                'historical_pid60256_reuse_witness':annex['historical_pid60256_reuse_witness'],
                'source_pins':receipt['source_pins'],'censuses':censuses,'scope_snapshots':snapshots,
                'collector_execution':{'source_refs':[self.ev],'typed_terminal_ref':terminal,
                    'command':'synthetic pure collector','helper_generation':census['processes'][0]}}
            receipt['quiescence'][phase]=self.write(f'{phase}/proof.json',proof)
        return fixture,receipt

    def verify_reuse_phases(self, fixture, receipt):
        import process_generation as gen
        base,capsule,annex,_=fixture;w=annex['historical_pid60256_reuse_witness']
        fake_api=SimpleNamespace(inventory=lambda p:{},provenance_module=lambda:SimpleNamespace(inventory=lambda p:{}))
        with patch.object(ta,'PID60256_DIAGNOSIS',w['diagnosis']['sha256']), \
             patch.object(ta,'PID60256_OBSERVATION',w['current_observation']['sha256']), \
             patch.object(ta,'generation_annex',return_value=(base,[],[])), \
             patch.object(ta,'execution_modules',return_value=(audit.measurement_dispute_module(),cs,gen)), \
             patch.object(ta,'api',return_value=fake_api),patch.object(cs,'save_inventory',return_value={}):
            ta.verify_quiescence(self.root,receipt,capsule,set())

    def test_direct_reuse_all_three_phases_replay_real_guard_and_every_census_can_block(self):
        fixture,receipt=self.quiescence_reuse_fixture();self.verify_reuse_phases(fixture,receipt)
        for phase in ('before','after','acceptance'):
            for number in (0,1):
                fixture,receipt=self.quiescence_reuse_fixture()
                proof=json.loads((self.root/receipt['quiescence'][phase]['path']).read_bytes())
                bad=copy.deepcopy(fixture[3]);bad['processes'][-1]['CreationDate']='/Date(31)/'
                proof['censuses'][number]=self.write(f'{phase}/census-{number}.json',bad)
                term=proof['collector_execution']['typed_terminal_ref'];doc=json.loads((self.root/term['path']).read_bytes())
                doc['censuses']=proof['censuses'];proof['collector_execution']['typed_terminal_ref']=self.write(term['path'],doc)
                receipt['quiescence'][phase]=self.write(f'{phase}/proof.json',proof)
                with self.subTest(phase=phase,number=number),self.assertRaisesRegex(ValueError,'exact proved'):
                    self.verify_reuse_phases(fixture,receipt)

    def test_direct_reuse_phase_witness_or_original_helper_exclusion_conflict_blocks(self):
        for phase in ('before','after','acceptance'):
            fixture,receipt=self.quiescence_reuse_fixture()
            proof=json.loads((self.root/receipt['quiescence'][phase]['path']).read_bytes());proof.pop('historical_pid60256_reuse_witness')
            receipt['quiescence'][phase]=self.write(f'{phase}/proof.json',proof)
            with self.subTest(phase=phase),self.assertRaisesRegex(ValueError,'identity/source'):
                self.verify_reuse_phases(fixture,receipt)
        fixture,receipt=self.quiescence_reuse_fixture()
        proof=json.loads((self.root/receipt['quiescence']['before']['path']).read_bytes())
        proof['collector_execution']['helper_generation']=fixture[3]['processes'][-1]
        receipt['quiescence']['before']=self.write('before/proof.json',proof)
        with self.assertRaisesRegex(ValueError,'exclude a historical'):
            self.verify_reuse_phases(fixture,receipt)

    def test_direct_reuse_witness_sources_enter_full_downstream_evidence_closure(self):
        fixture,receipt=self.quiescence_reuse_fixture();_,capsule,annex,_=fixture
        authority={k:self.ev for k in ('input_capsule','protocol','merged_source_review','driver_source','runtime_manifest')}
        capsule.update(original_report=self.ev,artifacts=[],original_reviews=[])
        receipt.update(verdict=self.ev,actual_trace_review=self.ev,participant_registry=self.ev,
            actual_trace_reviewer={'source_rollouts':[]},adjudicator={'native':self.ev,'final':self.ev,
                'model_evidence':self.ev,'execution':{'generation_census_ref':self.ev},'rollouts':[],'trace_files':[]})
        result=ta.evidence_closure(self.root,receipt,authority,capsule,{'defect_findings':[],'check_eligibility':[]},
                                   {'source_effects_evidence':[]})
        self.assertTrue({r['path'] for r in ta.direct_pid_reuse_refs(annex)} <= {r['path'] for r in result})


if __name__=='__main__': unittest.main()
