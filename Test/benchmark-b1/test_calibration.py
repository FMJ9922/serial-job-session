import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('calibration',Path(__file__).with_name('calibration.py'))
calibration=importlib.util.module_from_spec(spec);spec.loader.exec_module(calibration)

class CalibrationTests(unittest.TestCase):
    def test_prepare_is_isolated_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source'
            for part in ('Assets','Packages','ProjectSettings'):(source/part).mkdir(parents=True)
            settings=source/'ProjectSettings/ProjectSettings.asset'
            original='  companyName: Original\n  productName: Original\n'
            settings.write_text(original)
            (source/'Library').mkdir()
            project=calibration.prepare(root/'calibration',source)
            self.assertEqual(settings.read_text(),original)
            self.assertFalse((project/'Library').exists())
            self.assertIn('B1CalibrationOnly',(project/'ProjectSettings/ProjectSettings.asset').read_text())
            self.assertTrue((project/'Assets/Editor/B1Calibration.cs').exists())
            with self.assertRaises(FileExistsError):calibration.prepare(root/'calibration',source)

    def test_wait_times_out(self):
        with self.assertRaises(TimeoutError):calibration.wait_for(lambda:False,timeout=.01,interval=.001)

if __name__=='__main__':unittest.main()
