# AIDM Windows dependencies

This repository owns the prebuilt Windows dependencies consumed by the [AIDM engine](https://github.com/AIDroneMap/aidm). The engine pins a release tag and each asset's SHA-256 before using it in its Windows build.

The first release is being implemented from [spec #1](https://github.com/AIDroneMap/aidm-windows-deps/issues/1). The repository rules and documentation came first; the build and the release follow in the tickets of that spec.

## Release assets

- A vcpkg deps export zip, built from a pinned manifest for x86-64-v3 with `/arch:AVX2`, dynamic libraries and release binaries only.
- A GDAL 3.8.4 Python wheel built against the GDAL in that export, with bindings only. The export supplies the DLLs and data files.
- The Python embeddable package and ExifTool 64-bit Windows zip, re-hosted unchanged and checked against their publishers' hashes.
- `vcpkg-list.txt`, `LICENSES.md`, `BUILD-INFO.txt` and `SHA256SUMS`, recording the ports, licences, build versions and asset hashes.

The engine uses these assets to build its Windows engine archives. The task API combines an engine archive with its Windows files into a native package. This repository publishes the dependencies, rather than the engine or the native package.

## Building and checking

`release.yml` builds on a hosted Windows runner. A dispatch uploads workflow artifacts; a version tag creates a draft release for a maintainer to publish. There is no scheduled build or binary cache.

The workflow runs `scripts/check-release.py` on the complete asset folder before uploading it. A failed verdict fails the build and prevents a draft release. Fixture unit tests run on Linux through `tests.yml`; the Windows workflow proves the build and the checks that execute Windows files.

To check a downloaded release, use Windows 11 x64 with Python 3.12 and the Visual C++ runtime installed. Check out this repository at the release's tag and download every release asset into one folder. Keep that folder separate from the checkout and unpacked files. From the checkout, run:

```powershell
python scripts/check-release.py C:\path\to\assets --manifest vcpkg.json
```

The command verifies every asset's SHA-256, the export's pinned versions and release payload, the port and licence lists, ExifTool's version, and GDAL's GeoTIFF and PROJ operations in a fresh unpacking of the bundled Python. It checks that Python and GDAL share the export's SQLite and that Python can query a database. It needs no downloads and leaves the assets unchanged. Every verdict must say `PASS`; a failure exits with status 1. The check executes the downloaded Python, ExifTool and GDAL files, so use assets from this project's release.

The dependency release contains no Visual C++ runtime files. The engine package copies them from its own build runner's Visual Studio. The manual check uses the runtime installed on the Windows machine.

See [AGENTS.md](AGENTS.md) for contribution rules and test depths, [REVIEW.md](REVIEW.md) for the review mandate, and [CHANGELOG.md](CHANGELOG.md) for changes and evidence.

## Licence

The repository's own files are licensed under [AGPL-3.0](LICENSE). Release assets carry the licences of the libraries and files they contain, listed in the release's `LICENSES.md`.
