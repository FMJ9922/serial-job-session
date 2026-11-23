"""Calibration-only legacy Input substitution; never touches a contestant.

Requires the already-running script-input calibration Editor. No Unity launch,
desktop input, native window activation, or consumer-source rewriting.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

ROOT=Path('E:/Test/.benchmark-admin/b1-calibration-script-input')
PROJECT=ROOT/'Project'
OUT=ROOT/'legacy-interception'

CONSUMER=r'''
using System;using UnityEngine;using UnityEngine.UI;
public class LegacyReadConsumer:MonoBehaviour {
 public KeyCode binding=KeyCode.K;public int downs,heldFrames,ups,rebinds;public bool capturing;public Button rebindButton;
 void Start(){var canvas=new GameObject("LegacyConsumerCanvas",typeof(Canvas)).GetComponent<Canvas>();canvas.renderMode=RenderMode.ScreenSpaceOverlay;
  var go=new GameObject("Rebind",typeof(RectTransform),typeof(Image),typeof(Button));go.transform.SetParent(canvas.transform,false);rebindButton=go.GetComponent<Button>();rebindButton.onClick.AddListener(()=>{capturing=true;});}
 void Update(){
  if(capturing){foreach(var key in new[]{KeyCode.F13,KeyCode.F14})if(Input.GetKeyDown(key)){binding=key;capturing=false;rebinds++;return;}return;}
  if(Input.GetKeyDown(binding))downs++;
  if(Input.GetKey(binding))heldFrames++;
  if(Input.GetKeyUp(binding))ups++;
 }
}
'''

EDITOR=r'''
using System;using System.IO;using System.Linq;using System.Collections.Generic;using System.Reflection;using System.Security.Cryptography;
using HarmonyLib;using UnityEngine;using UnityEditor;using UnityEngine.EventSystems;using UnityEngine.LowLevel;
[InitializeOnLoad]public static class LegacyInputInterception {
 const string Owner="b1.calibration.legacy-input-only";
 static string Root=>Directory.GetParent(Application.dataPath).FullName;
 static string Output=>Path.Combine(Directory.GetParent(Root).FullName,"legacy-interception");
 static Harmony harmony;static bool active,hasLoop;static PlayerLoopSystem originalLoop;
 static readonly HashSet<KeyCode> reserved=new HashSet<KeyCode>{KeyCode.K,KeyCode.F13,KeyCode.F14};
 static readonly HashSet<KeyCode> desired=new HashSet<KeyCode>(),held=new HashSet<KeyCode>(),down=new HashSet<KeyCode>(),up=new HashSet<KeyCode>();
 static int committedFrame=-1;static List<Read> reads=new List<Read>();
 [Serializable]public class Read{public int frame;public string method,key;public bool value;}
 [Serializable]public class State{public bool active,loopInstalled;public int frame,legacyK,legacySpace,downs,heldFrames,ups,rebinds,ourPatchCount,traceCount;public string binding,error;public bool capturing,rawK,rawKDown,rawKUp,rawF13;public string inputILHash;}
 static LegacyInputInterception(){EditorApplication.update+=Tick;AssemblyReloadEvents.beforeAssemblyReload+=Cleanup;EditorApplication.playModeStateChanged+=s=>{if(s==PlayModeStateChange.ExitingPlayMode||s==PlayModeStateChange.EnteredEditMode)Cleanup();};}
 static void Commit(){committedFrame=Time.frameCount;down.Clear();up.Clear();foreach(var key in reserved){bool a=held.Contains(key),b=desired.Contains(key);if(!a&&b)down.Add(key);if(a&&!b)up.Add(key);}held.Clear();held.UnionWith(desired);}
 static bool Value(KeyCode key,string method){if(method=="GetKey")return held.Contains(key);if(committedFrame!=Time.frameCount)return false;return method=="GetKeyDown"?down.Contains(key):up.Contains(key);}
 static bool PrefixKey(KeyCode __0,ref bool __result,MethodBase __originalMethod){if(!active||!reserved.Contains(__0))return true;__result=Value(__0,__originalMethod.Name);Record(__originalMethod.Name,__0,__result);return false;}
 static bool PrefixString(string __0,ref bool __result,MethodBase __originalMethod){KeyCode key;if(!active||!Enum.TryParse(__0,true,out key)||!reserved.Contains(key))return true;__result=Value(key,__originalMethod.Name);Record(__originalMethod.Name,key,__result);return false;}
 static void Record(string method,KeyCode key,bool value){if(reads.Count<50000)reads.Add(new Read{frame=Time.frameCount,method=method,key=key.ToString(),value=value});}
 static bool Insert(ref PlayerLoopSystem node){if(node.subSystemList==null)return false;var list=node.subSystemList.ToList();for(int i=0;i<list.Count;i++){var child=list[i];if(child.type==typeof(UnityEngine.PlayerLoop.Update.ScriptRunBehaviourUpdate)){list.Insert(i,new PlayerLoopSystem{type=typeof(LegacyInputInterception),updateDelegate=Commit});node.subSystemList=list.ToArray();return true;}if(Insert(ref child)){list[i]=child;node.subSystemList=list.ToArray();return true;}}return false;}
 static void Install(){if(active)throw new Exception("Already installed");originalLoop=PlayerLoop.GetCurrentPlayerLoop();var loop=originalLoop;if(!Insert(ref loop))throw new Exception("Cannot locate ScriptRunBehaviourUpdate");harmony=new Harmony(Owner);
  try{foreach(var name in new[]{"GetKey","GetKeyDown","GetKeyUp"}){harmony.Patch(typeof(Input).GetMethod(name,new[]{typeof(KeyCode)}),prefix:new HarmonyMethod(typeof(LegacyInputInterception),"PrefixKey"));harmony.Patch(typeof(Input).GetMethod(name,new[]{typeof(string)}),prefix:new HarmonyMethod(typeof(LegacyInputInterception),"PrefixString"));}PlayerLoop.SetPlayerLoop(loop);hasLoop=true;active=true;}catch{Cleanup();throw;}}
 static void Cleanup(){active=false;desired.Clear();held.Clear();down.Clear();up.Clear();if(harmony!=null){harmony.UnpatchAll(Owner);harmony=null;}if(hasLoop){PlayerLoop.SetPlayerLoop(originalLoop);hasLoop=false;}SaveTrace();}
 static string ILHash(){var bytes=typeof(ScriptInputProbe).GetMethod("Update",BindingFlags.Instance|BindingFlags.NonPublic).GetMethodBody().GetILAsByteArray();using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
 static State Snapshot(){var probe=UnityEngine.Object.FindObjectOfType<ScriptInputProbe>();var c=UnityEngine.Object.FindObjectOfType<LegacyReadConsumer>();int patches=0;foreach(var name in new[]{"GetKey","GetKeyDown","GetKeyUp"})foreach(var argument in new[]{typeof(KeyCode),typeof(string)}){var info=Harmony.GetPatchInfo(typeof(Input).GetMethod(name,new[]{argument}));if(info!=null&&info.Owners.Contains(Owner))patches++;}
  return new State{active=active,loopInstalled=hasLoop,frame=Time.frameCount,legacyK=probe==null?-1:probe.state.legacyK,legacySpace=probe==null?-1:probe.state.legacySpace,downs=c==null?-1:c.downs,heldFrames=c==null?-1:c.heldFrames,ups=c==null?-1:c.ups,rebinds=c==null?-1:c.rebinds,binding=c==null?"missing":c.binding.ToString(),capturing=c!=null&&c.capturing,ourPatchCount=patches,traceCount=reads.Count,rawK=Input.GetKey(KeyCode.K),rawKDown=Input.GetKeyDown(KeyCode.K),rawKUp=Input.GetKeyUp(KeyCode.K),rawF13=Input.GetKey(KeyCode.F13),inputILHash=ILHash()};}
 static void SaveTrace(){try{Directory.CreateDirectory(Output);File.WriteAllText(Path.Combine(Output,"read-trace.json"),"["+string.Join(",",reads.Select(JsonUtility.ToJson).ToArray())+"]");}catch{}}
 static void Tick(){if(EditorApplication.isCompiling)return;string path=Path.Combine(Root,"legacy-command.txt");if(!File.Exists(path))return;string op;try{op=File.ReadAllText(path).Trim();File.Delete(path);}catch(IOException){return;}
  string error=null;try{
   if(op=="add-consumer")new GameObject("LegacyReadConsumer").AddComponent<LegacyReadConsumer>();
   if(op=="install")Install();
   if(op.EndsWith("-down")||op.EndsWith("-up")){string part=op.Substring(0,op.LastIndexOf('-'));KeyCode key=(KeyCode)Enum.Parse(typeof(KeyCode),part,true);if(!reserved.Contains(key))throw new Exception("Key outside calibration reservation");if(op.EndsWith("-down"))desired.Add(key);else desired.Remove(key);}
   if(op=="rebind"){var c=UnityEngine.Object.FindObjectOfType<LegacyReadConsumer>();var e=new PointerEventData(EventSystem.current){button=PointerEventData.InputButton.Left};ExecuteEvents.Execute(c.rebindButton.gameObject,e,ExecuteEvents.pointerDownHandler);ExecuteEvents.Execute(c.rebindButton.gameObject,e,ExecuteEvents.pointerUpHandler);ExecuteEvents.Execute(c.rebindButton.gameObject,e,ExecuteEvents.pointerClickHandler);}
   if(op=="cleanup")Cleanup();
  }catch(Exception ex){error=ex.ToString();Cleanup();}
  var state=Snapshot();state.error=error;Directory.CreateDirectory(Output);File.WriteAllText(Path.Combine(Output,"latest.json"),JsonUtility.ToJson(state));SaveTrace();
 }
}
'''

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def wait(predicate,timeout=120):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=predicate()
            if value:return value
        except (OSError,ValueError,KeyError,ConnectionError):pass
        time.sleep(.2)
    raise TimeoutError(f'Legacy calibration timeout {timeout}s')

def environment():
    spec=importlib.util.spec_from_file_location('b1_env',ROOT.parent/'check_environment.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def port_for_project():
    for file in (Path.home()/'.unity-mcp').glob('unity-mcp-port-*.json'):
        try:
            d=json.loads(file.read_text(encoding='utf-8-sig'))
            if Path(d.get('project_path','')).resolve()==(PROJECT/'Assets').resolve():return d['unity_port']
        except (ValueError,OSError):pass

def send(value,legacy=False):
    path=PROJECT/('legacy-command.txt' if legacy else 'command.txt');temporary=path.with_suffix('.tmp')
    wait(lambda:not path.exists(),30);temporary.write_text(value,encoding='utf-8');temporary.replace(path);wait(lambda:not path.exists(),30)
    if legacy:
        wait(lambda:(OUT/'latest.json').exists(),10);result=json.loads((OUT/'latest.json').read_text());
        if result.get('error'):raise RuntimeError(result['error'])
        return result

def snap():
    (OUT/'latest.json').unlink(missing_ok=True);return send('status',True)

def deploy():
    if not OUT.exists():raise RuntimeError('Expected downloaded official Harmony package and license')
    package=OUT/'lib.harmony.2.3.3.nupkg'
    with zipfile.ZipFile(package) as z:
        dll=z.read('lib/net472/0Harmony.dll');license=z.read('LICENSE');nuspec=z.read('Lib.Harmony.nuspec')
    if b'MIT' not in license:raise RuntimeError('Expected MIT license')
    (OUT/'LICENSE.package').write_bytes(license);(OUT/'Lib.Harmony.nuspec').write_bytes(nuspec)
    (OUT/'0Harmony.dll').write_bytes(dll)
    consumer=PROJECT/'Assets/LegacyReadConsumer.cs';editor=PROJECT/'Assets/Editor/LegacyInputInterception.cs'
    consumer.write_text(CONSUMER,encoding='utf-8');editor.write_text(EDITOR,encoding='utf-8')
    plugin=PROJECT/'Assets/Plugins/B1LegacyCalibration';plugin.mkdir(parents=True,exist_ok=True)
    (plugin/'0Harmony.dll').write_bytes(dll);(plugin/'LICENSE').write_bytes(license)
    return consumer,editor

def offline_compile(consumer,editor):
    folder=OUT/'offline';folder.mkdir(exist_ok=True);results=[]
    for name,source in [('Assembly-CSharp',consumer),('Assembly-CSharp-Editor',editor)]:
        original=PROJECT/f'Library/Bee/artifacts/1900b0aEDbg.dag/{name}.rsp';text=original.read_text(encoding='utf-8-sig')
        if name.endswith('Editor'):
            text=text.replace('Library/Bee/artifacts/1900b0aEDbg.dag/Assembly-CSharp.ref.dll',str(folder/'Assembly-CSharp.ref.dll').replace('\\','/'))
        response=folder/(name+'.rsp');response.write_text(text,encoding='utf-8')
        args=['E:/Unity/2021.3.36f2/Editor/Data/NetCoreRuntime/dotnet.exe','exec','E:/Unity/2021.3.36f2/Editor/Data/DotNetSdkRoslyn/csc.dll','@'+str(response),str(source),'-r:'+str(OUT/'0Harmony.dll'),'-out:'+str(folder/(name+'.dll')),'-refout:'+str(folder/(name+'.ref.dll'))]
        done=subprocess.run(args,cwd=PROJECT,text=True,capture_output=True);results.append({'name':name,'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr})
    (OUT/'offline-compile.json').write_text(json.dumps(results,indent=2))
    if any(x['exit_code'] for x in results):raise RuntimeError('Offline compile failed; no Unity refresh attempted: '+json.dumps(results))

def run():
    consumer,editor=deploy();report={'status':'running','scope':'calibration only; no contestant accessed','started_utc':datetime.now(timezone.utc).isoformat(),'original_consumer_source_sha256':sha(PROJECT/'Assets/ScriptInputProbe.cs'),'new_consumer_source_sha256':sha(consumer),'harmony_package_sha256':sha(OUT/'lib.harmony.2.3.3.nupkg'),'harmony_dll_sha256':sha(OUT/'0Harmony.dll'),'license_sha256':sha(OUT/'LICENSE.package'),'states':{}}
    e=environment();cleanup=False
    try:
        offline_compile(consumer,editor)
        port=wait(port_for_project,30)
        try:report['refresh']=e.request(port,{'type':'refresh_unity','params':{'mode':'force','scope':'all','compile':'request','wait_for_ready':False}},timeout=30)
        except (ConnectionError,OSError):pass
        def ready():
            p=port_for_project()
            if not p:return False
            s=e.request(p,{'type':'get_editor_state','params':{}},timeout=5)
            return p if s.get('result',{}).get('data',{}).get('activity',{}).get('phase')=='idle' else False
        wait(ready,180);time.sleep(2)
        send('setup');wait(lambda:json.loads((PROJECT/'state.json').read_text())['frames']>20,120)
        send('add-consumer',True);time.sleep(.5)
        report['states']['unpatched']=snap()
        report['states']['installed']=send('install',True);cleanup=True
        send('K-down',True);time.sleep(.3);report['states']['k_pressed']=snap()
        time.sleep(.3);report['states']['k_held']=snap()
        send('K-up',True);time.sleep(.3);report['states']['k_released']=snap()
        report['states']['rebind_waiting']=send('rebind',True)
        send('F13-down',True);time.sleep(.3);report['states']['rebound']=snap();send('F13-up',True);time.sleep(.3)
        report['states']['before_old_key']=snap();send('K-down',True);time.sleep(.3);report['states']['after_old_key']=snap();send('K-up',True);time.sleep(.3)
        report['states']['before_new_key']=snap();send('F13-down',True);time.sleep(.3);report['states']['after_new_key']=snap();send('F13-up',True);time.sleep(.3)
        report['states']['cleaned']=send('cleanup',True);cleanup=False
        time.sleep(.3);report['states']['restored_natural']=snap()
        s=report['states'];report['checks']={
            'original_probe_receives_down':s['k_pressed']['legacyK']>s['unpatched']['legacyK'],
            'down_once':s['k_pressed']['downs']==s['unpatched']['downs']+1 and s['k_held']['downs']==s['k_pressed']['downs'],
            'held_continues':s['k_held']['heldFrames']>s['k_pressed']['heldFrames'],
            'up_once':s['k_released']['ups']==s['k_held']['ups']+1,
            'consumer_rebound_via_ui_and_input':s['rebind_waiting']['capturing'] and s['rebound']['binding']=='F13' and s['rebound']['rebinds']==1,
            'old_key_no_longer_action':s['after_old_key']['downs']==s['before_old_key']['downs'],
            'new_key_drives_action':s['after_new_key']['downs']==s['before_new_key']['downs']+1,
            'our_prefixes_removed_and_loop_restored':not s['restored_natural']['active'] and not s['restored_natural']['loopInstalled'] and s['restored_natural']['ourPatchCount']==0,
            'original_update_il_unchanged':len({x['inputILHash'] for x in s.values()})==1,
            'original_source_unchanged':sha(PROJECT/'Assets/ScriptInputProbe.cs')==report['original_consumer_source_sha256'],
            'new_consumer_source_unchanged':sha(consumer)==report['new_consumer_source_sha256']}
        report['status']='passed' if all(report['checks'].values()) else 'unsupported'
    except Exception as error:report['status']='failed';report['error']=f'{type(error).__name__}: {error}'
    finally:
        try:
            if cleanup:report['emergency_cleanup']=send('cleanup',True)
            send('stop');time.sleep(2)
        except Exception as error:report['cleanup_error']=str(error)
        report['finished_utc']=datetime.now(timezone.utc).isoformat();report['source_sha256']=sha(Path(__file__))
        report['evidence_sha256']={p.relative_to(OUT).as_posix():sha(p) for p in [OUT/'read-trace.json',OUT/'offline-compile.json',OUT/'LICENSE.package',OUT/'0Harmony.dll'] if p.exists()}
        (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report

if __name__=='__main__':
    result=run();print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['status']=='passed' else 1)
