"""Check wheel payloads and CPython development files without compiling."""

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


RELEASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gdal_wheel", RELEASE / "build_gdal_wheel.py")
gdal_wheel = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(RELEASE), *sys.path]):
    SPEC.loader.exec_module(gdal_wheel)


class WheelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wheel = self.root / "gdal-3.8.4-cp312-cp312-win_amd64.whl"
        self.files = {
            "osgeo/__init__.py": "",
            **{f"osgeo/{name}.cp312-win_amd64.pyd": "fixture"
               for name in gdal_wheel.MODULES},
            "gdal-3.8.4.dist-info/licenses/LICENSE.txt": "GDAL notice",
            "gdal-3.8.4.data/scripts/gdal_calc.py": "fixture",
        }

    def check(self):
        with zipfile.ZipFile(self.wheel, "w") as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
        gdal_wheel.check_wheel(self.wheel)

    def test_bindings_array_and_licence_pass(self):
        self.check()

    def test_every_binding_is_required(self):
        for name in gdal_wheel.MODULES:
            with self.subTest(module=name):
                member = f"osgeo/{name}.cp312-win_amd64.pyd"
                value = self.files.pop(member)
                with self.assertRaisesRegex(ValueError, f"missing osgeo/{name}.pyd"):
                    self.check()
                self.files[member] = value

    def test_dlls_and_data_at_any_depth_fail(self):
        for name in ("gdal.dll", "osgeo/.libs/GDAL.DLL", "osgeo/data/epsg.csv",
                     "osgeo/DATA/", "nested/share/proj/proj.db", "osgeo/proj.db"):
            with self.subTest(member=name):
                self.files[name] = "unwanted payload"
                with self.assertRaisesRegex(ValueError, "DLL or data payload"):
                    self.check()
                del self.files[name]


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.python = self.root / "python"
        self.package = self.root / "python.nupkg"
        self.files = {"tools/include/Python.h": "header",
                      "tools/include/datetime.h": "datetime",
                      "tools/include/cpython/object.h": "nested header",
                      "tools/libs/python312.lib": "import library",
                      "tools/python312.dll": "must not extract",
                      "tools/Lib/os.py": "must not extract"}

    def extract(self, digest=None):
        with zipfile.ZipFile(self.package, "w") as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
        if digest is None:
            digest = hashlib.sha512(self.package.read_bytes()).hexdigest()
        gdal_wheel.development_files(self.package, self.python, digest)

    def test_extracts_only_headers_and_import_libraries(self):
        self.extract()
        paths = {p.relative_to(self.python).as_posix()
                 for p in self.python.rglob("*") if p.is_file()}
        self.assertEqual(paths, {"include/Python.h", "include/datetime.h",
                                 "include/cpython/object.h", "libs/python312.lib"})

    def test_publisher_hash_mismatch_fails_before_extraction(self):
        with self.assertRaisesRegex(ValueError, "publisher SHA-512"):
            self.extract("0" * 128)
        self.assertFalse(self.python.exists())

    def test_missing_development_file_fails(self):
        del self.files["tools/libs/python312.lib"]
        with self.assertRaisesRegex(ValueError, "Missing CPython development file: libs/python312.lib"):
            self.extract()

    def test_path_outside_development_folders_fails(self):
        self.files["tools/include/../../python.exe"] = "must not extract"
        with self.assertRaisesRegex(ValueError, "Invalid CPython development path"):
            self.extract()
        self.assertFalse((self.root / "python.exe").exists())


if __name__ == "__main__":
    unittest.main()
