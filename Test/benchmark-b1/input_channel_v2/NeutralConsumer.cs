// Frozen neutral consumer, never patched or changed during calibration.
using System;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;
namespace B1InputChannel002 {
public class NeutralConsumer:MonoBehaviour {
 public KeyCode binding=KeyCode.K;public int downs,heldFrames,ups,captures,buttonClicks;public bool capturing;public Button rebindButton;
 void Start(){
  if(EventSystem.current==null)new GameObject("NeutralEventSystem",typeof(EventSystem));
  var canvas=new GameObject("NeutralCanvas",typeof(Canvas)).GetComponent<Canvas>();canvas.renderMode=RenderMode.ScreenSpaceOverlay;
  var target=new GameObject("NeutralRebindButton",typeof(RectTransform),typeof(Image),typeof(Button));target.transform.SetParent(canvas.transform,false);
  rebindButton=target.GetComponent<Button>();rebindButton.onClick.AddListener(OnRebindClicked);
 }
 void OnRebindClicked(){buttonClicks++;capturing=true;}
 void Update(){
  if(capturing){foreach(KeyCode key in Enum.GetValues(typeof(KeyCode)))if(Input.GetKeyDown(key)){binding=key;capturing=false;captures++;return;}return;}
  if(Input.GetKeyDown(binding))downs++;
  if(Input.GetKey(binding))heldFrames++;
  if(Input.GetKeyUp(binding))ups++;
 }
}
}
