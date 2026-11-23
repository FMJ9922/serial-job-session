import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('input002_stage',HERE/'stage_neutral.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class StageTests(unittest.TestCase):
 def test_required_builtin_module_manifest_is_offline_and_identity_checked(self):
  with tempfile.TemporaryDirectory() as tmp:
   packages=Path(tmp);ugui=packages/'com.unity.ugui';ugui.mkdir()
   for name in ('com.unity.modules.jsonserialize','com.unity.modules.ui','com.unity.modules.imgui'):
    (packages/name).mkdir();(packages/name/'package.json').write_text(json.dumps({'name':name,'version':'1.0.0'}))
   deps=s.neutral_dependencies(ugui)
   self.assertEqual(deps['com.unity.modules.jsonserialize'],'1.0.0')
   self.assertTrue(deps['com.unity.ugui'].startswith('file:'))
   (packages/'com.unity.modules.jsonserialize/package.json').write_text('{"name":"wrong","version":"1.0.0"}')
   with self.assertRaisesRegex(ValueError,'identity'):s.neutral_dependencies(ugui)
 def test_plan_is_unexecuted_pinned_and_neutral(self):
  p=s.plan();self.assertFalse(p['actual_calibration_run_performed']);self.assertEqual(p['unity'],'2022.3.62f2')
  self.assertEqual(p['namespace'],'B1InputChannel002');self.assertEqual(len(p['existing001_pins']),3)
  self.assertTrue(all(name in p['source_files'] for name in s.SOURCES))
 def test_source_has_no_desktop_or_business_input_bypass(self):
  text='\n'.join((HERE/n).read_text() for n in s.SOURCES)
  for forbidden in ('DllImport','SendInput','SetCursorPos','SetForegroundWindow','.Focus(','.Show(', 'keybd_event','mouse_event','GetAsyncKeyState'):
   self.assertNotIn(forbidden,text)
  consumer=(HERE/'NeutralConsumer.cs').read_text();self.assertIn('Input.GetKeyDown(key)',consumer)
  self.assertIn('Enum.GetValues(typeof(KeyCode))',consumer);self.assertNotIn('TypedKeyChannel',consumer)
  entry=(HERE/'NeutralEntry.cs').read_text()
  self.assertIn('Application.isBatchMode',entry)
  self.assertIn('normal_exit_invoked_not_observed_complete',entry)
  self.assertLess(entry.index('File.WriteAllText(Path.Combine(request.output,"exit-invocation.json")'),entry.index('EditorApplication.Exit(0)'))
  self.assertNotIn('delayCall',entry)
  self.assertIn('ExecuteEvents.pointerClickHandler',(HERE/'NeutralRun.cs').read_text())
 def test_reparse_and_dangling_link_rejected(self):
  from types import SimpleNamespace
  with mock.patch.object(Path,'lstat',return_value=SimpleNamespace(st_file_attributes=0x400)), mock.patch.object(Path,'is_symlink',return_value=False):
   with self.assertRaisesRegex(ValueError,'Linked'):s.no_links(Path('nonexistent/child'))
  with mock.patch.object(Path,'lstat',return_value=SimpleNamespace(st_file_attributes=0)), mock.patch.object(Path,'is_symlink',return_value=True):
   with self.assertRaisesRegex(ValueError,'Linked'):s.no_links(Path('dangling'))
 def test_unapproved_or_preexisting_target_cannot_be_modified(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);target=root/'Neutral/Project';approval=root/'approval.json'
   with mock.patch.object(s,'PROJECT',target),mock.patch.object(s.subprocess,'run') as process:
    p=s.plan();a={'schema':'b1-input002-materialization-approval-v1','approved':False,
      **{k:p[k] for k in ('source_manifest_sha256','project','unity_exe','unity')}}
    approval.write_text(json.dumps(a));h=s.digest(approval.read_bytes())
    with self.assertRaises(ValueError):s.materialize(approval,h)
    self.assertFalse(target.parent.exists());process.assert_not_called()
    a['approved']=True;approval.write_text(json.dumps(a));h=s.digest(approval.read_bytes())
    target.parent.mkdir();marker=target.parent/'retained';marker.write_bytes(b'unchanged')
    with self.assertRaises(FileExistsError):s.materialize(approval,h)
    self.assertEqual(marker.read_bytes(),b'unchanged');process.assert_not_called()
 def test_changed_source_or_approval_hash_blocks_before_writes(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);approval=root/'approval.json';approval.write_text('{}')
   with mock.patch.object(s,'PROJECT',root/'Neutral/Project'):
    with self.assertRaisesRegex(ValueError,'Approval hash'):s.materialize(approval,'0'*64)
    with self.assertRaisesRegex(ValueError,'Exact staged'):s.materialize(approval,s.digest(approval.read_bytes()))
    self.assertFalse((root/'Neutral').exists())
 def test_evidence_validator_remains_pending_and_rejects_missing_or_inconsistent_reads(self):
  with tempfile.TemporaryDirectory() as tmp:
   project=Path(tmp)/'Project';evidence=project.parent/'Evidence';evidence.mkdir()
   report={'status':'measurement_completed_pending_independent_review','unity':s.VERSION,'project':str(project),
    'company':'B1JudgeNeutral','product':'TypedKeyCode002NeutralAttempt2','persistentDataPath':'fixture/private',
    'owner':'b1.calibration.002.typed-keycode-only','isBatchMode':True,'runIdentity':'a'*32,'completed':True,'cleanupVerified':True,'sourceAndILUnchanged':True,
    'unpatchedIdleNoLeak':True,'unpatchedIdleFrames':2,'coverageKeys':[107],'expectedKeys':1,'observedKeys':1,
    'keys':[{'key':107,'passed':True,'uiCaptured':True,'idleNoLeak':True,'oldKeyInactive':True,'downDelta':1,'heldDelta':3,'upDelta':1}]}
   idle={'company':'B1JudgeNeutral','product':'TypedKeyCode002NeutralAttempt2','persistentDataPath':'fixture/private','isBatchMode':True,'runIdentity':'a'*32,'status':'idle_pending_independent_review','project':str(project),'unity':s.VERSION,
    'cleanupVerified':True,'playing':False,'compiling':False,'updating':False,'patches':0,'loops':0,'virtualKeys':0}
   (evidence/'run-completed.json').write_text(json.dumps(report));(evidence/'terminal-idle.json').write_text(json.dumps(idle))
   trace=[{'frame':1,'key':107,'method':'GetKeyDown','value':True}]
   (evidence/'read-trace.json').write_text(json.dumps(trace))
   with mock.patch.object(s,'PROJECT',project):
    self.assertEqual(s.validate_evidence()['status'],'measurement_completed_pending_independent_review')
    trace.append({'frame':1,'key':107,'method':'GetKeyDown','value':False})
    (evidence/'read-trace.json').write_text(json.dumps(trace))
    with self.assertRaisesRegex(ValueError,'Inconsistent'):s.validate_evidence()
    (evidence/'read-trace.json').write_text('[]')
    with self.assertRaisesRegex(ValueError,'trace missing'):s.validate_evidence()

if __name__=='__main__':unittest.main()
