# Typed KeyCode calibration002 — staged, unexecuted

This new package is a neutral measurement proposal. It does not grant a score,
change B1 requirements, repair a contestant, or claim a successful dynamic run.
Frozen input-channel001 remains immutable. Its bounded K/F13/F14 result does
not establish arbitrary keys, Input(string), anyKey, axes or Input System.

Target: `E:/Test/.benchmark-admin/b1/input-channel-002-neutral-attempt2/Project` only.
Namespace: `B1InputChannel002`. Company/product: `B1JudgeNeutral` /
`TypedKeyCode002NeutralAttempt2`; this isolates neutral PlayerPrefs/save names.
The entire target parent must be absent. The stage command rejects junctions,
overwrites and changed approved sources. No original project or Library from
any of the six contestants is copied. Local bundled UGUI and the exact001
Harmony DLL and licenses provide the dependencies without a network fetch.

## Concrete approval and single-run sequence

1. Root reviews all four C# files and `stage_neutral.py`, then runs `plan`.
   The plan hashes every staged .cs/.py/.md file. Root's exact-source approval
   has schema `b1-input002-materialization-approval-v1`, `approved:true`, and
   the exact `source_manifest_sha256`, `project`, `unity_exe`, `unity` from plan.
   This is AI judge authorization, not a new human approval requirement.
2. `materialize --approval <file> --approval-sha256 <SHA>` creates the entirely
   new target after verifying the original DLL/licenses and actual executable
   ProductVersion **2022.3.62f2_7670c08855a9**, separately from runtime Application.unityVersion **2022.3.62f2**. The folder happens to be named2021.3.36f2;
   that folder name is not evidence of the executable version. This command
   never launches an Editor.
3. After root approves the exact launch, start one new neutral Editor Hidden in explicit batch mode:

```powershell
$NeutralUnity = 'E:/Unity/2021.3.36f2/Editor/Unity.exe'
$NeutralProject = 'E:/Test/.benchmark-admin/b1/input-channel-002-neutral-attempt2/Project'
Start-Process -WindowStyle Hidden -FilePath $NeutralUnity -ArgumentList @('-batchmode', '-projectPath', $NeutralProject, '-executeMethod', 'B1InputChannel002.NeutralEntry.Start', '-logFile', 'E:/Test/.benchmark-admin/b1/input-channel-002-neutral-attempt2/editor.log') -PassThru
```

   No existing Editor is restarted. Merely importing the package cannot run a
   calibration: only the explicit executeMethod arms it. No desktop input,
   cursor, window activation, Show or Focus APIs are used. UGUI events are
   delivered to the real runtime Button through ExecuteEvents; the original
   callback sets capture mode. No adapter sets binding or consumer counters.
4. Observe actual `run-completed.json` **and** `terminal-idle.json`. Scheduling
   Play Mode or a player-loop update is not completion. The pump bounds the run,
   stops Play Mode and records actual idle/cleanup; then writes an exit invocation and calls normal EditorApplication.Exit(0) in batch mode.
   Failure/timeout is blocked infrastructure, not a business requirement failure.
   `validate` checks coverage, counter observations and consistent actual read
   traces, and still returns `measurement_completed_pending_independent_review`.
5. A different reviewer examines actual source/call/command logs, before/after
   fixture/DLL hashes and IL, original method identities, all per-key read
   traces, actual key coverage and the idle artifact. Only after this independent
   review may root freeze input-channel002 and uniformly provide it to six judges.
   Retain failed incidents; do not silently overwrite a run or call a queued
   action successful. Do not activate this adapter in a formal case in this stage.

## Finite claims and original consumers

Coverage is every **unique defined keyboard KeyCode numeric value** satisfying
`None < key < Mouse0` on the actual2022.3.62f2 runtime. Aliases are deduplicated.
Mouse/joystick values and None are rejected by SetDesired. Outside-coverage
typed reads delegate to the original API; physical interference may therefore
block capture and must not be misclassified as a game failure. No string,
anyKey, text event, axis, Input System, window focus or OS input is calibrated.

Harmony owner `b1.calibration.002.typed-keycode-only` patches exactly three
typed overloads: GetKey, GetKeyDown, GetKeyUp(KeyCode). Desired state commits
before ScriptRunBehaviourUpdate. Down/up occur for one committed frame, held
persists, and repeated reads in one frame must agree. Each keyboard value is
captured by the unchanged neutral Update's Enum.GetValues(KeyCode) path, then
drives one down, three held frames, one up, two idle frames and an old-K negative
check through the current binding. This tests an actual consumer and UI capture;
it does not assign actions or bindings directly or replace original Update.

Consumer source and Update/callback IL, original Input method IL/module MVID/
assembly bytes and exact Harmony ownership are checked before/after. Cleanup
removes only this owner's patches and player-loop node, preserving other loop
nodes. All virtual sets clear; two additional unpatched consumer frames must
remain idle. Unexpected external physical activity makes the neutral run
blocked/inconclusive, not a false no-leak claim. A trace limit, watchdog and
reload/Play Mode exit cleanup bound interception.

The calibration is **not** business evidence: conflict/cancel/default behavior,
actual game actions, settings persistence and a genuine consolidated restart
remain requirements of each original game and its unchanged100-item audit.

The first launch uses `-batchmode`, not `-nographics`: this fixture needs an
actual Canvas/EventSystem player loop, with no pixel/audio claims. Runtime and
actual-idle reports record `Application.isBatchMode` and a shared unique run ID;
the validator requires both. No interactive fallback/restart is authorized.
The software frame watchdog does not bound a blocked main thread or compilation.
Root must apply an external observation deadline (900 seconds from launch);
missing completion then remains blocked for independent diagnosis, never pass.
After normal Stop Play and actual idle are recorded, the batch entry writes
`exit-invocation.json` (actual PID/UTC/run ID, no success assertion) immediately
before a direct normal EditorApplication.Exit(0), without delayCall. This also
applies to a failed calibration after cleanup/actual idle; failure remains blocked.
The invocation is not exit completion: verify
the exact PID is absent using a read-only process census before claiming closure.
No existing Editor may be restarted or closed by this neutral procedure.

## Preserved first failed attempt and revised module manifest

The first neutral run was blocked before calibration: actual Unity compilation
reported missing JsonUtility because jsonserialize was not declared. Its project,
trial, source snapshot and logs remain untouched; offline compilation had broader
engine references and did not prove the real project manifest was sufficient.
This revision targets a distinct attempt2 directory and private product. It adds
original bundled jsonserialize 1.0.0, plus explicit ui/imgui 1.0.0 (already UGUI
dependencies), validating each bundled package identity before writing. Typed
Input uses UnityEngine.InputLegacyModule, which has no separate builtin package
entry here; no fictitious module or network dependency is added. The consumer and
input interception logic are unchanged. A new independently reviewed single batch
launch is required; the failed attempt grants no dynamic input claim.
