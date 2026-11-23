"""Compile staged code to a temporary assembly; never load Unity or use a project."""
from pathlib import Path
import json
import subprocess
import tempfile
import stage_neutral as stage

def main():
 engine=stage.UNITY.parent/'Data';old=stage.OLD
 manifest=old/'manifest.json'
 if stage.digest(manifest.read_bytes())!='5baf99691a4822ee935595002daff1900e0b550b53c31e04e72b0bd6af108bd7':raise ValueError('Original001 manifest changed')
 rsp=old/'offline/Assembly-CSharp.rsp';expected=json.loads(manifest.read_bytes())['files']['offline/Assembly-CSharp.rsp']
 if stage.digest(rsp.read_bytes())!=expected:raise ValueError('Original compiler reference list changed')
 for n,h in stage.PINS.items():
  if stage.digest((old/n).read_bytes())!=h:raise ValueError('Frozen001 dependency changed')
 refs=[line for line in rsp.read_text().splitlines() if line.startswith('-r:"E:/Unity/')]
 ui=engine/'Resources/PackageManager/ProjectTemplates/libcache/com.unity.template.3d-8.1.3/ScriptAssemblies/UnityEngine.UI.dll'
 refs+=['-r:"'+str(old/'0Harmony.dll')+'"','-r:"'+str(ui)+'"']
 with tempfile.TemporaryDirectory(prefix='b1-input002-compile-') as tmp:
  root=Path(tmp);args=root/'compile.rsp';output=root/'NeutralDraft.dll'
  args.write_text('\n'.join(['-target:library','-nostdlib+','-langversion:9','-out:"'+str(output)+'"',*refs,
    *['"'+str(stage.HERE/n)+'"' for n in stage.SOURCES]]))
  run=subprocess.run([str(engine/'NetCoreRuntime/dotnet.exe'),str(engine/'DotNetSdkRoslyn/csc.dll'),'-noconfig','@'+str(args)],
   capture_output=True,encoding='utf-8',timeout=60)
  print(json.dumps({'scope':'offline compile only; no assembly execution or Unity/project activity',
   'exit_code':run.returncode,'assembly_emitted':output.is_file(),
   'source_sha256':{n:stage.digest((stage.HERE/n).read_bytes()) for n in stage.SOURCES},
   'compiler_sha256':stage.digest((engine/'DotNetSdkRoslyn/csc.dll').read_bytes()),
   'ui_reference_sha256':stage.digest(ui.read_bytes()),'stdout':run.stdout,'stderr':run.stderr,
   'dynamic_channel_calibrated':False},indent=2))
  if run.returncode or not output.is_file():raise SystemExit(1)
if __name__=='__main__':main()
