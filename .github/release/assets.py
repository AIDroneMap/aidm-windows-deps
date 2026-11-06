"""Download pinned publisher files and write the release's lists and hashes."""

import argparse
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request
import zipfile


TRIPLET = "x64-windows-avx2"
CONFIG = Path(__file__).resolve().parent


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(assets, pins):
    assets.mkdir(parents=True, exist_ok=True)
    for pin in pins:
        name = pin["filename"]
        if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]):
            raise ValueError(f"Invalid publisher pin: {name}")
        # An unverified response must never become a release asset.
        with tempfile.TemporaryDirectory(dir=assets.parent) as temporary:
            candidate = Path(temporary) / name
            with urllib.request.urlopen(pin["url"], timeout=120) as response:
                with candidate.open("wb") as output:
                    shutil.copyfileobj(response, output)
            actual = sha256(candidate)
            if actual != pin["sha256"]:
                raise ValueError(
                    f"Publisher SHA-256 mismatch for {name}: "
                    f"expected {pin['sha256']}, got {actual}"
                )
            candidate.replace(assets / name)
        print(f"Verified publisher SHA-256: {name}")


def decode(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def notice(source, text):
    # Copyright files can contain Markdown headings of their own.
    quoted = "\n".join("    " + line for line in text.splitlines())
    return f"\nSource: {source}\n\n{quoted}\n"


def installed_ports(status):
    ports = {}
    for paragraph in re.split(r"\r?\n\s*\r?\n", status.strip()):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line)
        if "Feature" in fields or fields.get("Status") != "install ok installed":
            continue
        name, architecture = fields["Package"], fields["Architecture"]
        if architecture != TRIPLET:
            raise ValueError(f"Unexpected exported triplet: {name}:{architecture}")
        if name in ports:
            raise ValueError(f"Duplicate exported port: {name}")
        revision = int(fields.get("Port-Version", 0))
        ports[name] = fields["Version"] + (f"#{revision}" if revision else "")
    if not ports:
        raise ValueError("The export has no installed ports.")
    return ports


def metadata(assets, manifest, licences, build_info, config=CONFIG):
    for key in ("runner-image-version", "vcpkg-commit", "triplet", "visual-studio-version"):
        if not build_info.get(key):
            raise ValueError(f"Missing build information: {key}")
    if build_info["vcpkg-commit"] != manifest["builtin-baseline"]:
        raise ValueError("Build information does not match the vcpkg baseline.")
    if build_info["triplet"] != TRIPLET:
        raise ValueError("Build information does not match the export triplet.")
    sections = ["# Release licences\n"]
    with zipfile.ZipFile(assets / "aidm-windows-deps.zip") as exported:
        status_paths = [name for name in exported.namelist()
                        if name.endswith("/installed/vcpkg/status")]
        if len(status_paths) != 1:
            raise ValueError("The export must contain one installed status file.")
        root = status_paths[0].removesuffix("vcpkg/status")
        ports = installed_ports(decode(exported.read(status_paths[0])))
        for name, version in sorted(ports.items()):
            copyright_path = f"{root}{TRIPLET}/share/{name}/copyright"
            sections.append(f"\n## {name}\n\nVersion: {version}\n")
            sections.append(notice(copyright_path, decode(exported.read(copyright_path))))
        for runtime in licences["export_runtimes"]:
            binary = f"{root}{TRIPLET}/bin/{runtime['filename']}"
            if binary not in exported.namelist():
                raise ValueError(f"Missing export runtime: {runtime['filename']}")
            sections.append(f"\n## {runtime['filename']}\n\nLicence: {runtime['license']}\n")
            sections.append(f"\nPackage: {runtime['package']}\n")
            sections.append(f"\nPackage SHA-512 at the vcpkg baseline: {runtime['sha512']}\n")
            for text in runtime["texts"]:
                sections.append(notice(text["source"],
                                       (config / text["file"]).read_text(encoding="utf-8")))
    seen = set()
    for asset in sorted(assets.iterdir()):
        if not asset.is_file() or asset.name in {
            "aidm-windows-deps.zip", "LICENSES.md", "BUILD-INFO.txt", "vcpkg-list.txt", "SHA256SUMS"
        }:
            continue
        matches = [item for item in licences["assets"]
                   if fnmatch.fnmatchcase(asset.name, item["pattern"])]
        if len(matches) != 1:
            raise ValueError(f"Expected one asset licence entry: {asset.name}")
        entry = matches[0]
        seen.add(entry["pattern"])
        sections.append(f"\n## {asset.name}\n\nLicence: {entry['license']}\n")
        sections.append(f"\nSource: {entry['source']}\n")
        if entry.get("note"):
            sections.append(f"\n{entry['note']}\n")
        with zipfile.ZipFile(asset) as archive:
            for member in entry["members"]:
                sections.append(notice(f"{asset.name}/{member}", decode(archive.read(member))))
            for member in entry.get("license_archives", []):
                with zipfile.ZipFile(io.BytesIO(archive.read(member))) as nested:
                    for name in sorted(nested.namelist()):
                        if not name.endswith("/"):
                            sections.append(notice(f"{asset.name}/{member}/{name}",
                                                   decode(nested.read(name))))
    missing = {item["pattern"] for item in licences["assets"]} - seen
    if missing:
        raise ValueError(f"Missing licensed assets: {sorted(missing)}")
    (assets / "vcpkg-list.txt").write_text("".join(
        f"{name}:{TRIPLET} {version}\n" for name, version in sorted(ports.items())
    ), encoding="utf-8")
    (assets / "LICENSES.md").write_text("".join(sections), encoding="utf-8")
    (assets / "BUILD-INFO.txt").write_text("".join(
        f"{key}: {value}\n" for key, value in build_info.items()
    ), encoding="utf-8")


def checksums(assets):
    files = sorted(path for path in assets.iterdir()
                   if path.is_file() and path.name != "SHA256SUMS")
    (assets / "SHA256SUMS").write_text("".join(
        f"{sha256(path)}  {path.name}\n" for path in files
    ), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("download", "metadata"))
    parser.add_argument("--assets", type=Path, default=Path("assets"))
    args = parser.parse_args()
    if args.command == "download":
        download(args.assets, json.loads((CONFIG / "publisher-pins.json").read_text()))
    else:
        manifest = json.loads(Path("vcpkg.json").read_text())
        metadata(args.assets, manifest,
                 json.loads((CONFIG / "asset-licenses.json").read_text()), {
                     "runner-image-version": os.environ.get("ImageVersion"),
                     "vcpkg-commit": manifest["builtin-baseline"],
                     "triplet": TRIPLET,
                     "visual-studio-version": os.environ.get("BUILD_VISUAL_STUDIO_VERSION"),
                 })
        checksums(args.assets)


if __name__ == "__main__":
    main()
