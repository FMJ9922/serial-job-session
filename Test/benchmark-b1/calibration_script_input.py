"""One-launch, no-desktop-input Unity calibration outside all contestants.

Only OS calls: read-only GetCursorPos/GetForegroundWindow. No native input,
window activation, EditorWindow.Show/Focus, or real device event injection.
"""
from __future__ import annotations
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

ROOT=Path('E:/Test/.benchmark-admin/b1-calibration-script-input')
SOURCE=Path('E:/Test/.benchmark-admin/b1-calibration/Project')
UNITY=Path('E:/Unity/2021.3.36f2/Editor/Unity.exe')

RUNTIME=r'''
using System;using System.IO;using UnityEngine;using UnityEngine.UI;
using UnityEngine.EventSystems;using UnityEngine.InputSystem;using UnityEngine.InputSystem.LowLevel;
public class ScriptInputProbe:MonoBehaviour {
 [Serializable] public class State {public int frames,uguiClicks,imguiClicks,imguiKeys,legacyK,legacySpace,virtualSpace;public float rms;public bool muted;public int virtualDeviceId;public string[] uiEvents;}
 public State state=new State();public Button button;public Keyboard virtualKeyboard;InputAction spaceAction;
 AudioSource sound;float[] samples=new float[1024];float next;System.Collections.Generic.List<string> trace=new System.Collections.Generic.List<string>();
 void Start(){Application.runInBackground=true;
  var cam=new GameObject("ProbeCamera").AddComponent<Camera>();cam.clearFlags=CameraClearFlags.SolidColor;cam.backgroundColor=new Color(.1f,.25f,.3f);cam.gameObject.AddComponent<AudioListener>();
  new GameObject("Events",typeof(EventSystem),typeof(StandaloneInputModule));
  var canvas=new GameObject("Canvas",typeof(Canvas),typeof(CanvasScaler),typeof(GraphicRaycaster)).GetComponent<Canvas>();canvas.renderMode=RenderMode.ScreenSpaceOverlay;
  var target=new GameObject("ProbeUGUIButton",typeof(RectTransform),typeof(Image),typeof(Button),typeof(ProbePointerTrace));target.transform.SetParent(canvas.transform,false);button=target.GetComponent<Button>();button.onClick.AddListener(()=>{state.uguiClicks++;trace.Add("onClick");Debug.Log("SCRIPT_UGUI_ONCLICK");});
  var rect=target.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(250,100);rect.anchoredPosition=new Vector2(0,-200);
  InputSystem.settings.backgroundBehavior=InputSettings.BackgroundBehavior.IgnoreFocus;InputSystem.settings.editorInputBehaviorInPlayMode=InputSettings.EditorInputBehaviorInPlayMode.AllDeviceInputAlwaysGoesToGameView;
  virtualKeyboard=InputSystem.AddDevice<Keyboard>("B1VirtualKeyboard");state.virtualDeviceId=virtualKeyboard.deviceId;
  spaceAction=new InputAction("ProbeSpace",InputActionType.Button,"<Keyboard>/space");spaceAction.performed+=context=>{if(context.control.device==virtualKeyboard){state.virtualSpace++;Debug.Log("SCRIPT_VIRTUAL_SPACE_ACTION");}};spaceAction.Enable();
  sound=gameObject.AddComponent<AudioSource>();var clip=AudioClip.Create("Tone",44100,1,44100,false);var data=new float[44100];for(int i=0;i<data.Length;i++)data[i]=.2f*Mathf.Sin(i*2*Mathf.PI*440/44100);clip.SetData(data,0);sound.clip=clip;sound.loop=true;sound.Play();
  Debug.Log("SCRIPT_RUNTIME_READY");
 }
 public void Trace(string value){trace.Add(value);}
 void OnGUI(){var e=Event.current;if(e.type==EventType.KeyDown&&e.keyCode==KeyCode.K){state.imguiKeys++;Debug.Log("SCRIPT_IMGUI_KEY");}
  if(GUI.Button(new Rect(0,0,Screen.width,Screen.height),"Script-only IMGUI event probe")){state.imguiClicks++;Debug.Log("SCRIPT_IMGUI_CLICK");}
  GUI.Label(new Rect(10,10,1000,80),"UGUI="+state.uguiClicks+" IMGUI="+state.imguiClicks+" LegacyK="+state.legacyK+" VirtualSpace="+state.virtualSpace);
 }
 void Update(){state.frames++;if(Input.GetKeyDown(KeyCode.K))state.legacyK++;if(Input.GetKeyDown(KeyCode.Space))state.legacySpace++;
  sound.volume=state.muted?0:1;if(Time.unscaledTime<next)return;next=Time.unscaledTime+.1f;
  AudioListener.GetOutputData(samples,0);float total=0;foreach(float x in samples)total+=x*x;state.rms=Mathf.Sqrt(total/samples.Length);state.uiEvents=trace.ToArray();
  File.WriteAllText(Path.Combine(Directory.GetParent(Application.dataPath).FullName,"state.json"),JsonUtility.ToJson(state));
 }
 void OnDestroy(){if(spaceAction!=null)spaceAction.Dispose();if(virtualKeyboard!=null&&virtualKeyboard.added)InputSystem.RemoveDevice(virtualKeyboard);}
}
public class ProbePointerTrace:MonoBehaviour,IPointerDownHandler,IPointerUpHandler,IPointerClickHandler {
 void Record(string e){UnityEngine.Object.FindObjectOfType<ScriptInputProbe>().Trace(e);}
 public void OnPointerDown(PointerEventData e){Record("pointerDown");}
 public void OnPointerUp(PointerEventData e){Record("pointerUp");}
 public void OnPointerClick(PointerEventData e){Record("pointerClick");}
}
'''

EDITOR=r'''
using System;using System.IO;using UnityEngine;using UnityEditor;using UnityEditor.SceneManagement;using UnityEngine.EventSystems;
using UnityEngine.InputSystem;using UnityEngine.InputSystem.LowLevel;
[InitializeOnLoad]public static class ScriptInputCalibration {
 static string Root=>Directory.GetParent(Application.dataPath).FullName;
 static ScriptInputCalibration(){SessionState.SetBool("MCPForUnity.StartupConfigRewrite.Ran",true);EditorApplication.update+=Tick;EditorApplication.delayCall+=Boot;}
 public static void Boot(){SessionState.SetBool("MCPForUnity.StartupConfigRewrite.Ran",true);MCPForUnity.Editor.McpCiBoot.StartStdioForCi();File.WriteAllText(Path.Combine(Root,"ready.json"),"{\"version\":\""+Application.unityVersion+"\",\"project\":\""+Root.Replace("\\","/")+"\"}");}
 static EditorWindow ExistingGameView(){foreach(var w in Resources.FindObjectsOfTypeAll<EditorWindow>())if(w.GetType().FullName=="UnityEditor.GameView")return w;return null;}
 static void Tick(){if(EditorApplication.isCompiling)return;var path=Path.Combine(Root,"command.txt");if(!File.Exists(path))return;
  string op;try{op=File.ReadAllText(path).Trim();File.Delete(path);}catch(IOException){return;}
  try{
   if(op=="setup"){EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);new GameObject("ScriptInputProbe").AddComponent<ScriptInputProbe>();EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),"Assets/ScriptInputCalibration.unity");EditorApplication.isPlaying=true;}
   var p=UnityEngine.Object.FindObjectOfType<ScriptInputProbe>();
   if(op=="ugui"){var d=new PointerEventData(EventSystem.current){button=PointerEventData.InputButton.Left,position=new Vector2(960,340)};ExecuteEvents.Execute(p.button.gameObject,d,ExecuteEvents.pointerDownHandler);ExecuteEvents.Execute(p.button.gameObject,d,ExecuteEvents.pointerUpHandler);ExecuteEvents.Execute(p.button.gameObject,d,ExecuteEvents.pointerClickHandler);}
   if(op.StartsWith("imgui")){var view=ExistingGameView();if(view==null){File.WriteAllText(Path.Combine(Root,"imgui-unavailable.txt"),"No pre-existing GameView; no Show/Focus permitted");}
    else{Event e=null;if(op=="imgui-down")e=new Event{type=EventType.MouseDown,button=0,mousePosition=new Vector2(100,100)};if(op=="imgui-up")e=new Event{type=EventType.MouseUp,button=0,mousePosition=new Vector2(100,100)};if(op=="imgui-keydown")e=new Event{type=EventType.KeyDown,keyCode=KeyCode.K,character='k'};if(op=="imgui-keyup")e=new Event{type=EventType.KeyUp,keyCode=KeyCode.K,character='k'};if(e!=null){bool accepted=view.SendEvent(e);File.AppendAllText(Path.Combine(Root,"internal-events.txt"),op+" accepted="+accepted+"\n");}}
   }
   if(op.StartsWith("queued-")){Event e=null;if(op=="queued-down")e=new Event{type=EventType.MouseDown,button=0,mousePosition=new Vector2(100,100)};if(op=="queued-up")e=new Event{type=EventType.MouseUp,button=0,mousePosition=new Vector2(100,100)};if(op=="queued-keydown")e=new Event{type=EventType.KeyDown,keyCode=KeyCode.K,character='k'};if(op=="queued-keyup")e=new Event{type=EventType.KeyUp,keyCode=KeyCode.K,character='k'};if(e!=null){EditorGUIUtility.QueueGameViewInputEvent(e);File.AppendAllText(Path.Combine(Root,"internal-events.txt"),op+" queued\n");}}
   if(op=="virtual-down"){InputSystem.EnableDevice(p.virtualKeyboard);InputSystem.QueueStateEvent(p.virtualKeyboard,new KeyboardState(Key.Space));InputSystem.Update();}
   if(op=="virtual-up"){InputSystem.QueueStateEvent(p.virtualKeyboard,new KeyboardState());InputSystem.Update();}
   if(op=="mute")p.state.muted=true;
   if(op=="capture"){var cam=Camera.main;if(cam==null)cam=UnityEngine.Object.FindObjectOfType<Camera>();var rt=new RenderTexture(1920,1080,24);cam.targetTexture=rt;cam.Render();var old=RenderTexture.active;RenderTexture.active=rt;var t=new Texture2D(1920,1080,TextureFormat.RGB24,false);t.ReadPixels(new Rect(0,0,1920,1080),0,0);t.Apply();File.WriteAllBytes(Path.Combine(Root,"camera.png"),t.EncodeToPNG());RenderTexture.active=old;cam.targetTexture=null;UnityEngine.Object.DestroyImmediate(rt);UnityEngine.Object.DestroyImmediate(t);}
   if(op=="stop"){EditorApplication.isPlaying=false;}
   Debug.Log("SCRIPT_COMMAND_"+op);
  }catch(Exception error){Debug.LogException(error);}
 }
}
'''

def write_json(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def desktop_snapshot():
    class POINT(ctypes.Structure):_fields_=[('x',ctypes.c_long),('y',ctypes.c_long)]
    point=POINT();user=ctypes.windll.user32
    if not user.GetCursorPos(ctypes.byref(point)):raise OSError('GetCursorPos failed')
    user.GetForegroundWindow.restype=ctypes.c_void_p
    return {'cursor':[point.x,point.y],'foreground_hwnd':user.GetForegroundWindow()}

def wait_for(predicate,seconds=120):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            value=predicate()
            if value:return value
        except (OSError,ValueError,KeyError):pass
        time.sleep(.25)
    raise TimeoutError(f'Condition timeout {seconds}s')

def prepare(root=ROOT,source=SOURCE):
    project=root/'Project'
    if root.exists():raise FileExistsError(f'Refusing overwrite: {root}')
    root.mkdir(parents=True);project.mkdir()
    for folder in ('Packages','ProjectSettings'):shutil.copytree(source/folder,project/folder)
    manifest=project/'Packages/manifest.json';data=json.loads(manifest.read_text(encoding='utf-8-sig'));data['dependencies']['com.unity.inputsystem']='1.7.0';write_json(manifest,data)
    settings=project/'ProjectSettings/ProjectSettings.asset';text=settings.read_text(encoding='utf-8-sig')
    for key,value in [('companyName','SJSBenchmarkScriptCalibration'),('productName','B1ScriptInputOnly'),('activeInputHandler','2')]:text=re.sub(r'(?m)^  '+key+r':.*$','  '+key+': '+value,text)
    settings.write_text(text,encoding='utf-8')
    (project/'Assets/Editor').mkdir(parents=True)
    (project/'Assets/ScriptInputProbe.cs').write_text(RUNTIME,encoding='utf-8')
    (project/'Assets/Editor/ScriptInputCalibration.cs').write_text(EDITOR,encoding='utf-8')
    return project

def command(project,value):
    wait_for(lambda:not (project/'command.txt').exists(),30)
    path=project/'command.tmp';path.write_text(value,encoding='utf-8');path.replace(project/'command.txt')
    wait_for(lambda:not (project/'command.txt').exists(),30)

def state(project):return json.loads((project/'state.json').read_text(encoding='utf-8'))

def port_for(project):
    for file in (Path.home()/'.unity-mcp').glob('unity-mcp-port-*.json'):
        try:
            item=json.loads(file.read_text(encoding='utf-8-sig'))
            if Path(item.get('project_path','')).resolve()==(project/'Assets').resolve():return item['unity_port']
        except (OSError,ValueError):pass

def run(recover=False,attach_pid=None,recover_compile=False):
    if recover_compile:
        project=ROOT/'Project'
        checks=json.loads((ROOT/'offline-compile-report.json').read_text())
        if len(checks)!=2 or any(x['exit_code'] for x in checks):raise RuntimeError('Offline compile validation must pass before this authorized recovery')
        with (ROOT/'compile-recovery-used.json').open('x',encoding='utf-8') as record:json.dump({'reason':'Authorized recovery of initial calibration-only compilation error before managed API available','previous_pid':28424,'avoidable_error':'InputSystem.Update(InputUpdateType) internal; fixed to public Update(); both assemblies offline compiled successfully'},record)
        shutil.copy2(ROOT/'editor.log',ROOT/'editor-initial-compilation-failure.log')
        shutil.copy2(ROOT/'report.json',ROOT/'report-initial-compilation-failure.json')
    elif attach_pid:
        project=ROOT/'Project'
        if not (ROOT/'recovery-used.json').is_file():raise RuntimeError('Attach only to the already authorized recovered calibration instance')
        shutil.copy2(ROOT/'report.json',ROOT/'report-before-driver-attach.json')
        (project/'Assets/ScriptInputProbe.cs').write_text(RUNTIME,encoding='utf-8')
        (project/'Assets/Editor/ScriptInputCalibration.cs').write_text(EDITOR,encoding='utf-8')
    elif recover:
        project=ROOT/'Project'
        if not (ROOT/'package-provenance.json').is_file() or not (project/'Assets/Editor/ScriptInputCalibration.cs').is_file():raise RuntimeError('Recovery requires this disposable project and fixed official package')
        with (ROOT/'recovery-used.json').open('x',encoding='utf-8') as record:json.dump({'reason':'Authorized recovery of initial UPM startup network failure; no ordinary-refresh restart','previous_pid':68156},record)
        (project/'Assets/ScriptInputProbe.cs').write_text(RUNTIME,encoding='utf-8')
        (project/'Assets/Editor/ScriptInputCalibration.cs').write_text(EDITOR,encoding='utf-8')
        shutil.copy2(ROOT/'editor.log',ROOT/'editor-first-startup-failure.log')
        shutil.copy2(ROOT/'report.json',ROOT/'report-first-startup-failure.json')
    else:project=prepare()
    report={'status':'running','project':str(project),'started_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'launches':0,'infrastructure_recovery':recover or bool(attach_pid) or recover_compile}
    observations=[];stop=threading.Event()
    def watch():
        while not stop.is_set():
            observations.append({'utc':datetime.now(timezone.utc).isoformat(),**desktop_snapshot()});stop.wait(.2)
    thread=threading.Thread(target=watch,daemon=True);thread.start()
    report['desktop_before']=desktop_snapshot();write_json(ROOT/'report.json',report)
    try:
        if attach_pid:report['launches']=2;report['pid']=attach_pid;report['driver_attached_without_editor_restart']=True
        else:
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
            process=subprocess.Popen([str(UNITY),'-projectPath',str(project),'-logFile',str(ROOT/'editor.log'),'-executeMethod','ScriptInputCalibration.Boot','-job-worker-count','2'],startupinfo=startup)
            report['launches']=3 if recover_compile else (2 if recover else 1);report['pid']=process.pid
        write_json(ROOT/'report.json',report)
        wait_for(lambda:(project/'ready.json').exists(),1200)
        report['identity']=json.loads((project/'ready.json').read_text());command(project,'setup')
        report['initial']=wait_for(lambda:state(project) if state(project)['frames']>10 else None,180)
        command(project,'ugui');report['after_ugui']=wait_for(lambda:state(project) if state(project)['uguiClicks']>0 else None,30)
        for op in ('imgui-down','imgui-up','imgui-keydown','imgui-keyup'):
            command(project,op);time.sleep(.6)
        time.sleep(2);report['after_imgui']=state(project)
        for op in ('queued-down','queued-up','queued-keydown','queued-keyup'):
            command(project,op);time.sleep(.6)
        time.sleep(2);report['after_queued']=state(project)
        for op in ('virtual-down','virtual-up'):command(project,op);time.sleep(.6)
        time.sleep(2);report['after_virtual']=state(project)
        report['audio_playing']=wait_for(lambda:state(project) if state(project)['rms']>.005 else None,30)
        command(project,'mute');report['audio_muted']=wait_for(lambda:state(project) if state(project)['rms']<.00001 else None,30)
        command(project,'capture');wait_for(lambda:(project/'camera.png').exists(),30)
        spec=importlib.util.spec_from_file_location('calibration_environment',ROOT.parent/'check_environment.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        port=wait_for(lambda:port_for(project),30)
        report['console']=module.request(port,{'type':'read_console','params':{'action':'get','types':['log','warning','error'],'count':100,'format':'detailed'}})
        report['mcp_identity']=module.request(port,{'type':'get_project_info','params':{}})
        report['capabilities']={'ugui_event_chain':report['after_ugui']['uguiClicks']>0 and all(x in report['after_ugui']['uiEvents'] for x in ('pointerDown','pointerUp','pointerClick','onClick')),
           'imgui_gameview_mouse':report['after_imgui']['imguiClicks']>0,'imgui_gameview_key':report['after_imgui']['imguiKeys']>0,
           'legacy_input_internal_key':report['after_imgui']['legacyK']>0,'new_inputsystem_virtual_key':report['after_virtual']['virtualSpace']>0,
           'imgui_queued_mouse':report['after_queued']['imguiClicks']>report['after_imgui']['imguiClicks'],'imgui_queued_key':report['after_queued']['imguiKeys']>report['after_imgui']['imguiKeys'],
           'legacy_input_queued_key':report['after_queued']['legacyK']>report['after_imgui']['legacyK'],
           'virtual_key_updates_legacy_input':report['after_virtual']['legacySpace']>0,'audio_output':True,'audio_mute':True}
        report['status']='completed'
    except Exception as error:report['status']='failed';report['error']=f'{type(error).__name__}: {error}'
    finally:
        try:
            if (project/'ready.json').exists():command(project,'stop');time.sleep(3)
        except Exception as error:report['stop_error']=str(error)
        stop.set();thread.join(timeout=2)
        report['desktop_after']=desktop_snapshot();write_json(ROOT/'desktop-observations.json',observations)
        initial=report['desktop_before'];report['desktop_same_before_after']=initial==report['desktop_after'];report['desktop_observed_changes']=sum(any(x[k]!=initial[k] for k in ('cursor','foreground_hwnd')) for x in observations)
        report['desktop_causality_note']='Read-only samples; user activity can change cursor/focus. No OS input or activation API is called by this harness.'
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        report['evidence_sha256']={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in [project/'state.json',project/'camera.png',project/'internal-events.txt',ROOT/'editor.log',ROOT/'desktop-observations.json'] if p.is_file()}
        write_json(ROOT/'report.json',report)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');parser.add_argument('--recover-startup',action='store_true',help='One explicitly authorized recovery after initial UPM infrastructure failure');parser.add_argument('--recover-compiled-startup',action='store_true',help='Explicitly authorized recovery after calibration compile failure; requires both offline compiles pass');parser.add_argument('--attach-pid',type=int,help='Attach driver to existing recovered calibration Editor; never launches or restarts it');args=parser.parse_args()
    if not args.run:parser.error('--run required; creates independent project and launches Editor once')
    result=run(recover=args.recover_startup,attach_pid=args.attach_pid,recover_compile=args.recover_compiled_startup);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['status']=='completed' else 1)
