"""Prepare a neutral typed-KeyCode calibration only after exact root approval.

This script never launches Unity, mutates an existing project or sends input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
OLD=Path('E:/Test/.benchmark-admin/b1/judge-input-channel-001')
PROJECT=Path('E:/Test/.benchmark-admin/b1/input-channel-002-neutral-attempt2/Project')
UNITY=Path('E:/Unity/2021.3.36f2/Editor/Unity.exe')
VERSION='2022.3.62f2'
EXE_PRODUCT_VERSION='2022.3.62f2_7670c08855a9'
SOURCES=('TypedKeyChannel.cs','NeutralConsumer.cs','NeutralRun.cs','NeutralEntry.cs')
PINS={'0Harmony.dll':'e96fc01100b6bbdc094b900efe47850cf97aa9df7c6a78cbe723b1decf5a506a',
 'LICENSE':'a5883d0c45eb7e176d3d1631220e7f96e958e37064773d40245ae6db560bbd6d',
 'LICENSE.package':'407ddd98200c9f17f49942b5a7791e288a2bd553b59bf2f462db87dbb21c50bc'}
def digest(data):return hashlib.sha256(data).hexdigest()
def source_manifest():
 return {p.name:digest(p.read_bytes()) for p in sorted(HERE.iterdir()) if p.is_file() and p.suffix in ('.cs','.py','.md')}
def plan():
 files=source_manifest()
 return {'schema':'b1-input002-staging-plan-v1','unity':VERSION,'exe_product_version':EXE_PRODUCT_VERSION,'project':str(PROJECT),'output':str(PROJECT.parent/'Evidence'),
  'namespace':'B1InputChannel002','company':'B1JudgeNeutral','product':'TypedKeyCode002NeutralAttempt2','unity_exe':str(UNITY),
  'source_files':files,'source_manifest_sha256':digest(json.dumps(files,sort_keys=True,separators=(',',':')).encode()),
  'existing001_pins':PINS,'coverage':'Unique defined KeyCode values: None < key < Mouse0; typed overloads only.',
  'actual_calibration_run_performed':False,'permission_scope':'Plan/materialize only. No Unity launch or input performed by this script.'}
def no_links(path):
 for p in (path,*path.parents):
  try: info=p.lstat()
  except FileNotFoundError: continue
  if p.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('Linked/junction target refused')
def neutral_dependencies(ugui):
 dependencies={'com.unity.ugui':'file:'+ugui.as_posix()}
 for name in ('com.unity.modules.jsonserialize','com.unity.modules.ui','com.unity.modules.imgui'):
  package=ugui.parent/name/'package.json'
  info=json.loads(package.read_bytes())
  if info.get('name')!=name or info.get('version')!='1.0.0':raise ValueError('Required original builtin module identity mismatch')
  dependencies[name]='1.0.0'
 return dependencies

def materialize(approval,approval_sha):
 raw=approval.read_bytes()
 if digest(raw)!=approval_sha:raise ValueError('Approval hash mismatch')
 a=json.loads(raw);p=plan()
 if (a.get('schema')!='b1-input002-materialization-approval-v1' or a.get('approved') is not True
     or a.get('source_manifest_sha256')!=p['source_manifest_sha256'] or a.get('project')!=str(PROJECT)
     or a.get('unity_exe')!=str(UNITY) or a.get('unity')!=VERSION):raise ValueError('Exact staged sources/target approval required')
 no_links(PROJECT)
 if PROJECT.parent.exists():raise FileExistsError('Neutral target parent must be entirely new; never merge/overwrite')
 for n,h in PINS.items():
  if digest((OLD/n).read_bytes())!=h:raise ValueError('Frozen001 dependency pin mismatch')
 observed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
  "(Get-Item -LiteralPath '"+str(UNITY).replace("'","''")+"').VersionInfo.ProductVersion"],capture_output=True,check=True,timeout=30).stdout.decode('utf-8-sig').strip()
 if observed!=EXE_PRODUCT_VERSION:raise ValueError('Actual Unity ProductVersion mismatch; folder name is not version evidence')
 ugui=UNITY.parent/'Data/Resources/PackageManager/BuiltInPackages/com.unity.ugui'
 if not (ugui/'package.json').is_file():raise ValueError('Offline original bundled UGUI package missing; do not fetch network packages')
 dependencies=neutral_dependencies(ugui)
 payload={}
 for n in SOURCES:
  relative='Assets/B1Input002/'+('Editor/' if n=='NeutralEntry.cs' else '')+n
  payload[relative]=(HERE/n).read_bytes()
 for n in PINS:payload['Assets/B1Input002/ThirdParty/'+n]=(OLD/n).read_bytes()
 payload['ProjectSettings/ProjectVersion.txt']=('m_EditorVersion: '+VERSION+'\n').encode()
 payload['Packages/manifest.json']=(json.dumps({'dependencies':dependencies},indent=2)+'\n').encode()
 request={k:p[k] for k in ('unity','project','output','company','product')}
 request['sources']=[{'path':n,'sha256':digest(b)} for n,b in sorted(payload.items()) if n.endswith(('.cs','.dll'))]
 payload['input002-request.json']=(json.dumps(request,indent=2)+'\n').encode()
 payload['STAGING.json']=(json.dumps({'plan':p,'approval_sha256':approval_sha,'actual_exe_product_version':observed,
  'files':{n:digest(b) for n,b in payload.items()},'unity_launched':False},indent=2)+'\n').encode()
 for n,b in payload.items():
  target=PROJECT/n;target.parent.mkdir(parents=True,exist_ok=True)
  with target.open('xb') as f:f.write(b)
 return {'project':str(PROJECT),'files':len(payload),'unity_launched':False,
  'next_step':'Root reviews and runs one separately approved Hidden Unity executeMethod B1InputChannel002.NeutralEntry.Start. Materialization alone is not a run or pass.'}
def validate_evidence():
 root=PROJECT.parent/'Evidence';report=json.loads((root/'run-completed.json').read_bytes());idle=json.loads((root/'terminal-idle.json').read_bytes())
 if any(x.get('company')!='B1JudgeNeutral' or x.get('product')!='TypedKeyCode002NeutralAttempt2'
        or not isinstance(x.get('persistentDataPath'),str) or not x['persistentDataPath'] for x in (report,idle)):
  raise ValueError('Neutral private save identity missing')
 if report['persistentDataPath']!=idle['persistentDataPath']:raise ValueError('Neutral save path changed')
 if (report.get('isBatchMode') is not True or idle.get('isBatchMode') is not True
     or not isinstance(report.get('runIdentity'),str) or len(report['runIdentity'])!=32
     or any(c not in '0123456789abcdef' for c in report['runIdentity'])
     or idle.get('runIdentity')!=report['runIdentity']):raise ValueError('Actual batchmode/run identity missing or mismatched')
 if (report.get('status')!='measurement_completed_pending_independent_review' or report.get('unity')!=VERSION
     or report.get('project')!=str(PROJECT) or report.get('owner')!='b1.calibration.002.typed-keycode-only'
     or any(report.get(k) is not True for k in ('completed','cleanupVerified','sourceAndILUnchanged','unpatchedIdleNoLeak'))
     or report.get('unpatchedIdleFrames')!=2 or report.get('error')):raise ValueError('Neutral measurement incomplete/blocked')
 keys=report.get('coverageKeys');rows=report.get('keys')
 if (not isinstance(keys,list) or not keys or any(type(k) is not int or not 0<k<323 for k in keys)
     or keys!=sorted(set(keys)) or any(type(report.get(k)) is not int or report[k]!=len(keys) for k in ('expectedKeys','observedKeys'))
     or not isinstance(rows,list) or [r.get('key') for r in rows]!=keys):raise ValueError('Declared unique keyboard coverage incomplete')
 for row in rows:
  if (any(row.get(k) is not True for k in ('passed','uiCaptured','idleNoLeak','oldKeyInactive'))
      or any(type(row.get(k)) is not int for k in ('downDelta','heldDelta','upDelta'))
      or [row.get(k) for k in ('downDelta','heldDelta','upDelta')]!=[1,3,1]):raise ValueError('Actual neutral consumer/UI observation mismatch')
 if (idle.get('status')!='idle_pending_independent_review' or idle.get('project')!=str(PROJECT) or idle.get('unity')!=VERSION
     or idle.get('cleanupVerified') is not True or any(idle.get(k) is not False for k in ('playing','compiling','updating'))
     or any(type(idle.get(k)) is not int or idle[k]!=0 for k in ('patches','loops','virtualKeys'))):raise ValueError('Actual idle/cleanup not observed')
 trace=json.loads((root/'read-trace.json').read_bytes());seen={}
 if not isinstance(trace,list) or not trace:raise ValueError('Actual input-read trace missing')
 for row in trace:
  identity=(row.get('frame'),row.get('key'),row.get('method'))
  if (type(identity[0]) is not int or identity[1] not in keys or identity[2] not in ('GetKey','GetKeyDown','GetKeyUp')
      or type(row.get('value')) is not bool or identity in seen and seen[identity]!=row['value']):raise ValueError('Inconsistent or unknown frame/key read')
  seen[identity]=row['value']
 return {'status':'measurement_completed_pending_independent_review','observed_keys':len(keys),
  'files':{p.name:digest(p.read_bytes()) for p in sorted(root.iterdir()) if p.is_file()},
  'actual_source_trace_effects_review_still_required':True,'business_requirements_passed':False}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('plan','materialize','validate'))
 parser.add_argument('--approval',type=Path);parser.add_argument('--approval-sha256');args=parser.parse_args()
 if args.command=='materialize' and (args.approval is None or args.approval_sha256 is None):parser.error('materialize requires exact approval file/hash')
 result=plan() if args.command=='plan' else validate_evidence() if args.command=='validate' else materialize(args.approval,args.approval_sha256)
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
