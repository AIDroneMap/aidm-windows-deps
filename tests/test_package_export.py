"""Package a raw export with status metadata and release runtime DLLs."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile


class PackageExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github/workflows/release.yml").read_text(encoding="utf-8")
        step = workflow.split("      - name: Package release export\n", 1)[1]
        script = step.split("        run: |\n", 1)[1]
        cls.script = textwrap.dedent(script.split("\n      - name:", 1)[0])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.export = self.root / "assets/aidm-windows-deps"
        (self.export / "installed/vcpkg/info").mkdir(parents=True)
        (self.export / ".vcpkg-root").touch()
        self.status = self.root / "vcpkg_installed/vcpkg/status"
        self.status.parent.mkdir(parents=True)
        self.status.write_bytes(b"Package: lapack-reference\r\nVersion: 3.12.1\r\n")
        self.triplet = self.export / "installed/x64-windows-avx2"
        (self.triplet / "bin").mkdir(parents=True)
        self.runtimes = ["libgcc_s_seh-1.dll", "libgfortran-5.dll",
                         "libquadmath-0.dll", "libwinpthread-1.dll"]
        for runtime in self.runtimes:
            (self.triplet / "bin" / runtime).write_bytes(b"release fixture")
        self.archive = self.root / "assets/aidm-windows-deps.zip"

    def package(self):
        return subprocess.run([sys.executable, "-c", self.script],
                              cwd=self.root, capture_output=True, text=True, check=False)

    def test_status_added_debug_logged_and_release_runtimes_kept(self):
        debug = self.triplet / "debug/bin"
        debug.mkdir(parents=True)
        for runtime in self.runtimes:
            (debug / runtime).write_bytes(b"debug fixture")
        result = self.package()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            names = archive.namelist()
            self.assertEqual(archive.read("aidm-windows-deps/installed/vcpkg/status"),
                             self.status.read_bytes())
            self.assertFalse(any("/debug/" in name for name in names))
            self.assertIn("aidm-windows-deps/.vcpkg-root", names)
            for runtime in self.runtimes:
                self.assertIn(f"installed/x64-windows-avx2/debug/bin/{runtime}",
                              result.stdout)
                self.assertEqual(archive.read(
                    f"aidm-windows-deps/installed/x64-windows-avx2/bin/{runtime}"),
                    b"release fixture")
        self.assertFalse((self.triplet / "debug").exists())
        self.assertEqual(list(self.archive.parent.glob("*.zip")), [self.archive])

    def test_export_without_debug_tree_is_packaged(self):
        result = self.package()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.archive.is_file())
        self.assertNotIn("Removing debug export path", result.stdout)

    def test_release_symbols_removed_and_logged_without_changing_binaries(self):
        symbols = [self.triplet / "bin/gdal.pdb", self.triplet / "bin/PROJ.PDB",
                   self.triplet / "lib/port.pdb"]
        for path in symbols:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"symbols")
        result = self.package()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertFalse(any(name.lower().endswith(".pdb") for name in archive.namelist()))
            for runtime in self.runtimes:
                self.assertEqual(archive.read(
                    f"aidm-windows-deps/installed/x64-windows-avx2/bin/{runtime}"),
                    b"release fixture")
        for path in symbols:
            self.assertFalse(path.exists())
            self.assertIn(f"Removing export symbols: {path.relative_to(self.export).as_posix()}",
                          result.stdout)

    def test_missing_install_status_fails_without_an_archive(self):
        self.status.unlink()
        result = self.package()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()
