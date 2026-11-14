#!/usr/bin/env python3
"""Check a downloaded deps release without downloading or changing any asset.

Usage: python scripts/check-release.py ASSETS --manifest vcpkg.json

LICENSES.md uses a level-two heading containing the exact port name or asset
filename for each entry, followed by its licence text. Zip names are discovered
from their contents, except for the original Python and ExifTool filenames.
Windows execution is isolated in WindowsProbe so Linux fixtures can replace it.
The command has no option to skip those checks on a downloaded release.
"""

import argparse
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import zipfile
import zlib


TRIPLET = "x64-windows-avx2"
PYTHON_NAME = re.compile(r"python-\d+\.\d+\.\d+-embed-amd64\.zip", re.I)
EXIF_NAME = re.compile(r"exiftool-(\d+\.\d+)_64\.zip", re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def one(items, description):
    items = list(items)
    require(len(items) == 1, f"Expected one {description}, found {len(items)}")
    return items[0]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def extract(archive, destination):
    """Validate all paths before writing, including Windows paths on Linux."""
    with zipfile.ZipFile(archive) as source:
        entries = []
        seen = set()
        for entry in source.infolist():
            require(not entry.flag_bits & 1, f"Encrypted zip entry: {entry.filename}")
            name = entry.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            require(parts and not name.startswith("/") and ":" not in name
                    and ".." not in parts and not any(
                        part.endswith((".", " ")) or re.fullmatch(
                            r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I)
                        for part in parts), f"Unsafe zip path: {entry.filename}")
            require(not stat.S_ISLNK(entry.external_attr >> 16),
                    f"Zip contains a symlink: {entry.filename}")
            key = "/".join(parts).casefold()
            require(key not in seen, f"Duplicate zip path: {entry.filename}")
            seen.add(key)
            entries.append((entry, destination.joinpath(*parts)))
        for entry, target in entries:
            try:
                if entry.filename.endswith(("/", "\\")):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(entry) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
            except Exception as error:
                # Decoders and file operations can fail after the headers passed.
                raise ValueError(f"Cannot extract {entry.filename}: {error}") from error


def status_ports(text):
    ports = {}
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        fields = {}
        for line in paragraph.splitlines():
            if line[:1].isspace():
                continue
            key, separator, value = line.partition(":")
            require(separator, "Malformed vcpkg status field")
            fields[key] = value.strip()
        if fields.get("Status") != "install ok installed" or "Feature" in fields:
            continue
        key = (fields["Package"], fields["Architecture"])
        require(key not in ports, f"Duplicate installed port: {key}")
        ports[key] = (fields["Version"], int(fields.get("Port-Version", "0")))
    require(ports, "No installed ports in export")
    return ports


GDAL_PROBE = r'''
import json
import os
from pathlib import Path
import sys

handles = [os.add_dll_directory(sys.argv[1])]
import sqlite3
from osgeo import gdal, ogr, osr
gdal.UseExceptions()
osr.UseExceptions()
driver = gdal.GetDriverByName("GTiff")
result = {"version": gdal.__version__, "geotiff": driver is not None}
with sqlite3.connect(":memory:") as connection:
    result["sqlite_query"] = connection.execute("SELECT 17").fetchone() == (17,)
database = ogr.GetDriverByName("SQLite").CreateDataSource(":memory:")
query = database.ExecuteSQL("SELECT sqlite_version()")
result["gdal_sqlite_version"] = query.GetNextFeature().GetField(0)
database.ReleaseResultSet(query)
database = None
result["python_sqlite_version"] = sqlite3.sqlite_version
if driver is not None:
    path = str(Path.cwd() / "probe.tif")
    image = driver.Create(path, 1, 1, 1, gdal.GDT_Byte)
    image.GetRasterBand(1).Fill(17)
    image = None
    image = gdal.Open(path)
    result["pixel"] = image.GetRasterBand(1).ReadRaster(0, 0, 1, 1).hex()
    image = None
source, target = osr.SpatialReference(), osr.SpatialReference()
source.ImportFromEPSG(4326)
target.ImportFromEPSG(3857)
source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
result["coordinate"] = osr.CoordinateTransformation(source, target).TransformPoint(12, 45)
print(json.dumps(result))
'''


def probe_path(binary, environ=None):
    """PATH of a probe process: the export's bin folder and the Windows system folders.

    Nothing else may be on it. A runtime DLL of another program's folder, for example an
    older MSVCP140.dll, would otherwise be loaded before the one the export is built for.
    """
    environ = os.environ if environ is None else environ
    root = environ.get("SystemRoot") or environ.get("windir") or "C:\\Windows"
    return ";".join((str(binary), ntpath.join(root, "System32"), root))


class WindowsProbe:
    """Execute local release files only, with no network or installer."""

    def run(self, command, **kwargs):
        require(os.name == "nt", "Run this check on Windows; fixture results are not release proof")
        return subprocess.run(command, check=True, text=True, capture_output=True,
                              timeout=180, **kwargs).stdout.strip()

    def exiftool(self, archive, work):
        folder = work / "exiftool"
        extract(archive, folder)
        executable = one((path for path in folder.rglob("*")
                          if path.name.lower() in ("exiftool.exe", "exiftool(-k).exe")),
                         "ExifTool executable")
        if executable.name.lower() == "exiftool(-k).exe":
            executable = executable.rename(executable.with_name("exiftool.exe"))
        return self.run([str(executable), "-ver"], cwd=folder)

    def gdal(self, python_zip, wheel, installed, work):
        python_home = work / "python"
        extract(python_zip, python_home)
        site = python_home / "Lib" / "site-packages"
        extract(wheel, site)
        # Wheel .data entries are installed relative to their scheme directories.
        schemes = {"purelib": site, "platlib": site, "scripts": python_home / "Scripts",
                   "data": python_home, "headers": python_home / "Include"}
        for data in site.glob("*.data"):
            for scheme in data.iterdir():
                require(scheme.name in schemes, f"Unknown wheel scheme: {scheme.name}")
                shutil.copytree(scheme, schemes[scheme.name], dirs_exist_ok=True)
            shutil.rmtree(data)
        pth = one(python_home.glob("python*._pth"), "embeddable Python path file")
        pth.write_text(pth.read_text(encoding="utf-8") + "\nLib/site-packages\nimport site\n",
                       encoding="utf-8")
        binary = installed / TRIPLET / "bin"
        sqlite = one((p for p in binary.iterdir() if p.name.lower() == "sqlite3.dll"),
                     "export SQLite DLL")
        embedded_sqlite = one((p for p in python_home.iterdir() if p.name.lower() == "sqlite3.dll"),
                              "embedded Python SQLite DLL")
        # Windows loads the DLL next to Python before searching the export.
        shutil.copyfile(sqlite, embedded_sqlite)
        gdal_data = installed / TRIPLET / "share" / "gdal"
        proj_data = installed / TRIPLET / "share" / "proj"
        require(gdal_data.is_dir() and proj_data.is_dir(), "Export lacks GDAL or PROJ data")
        env = dict(os.environ, GDAL_DATA=str(gdal_data), PROJ_LIB=str(proj_data),
                   PROJ_DATA=str(proj_data), PROJ_NETWORK="OFF", USE_PATH_FOR_GDAL_PYTHON="YES")
        env["PATH"] = probe_path(binary)
        output = self.run([str(python_home / "python.exe"), "-I", "-c", GDAL_PROBE,
                           str(binary)], cwd=work, env=env)
        return json.loads(output)


class ReleaseCheck:
    def __init__(self, assets, manifest, probe=None):
        self.assets = Path(assets).resolve()
        self.manifest = Path(manifest)
        self.probe = probe if probe is not None else WindowsProbe()

    def files(self):
        return [path for path in self.assets.iterdir() if path.is_file()]

    def python_zip(self):
        return one((p for p in self.files() if PYTHON_NAME.fullmatch(p.name)), "Python embeddable zip")

    def exif_zip(self):
        return one((p for p in self.files() if EXIF_NAME.fullmatch(p.name)), "ExifTool zip")

    def checksums(self):
        listed = {}
        for line in (self.assets / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([a-fA-F0-9]{64}) [ *]([^/\\:]+)", line)
            require(match is not None, "Malformed SHA256SUMS line; use UTF-8 and full SHA-256 digests")
            value, name = match.groups()
            require(name not in (".", "..", "SHA256SUMS") and name not in listed,
                    f"Invalid or duplicate checksum filename: {name}")
            listed[name] = value.lower()
        actual = {p.name for p in self.files() if p.name != "SHA256SUMS"}
        require(actual == listed.keys(),
                f"Checksum file set differs: unlisted={sorted(actual - listed.keys())}, "
                f"missing={sorted(listed.keys() - actual)}")
        require(all(not p.is_symlink() and p.is_file() for p in self.assets.iterdir()),
                "Asset folder must contain only regular files")
        for name, expected in listed.items():
            require(digest(self.assets / name) == expected, f"SHA-256 mismatch: {name}")
        self.hashes_validated = True

    def export(self, work):
        candidates = []
        archive_errors = []
        for archive in self.files():
            if archive.suffix.lower() != ".zip":
                continue
            try:
                with zipfile.ZipFile(archive) as source:
                    if any(name.replace("\\", "/").endswith("/vcpkg/status")
                           for name in source.namelist()):
                        candidates.append(archive)
            except zipfile.BadZipFile as error:
                archive_errors.append(f"{archive.name}: {error}")
        if not candidates and archive_errors:
            raise ValueError("No readable export zip: " + "; ".join(archive_errors))
        self.export_zip = one(candidates, "export zip containing vcpkg/status")
        folder = work / "export"
        extract(self.export_zip, folder)
        status = one(folder.rglob("vcpkg/status"), "vcpkg status file")
        self.installed = status.parent.parent
        self.ports = status_ports(status.read_text(encoding="utf-8"))
        offending = []
        for path in sorted(folder.rglob("*")):
            debug = False
            if path.is_relative_to(self.installed):
                parts = [part.lower() for part in path.relative_to(self.installed).parts]
                # Header directories can be named debug, including Boost's headers.
                debug = len(parts) > 1 and (parts[1] == "debug" or (
                    parts[1] in ("bin", "lib") and "debug" in parts[2:]))
            if debug or (path.is_file() and path.suffix.lower() == ".pdb"):
                offending.append(path.relative_to(folder).as_posix())
        require(not offending, "Export contains debug payload:\n" + "\n".join(offending))

    def ports_match(self):
        require(hasattr(self, "ports"), "Export check must pass before comparing ports")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        overrides = {}
        for entry in manifest.get("overrides", []):
            values = [entry[key] for key in ("version", "version-string", "version-date", "version-semver")
                      if key in entry]
            require(len(values) == 1 and entry["name"] not in overrides, "Invalid or duplicate override")
            overrides[entry["name"]] = (values[0], entry.get("port-version", 0))
        for dependency in manifest["dependencies"]:
            name = dependency if isinstance(dependency, str) else dependency["name"]
            require(name in overrides, f"Manifest port has no override: {name}")
            architecture = "x64-windows" if isinstance(dependency, dict) and dependency.get("host") else TRIPLET
            require((name, architecture) in self.ports, f"Export lacks manifest port: {name}:{architecture}")
        for name, expected in overrides.items():
            versions = {version for (port, _), version in self.ports.items() if port == name}
            require(versions, f"Export lacks manifest port: {name}")
            require(versions == {expected},
                    f"Port version mismatch: {name}, expected {expected}, found {sorted(versions)}")

    def metadata(self):
        for name in ("vcpkg-list.txt", "LICENSES.md", "BUILD-INFO.txt"):
            require((self.assets / name).is_file(), f"Missing metadata: {name}")
        require(hasattr(self, "ports"), "Export check must pass before comparing the port list")
        listed = {}
        for line in (self.assets / "vcpkg-list.txt").read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([a-z0-9-]+):([a-z0-9-]+) ([^ #]+)(?:#([0-9]+))?", line)
            require(match is not None, f"Malformed port list entry: {line}")
            name, architecture, version, revision = match.groups()
            key = (name, architecture)
            require(key not in listed, f"Duplicate port list entry: {name}:{architecture}")
            listed[key] = (version, int(revision or 0))
        require(listed == self.ports, "vcpkg-list.txt differs from the export status")

    def licenses(self):
        require(hasattr(self, "ports"), "Export check must pass before comparing licences")
        text = (self.assets / "LICENSES.md").read_text(encoding="utf-8")
        sections = re.split(r"(?m)^## ", text)[1:]
        entries = {}
        for section in sections:
            name, separator, body = section.partition("\n")
            name = name.strip().strip("`")
            require(separator and body.strip() and name not in entries,
                    f"Empty or duplicate licence entry: {name}")
            entries[name] = body
        expected = {name for name, _ in self.ports} | {self.python_zip().name, self.exif_zip().name}
        require(expected <= entries.keys(), f"Missing licence entries: {sorted(expected - entries.keys())}")

    def exiftool(self, work):
        require(getattr(self, "hashes_validated", False), "Checksums must pass before executing release files")
        archive = self.exif_zip()
        expected = EXIF_NAME.fullmatch(archive.name)[1]
        actual = self.probe.exiftool(archive, work)
        require(actual == expected, f"ExifTool version mismatch: expected {expected}, found {actual}")

    def gdal(self, work):
        require(getattr(self, "hashes_validated", False), "Checksums must pass before executing release files")
        require(hasattr(self, "installed"), "Export check must pass before running GDAL")
        wheel = one((p for p in self.files() if re.fullmatch(r"gdal-.*\.whl", p.name, re.I)), "GDAL wheel")
        with zipfile.ZipFile(wheel) as source:
            for name in source.namelist():
                parts = name.replace("\\", "/").lower().split("/")
                require(not (parts[-1].endswith(".dll") or parts[-1] == "proj.db"
                             or any(part in ("data", "share") for part in parts[:-1])),
                        f"GDAL wheel must contain bindings only, without DLLs or data: {name}")
        result = self.probe.gdal(self.python_zip(), wheel, self.installed, work)
        require(result["version"] == "3.8.4", f"GDAL version mismatch: {result['version']}")
        require(result["geotiff"] is True, "GDAL has no GeoTIFF driver")
        require(result.get("pixel") == "11", "GDAL did not read the written GeoTIFF pixel")
        require(result.get("sqlite_query") is True, "Python SQLite did not open and query a database")
        require(result.get("python_sqlite_version")
                and result["python_sqlite_version"] == result.get("gdal_sqlite_version"),
                "Python and GDAL SQLite versions differ")
        coordinate = result["coordinate"]
        require(len(coordinate) == 3 and all(math.isfinite(value) for value in coordinate)
                and math.isclose(coordinate[0], 1335833.8895192828, abs_tol=0.01)
                and math.isclose(coordinate[1], 5621521.486192066, abs_tol=0.01),
                f"PROJ coordinate transform differs: {coordinate}")

    def run(self):
        for name in ("hashes_validated", "export_zip", "installed", "ports"):
            self.__dict__.pop(name, None)
        results = []
        with tempfile.TemporaryDirectory(prefix="deps-check-") as temporary:
            work = Path(temporary)
            checks = (("checksums", self.checksums), ("export", lambda: self.export(work)),
                      ("ports", self.ports_match), ("metadata", self.metadata),
                      ("licenses", self.licenses), ("exiftool", lambda: self.exiftool(work)),
                      ("gdal", lambda: self.gdal(work)))
            for name, check in checks:
                try:
                    check()
                except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError,
                        zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, struct.error,
                        subprocess.SubprocessError) as error:
                    results.append((name, False, str(error)))
                else:
                    results.append((name, True, "ok"))
        return results


def main(argv=None, probe=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path, help="Folder containing the release assets")
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).resolve().parents[1] / "vcpkg.json")
    args = parser.parse_args(argv)
    results = ReleaseCheck(args.assets, args.manifest, probe).run()
    for name, passed, message in results:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {message}")
    return int(any(not passed for _, passed, _ in results))


if __name__ == "__main__":
    raise SystemExit(main())
