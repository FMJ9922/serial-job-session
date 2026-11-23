// Judge-only typed Legacy Input channel. This does not implement game actions.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using HarmonyLib;
using UnityEngine;
using UnityEngine.LowLevel;

namespace B1InputChannel002 {
public static class TypedKeyChannel {
 public const string Owner="b1.calibration.002.typed-keycode-only";
 public static readonly KeyCode[] Coverage=Enum.GetValues(typeof(KeyCode)).Cast<KeyCode>().Distinct().Where(k=>(int)k>0&&(int)k<(int)KeyCode.Mouse0).OrderBy(k=>(int)k).ToArray();
 static readonly HashSet<KeyCode> allowed=new HashSet<KeyCode>(Coverage),desired=new HashSet<KeyCode>(),held=new HashSet<KeyCode>(),down=new HashSet<KeyCode>(),up=new HashSet<KeyCode>();
 static Harmony harmony; static int committed=-1; public static bool Active{get;private set;}
 [Serializable] public class Read { public int frame,key;public string method;public bool value; }
 public static readonly List<Read> Trace=new List<Read>();
 public static MethodInfo[] Targets(){return new[]{"GetKey","GetKeyDown","GetKeyUp"}.Select(n=>typeof(Input).GetMethod(n,new[]{typeof(KeyCode)})).ToArray();}
 public static string SHA(byte[] bytes){using(var s=SHA256.Create())return BitConverter.ToString(s.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
 public static string IL(MethodInfo method){var body=method.GetMethodBody();return body==null?"NO_MANAGED_BODY":SHA(body.GetILAsByteArray());}
 public static string[] OriginalFingerprints(){return Targets().Select(m=>m.DeclaringType.FullName+"."+m+"|"+m.Module.ModuleVersionId+"|"+IL(m)+"|"+SHA(File.ReadAllBytes(m.Module.Assembly.Location))).ToArray();}
 static bool Prefix(KeyCode __0,ref bool __result,MethodBase __originalMethod){
  if(!Active||!allowed.Contains(__0))return true;
  __result=__originalMethod.Name=="GetKey"?held.Contains(__0):committed==Time.frameCount&&(__originalMethod.Name=="GetKeyDown"?down.Contains(__0):up.Contains(__0));
  if(Trace.Count>=1000000)throw new InvalidOperationException("Bounded input trace overflow");
  Trace.Add(new Read{frame=Time.frameCount,key=(int)__0,method=__originalMethod.Name,value=__result});return false;
 }
 static void Commit(){committed=Time.frameCount;down.Clear();up.Clear();foreach(var key in Coverage){bool a=held.Contains(key),b=desired.Contains(key);if(!a&&b)down.Add(key);if(a&&!b)up.Add(key);}held.Clear();held.UnionWith(desired);}
 static bool Insert(ref PlayerLoopSystem node){if(node.subSystemList==null)return false;var list=node.subSystemList.ToList();for(int i=0;i<list.Count;i++){var child=list[i];if(child.type==typeof(UnityEngine.PlayerLoop.Update.ScriptRunBehaviourUpdate)){list.Insert(i,new PlayerLoopSystem{type=typeof(TypedKeyChannel),updateDelegate=Commit});node.subSystemList=list.ToArray();return true;}if(Insert(ref child)){list[i]=child;node.subSystemList=list.ToArray();return true;}}return false;}
 static void Remove(ref PlayerLoopSystem node){if(node.subSystemList==null)return;var kept=new List<PlayerLoopSystem>();foreach(var original in node.subSystemList){var child=original;if(child.type==typeof(TypedKeyChannel))continue;Remove(ref child);kept.Add(child);}node.subSystemList=kept.ToArray();}
 static int CountLoop(PlayerLoopSystem node){return (node.type==typeof(TypedKeyChannel)?1:0)+(node.subSystemList==null?0:node.subSystemList.Sum(CountLoop));}
 public static int LoopCount(){return CountLoop(PlayerLoop.GetCurrentPlayerLoop());}
 public static int PatchCount(){return Targets().Count(m=>{var p=Harmony.GetPatchInfo(m);return p!=null&&p.Owners.Contains(Owner);});}
 public static int VirtualCount(){return desired.Count+held.Count+down.Count+up.Count;}
 public static void Install(string expectedProject){
  if(Application.unityVersion!="2022.3.62f2"||Path.GetFullPath(Path.GetDirectoryName(Application.dataPath))!=Path.GetFullPath(expectedProject))throw new InvalidOperationException("Version/project identity mismatch");
  if(!Application.isPlaying||Active||LoopCount()!=0||PatchCount()!=0)throw new InvalidOperationException("Channel is not clean or not playing");
  foreach(var method in Targets()){if(method==null)throw new MissingMethodException("Typed Input overload missing");var p=Harmony.GetPatchInfo(method);if(p!=null&&p.Owners.Any())throw new InvalidOperationException("Foreign input patch present");}
  // Confirm the real Legacy Input backend is callable before interception.
  Input.GetKey(KeyCode.K);Input.GetKeyDown(KeyCode.K);Input.GetKeyUp(KeyCode.K);
  Trace.Clear();harmony=new Harmony(Owner);
  try{foreach(var method in Targets())harmony.Patch(method,prefix:new HarmonyMethod(typeof(TypedKeyChannel),"Prefix"));var loop=PlayerLoop.GetCurrentPlayerLoop();if(!Insert(ref loop))throw new InvalidOperationException("PlayerLoop update phase missing");PlayerLoop.SetPlayerLoop(loop);Active=true;if(PatchCount()!=3||LoopCount()!=1)throw new InvalidOperationException("Incomplete channel installation");}catch{Cleanup();throw;}
 }
 public static void SetDesired(params KeyCode[] keys){if(!Active||keys.Any(k=>!allowed.Contains(k)))throw new InvalidOperationException("Key outside calibrated declared keyboard set");desired.Clear();desired.UnionWith(keys);}
 public static void Cleanup(){Active=false;desired.Clear();held.Clear();down.Clear();up.Clear();committed=-1;try{if(harmony!=null)harmony.UnpatchAll(Owner);}finally{harmony=null;var loop=PlayerLoop.GetCurrentPlayerLoop();Remove(ref loop);PlayerLoop.SetPlayerLoop(loop);}if(PatchCount()!=0||LoopCount()!=0||VirtualCount()!=0)throw new InvalidOperationException("Input cleanup verification failed");}
}
}
