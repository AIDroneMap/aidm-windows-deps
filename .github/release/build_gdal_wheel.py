"""Build and import GDAL bindings against this run's dependency export."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

from assets import CONFIG, TRIPLET, download, sha256


MODULES = ("_gdal", "_gdalconst", "_osr", "_ogr", "_gnm", "_gdal_array")
SQLITE_PROBE = """
import sqlite3
from osgeo import ogr

with sqlite3.connect(':memory:') as connection:
    assert connection.execute('SELECT 17').fetchone() == (17,)
database = ogr.GetDriverByName('SQLite').CreateDataSource(':memory:')
result = database.ExecuteSQL('SELECT sqlite_version()')
gdal_sqlite_version = result.GetNextFeature().GetField(0)
database.ReleaseResultSet(result)
database = None
print(f'Python SQLite: {sqlite3.sqlite_version}')
print(f'GDAL SQLite: {gdal_sqlite_version}')
assert sqlite3.sqlite_version == gdal_sqlite_version, 'Python and GDAL SQLite versions differ.'
"""
PROBE = SQLITE_PROBE + """
import sys
import numpy
from osgeo import gdal, gdal_array, osr

assert sys.version_info[:3] == (3, 12, 10), sys.version
assert numpy.__version__ == '2.3.2', numpy.__version__
assert gdal.__version__ == '3.8.4', gdal.__version__
dataset = gdal.GetDriverByName('MEM').Create('', 1, 1, 1, gdal.GDT_Byte)
dataset.GetRasterBand(1).Fill(17)
assert dataset.ReadAsArray().tolist() == [[17]]
assert gdal_array.NumericTypeCodeToGDALTypeCode(numpy.uint8) == gdal.GDT_Byte
print(gdal.__version__)
print('GDAL array module imports and reads raster values.')
"""


def check_wheel(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            parts = PurePosixPath(name.lower()).parts
            if (name.lower().endswith(".dll") or {"data", "share"} & set(parts)
                    or name.lower().endswith("proj.db")):
                raise ValueError(f"GDAL wheel contains a DLL or data payload: {name}")
        for module in MODULES:
            if not any(name.startswith(f"osgeo/{module}.") and name.endswith(".pyd")
                       for name in names):
                raise ValueError(f"GDAL wheel is missing osgeo/{module}.pyd")
    print("Checked GDAL bindings and array module; no DLLs or data folders.")


def development_files(package, python, expected_sha512):
    with package.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha512").hexdigest()
    if digest != expected_sha512:
        raise ValueError("CPython development package does not match its publisher SHA-512.")
    with zipfile.ZipFile(package) as archive:
        for name in archive.namelist():
            if name.startswith(("tools/include/", "tools/libs/")) and not name.endswith("/"):
                relative = PurePosixPath(name).relative_to("tools")
                if ".." in relative.parts or "\\" in name:
                    raise ValueError(f"Invalid CPython development path: {name}")
                target = python.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    for name in ("include/Python.h", "include/datetime.h", "libs/python312.lib"):
        if not (python / name).is_file():
            raise ValueError(f"Missing CPython development file: {name}")


def embedded_python(archive, folder, pip_wheel):
    with zipfile.ZipFile(archive) as package:
        package.extractall(folder)
    # Only the temporary copy changes. The publisher's archive stays unchanged.
    (folder / "python312._pth").write_text(
        "python312.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8",
    )
    with zipfile.ZipFile(pip_wheel) as package:
        package.extractall(folder / "Lib/site-packages")
    return folder / "python.exe"


def probe_python(archive, folder, pip_wheel, exported):
    python = embedded_python(archive, folder, pip_wheel)
    # The embedded SQLite lacks the column metadata symbols GDAL imports.
    shutil.copyfile(exported / "bin/sqlite3.dll", folder / "sqlite3.dll")
    return python


def run(*command, **kwargs):
    subprocess.run([str(part) for part in command], check=True, **kwargs)


def build(assets, work):
    assets = assets.resolve()
    exported = assets / "aidm-windows-deps/installed" / TRIPLET
    if not (exported / "lib/gdal.lib").is_file():
        raise ValueError("The export must contain lib/gdal.lib before building the wheel.")
    tools = work / "tools"
    tools.mkdir()
    requirements = CONFIG / "gdal-build-requirements.txt"
    run(sys.executable, "-m", "pip", "--isolated", "download", "--require-hashes",
        "--no-deps", "--only-binary=:all:", "--platform=win_amd64",
        "--python-version=3.12", "--implementation=cp", "--abi=cp312",
        "--dest", tools, "-r", requirements)
    pip_wheel, = tools.glob("pip-*.whl")
    embedded = assets / "python-3.12.10-embed-amd64.zip"
    publisher_pins = json.loads((CONFIG / "publisher-pins.json").read_text())
    python_pin, = [pin for pin in publisher_pins if pin["filename"] == embedded.name]
    if sha256(embedded) != python_pin["sha256"]:
        raise ValueError("The embedded Python archive does not match its publisher SHA-256.")
    python = embedded_python(embedded, work / "build-python", pip_wheel)
    pin = json.loads((CONFIG / "gdal-build-pins.json").read_text())
    download(work, [pin])
    development_files(work / pin["filename"], python.parent, pin["sha512"])
    run(python, "-m", "pip", "--isolated", "install", "--require-hashes", "--no-deps",
        "--no-index", "--find-links", tools, "-r", requirements)
    env = os.environ.copy()
    for key, folder in (("INCLUDE", "include"), ("LIB", "lib"), ("PATH", "bin")):
        env[key] = str(exported / folder) + os.pathsep + env.get(key, "")
    env.update(DISTUTILS_USE_SDK="1", MSSdk="1", GDAL_DATA=str(exported / "share/gdal"),
               PROJ_LIB=str(exported / "share/proj"), USE_PATH_FOR_GDAL_PYTHON="1")
    env["CL"] = (env.get("CL", "") + " /arch:AVX2").strip()
    wheels = work / "wheels"
    run(python, "-m", "pip", "--isolated", "wheel", "--require-hashes", "--no-deps",
        "--no-build-isolation", "--no-cache-dir", "--no-binary=GDAL", "--wheel-dir", wheels,
        "-r", CONFIG / "gdal-source-requirements.txt", env=env)
    wheel, = wheels.glob("*.whl")
    # Carry the export's GDAL notice with the bindings and regenerate RECORD.
    unpacked = work / "unpacked"
    run(python, "-m", "wheel", "unpack", wheel, "--dest", unpacked)
    package, = unpacked.iterdir()
    info, = package.glob("*.dist-info")
    (info / "licenses").mkdir(exist_ok=True)
    shutil.copyfile(exported / "share/gdal/copyright", info / "licenses/LICENSE.txt")
    run(python, "-m", "wheel", "pack", package, "--dest-dir", wheels)
    wheel, = wheels.glob("*.whl")
    check_wheel(wheel)
    probe = probe_python(embedded, work / "probe-python", pip_wheel, exported)
    numpy_pin, = [line for line in requirements.read_text().splitlines()
                  if line.startswith("numpy==")]
    probe_requirements = work / "probe-requirements.txt"
    probe_requirements.write_text(
        f"{numpy_pin}\n{wheel.as_uri()} --hash=sha256:{sha256(wheel)}\n", encoding="utf-8",
    )
    run(probe, "-m", "pip", "--isolated", "install", "--require-hashes", "--no-deps",
        "--no-index", "--find-links", tools, "-r", probe_requirements)
    run(probe, "-c", PROBE, cwd=work, env=env)
    shutil.copyfile(wheel, assets / wheel.name)


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Build the GDAL wheel in a Windows x64 developer environment.")
    with tempfile.TemporaryDirectory(prefix="gdal-wheel-") as temporary:
        build(Path("assets"), Path(temporary))
