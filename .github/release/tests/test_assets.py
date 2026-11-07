"""Exercise publisher verification and release metadata on small fixtures."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location("release_assets", Path(__file__).parents[1] / "assets.py")
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


def write_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "assets"
        self.data = b"publisher bytes\r\n\x00\xff"
        self.pin = {"filename": "publisher.zip", "url": "https://example.invalid/publisher.zip",
                    "sha256": hashlib.sha256(self.data).hexdigest()}

    def test_download_keeps_original_name_and_bytes(self):
        with patch.object(assets.urllib.request, "urlopen", return_value=io.BytesIO(self.data)):
            assets.download(self.folder, [self.pin])
        self.assertEqual((self.folder / "publisher.zip").read_bytes(), self.data)
        self.assertEqual([path.name for path in self.folder.iterdir()], ["publisher.zip"])

    def test_wrong_publisher_hash_fails_without_an_asset(self):
        self.pin["sha256"] = "0" * 64
        with patch.object(assets.urllib.request, "urlopen", return_value=io.BytesIO(self.data)):
            with self.assertRaisesRegex(ValueError, "Publisher SHA-256 mismatch for publisher.zip"):
                assets.download(self.folder, [self.pin])
        self.assertEqual(list(self.folder.iterdir()), [])


class MetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "assets"
        self.folder.mkdir()
        self.manifest = {"builtin-baseline": "a" * 40}
        self.build = {"runner-image-version": "20261105.1", "vcpkg-commit": "a" * 40,
                      "triplet": assets.TRIPLET, "visual-studio-version": "17.14.1"}
        self.status = (
            "Package: gdal\nVersion: 3.8.4\nArchitecture: x64-windows-avx2\n"
            "Status: install ok installed\n\n"
            "Package: gdal\nFeature: core\nArchitecture: x64-windows-avx2\n"
            "Status: install ok installed\n\n"
            "Package: transitive\nVersion: 4.8.1\nPort-Version: 4\n"
            "Architecture: x64-windows-avx2\nStatus: install ok installed\n"
        )
        self.export_files = {
            "deps/installed/vcpkg/status": self.status,
            "deps/installed/x64-windows-avx2/share/gdal/copyright": "GDAL licence\n## embedded heading",
            "deps/installed/x64-windows-avx2/share/transitive/copyright": "Transitive licence",
            "deps/installed/x64-windows-avx2/bin/libquadmath-0.dll": b"fixture DLL",
        }
        self.export()
        inner = io.BytesIO()
        write_zip(inner, {"Perl/Artistic": "Artistic terms", "pthreads/COPYING": "Lockless notice"})
        write_zip(self.folder / "publisher.zip", {
            "LICENSE.txt": b"Publisher licence\r\n\xf6\r\n",
            "nested.zip": inner.getvalue(),
        })
        (self.root / "COPYING.LIB").write_text("LGPL licence", encoding="utf-8")
        self.licences = {
            "assets": [{"pattern": "publisher.zip", "source": "Publisher page",
                        "license": "Publisher terms", "members": ["LICENSE.txt"],
                        "license_archives": ["nested.zip"]}],
            "export_runtimes": [{"filename": "libquadmath-0.dll", "license": "LGPL-2.1-or-later",
                                 "package": "Pinned package", "sha512": "b" * 128,
                                 "texts": [{"file": "COPYING.LIB", "source": "Package licence"}]}],
        }

    def export(self):
        write_zip(self.folder / "aidm-windows-deps.zip", self.export_files)

    def generate(self):
        assets.metadata(self.folder, self.manifest, self.licences, self.build, self.root)
        assets.checksums(self.folder)

    def test_lists_include_transitive_ports_revisions_and_nested_notices(self):
        self.generate()
        self.assertEqual((self.folder / "vcpkg-list.txt").read_text(),
                         "gdal:x64-windows-avx2 3.8.4\ntransitive:x64-windows-avx2 4.8.1#4\n")
        text = (self.folder / "LICENSES.md").read_text()
        self.assertEqual(re.findall(r"(?m)^## (.*)$", text),
                         ["gdal", "transitive", "libquadmath-0.dll", "publisher.zip"])
        for expected in ("Transitive licence", "LGPL-2.1-or-later", "Artistic terms",
                         "Lockless notice", "Publisher licence", "ö"):
            self.assertIn(expected, text)
        info = (self.folder / "BUILD-INFO.txt").read_text()
        for key, value in self.build.items():
            self.assertIn(f"{key}: {value}\n", info)

    def test_hashes_cover_all_assets_including_a_later_wheel_and_metadata(self):
        write_zip(self.folder / "GDAL-3.8.4-fixture.whl", {"LICENSE": "Wheel terms"})
        self.licences["assets"].append({"pattern": "GDAL-3.8.4-*.whl", "source": "GDAL source",
                                        "license": "MIT", "members": ["LICENSE"]})
        self.generate()
        listed = {}
        for line in (self.folder / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            listed[name] = digest
            self.assertEqual(digest, hashlib.sha256((self.folder / name).read_bytes()).hexdigest())
        self.assertEqual(set(listed), {path.name for path in self.folder.iterdir()} - {"SHA256SUMS"})
        self.assertIn("GDAL-3.8.4-fixture.whl", (self.folder / "LICENSES.md").read_text())

    def test_missing_transitive_port_copyright_fails(self):
        del self.export_files["deps/installed/x64-windows-avx2/share/transitive/copyright"]
        self.export()
        with self.assertRaisesRegex(ValueError, "Missing export copyright: transitive"):
            self.generate()

    def wrapper(self):
        self.export_files["deps/installed/vcpkg/status"] += (
            "\nPackage: blas\nVersion: 2023-04-14\nPort-Version: 3\n"
            "Architecture: x64-windows-avx2\nStatus: install ok installed\n"
        )
        self.export()
        self.licences["wrapper_baseline"] = self.manifest["builtin-baseline"]
        self.licences["wrapper_ports"] = {"blas": {
            "version": "2023-04-14#3", "note": "Wrapper only. Implementation: gdal.",
            "source": "Pinned port definition", "implementations": ["gdal"],
            "texts": [{"file": "COPYING.LIB", "source": "Wrapper licence"}],
        }}

    def test_known_wrapper_without_copyright_gets_its_own_entry(self):
        self.wrapper()
        self.generate()
        text = (self.folder / "LICENSES.md").read_text()
        self.assertIn("\n## blas\n", text)
        self.assertIn("Wrapper only. Implementation: gdal.", text)
        self.assertIn("Source: Pinned port definition", text)
        self.assertIn("Source: Wrapper licence", text)
        self.assertIn("blas:x64-windows-avx2 2023-04-14#3\n",
                      (self.folder / "vcpkg-list.txt").read_text())

    def test_wrapper_exception_does_not_cover_a_different_version_or_baseline(self):
        self.wrapper()
        self.licences["wrapper_ports"]["blas"]["version"] = "2023-04-14#2"
        with self.assertRaisesRegex(ValueError, "Unrecognised copyright-free port: blas"):
            self.generate()
        self.licences["wrapper_ports"]["blas"]["version"] = "2023-04-14#3"
        self.licences["wrapper_baseline"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "Unrecognised copyright-free port: blas"):
            self.generate()

    def test_missing_runtime_fails(self):
        del self.export_files["deps/installed/x64-windows-avx2/bin/libquadmath-0.dll"]
        self.export()
        with self.assertRaisesRegex(ValueError, "Missing export runtime: libquadmath-0.dll"):
            self.generate()

    def test_unlisted_asset_fails(self):
        (self.folder / "unknown.zip").write_bytes(b"unknown asset")
        with self.assertRaisesRegex(ValueError, "Expected one asset licence entry: unknown.zip"):
            self.generate()

    def test_missing_licensed_asset_fails(self):
        (self.folder / "publisher.zip").unlink()
        with self.assertRaisesRegex(ValueError, "Missing licensed assets"):
            self.generate()

    def test_missing_build_version_fails(self):
        self.build["visual-studio-version"] = ""
        with self.assertRaisesRegex(ValueError, "Missing build information: visual-studio-version"):
            self.generate()

    def test_wrong_baseline_fails(self):
        self.build["vcpkg-commit"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "does not match the vcpkg baseline"):
            self.generate()


class RuntimeNoticeTests(unittest.TestCase):
    def test_checked_in_licences_cover_the_four_export_runtimes(self):
        config = json.loads((assets.CONFIG / "asset-licenses.json").read_text())
        runtimes = {entry["filename"]: entry for entry in config["export_runtimes"]}
        self.assertEqual(set(runtimes), {"libgcc_s_seh-1.dll", "libgfortran-5.dll",
                                         "libquadmath-0.dll", "libwinpthread-1.dll"})
        for name in ("libgcc_s_seh-1.dll", "libgfortran-5.dll"):
            self.assertEqual(runtimes[name]["license"], "GPL-3.0-or-later WITH GCC-exception-3.1")
        self.assertEqual(runtimes["libquadmath-0.dll"]["license"], "LGPL-2.1-or-later")
        for entry in runtimes.values():
            text = "\n".join((assets.CONFIG / item["file"]).read_text() for item in entry["texts"])
            self.assertTrue(text.strip())
            if "winpthread" in entry["filename"]:
                self.assertIn("2011 mingw-w64 project", text)
                self.assertIn("2010 Lockless Inc.", text)
            if "GCC-exception" in entry["license"]:
                self.assertIn("GCC RUNTIME LIBRARY EXCEPTION", text)
                self.assertIn("Version 3.1", text)


if __name__ == "__main__":
    unittest.main()
