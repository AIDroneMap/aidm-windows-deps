"""Release verdicts on synthetic assets; no executable or engine is run."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
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
EXPORT = "deps-export.zip"
WHEEL = "GDAL-3.8.4-cp312-cp312-win_amd64.whl"
PREFIX = "deps/installed/"


def status(name, version, port_version=0, **fields):
    record = {"Package": name, "Version": version, "Port-Version": str(port_version),
              "Architecture": check.TRIPLET, "Status": "install ok installed", **fields}
    return "\n".join(f"{key}: {value}" for key, value in record.items()) + "\n\n"


class RecordedWindows:
    def __init__(self):
        self.version = "13.00"
        self.result = {"version": "3.8.4", "geotiff": True, "pixel": "11",
                       "coordinate": [1335833.8895192828, 5621521.486192066, 0.0],
                       "sqlite_query": True, "python_sqlite_version": "3.50.3",
                       "gdal_sqlite_version": "3.50.3"}
        self.calls = []

    def exiftool(self, archive, work):
        self.calls.append("exiftool")
        return self.version

    def gdal(self, python_zip, wheel, installed, work):
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
                             PREFIX + check.TRIPLET + "/bin/gdal.dll": "fixture",
                             PREFIX + check.TRIPLET + "/bin/sqlite3.dll": "export SQLite",
                             PREFIX + check.TRIPLET + "/share/gdal/gdalvrt.xsd": "fixture",
                             PREFIX + check.TRIPLET + "/share/proj/proj.db": "fixture"}
        self.write_export()
        self.write_zip(PYTHON, {"python.exe": "fixture", "python312._pth": "python312.zip\n.\n",
                                "sqlite3.dll": "embedded SQLite"})
        self.write_zip(EXIF, {"exiftool-13.00_64/exiftool(-k).exe": "fixture"})
        self.write_zip(WHEEL, {"osgeo/__init__.py": "", "osgeo/_gdal.pyd": "fixture"})
        for name in ("BUILD-INFO.txt",):
            (self.assets / name).write_text("fixture\n", encoding="utf-8")
        self.license_names = ["gdal", "proj", PYTHON, EXIF]
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
        self.write_port_list()

    def write_port_list(self):
        ports = check.status_ports(self.status)
        (self.assets / "vcpkg-list.txt").write_text("".join(
            f"{name}:{architecture} {version}" + (f"#{revision}" if revision else "") + "\n"
            for (name, architecture), (version, revision) in sorted(ports.items())
        ), encoding="utf-8")

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
        self.assertFalse((self.assets / "vc-runtime.zip").exists())
        self.assertEqual(self.failed(), set())
        output = io.StringIO()
        with redirect_stdout(output):
            result = check.main([str(self.assets), "--manifest", str(self.manifest)], self.probe)
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().count("PASS "), 7)

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

    def test_unsupported_zip_compression_reports_failure_and_continues(self):
        archive_path = self.assets / EXPORT
        with zipfile.ZipFile(archive_path) as archive:
            local_header = archive.infolist()[0].header_offset
            central_header = archive.start_dir
        data = bytearray(archive_path.read_bytes())
        self.assertEqual(data[local_header:local_header + 4], b"PK\x03\x04")
        self.assertEqual(data[central_header:central_header + 4], b"PK\x01\x02")
        struct.pack_into("<H", data, local_header + 8, 99)
        struct.pack_into("<H", data, central_header + 10, 99)
        archive_path.write_bytes(data)
        self.sums()

        result = subprocess.run([sys.executable, str(SCRIPT), str(self.assets),
                                 "--manifest", str(self.manifest)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("PASS checksums: ok", result.stdout)
        self.assertIn("FAIL export: Cannot extract " + PREFIX + "vcpkg/status: "
                      "That compression method is not supported", result.stdout)
        self.assertIn("FAIL metadata:", result.stdout)
        self.assertIn("FAIL gdal:", result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 7)
        self.assertEqual(result.stderr, "")

    def test_encrypted_zip_reports_failure_and_continues(self):
        archive_path = self.assets / EXPORT
        with zipfile.ZipFile(archive_path) as archive:
            entry = archive.infolist()[0]
            self.assertEqual(entry.filename, PREFIX + "vcpkg/status")
            local_header = entry.header_offset
            central_header = archive.start_dir
        data = bytearray(archive_path.read_bytes())
        self.assertEqual(data[local_header:local_header + 4], b"PK\x03\x04")
        self.assertEqual(data[central_header:central_header + 4], b"PK\x01\x02")
        for flag_offset in (local_header + 6, central_header + 8):
            flags = struct.unpack_from("<H", data, flag_offset)[0]
            struct.pack_into("<H", data, flag_offset, flags | 1)
        archive_path.write_bytes(data)
        self.sums()

        result = subprocess.run([sys.executable, str(SCRIPT), str(self.assets),
                                 "--manifest", str(self.manifest)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("PASS checksums: ok", result.stdout)
        self.assertIn("FAIL export: Encrypted zip entry: " + PREFIX + "vcpkg/status", result.stdout)
        self.assertIn("FAIL metadata:", result.stdout)
        self.assertIn("FAIL gdal:", result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 7)
        self.assertEqual(result.stderr, "")

    def assert_archive_command_failure(self, error_text, injection=None):
        self.sums()
        command = [sys.executable]
        if injection is not None:
            command += ["-c", injection]
        command += [str(SCRIPT), str(self.assets), "--manifest", str(self.manifest)]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("PASS checksums: ok", result.stdout)
        self.assertIn("FAIL export:", result.stdout)
        export_verdict = next(line for line in result.stdout.splitlines() if line.startswith("FAIL export:"))
        self.assertIn(error_text, export_verdict)
        self.assertIn("FAIL metadata:", result.stdout)
        self.assertIn("FAIL gdal:", result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 7)
        self.assertEqual(result.stderr, "")

    def test_corrupt_deflate_reports_failure_and_continues(self):
        archive_path = self.assets / EXPORT
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, contents in self.export_files.items():
                archive.writestr(name, contents)
        with zipfile.ZipFile(archive_path) as archive:
            entry = archive.infolist()[0]
            local_header = entry.header_offset
        data = bytearray(archive_path.read_bytes())
        name_size, extra_size = struct.unpack_from("<HH", data, local_header + 26)
        data[local_header + 30 + name_size + extra_size] = 0x07
        archive_path.write_bytes(data)
        self.assert_archive_command_failure("invalid block type")

    def test_wrong_crc_reports_failure_and_continues(self):
        archive_path = self.assets / EXPORT
        with zipfile.ZipFile(archive_path) as archive:
            local_header = archive.infolist()[0].header_offset
            central_header = archive.start_dir
        data = bytearray(archive_path.read_bytes())
        crc = struct.unpack_from("<I", data, local_header + 14)[0] ^ 1
        struct.pack_into("<I", data, local_header + 14, crc)
        struct.pack_into("<I", data, central_header + 16, crc)
        archive_path.write_bytes(data)
        self.assert_archive_command_failure("Bad CRC-32")

    def test_bad_central_directory_reports_failure_and_continues(self):
        archive_path = self.assets / EXPORT
        with zipfile.ZipFile(archive_path) as archive:
            central_header = archive.start_dir
        data = bytearray(archive_path.read_bytes())
        data[central_header:central_header + 4] = b"BAD!"
        archive_path.write_bytes(data)
        self.assert_archive_command_failure("Bad magic number for central directory")

    def test_truncated_member_reports_failure_and_continues(self):
        archive_path = self.assets / EXPORT
        with zipfile.ZipFile(archive_path) as archive:
            entry = archive.infolist()[-1]
            central_header = archive.start_dir
        data = bytearray(archive_path.read_bytes())
        name_size, extra_size = struct.unpack_from("<HH", data, entry.header_offset + 26)
        member_end = entry.header_offset + 30 + name_size + extra_size + entry.compress_size
        self.assertEqual(member_end, central_header)
        # Remove the final payload byte, retaining a readable central directory.
        del data[member_end - 1:member_end]
        end_record = data.rfind(b"PK\x05\x06")
        self.assertNotEqual(end_record, -1)
        struct.pack_into("<I", data, end_record + 16, central_header - 1)
        archive_path.write_bytes(data)
        self.assert_archive_command_failure("Overlapped entries")

    def member_read_injection(self, error_class):
        return (
            "import runpy, sys, zipfile, zlib\n"
            f"error = {error_class}('injected member read failure')\n"
            "def fail_read(self, *args, **kwargs):\n"
            "    raise error\n"
            "zipfile.ZipExtFile.read = fail_read\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )

    def test_member_read_error_families_report_failure_and_continue(self):
        for error_class in ("zipfile.BadZipFile", "zlib.error", "RuntimeError",
                            "NotImplementedError", "EOFError", "OSError", "zipfile.LargeZipFile"):
            with self.subTest(error_class=error_class):
                self.assert_archive_command_failure("injected member read failure",
                                                    self.member_read_injection(error_class))

    def test_generic_member_read_exception_reports_failure_and_continues(self):
        self.assert_archive_command_failure("injected member read failure",
                                            self.member_read_injection("Exception"))

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

    def test_port_list_must_match_all_export_ports_and_revisions(self):
        self.status += status("curl", "8.15.0", 1) + status("gdal", "3.8.4", Feature="core")
        self.write_export()
        self.license_names.append("curl")
        self.write_licenses()
        path = self.assets / "vcpkg-list.txt"
        original = path.read_text()
        path.write_bytes(original.replace("\n", "\r\n").encode())
        self.sums()
        self.assertEqual(self.failed(), set())
        for text in (original.replace("#1", "#2"),
                     original.replace("proj:x64-windows-avx2 9.3.1\n", ""),
                     original + "extra:x64-windows-avx2 1.0\n",
                     original + original, "not a port list\n"):
            with self.subTest(text=text):
                path.write_text(text)
                self.sums()
                self.assertEqual(self.failed(), {"metadata"})

    def test_release_export_rejects_debug_folders_and_symbols(self):
        for name in ("debug/", "debug/bin/gdal.dll", "bin/GDAL.PDB", "share/gdal/tool.pdb"):
            with self.subTest(name=name):
                key = PREFIX + check.TRIPLET + "/" + name
                self.export_files[key] = "fixture"
                self.write_export()
                self.assert_failure("export")
                self.export_files.pop(key)
        self.export_files[PREFIX + check.TRIPLET + "/share/gdal/debug.txt"] = "fixture"
        self.write_export()
        self.sums()
        self.assertEqual(self.failed(), set())

    def test_header_debug_folder_is_release_payload(self):
        self.export_files[PREFIX + check.TRIPLET + "/include/boost/preprocessor/debug/x.hpp"] = "fixture"
        self.write_export()
        self.sums()
        self.assertEqual(self.failed(), set())

    def test_debug_verdict_lists_every_offending_path(self):
        paths = [check.TRIPLET + "/debug/bin/a.dll", check.TRIPLET + "/bin/a.pdb",
                 check.TRIPLET + "/lib/debug/b.lib", "x64-windows/debug/bin/host.dll"]
        for path in paths:
            self.export_files[PREFIX + path] = "fixture"
        self.export_files["deps/export.pdb"] = "fixture"
        self.write_export()
        self.sums()
        failures = {name: message for name, passed, message in self.results() if not passed}
        self.assertEqual(set(failures), {"export"})
        for path in paths + ["deps/export.pdb"]:
            self.assertIn(path, failures["export"])

    def test_second_export_is_rejected(self):
        (self.assets / "second.zip").write_bytes((self.assets / EXPORT).read_bytes())
        self.assert_failure("export")

    def test_license_for_every_export_port_and_rehosted_file(self):
        for name in ("gdal", "proj", PYTHON, EXIF):
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
                           ("coordinate", [12, 45, 0]), ("coordinate", [float("nan"), 1, 0]),
                           ("sqlite_query", False), ("gdal_sqlite_version", "3.49.0"),
                           ("python_sqlite_version", "")):
            with self.subTest(key=key, value=value):
                original = self.probe.result[key]
                self.probe.result[key] = value
                self.assertEqual(self.failed(), {"gdal"})
                self.probe.result[key] = original

    def assert_wheel_entry_rejected(self, name):
        self.write_zip(WHEEL, {name: "duplicate"})
        self.sums()
        failures = {check_name: message for check_name, passed, message in self.results() if not passed}
        self.assertEqual(set(failures), {"gdal"})
        self.assertIn(name, failures["gdal"])
        self.assertNotIn("gdal", self.probe.calls)

    def test_gdal_wheel_must_not_bundle_dlls(self):
        for name in ("gdal.dll", "osgeo/gdal.dll", "osgeo\\GDAL.DLL"):
            with self.subTest(name=name):
                self.assert_wheel_entry_rejected(name)

    def test_gdal_wheel_must_not_bundle_data_folders(self):
        for name in ("data/gcs.csv", "osgeo/data/gdal/gcs.csv", "osgeo/data/",
                     "osgeo\\DATA\\gdal\\gcs.csv"):
            with self.subTest(name=name):
                self.assert_wheel_entry_rejected(name)

    def test_gdal_wheel_must_not_bundle_share_folders(self):
        for name in ("share/metadata.json", "osgeo/resources/share/metadata.json",
                     "osgeo/share/", "osgeo\\SHARE\\metadata.json"):
            with self.subTest(name=name):
                self.assert_wheel_entry_rejected(name)

    def test_gdal_wheel_must_not_bundle_proj_database(self):
        self.assert_wheel_entry_rejected("osgeo/proj.db")

    def test_missing_each_required_archive(self):
        for name, failure in ((PYTHON, "gdal"), (EXIF, "exiftool"), (WHEEL, "gdal")):
            with self.subTest(name=name):
                path = self.assets / name
                contents = path.read_bytes()
                path.unlink()
                self.assert_failure(failure)
                path.write_bytes(contents)

    def test_default_probe_cannot_claim_windows_execution_on_linux(self):
        with patch.object(check.os, "name", "posix"):
            with self.assertRaisesRegex(ValueError, "Run this check on Windows"):
                check.WindowsProbe().run(["unused.exe"])

    def test_default_probe_prepares_embedded_python_without_network(self):
        probe = check.WindowsProbe()
        archive_hash = check.digest(self.assets / PYTHON)

        def recorded_run(command, *, cwd, env):
            python_home = Path(command[0]).parent
            self.assertTrue((python_home / "Lib/site-packages/osgeo/_gdal.pyd").is_file())
            self.assertIn("Lib/site-packages\nimport site", (python_home / "python312._pth").read_text())
            self.assertTrue(Path(env["GDAL_DATA"]).is_dir())
            self.assertTrue(Path(env["PROJ_LIB"]).is_dir())
            self.assertEqual(env["PROJ_NETWORK"], "OFF")
            self.assertEqual(env["USE_PATH_FOR_GDAL_PYTHON"], "YES")
            self.assertEqual(command[1:3], ["-I", "-c"])
            self.assertEqual((python_home / "sqlite3.dll").read_text(), "export SQLite")
            self.assertEqual(Path(command[-1]), Path(env["GDAL_DATA"]).parents[1] / "bin")
            self.assertNotIn("runtime", env["PATH"].split(";"))
            return json.dumps(self.probe.result)

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            check.extract(self.assets / EXPORT, work / "export")
            installed = work / "export/deps/installed"
            with patch.object(probe, "run", side_effect=recorded_run) as run:
                result = probe.gdal(self.assets / PYTHON, self.assets / WHEEL, installed, work)
            self.assertEqual(result, self.probe.result)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(check.digest(self.assets / PYTHON), archive_hash)

    def test_probe_path_holds_only_the_export_and_the_system_folders(self):
        foreign = r"C:\Users\pilot\Android\emulator\lib64"
        probe = check.WindowsProbe()
        seen = []

        def recorded_run(command, *, cwd, env):
            seen.append(env)
            return json.dumps(self.probe.result)

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            check.extract(self.assets / EXPORT, work / "export")
            installed = work / "export/deps/installed"
            binary = installed / check.TRIPLET / "bin"
            hostile = {"PATH": check.os.pathsep.join((foreign, r"C:\Program Files\Tool")),
                       "SystemRoot": r"D:\Win", "PYTHONPATH": foreign}
            with patch.dict(check.os.environ, hostile), \
                    patch.object(probe, "run", side_effect=recorded_run):
                probe.gdal(self.assets / PYTHON, self.assets / WHEEL, installed, work)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["PATH"].split(";"),
                         [str(binary), r"D:\Win\System32", r"D:\Win"])
        self.assertNotIn(foreign, seen[0]["PATH"])
        self.assertNotIn("Program Files", seen[0]["PATH"])

    def test_probe_path_names_the_system_folders_without_a_system_root(self):
        self.assertEqual(check.probe_path("bin", {}).split(";"),
                         ["bin", r"C:\Windows\System32", r"C:\Windows"])
        self.assertEqual(check.probe_path("bin", {"windir": r"E:\W"}).split(";"),
                         ["bin", r"E:\W\System32", r"E:\W"])

    def test_sqlite_replacement_matches_embedded_filename_case(self):
        self.write_zip(PYTHON, {"python.exe": "fixture", "python312._pth": ".\n",
                                "SQLITE3.DLL": "embedded SQLite"})
        probe = check.WindowsProbe()

        def recorded_run(command, **kwargs):
            home = Path(command[0]).parent
            self.assertEqual((home / "SQLITE3.DLL").read_text(), "export SQLite")
            self.assertEqual(len([p for p in home.iterdir() if p.name.lower() == "sqlite3.dll"]), 1)
            return json.dumps(self.probe.result)

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            check.extract(self.assets / EXPORT, work / "export")
            with patch.object(probe, "run", side_effect=recorded_run):
                probe.gdal(self.assets / PYTHON, self.assets / WHEEL,
                           work / "export/deps/installed", work)

    def test_missing_export_sqlite_is_a_failed_gdal_verdict(self):
        del self.export_files[PREFIX + check.TRIPLET + "/bin/sqlite3.dll"]
        self.write_export()
        self.sums()
        probe = check.WindowsProbe()
        with patch.object(probe, "exiftool", return_value="13.00"), patch.object(probe, "run") as run:
            failures = {name: message for name, passed, message in self.results(probe) if not passed}
        self.assertEqual(set(failures), {"gdal"})
        self.assertIn("export SQLite DLL", failures["gdal"])
        run.assert_not_called()

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
        self.assertEqual(results[-1][0], "gdal")


if __name__ == "__main__":
    unittest.main()
