import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('calibration_script_input',Path(__file__).with_name('calibration_script_input.py'))
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)

class ScriptInputCalibrationTests(unittest.TestCase):
    def test_native_calls_are_read_only(self):
        source=Path(MODULE.__file__).read_text(encoding='utf-8')
        tree=ast.parse(source)
        calls={node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)}
        self.assertTrue({'GetCursorPos','GetForegroundWindow'}<=calls)
        forbidden={'SetCursorPos','SetForegroundWindow','SendInput','mouse_event','keybd_event','AttachThreadInput','ShowWindow','SetWindowPos','BringWindowToTop'}
        self.assertFalse(calls & forbidden)
        self.assertNotIn('.Focus(',MODULE.EDITOR)
        self.assertNotIn('.Show(',MODULE.EDITOR)

    def test_prepare_never_copies_old_probe_or_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'old'
            (source/'Packages').mkdir(parents=True);(source/'ProjectSettings').mkdir()
            (source/'Assets/Editor').mkdir(parents=True)
            (source/'Assets/Editor/UnsafeOldProbe.cs').write_text('old probe')
            (source/'Packages/manifest.json').write_text(json.dumps({'dependencies':{}}))
            (source/'ProjectSettings/ProjectSettings.asset').write_text('  companyName: Old\n  productName: Old\n  activeInputHandler: 0\n')
            original=(source/'ProjectSettings/ProjectSettings.asset').read_text()
            project=MODULE.prepare(root/'new',source)
            self.assertFalse((project/'Assets/Editor/UnsafeOldProbe.cs').exists())
            self.assertFalse((project/'Library').exists())
            self.assertEqual((source/'ProjectSettings/ProjectSettings.asset').read_text(),original)
            self.assertIn('activeInputHandler: 2',(project/'ProjectSettings/ProjectSettings.asset').read_text())
            with self.assertRaises(FileExistsError):MODULE.prepare(root/'new',source)

if __name__=='__main__':unittest.main()
