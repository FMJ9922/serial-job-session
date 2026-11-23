"""Disposable Unity audit-channel calibration. Never touches contestant projects.

Run: python Test/benchmark-b1/calibration.py --run
Evidence: E:/Test/.benchmark-admin/b1-calibration/report.json
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import time

DEFAULT_ROOT = Path('E:/Test/.benchmark-admin/b1-calibration')
DEFAULT_SOURCE = Path('E:/Test/Demo/AITest')
DEFAULT_EDITOR = Path('E:/Unity/2021.3.36f2/Editor/Unity.exe')

RUNTIME = r'''
using UnityEngine;
using System.IO;
public class B1Probe : MonoBehaviour {
 public int clicks, keys; public float rms; public bool muted;
 float next; AudioSource source; float[] samples = new float[1024];
 void Start() {
  Application.runInBackground=true;
  var cam=new GameObject("CalibrationCamera").AddComponent<Camera>();
  cam.backgroundColor=new Color(.1f,.25f,.3f); cam.clearFlags=CameraClearFlags.SolidColor;
  cam.gameObject.AddComponent<AudioListener>();
  source=gameObject.AddComponent<AudioSource>();
  var clip=AudioClip.Create("Calibration440Hz",44100,1,44100,false);
  var data=new float[44100]; for(int i=0;i<data.Length;i++) data[i]=.2f*Mathf.Sin(i*2*Mathf.PI*440/44100);
  clip.SetData(data,0);source.clip=clip;source.loop=true;source.Play();
  PlayerPrefs.SetString("B1Probe","B1-persisted-2026");PlayerPrefs.Save();
  Debug.Log("B1_RUNTIME_READY");
 }
 void OnGUI() {
  if(GUI.Button(new Rect(0,0,Screen.width,Screen.height),"B1 Calibration Button")) {clicks++; Debug.Log("B1_UI_CLICK");}
  GUI.Label(new Rect(20,110,600,70),"Clicks="+clicks+" Keys="+keys+" RMS="+rms);
 }
 void Update() {
  if(Input.GetKeyDown(KeyCode.K)) {keys++;Debug.Log("B1_NATIVE_KEY");}
  if(Time.unscaledTime<next)return; next=Time.unscaledTime+.25f;
  source.volume=muted?0:1;
  AudioListener.GetOutputData(samples,0);float total=0;foreach(float s in samples)total+=s*s;
  rms=Mathf.Sqrt(total/samples.Length);
  File.WriteAllText(Path.Combine(Directory.GetParent(Application.dataPath).FullName,"probe.json"),JsonUtility.ToJson(this));
 }
}
'''

EDITOR = r'''
using UnityEngine;using UnityEditor;using UnityEditor.SceneManagement;
using System;using System.IO;using System.Reflection;
[InitializeOnLoad] public static class B1Calibration {
 static string Root=>Directory.GetParent(Application.dataPath).FullName;
 static B1Calibration(){SessionState.SetBool("MCPForUnity.StartupConfigRewrite.Ran",true);EditorApplication.update+=Tick;}
 public static void Boot(){SessionState.SetBool("MCPForUnity.StartupConfigRewrite.Ran",true);MCPForUnity.Editor.McpCiBoot.StartStdioForCi();
  File.WriteAllText(Path.Combine(Root,"ready.json"),JsonUtility.ToJson(new Info{version=Application.unityVersion,project=Root,persisted=PlayerPrefs.GetString("B1Probe","")}));}
 [Serializable] class Info{public string version,project,persisted;}
 static EditorWindow View(){var w=EditorWindow.GetWindow(Type.GetType("UnityEditor.GameView,UnityEditor"));w.Show();w.maximized=true;w.Focus();return w;}
 static void Size(){
  var asm=typeof(Editor).Assembly;var groupType=asm.GetType("UnityEditor.GameViewSizeGroupType");
  var sizesType=asm.GetType("UnityEditor.GameViewSizes");var singleton=typeof(ScriptableSingleton<>).MakeGenericType(sizesType);
  var sizes=singleton.GetProperty("instance").GetValue(null);
  var group=sizesType.GetMethod("GetGroup").Invoke(sizes,new object[]{Enum.Parse(groupType,"Standalone")});
  var sizeType=asm.GetType("UnityEditor.GameViewSize");var kind=asm.GetType("UnityEditor.GameViewSizeType");
  var size=Activator.CreateInstance(sizeType,new object[]{Enum.Parse(kind,"FixedResolution"),1920,1080,"B1 1920x1080"});
  group.GetType().GetMethod("AddCustomSize").Invoke(group,new object[]{size});
  int count=(int)group.GetType().GetMethod("GetTotalCount").Invoke(group,null);
  var view=View();view.GetType().GetProperty("selectedSizeIndex",BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic).SetValue(view,count-1);
 }
 static void Tick(){
  string command=Path.Combine(Root,"command.txt");if(!File.Exists(command)||EditorApplication.isCompiling)return;
  string op;try{op=File.ReadAllText(command).Trim();File.Delete(command);}catch(IOException){return;}
  try{
   if(op=="setup"){EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);new GameObject("B1Probe").AddComponent<B1Probe>();EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),"Assets/B1Calibration.unity");Size();EditorApplication.isPlaying=true;}
   if(op=="focus")View().Focus();
   if(op=="ui"){var v=View();v.SendEvent(new Event{type=EventType.MouseDown,button=0,mousePosition=new Vector2(80,70)});v.SendEvent(new Event{type=EventType.MouseUp,button=0,mousePosition=new Vector2(80,70)});}
   if(op=="mute")UnityEngine.Object.FindObjectOfType<B1Probe>().muted=true;
   if(op=="capture")ScreenCapture.CaptureScreenshot(Path.Combine(Root,"capture.png"));
   if(op=="quit"){EditorApplication.isPlaying=false;EditorApplication.delayCall+=()=>EditorApplication.Exit(0);}
   Debug.Log("B1_COMMAND_"+op);
  }catch(Exception e){Debug.LogException(e);}
 }
}
'''

def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def prepare(root: Path, source: Path):
    project = root/'Project'
    if project.exists():
        raise FileExistsError(f'Refusing to overwrite calibration project: {project}')
    project.mkdir(parents=True)
    for directory in ('Assets','Packages','ProjectSettings'):
        shutil.copytree(source/directory, project/directory)
    settings=project/'ProjectSettings/ProjectSettings.asset'
    text=settings.read_text(encoding='utf-8-sig')
    import re
    text=re.sub(r'(?m)^  companyName:.*$', '  companyName: SJSBenchmarkCalibration', text)
    text=re.sub(r'(?m)^  productName:.*$', '  productName: B1CalibrationOnly', text)
    settings.write_text(text,encoding='utf-8')
    (project/'Assets/B1Probe.cs').write_text(RUNTIME,encoding='utf-8')
    (project/'Assets/Editor').mkdir(exist_ok=True)
    (project/'Assets/Editor/B1Calibration.cs').write_text(EDITOR,encoding='utf-8')
    return project

def wait_for(predicate, timeout=600, interval=1):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            result=predicate()
            if result:return result
        except (OSError,ValueError):pass
        time.sleep(interval)
    raise TimeoutError(f'Calibration condition not met within {timeout}s')

def read_probe(project):
    return json.loads((project/'probe.json').read_text(encoding='utf-8-sig'))

def command(project, value):
    wait_for(lambda:not (project/'command.txt').exists(),30)
    temporary=project/'command.tmp'
    temporary.write_text(value,encoding='utf-8')
    temporary.replace(project/'command.txt')

def launch(editor,project,root,number):
    # Interactive calibration requires a visible native window for real input.
    # Background helpers remain hidden; this is the explicitly requested Editor probe.
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=1
    return subprocess.Popen([str(editor),'-projectPath',str(project),'-logFile',str(root/f'editor-{number}.log'),'-executeMethod','B1Calibration.Boot','-job-worker-count','2'],startupinfo=startup)

def native_key(pid, click=False):
    """OS keyboard event, not a direct call to the game handler."""
    user=ctypes.windll.user32
    windows=[]
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    def inspect(hwnd,_):
        process=ctypes.c_ulong();user.GetWindowThreadProcessId(hwnd,ctypes.byref(process))
        if process.value==pid and user.IsWindowVisible(hwnd):windows.append(hwnd)
        return True
    user.EnumWindows(callback(inspect),0)
    if not windows:raise RuntimeError('No visible calibration Editor window for native input')
    hwnd=windows[0]
    current=ctypes.windll.kernel32.GetCurrentThreadId()
    foreground=user.GetWindowThreadProcessId(user.GetForegroundWindow(),None)
    user.AttachThreadInput(current,foreground,True)
    try:
        user.ShowWindow(hwnd,9)
        user.SetWindowPos(hwnd,-1,0,0,0,0,0x43)
        user.BringWindowToTop(hwnd);user.SetForegroundWindow(hwnd)
    finally:user.AttachThreadInput(current,foreground,False)
    time.sleep(1)
    actual=ctypes.c_ulong();user.GetWindowThreadProcessId(user.GetForegroundWindow(),ctypes.byref(actual))
    if actual.value!=pid:raise RuntimeError('Foreground focus was denied; refusing to inject into another application')
    from ctypes import wintypes
    rect=wintypes.RECT();user.GetWindowRect(windows[0],ctypes.byref(rect))
    user.SetCursorPos((rect.left+rect.right)//2,(rect.top+rect.bottom)//2)
    user.mouse_event(2,0,0,0,0);time.sleep(.1);user.mouse_event(4,0,0,0,0)
    if click:return
    time.sleep(.5)
    scan=user.MapVirtualKeyW(0x4B,0)
    user.keybd_event(0x4B,scan,0,0);time.sleep(1);user.keybd_event(0x4B,scan,2,0)

def bridge(project):
    for registry in (Path.home()/'.unity-mcp').glob('unity-mcp-port-*.json'):
        try:
            item=json.loads(registry.read_text(encoding='utf-8-sig'))
            if Path(item.get('project_path','')).resolve()==(project/'Assets').resolve():return item['unity_port']
        except (OSError,ValueError):pass

def run(root=DEFAULT_ROOT,source=DEFAULT_SOURCE,editor=DEFAULT_EDITOR,retry=False):
    root.mkdir(parents=True,exist_ok=True)
    if retry:
        project=root/'Project'
        if not (project/'Assets/Editor/B1Calibration.cs').is_file():raise ValueError('Retry requires existing calibration harness')
        (project/'Assets/B1Probe.cs').write_text(RUNTIME,encoding='utf-8')
        (project/'Assets/Editor/B1Calibration.cs').write_text(EDITOR,encoding='utf-8')
        for name in ('ready.json','probe.json','command.txt'):(project/name).unlink(missing_ok=True)
        if (root/'report.json').is_file():shutil.copy2(root/'report.json',root/f'report-attempt-{time.time_ns()}.json')
    else:
        project=prepare(root,source)
    report={'project':str(project),'checks':{},'status':'running','started_utc':datetime.now(timezone.utc).isoformat(),
            'calibration_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'input_route':'Windows native mouse and keyboard; foreground PID guarded',
            'audio_route':'AudioListener.GetOutputData RMS (Unity mixer output; not OS speaker loopback)'}
    write_json(root/'report.json',report)
    process=None
    try:
        process=launch(editor,project,root,1)
        wait_for(lambda:(project/'ready.json').exists(),900)
        info=json.loads((project/'ready.json').read_text())
        report['identity']=info
        report['checks']['identity']=info['version']=='2022.3.62f2' and Path(info['project']).resolve()==project.resolve()
        module=importlib.util.spec_from_file_location('b1_environment',root.parent/'check_environment.py')
        environment=importlib.util.module_from_spec(module);module.loader.exec_module(environment)
        port=wait_for(lambda:bridge(project),60)
        report['mcp_identity']=environment.request(port,{'type':'get_project_info','params':{}})
        report['checks']['mcp_identity']=Path(report['mcp_identity']['result']['data']['projectRoot']).resolve()==project.resolve()
        command(project,'setup')
        wait_for(lambda:read_probe(project),120)
        command(project,'focus');time.sleep(2);native_key(process.pid,click=True)
        wait_for(lambda:read_probe(project)['clicks']>0,30)
        report['checks']['ui_event']=True
        command(project,'focus');time.sleep(2);native_key(process.pid)
        wait_for(lambda:read_probe(project)['keys']>0,30)
        report['checks']['native_keyboard']=True
        report['audio_playing']=wait_for(lambda:(p if (p:=read_probe(project))['rms']>.005 else None),30)
        report['checks']['audio_nonzero']=True
        capture=project/'capture.png'
        capture.unlink(missing_ok=True)
        command(project,'capture')
        wait_for(lambda:capture.is_file() and capture.read_bytes().endswith(b'IEND\xaeB`\x82'),30)
        import struct
        width,height=struct.unpack('>II',(project/'capture.png').read_bytes()[16:24])
        report['capture']={'path':str(project/'capture.png'),'width':width,'height':height,
                           'sha256':hashlib.sha256((project/'capture.png').read_bytes()).hexdigest()}
        report['checks']['gameview_1920x1080']=(width,height)==(1920,1080)
        command(project,'mute');time.sleep(2)
        report['audio_muted']=read_probe(project)
        report['checks']['audio_muted']=report['audio_muted']['rms']<.00001
        report['console']=environment.request(port,{'type':'read_console','params':{'action':'get','types':['log','warning','error'],'count':100,'format':'detailed'}})
        report['checks']['console']='B1_NATIVE_KEY' in json.dumps(report['console']) and 'B1_UI_CLICK' in json.dumps(report['console'])
        command(project,'quit');process.wait(timeout=60)
        (project/'ready.json').unlink()
        process=launch(editor,project,root,2)
        wait_for(lambda:(project/'ready.json').exists(),300)
        report['reopened']=json.loads((project/'ready.json').read_text())
        report['checks']['editor_reopen_persistence']=report['reopened']['persisted']=='B1-persisted-2026'
        command(project,'quit');process.wait(timeout=60)
        report['status']='passed' if all(report['checks'].values()) else 'failed'
    except Exception as error:
        report['status']='failed';report['error']=f'{type(error).__name__}: {error}'
    finally:
        if process and process.poll() is None:
            try:command(project,'quit');process.wait(timeout=30)
            except Exception:process.terminate()
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        report['editor_stopped']=process is None or process.poll() is not None
        write_json(root/'report.json',report)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='Create and run disposable calibration; refuses overwrite')
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--retry',action='store_true',help='Retry the existing disposable harness only; archive prior report')
    args=parser.parse_args()
    if not args.run:parser.error('--run is required to launch Unity')
    result=run(root=args.root,retry=args.retry);print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='passed' else 1)
