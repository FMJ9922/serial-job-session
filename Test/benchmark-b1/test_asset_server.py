"""Offline scope tests: no network, no credentials, no generated game assets."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required")
class AssetScopeTests(unittest.TestCase):
    def test_scope_and_symlinks(self):
        module_url = Path(__file__).with_name("asset_server.mjs").resolve().as_uri()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            image = project / "own.png"
            image.write_bytes(b"local-image-test")
            outside = root / "other.png"
            outside.write_bytes(b"not-own-image")
            script = """
import { scopedInput, isWithin, noLinks } from MODULE;
const project = PROJECT;
const outside = OUTSIDE;
const assert = (value) => { if (!value) throw new Error('Assertion failed'); };
const rejects = async (source) => { let failed = false; try { await scopedInput(project, source); } catch { failed = true; } assert(failed); };
assert(isWithin(project, project));
assert(!isWithin(project, project + '-sibling'));
assert(!isWithin(project, outside));
assert((await scopedInput(project, 'own.png')).startsWith('data:image/png;base64,'));
await rejects(outside);
await rejects('../other.png');
await rejects('https://example.com/private.png');
await rejects('data:image/png;base64,private');
await rejects('missing.png');
await noLinks(project);
console.log('scope-ok');
""".replace("MODULE", json.dumps(module_url)).replace("PROJECT", json.dumps(str(project))).replace("OUTSIDE", json.dumps(str(outside)))
            result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("scope-ok", result.stdout)
            link = project / "linked.png"
            try:
                link.symlink_to(outside)
            except OSError:
                return  # Windows may require privilege; other scope checks still ran.
            script = f"import {{scopedInput}} from {json.dumps(module_url)}; try {{ await scopedInput({json.dumps(str(project))}, 'linked.png'); process.exit(7); }} catch {{ console.log('link-rejected'); }}"
            result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("link-rejected", result.stdout)


if __name__ == "__main__":
    unittest.main()
