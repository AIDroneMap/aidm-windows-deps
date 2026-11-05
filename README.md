# AIDM Windows dependencies

This repository owns the prebuilt Windows dependencies consumed by the [AIDM engine](https://github.com/AIDroneMap/aidm). The engine pins a release tag and each asset's SHA-256 before using it in its Windows build.

The first release is being implemented from [spec #1](https://github.com/AIDroneMap/aidm-windows-deps/issues/1). This branch establishes the repository rules and documentation. It does not yet build or publish dependencies.

## Planned release assets

- A vcpkg deps export zip, built from a pinned manifest for x86-64-v3 with `/arch:AVX2`, dynamic libraries and release binaries only.
- A GDAL 3.8.4 Python wheel built against the GDAL in that export, with bindings only. The export supplies the DLLs and data files.
- A zip of the VC++ runtime DLLs needed by the export.
- The Python embeddable package and ExifTool 64-bit Windows zip, re-hosted unchanged and checked against their publishers' hashes.
- `vcpkg-list.txt`, `LICENSES.md`, `BUILD-INFO.txt` and `SHA256SUMS`, recording the ports, licences, build versions and asset hashes.

The engine uses these assets to build its Windows engine archives. The task API combines an engine archive with its Windows files into a native package. This repository publishes the dependencies, rather than the engine or the native package.

## Building and checking

The planned `release.yml` builds on a hosted Windows runner. A dispatch uploads workflow artifacts; a version tag creates a draft release for a maintainer to publish. There is no scheduled build or binary cache.

The planned `scripts/check-release.py` checks a folder of assets after a build and can also check a downloaded release. Fixture unit tests run on Linux through `tests.yml`; the Windows workflow proves the build and the release checks that need Windows. These files arrive in later tickets.

See [AGENTS.md](AGENTS.md) for contribution rules and test depths, [REVIEW.md](REVIEW.md) for the review mandate, and [CHANGELOG.md](CHANGELOG.md) for changes and evidence.

## Licence

The repository's own files are licensed under [AGPL-3.0](LICENSE). Release assets carry the licences of the libraries and files they contain, listed in the release's `LICENSES.md`.
