"""Check the export workflow's verdicts on small archive fixtures."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile


class ExportWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github/workflows/release.yml").read_text(encoding="utf-8")
        step = workflow.split("      - name: Check export versions and release payload\n", 1)[1]
        script = step.split("        shell: python\n        run: |\n", 1)[1]
        cls.check = textwrap.dedent(script.split("\n      - name:", 1)[0])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "assets").mkdir()
        self.manifest = {
            "dependencies": ["gdal"],
            "overrides": [
                {"name": "gdal", "version": "3.8.4"},
                {"name": "netcdf-c", "version": "4.8.1", "port-version": 4},
            ],
        }
        self.ports = [("gdal", "3.8.4", 0), ("netcdf-c", "4.8.1", 4)]
        self.extra_files = []

    def verdict(self):
        (self.root / "vcpkg.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        status = "\n\n".join(
            f"Package: {name}\nVersion: {version}\nPort-Version: {revision}\n"
            "Architecture: x64-windows-avx2\nStatus: install ok installed"
            for name, version, revision in self.ports
        )
        with zipfile.ZipFile(self.root / "assets/aidm-windows-deps.zip", "w") as archive:
            archive.writestr("aidm-windows-deps/installed/vcpkg/status", status)
            for name in self.extra_files:
                archive.writestr(name, "fixture")
        return subprocess.run([sys.executable, "-c", self.check], cwd=self.root,
                              capture_output=True, text=True, check=False)

    def test_transitive_override_is_checked_and_accepted(self):
        result = self.verdict()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Checked 2 pinned ports", result.stdout)

    def test_transitive_port_version_must_match(self):
        self.ports[1] = ("netcdf-c", "4.8.1", 3)
        result = self.verdict()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Wrong or missing export port netcdf-c", result.stderr)

    def test_transitive_override_must_be_exported(self):
        self.ports.pop()
        result = self.verdict()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Wrong or missing export port netcdf-c", result.stderr)

    def test_direct_port_still_needs_an_override(self):
        self.manifest["overrides"].pop(0)
        result = self.verdict()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Each manifest dependency must have one override", result.stderr)

    def test_debug_payload_still_fails(self):
        self.extra_files.append("aidm-windows-deps/installed/x64-windows-avx2/debug/bin/gdal.dll")
        result = self.verdict()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Debug payload found", result.stderr)


if __name__ == "__main__":
    unittest.main()
