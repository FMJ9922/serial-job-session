using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEngine;
using UnityEngine.EventSystems;
namespace B1InputChannel002 {
public class NeutralRun:MonoBehaviour {
 [Serializable]public class KeyResult{public int key,downDelta,heldDelta,upDelta;public string name;public bool uiCaptured,idleNoLeak,oldKeyInactive,passed;}
 [Serializable]public class Report{public string status,unity,project,owner,consumerILBefore,consumerILAfter,consumerSourceBefore,consumerSourceAfter,error,runIdentity,company,product,persistentDataPath;public bool isBatchMode,completed,cleanupVerified,sourceAndILUnchanged,unpatchedIdleNoLeak;public int expectedKeys,observedKeys,unpatchedIdleFrames;public int[] coverageKeys;public string[] inputOriginalBefore,inputOriginalAfter;public KeyResult[] keys;}
 public static string Project,Output,RunIdentity;public static bool Done;public static string Error;
 NeutralConsumer consumer;string beforeIL,beforeSource;string[] originals;readonly List<KeyResult> results=new List<KeyResult>();DateTime started;bool unpatchedIdle;
 string ConsumerPath{get{return Path.Combine(Application.dataPath,"B1Input002/NeutralConsumer.cs");}}
 string HashSource(){return TypedKeyChannel.SHA(File.ReadAllBytes(ConsumerPath));}
 string ConsumerIL(){return string.Join("|",new[]{"Update","OnRebindClicked"}.Select(n=>TypedKeyChannel.IL(typeof(NeutralConsumer).GetMethod(n,BindingFlags.Instance|BindingFlags.NonPublic))).ToArray());}
 void Awake(){started=DateTime.UtcNow;Application.runInBackground=true;consumer=gameObject.AddComponent<NeutralConsumer>();}
 IEnumerator Start(){yield return null;yield return null;try{beforeIL=ConsumerIL();beforeSource=HashSource();originals=TypedKeyChannel.OriginalFingerprints();TypedKeyChannel.Install(Project);}catch(Exception e){Fail(e);yield break;}
  foreach(var key in TypedKeyChannel.Coverage){
   TypedKeyChannel.SetDesired();yield return null;yield return null;
   int captures=consumer.captures,clicks=consumer.buttonClicks;
   var pointer=new PointerEventData(EventSystem.current){button=PointerEventData.InputButton.Left};
   ExecuteEvents.Execute(consumer.rebindButton.gameObject,pointer,ExecuteEvents.pointerDownHandler);
   ExecuteEvents.Execute(consumer.rebindButton.gameObject,pointer,ExecuteEvents.pointerUpHandler);
   ExecuteEvents.Execute(consumer.rebindButton.gameObject,pointer,ExecuteEvents.pointerClickHandler);
   if(!consumer.capturing||consumer.buttonClicks!=clicks+1){Fail(new Exception("Actual UI capture entry failed"));yield break;}
   TypedKeyChannel.SetDesired(key);yield return null;
   bool captured=!consumer.capturing&&consumer.binding==key&&consumer.captures==captures+1;
   TypedKeyChannel.SetDesired();yield return null;yield return null;
   int d=consumer.downs,h=consumer.heldFrames,u=consumer.ups;
   TypedKeyChannel.SetDesired(key);yield return null;yield return null;yield return null;
   int dd=consumer.downs-d,hh=consumer.heldFrames-h;
   TypedKeyChannel.SetDesired();yield return null;
   int uu=consumer.ups-u;d=consumer.downs;h=consumer.heldFrames;u=consumer.ups;
   yield return null;yield return null;
   bool idle=consumer.downs==d&&consumer.heldFrames==h&&consumer.ups==u;
   bool old=true;if(key!=KeyCode.K){TypedKeyChannel.SetDesired(KeyCode.K);yield return null;TypedKeyChannel.SetDesired();yield return null;old=consumer.downs==d&&consumer.heldFrames==h&&consumer.ups==u;}
   var row=new KeyResult{key=(int)key,name=key.ToString(),uiCaptured=captured,downDelta=dd,heldDelta=hh,upDelta=uu,idleNoLeak=idle,oldKeyInactive=old};
   row.passed=captured&&dd==1&&hh==3&&uu==1&&idle&&old;results.Add(row);
   if(!row.passed){Fail(new Exception("Neutral consumer observation mismatch for numeric key "+(int)key));yield break;}
  }
  TypedKeyChannel.SetDesired();yield return null;yield return null;TypedKeyChannel.Cleanup();
  int finalDown=consumer.downs,finalHeld=consumer.heldFrames,finalUp=consumer.ups;yield return null;yield return null;
  unpatchedIdle=consumer.downs==finalDown&&consumer.heldFrames==finalHeld&&consumer.ups==finalUp;
  Finish(unpatchedIdle?null:"Unpatched idle observation changed; possible physical interference or incomplete cleanup, measurement blocked");
 }
 void Update(){if(!Done&&(DateTime.UtcNow-started).TotalSeconds>180)Fail(new TimeoutException("Neutral frame measurement timeout"));}
 void Fail(Exception e){Finish(e.ToString());}
 void Finish(string failure){if(Done)return;Done=true;Error=failure;try{TypedKeyChannel.Cleanup();}catch(Exception e){Error=(Error??"")+"\n"+e;}
  var afterIL=ConsumerIL();var afterSource=HashSource();var afterOriginals=TypedKeyChannel.OriginalFingerprints();
  bool clean=!TypedKeyChannel.Active&&TypedKeyChannel.PatchCount()==0&&TypedKeyChannel.LoopCount()==0&&TypedKeyChannel.VirtualCount()==0;
  bool unchanged=beforeIL==afterIL&&beforeSource==afterSource&&originals!=null&&originals.SequenceEqual(afterOriginals);
  var report=new Report{status=Error==null&&clean&&unchanged&&results.Count==TypedKeyChannel.Coverage.Length?"measurement_completed_pending_independent_review":"blocked",unity=Application.unityVersion,project=Project,company=Application.companyName,product=Application.productName,persistentDataPath=Application.persistentDataPath,runIdentity=RunIdentity,isBatchMode=Application.isBatchMode,owner=TypedKeyChannel.Owner,completed=Error==null,cleanupVerified=clean,sourceAndILUnchanged=unchanged,unpatchedIdleNoLeak=unpatchedIdle,unpatchedIdleFrames=unpatchedIdle?2:0,coverageKeys=TypedKeyChannel.Coverage.Select(k=>(int)k).ToArray(),consumerILBefore=beforeIL,consumerILAfter=afterIL,consumerSourceBefore=beforeSource,consumerSourceAfter=afterSource,inputOriginalBefore=originals,inputOriginalAfter=afterOriginals,expectedKeys=TypedKeyChannel.Coverage.Length,observedKeys=results.Count,keys=results.ToArray(),error=Error};
  Directory.CreateDirectory(Output);File.WriteAllText(Path.Combine(Output,"run-completed.json"),JsonUtility.ToJson(report,true));File.WriteAllText(Path.Combine(Output,"read-trace.json"),"["+string.Join(",",TypedKeyChannel.Trace.Select(x=>JsonUtility.ToJson(x)).ToArray())+"]");
 }
 void OnDisable(){if(!Done)Fail(new Exception("Neutral driver disabled before actual completion"));else TypedKeyChannel.Cleanup();}
}
}
