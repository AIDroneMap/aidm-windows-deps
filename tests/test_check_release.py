"""Release verdicts on synthetic assets; no executable or engine is run."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-release.py"
SPEC = importlib.util.spec_from_file_location("check_release", SCRIPT)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

PYTHON = "python-3.12.10-embed-amd64.zip"
EXIF = "exiftool-13.00_64.zip"
RUNTIME = "vc-runtime.zip"
EXPORT = "deps-export.zip"
WHEEL = "GDAL-3.8.4-cp312-cp312-win_amd64.whl"
PREFIX = "deps/installed/"


def pe_binary(imports=("VCRUNTIME140.dll", "KERNEL32.dll"), delay=False):
    """PE32+ fixture with .rdata at RVA 0x2000 and file offset 0x200."""
    data = bytearray(2048)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<Q", data, optional + 24, 0x140000000)
    struct.pack_into("<I", data, optional + 60, 0x200)
    struct.pack_into("<I", data, optional + 108, 16)
    width = 32 if delay else 20
    struct.pack_into("<II", data, optional + 112 + (13 if delay else 1) * 8,
                     0x2000, (len(imports) + 1) * width)
    section = optional + 240
    data[section:section + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, section + 8, 0x600, 0x2000, 0x600, 0x200)
    for index, name in enumerate(imports):
        name_offset = 0x400 + index * 80
        name_rva = 0x2000 + name_offset - 0x200
        descriptor = 0x200 + index * width
        if delay:
            struct.pack_into("<II", data, descriptor, 1, name_rva)
        else:
            struct.pack_into("<I", data, descriptor + 12, name_rva)
        encoded = name.encode("ascii") + b"\0"
        data[name_offset:name_offset + len(encoded)] = encoded
    return bytes(data)


def status(name, version, port_version=0, **fields):
    record = {"Package": name, "Version": version, "Port-Version": str(port_version),
              "Architecture": check.TRIPLET, "Status": "install ok installed", **fields}
    return "\n".join(f"{key}: {value}" for key, value in record.items()) + "\n\n"


class RecordedWindows:
    def __init__(self):
        self.version = "13.00"
        self.result = {"version": "3.8.4", "geotiff": True, "pixel": "11",
                       "coordinate": [1335833.8895192828, 5621521.486192066, 0.0]}
        self.calls = []

    def exiftool(self, archive, work):
        self.calls.append("exiftool")
        return self.version

    def gdal(self, python_zip, wheel, installed, runtime, work):
        self.calls.append("gdal")
        return self.result


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.manifest = self.root / "vcpkg.json"
        # PROJ is transitive. The one direct dependency has one override.
        self.definition = {"dependencies": ["gdal"],
                           "overrides": [{"name": "gdal", "version": "3.8.4"}]}
        self.write_manifest()
        self.status = status("gdal", "3.8.4") + status("proj", "9.3.1")
        self.export_files = {PREFIX + "vcpkg/status": self.status,
                             PREFIX + check.TRIPLET + "/bin/gdal.dll": pe_binary(),
                             PREFIX + check.TRIPLET + "/share/gdal/gdalvrt.xsd": "fixture",
                             PREFIX + check.TRIPLET + "/share/proj/proj.db": "fixture"}
        self.write_export()
        self.write_zip(PYTHON, {"python.exe": "fixture", "python312._pth": "python312.zip\n.\n"})
        self.write_zip(EXIF, {"exiftool-13.00_64/exiftool(-k).exe": "fixture"})
        self.write_zip(RUNTIME, {"runtime/VCRUNTIME140.dll": "fixture"})
        self.write_zip(WHEEL, {"osgeo/__init__.py": "", "osgeo/_gdal.pyd": "fixture"})
        for name in ("vcpkg-list.txt", "BUILD-INFO.txt"):
            (self.assets / name).write_text("fixture\n", encoding="utf-8")
        self.license_names = ["gdal", "proj", PYTHON, EXIF, RUNTIME]
        self.write_licenses()
        self.sums()
        self.probe = RecordedWindows()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.definition), encoding="utf-8")

    def write_zip(self, name, files):
        with zipfile.ZipFile(self.assets / name, "w") as archive:
            for path, contents in files.items():
                archive.writestr(path, contents)

    def write_export(self):
        self.export_files[PREFIX + "vcpkg/status"] = self.status
        self.write_zip(EXPORT, self.export_files)

    def write_licenses(self):
        (self.assets / "LICENSES.md").write_text(
            "# Licences\n\n" + "".join(f"## {name}\n\nLicence text.\n\n" for name in self.license_names),
            encoding="utf-8")

    def sums(self):
        (self.assets / "SHA256SUMS").write_text("".join(
            f"{check.digest(path)}  {path.name}\n" for path in sorted(self.assets.iterdir())
            if path.name != "SHA256SUMS"), encoding="utf-8")

    def results(self, probe=None):
        return check.ReleaseCheck(self.assets, self.manifest,
                                  self.probe if probe is None else probe).run()

    def failed(self):
        return {name for name, passed, message in self.results() if not passed}

    def assert_failure(self, name):
        self.sums()
        self.assertIn(name, self.failed())

    def test_complete_release_and_success_exit(self):
        self.assertEqual(self.failed(), set())
        output = io.StringIO()
        with redirect_stdout(output):
            result = check.main([str(self.assets), "--manifest", str(self.manifest)], self.probe)
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().count("PASS "), 8)

    def test_wrong_hash_fails_and_never_executes_files(self):
        (self.assets / EXIF).write_bytes(b"changed")
        self.assertIn("checksums", self.failed())
        self.assertEqual(self.probe.calls, [])
        output = io.StringIO()
        with redirect_stdout(output):
            result = check.main([str(self.assets), "--manifest", str(self.manifest)], self.probe)
        self.assertEqual(result, 1)
        self.assertIn("FAIL checksums: SHA-256 mismatch: " + EXIF, output.getvalue())

    def test_unlisted_and_missing_assets(self):
        (self.assets / "unlisted.txt").write_text("extra")
        self.assertIn("checksums", self.failed())
        (self.assets / "unlisted.txt").unlink()
        (self.assets / WHEEL).unlink()
        self.assertIn("checksums", self.failed())

    def test_invalid_checksum_lines(self):
        path = self.assets / "SHA256SUMS"
        original = path.read_text()
        for line in ("0" * 63 + "  name", "0" * 65 + "  name", "not a checksum",
                     "0" * 64 + "  ../name", "\ufeff" + original, original + original):
            with self.subTest(line=line[:70]):
                path.write_text(line, encoding="utf-8")
                self.assertIn("checksums", self.failed())

    def test_checksum_crlf_uppercase_and_binary_marker(self):
        path = self.assets / "SHA256SUMS"
        lines = path.read_text().splitlines()
        path.write_bytes("\r\n".join(line[:64].upper() + " *" + line[66:] for line in lines).encode())
        self.assertEqual(self.failed(), set())

    def test_invalid_export(self):
        (self.assets / EXPORT).write_bytes(b"not a zip")
        self.assert_failure("export")

    def test_zip_paths_and_symlink_rejected(self):
        for name in ("../escape", "/escape", "C:\\escape", "..\\escape", "NUL.txt", "file. "):
            with self.subTest(name=name):
                self.export_files[name] = "unsafe"
                self.write_export()
                self.assert_failure("export")
                self.export_files.pop(name)
        self.write_export()
        with zipfile.ZipFile(self.assets / EXPORT, "a") as archive:
            entry = zipfile.ZipInfo("link")
            entry.external_attr = (0o120777 << 16)
            archive.writestr(entry, "../escape")
        self.assert_failure("export")
        self.assertFalse((self.root / "escape").exists())

    def test_backslash_export_paths(self):
        self.write_zip(EXPORT, {name.replace("/", "\\"): value for name, value in self.export_files.items()})
        self.sums()
        self.assertEqual(self.failed(), set())

    def test_missing_or_uninstalled_manifest_port(self):
        for record in (status("proj", "9.3.1"),
                       status("gdal", "3.8.4", Status="deinstall ok not-installed"),
                       status("gdal", "3.8.4", Feature="core"),
                       status("gdal", "3.8.4", Architecture="x64-windows")):
            with self.subTest(record=record):
                self.status = record + status("proj", "9.3.1") if "gdal" in record else record
                self.write_export()
                self.assert_failure("ports")

    def test_wrong_port_version_or_revision(self):
        for version, revision in (("3.8.3", 0), ("3.8.4", 1)):
            with self.subTest(version=version, revision=revision):
                self.status = status("gdal", version, revision) + status("proj", "9.3.1")
                self.write_export()
                self.assert_failure("ports")

    def test_object_dependency_and_override_version_schemes(self):
        self.definition["dependencies"] = [{"name": "gdal", "features": ["core"]}]
        self.status = status("gdal", "3.8.4", 2).replace("\n", "\r\n") + status("proj", "9.3.1")
        self.write_export()
        self.sums()
        for key in ("version", "version-string", "version-semver", "version-date"):
            with self.subTest(key=key):
                self.definition["overrides"] = [{"name": "gdal", key: "3.8.4", "port-version": 2}]
                self.write_manifest()
                self.assertEqual(self.failed(), set())

    def test_direct_dependency_requires_override(self):
        self.definition["overrides"] = []
        self.write_manifest()
        self.assertIn("ports", self.failed())

    def test_transitive_override_is_checked(self):
        self.definition["overrides"].append({"name": "proj", "version": "9.4.0"})
        self.write_manifest()
        self.assertEqual(self.failed(), {"ports"})

    def test_host_ports_need_licenses_and_can_be_manifest_dependencies(self):
        self.status += status("yasm", "1.3.0", Architecture="x64-windows")
        self.write_export()
        self.assert_failure("licenses")
        self.license_names.append("yasm")
        self.write_licenses()
        self.definition["dependencies"].append({"name": "yasm", "host": True})
        self.definition["overrides"].append({"name": "yasm", "version": "1.3.0"})
        self.write_manifest()
        self.sums()
        self.assertEqual(self.failed(), set())

    def test_missing_metadata(self):
        for name in ("vcpkg-list.txt", "BUILD-INFO.txt", "LICENSES.md"):
            with self.subTest(name=name):
                path = self.assets / name
                contents = path.read_bytes()
                path.unlink()
                self.assert_failure("metadata")
                path.write_bytes(contents)

    def test_license_for_every_export_port_and_rehosted_file(self):
        for name in ("gdal", "proj", PYTHON, EXIF, RUNTIME):
            with self.subTest(name=name):
                self.license_names.remove(name)
                self.write_licenses()
                # Mentioning a port in another licence does not count as its entry.
                with (self.assets / "LICENSES.md").open("a") as stream:
                    stream.write(f"The text mentions {name}.\n")
                self.assert_failure("licenses")
                self.license_names.append(name)

    def test_exiftool_mismatched_recorded_version(self):
        self.probe.version = "13.0"
        self.assertEqual(self.failed(), {"exiftool"})

    def test_each_gdal_recorded_result(self):
        for key, value in (("version", "3.9.0"), ("geotiff", False), ("pixel", "00"),
                           ("coordinate", [12, 45, 0]), ("coordinate", [float("nan"), 1, 0])):
            with self.subTest(key=key, value=value):
                original = self.probe.result[key]
                self.probe.result[key] = value
                self.assertEqual(self.failed(), {"gdal"})
                self.probe.result[key] = original

    def test_gdal_wheel_must_not_bundle_dlls(self):
        self.write_zip(WHEEL, {"osgeo/gdal.dll": "duplicate"})
        self.assert_failure("gdal")

    def test_missing_each_required_archive(self):
        for name, failure in ((PYTHON, "gdal"), (EXIF, "exiftool"), (WHEEL, "gdal"), (RUNTIME, "runtime")):
            with self.subTest(name=name):
                path = self.assets / name
                contents = path.read_bytes()
                path.unlink()
                self.assert_failure(failure)
                path.write_bytes(contents)

    def test_runtime_import_case_and_system_dll(self):
        self.export_files[PREFIX + check.TRIPLET + "/bin/gdal.dll"] = pe_binary(
            ("vcruntime140.DLL", "KERNEL32.dll", "api-ms-win-crt-heap-l1-1-0.dll"))
        self.write_export()
        self.sums()
        self.assertEqual(self.failed(), set())

    def test_missing_runtime_normal_and_delay_imports(self):
        for delay in (False, True):
            with self.subTest(delay=delay):
                self.export_files[PREFIX + check.TRIPLET + "/bin/gdal.dll"] = pe_binary(
                    ("MSVCP140.dll",), delay=delay)
                self.write_export()
                self.assert_failure("runtime")

    def test_runtime_imports_in_export_tools(self):
        self.export_files[PREFIX + check.TRIPLET + "/tools/gdal/gdalinfo.exe"] = pe_binary(("MSVCP140.dll",))
        self.write_export()
        self.assert_failure("runtime")

    def test_malformed_pe_fails_instead_of_hiding_imports(self):
        for contents in (b"MZ", b"not a binary", pe_binary()[:1000]):
            with self.subTest(size=len(contents)):
                self.export_files[PREFIX + check.TRIPLET + "/bin/gdal.dll"] = contents
                self.write_export()
                self.assert_failure("runtime")

    def test_default_probe_cannot_claim_windows_execution_on_linux(self):
        with patch.object(check.os, "name", "posix"):
            with self.assertRaisesRegex(ValueError, "Run this check on Windows"):
                check.WindowsProbe().run(["unused.exe"])

    def test_default_probe_prepares_embedded_python_without_network(self):
        probe = check.WindowsProbe()

        def recorded_run(command, *, cwd, env):
            python_home = Path(command[0]).parent
            self.assertTrue((python_home / "Lib/site-packages/osgeo/_gdal.pyd").is_file())
            self.assertIn("Lib/site-packages\nimport site", (python_home / "python312._pth").read_text())
            self.assertTrue(Path(env["GDAL_DATA"]).is_dir())
            self.assertTrue(Path(env["PROJ_LIB"]).is_dir())
            self.assertEqual(env["PROJ_NETWORK"], "OFF")
            self.assertEqual(env["USE_PATH_FOR_GDAL_PYTHON"], "YES")
            self.assertEqual(command[1:3], ["-I", "-c"])
            self.assertTrue(Path(command[-1], "VCRUNTIME140.dll").is_file())
            return json.dumps(self.probe.result)

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            check.extract(self.assets / EXPORT, work / "export")
            installed = work / "export/deps/installed"
            with patch.object(probe, "run", side_effect=recorded_run) as run:
                result = probe.gdal(self.assets / PYTHON, self.assets / WHEEL, installed,
                                    self.assets / RUNTIME, work)
            self.assertEqual(result, self.probe.result)
            self.assertEqual(run.call_count, 1)

    def test_default_exiftool_probe_renames_pause_executable(self):
        probe = check.WindowsProbe()

        def recorded_run(command, *, cwd):
            self.assertEqual(Path(command[0]).name, "exiftool.exe")
            self.assertTrue(Path(command[0]).is_file())
            self.assertEqual(command[1:], ["-ver"])
            return "13.00"

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(probe, "run", side_effect=recorded_run):
                self.assertEqual(probe.exiftool(self.assets / EXIF, Path(temporary)), "13.00")

    def test_reused_checker_does_not_reuse_checksum_success(self):
        checker = check.ReleaseCheck(self.assets, self.manifest, self.probe)
        self.assertTrue(all(passed for _, passed, _ in checker.run()))
        self.probe.calls.clear()
        (self.assets / EXIF).write_bytes(b"changed")
        self.assertIn("checksums", {name for name, passed, _ in checker.run() if not passed})
        self.assertEqual(self.probe.calls, [])

    def test_probe_failure_is_a_named_verdict_and_other_checks_continue(self):
        with patch.object(self.probe, "exiftool", side_effect=OSError("cannot execute")):
            results = self.results()
        self.assertEqual({name for name, passed, _ in results if not passed}, {"exiftool"})
        self.assertEqual(results[-1][0], "runtime")


if __name__ == "__main__":
    unittest.main()
