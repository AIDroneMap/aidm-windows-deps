# aidm-windows-deps 1.0.0: the first deps export

Issue: https://github.com/AIDroneMap/aidm-windows-deps/issues/1

Written from the design note `docs/design/windows-engine-0.3.0.md` of the engine repository, ADR 0005 and ADR 0006 there, and the words in the engine's `GLOSSARY.md`. The design note records the decisions and the facts; this spec says what the first release of this repository publishes, how it is built and how it is proven. The tickets are cut from the "Implementation decisions" and "Testing decisions" sections, one pull request each.

## Problem statement

The engine's Windows build needs a set of prebuilt C++ libraries, a GDAL wheel that matches them, a Python embeddable package and ExifTool. The build path the engine inherited downloads all of them from a release of a reference project. The project's rule is that no release of AIDM downloads anything from a reference project's release asset, and the hosted Windows runner cannot build the libraries from source inside every engine run and still have time for the engine itself.

For the maintainer, a dependency that is downloaded from somewhere else is a dependency nobody in the project can rebuild, check or re-licence.

## Solution

This repository builds the Windows dependencies once per release of its own, on a hosted Windows runner, from a pinned vcpkg manifest, and publishes them as release assets with their hashes and licences. The engine pins one release by tag and SHA-256. Rebuilding is a tag of this repository, never part of an engine run.

The first release publishes a deps export compiled for x86-64-v3, a GDAL wheel built against that export, the VC++ runtime files, and the Python embeddable package and the ExifTool Windows executable re-hosted unchanged, each checked against the hash its publisher lists.

## User stories

1. As a builder of the engine, I want every Windows library the SuperBuild links against to come from one zip with one hash, so that the engine's Windows job downloads one file and pins one line.
2. As a builder of the engine, I want the export compiled with the same instruction set the engine uses (`/arch:AVX2`, the Windows form of ADR 0001), so that no library in the package runs below the engine's minimum CPU or above it.
3. As a builder of the engine, I want the GDAL wheel built against the GDAL in the export, so that the Python bindings and the DLLs the tools link against are the same GDAL.
4. As a builder of the engine, I want the GDAL wheel to hold the bindings only, and the DLLs, `GDAL_DATA` and PROJ data to come from the export, so that the package has one copy of GDAL and one copy of PROJ.
5. As a builder of the engine, I want the Python embeddable package and ExifTool in the same release as the libraries, so that the engine's package step downloads from one place and one `SHA256SUMS`.
6. As a builder of the engine, I want the VC++ runtime files in the release, so that the package runs on a Windows 11 that never had Visual Studio installed, without an installer.
7. As a builder of the engine, I want the release to list every port with its version, so that I can read what the export holds without unpacking it.
8. As a builder of the engine, I want the export to hold release binaries only, so that the download and the package are not doubled by debug builds nothing uses.
9. As a builder who finds a missing library or driver in the engine's Windows job, I want to add it in a pull request to `vcpkg.json` and get a new release, so that the fix is one pin change in the engine.
10. As a maintainer, I want the manifest to pin a vcpkg baseline and an override per port, so that two builds of the same tag compile the same sources.
11. As a maintainer, I want the workflow to clone vcpkg at the pinned commit instead of using whatever the runner image carries, so that the runner image's updates do not change the build.
12. As a maintainer, I want the release to record the runner image version and the Visual Studio version that built it, so that a difference between two releases can be traced to the compiler.
13. As a maintainer, I want every asset re-hosted from its publisher checked against the hash that publisher lists before it is uploaded, so that this repository never re-hosts a file that was altered on the way.
14. As a maintainer, I want a licence list with one entry per port and one per re-hosted file, so that the engine's package can ship its `licenses/` folder from a list that exists.
15. As a maintainer, I want the release check to run on the runner after every build and to be runnable by hand on a downloaded release, so that the same script answers "is this release complete" in both places.
16. As a maintainer, I want the workflow to publish only on a tag and to upload workflow artifacts on a dispatch, so that a trial build never becomes a release by accident.
17. As a maintainer, I want the repository to credit the reference project whose published port list the first manifest starts from, so that the origin is stated once, in the changelog.
18. As a maintainer, I want the version differences against the engine's Linux images recorded in the changelog of each release, so that an output difference between the platforms has a first place to look.
19. As a maintainer, I want no binary cache and no schedule, so that the only thing that produces a release is a tag I push.
20. As the independent reviewer, I want the workflow's own run to be the proof of a workflow pull request and the fixture tests to be the proof of a script pull request, so that I can approve from the record.
21. As a reader of the public repository, I want no private host, account or machine named in it, so that the public record holds only what is public.
22. As a pilot, I want the engine installed on my Windows machine to run without a Visual Studio or GDAL installation, so that the setup in AIDM UI is the whole setup.

## Implementation decisions

### The repository

Public, under the AIDroneMap organisation, named `aidm-windows-deps`. Its own files are AGPL-3.0 like the rest of the project; the artifacts carry the licences of what they contain. Its `AGENTS.md` keeps the skills section it already has and gains the rules of the engine's `AGENTS.md` that apply here: the writing rules, the sign-off, the footnote, the `Test depth:` line with the values `unit` and `workflow`, and the rule that no release of the project downloads from a reference project's release asset. It gets `REVIEW.md`, the engine's review mandate with this repository's two depths, `README.md`, `CHANGELOG.md` with `## Unreleased` at the top, and the labels `ready-for-agent` and `needs-maintainer`.

The changelog of the first release credits the reference project whose published port list and overrides the manifest starts from.

### The manifest

`vcpkg.json` in manifest mode. Its dependencies are the ports the engine's SuperBuild links against on Windows, which are the ports of the reference project's published list: Eigen, SuiteSparse, LAPACK, TBB, glog, curl, libxml2, zlib, libpng, libjpeg-turbo, libtiff, FLANN, the Boost components (filesystem, date-time, iostreams, foreach, signals2, interprocess, graph, asio, program-options), libgeotiff, CGAL, yasm, METIS, pybind11 and PROJ, plus GDAL. `builtin-baseline` is the commit of that list, and `overrides` pin each port to the version of that list. GDAL is pinned to 3.8.4, the version the engine's Linux images carry from their base system, with the port's default features. A feature the engine's Windows smoke test later shows missing is a pull request here and a new release.

The spec does not decide whether OpenCV and Ceres join the manifest. The engine's probe decides it: if the SuperBuild does not fit the hosted runner's job limit next to the export, they are added here first (ADR 0005).

### The triplet

A triplet file of this repository, `x64-windows-avx2`, derived from the community `x64-windows` triplet: dynamic library linkage, dynamic CRT, `VCPKG_C_FLAGS` and `VCPKG_CXX_FLAGS` set to `/arch:AVX2`, and `VCPKG_BUILD_TYPE` set to `release` so that no debug binaries are built or exported. The workflow passes the triplet with an overlay triplets directory.

### The workflow

One workflow, `release.yml`, on a hosted `windows-2025` runner. It runs on `workflow_dispatch` and on a tag push `v*`. Its steps:

1. Clone vcpkg at the manifest's `builtin-baseline` commit and bootstrap it. The vcpkg the runner image carries is not used.
2. `vcpkg install` with the overlay triplet and the manifest, no binary cache.
3. `vcpkg export --zip` with a fixed output name, which exports every installed port in manifest mode.
4. Build the GDAL wheel: install the Python embeddable package of this release into a folder, install pip and the numpy version the engine pins, and build the GDAL 3.8.4 Python bindings from their PyPI source distribution in a Visual Studio developer environment whose `INCLUDE` and `LIB` carry the export's include and lib folders. The bindings' setup script uses no `gdal-config` on Windows, so the compiler's own search paths are how it finds GDAL. The wheel holds the bindings and the array module; it bundles no DLL and no data folder.
5. Collect the VC++ runtime files from the redistributable folder of the runner's Visual Studio into one zip, and record the Visual Studio version.
6. Download the Python embeddable package from its publisher and the ExifTool 64-bit Windows zip from its publisher, and compare each with the hash the publisher lists on its own page. A mismatch fails the job.
7. Write `vcpkg-list.txt` from the installed ports and versions, `LICENSES.md` from the copyright file of each port in the export and the licence of each re-hosted file, `BUILD-INFO.txt` with the runner image version, the vcpkg commit, the triplet and the Visual Studio version, and `SHA256SUMS` over every asset.
8. Run the release check (below) on the assets.
9. On a dispatch, upload the assets as a workflow artifact. On a tag, create a draft release with the assets; the maintainer publishes it.

The assets of a release: the export zip, the GDAL wheel, the VC++ runtime zip, the Python embeddable zip under its original name, the ExifTool zip under its original name, `vcpkg-list.txt`, `LICENSES.md`, `BUILD-INFO.txt`, `SHA256SUMS`.

A second workflow, `tests.yml`, runs the unit tests on a Linux runner on every push to a branch and on every pull request.

### What the engine takes from a release

The engine pins the tag and the SHA-256 of each asset it downloads, in one file of its own. Its `win32env.bat` puts the export's `bin` on `PATH`, points `GDAL_DATA` and `PROJ_LIB` into the export's `share` folders, and sets the variable GDAL's Python package reads on Windows to find its DLLs on `PATH`. Its SuperBuild takes GDAL's headers and import library from the export, not from the wheel. Those are decisions of the engine spec, stated here so that the wheel and the export are built for them.

### Versions and releases

Tags are `v<major>.<minor>.<patch>`. A port version change or a port added is a minor release; a rebuild with no manifest change (a new runner image, a fix in the workflow) is a patch release. Each release's changelog entry lists the ports whose version changed and the differences against the engine's Linux images for PROJ, Boost and libtiff.

## Testing decisions

A good test checks what a consumer of the release sees: the assets, their names, their hashes, and that the GDAL wheel and the export work together in the Python the release ships. It does not check how vcpkg built a port.

One seam: `scripts/check-release.py`. It takes a folder of assets and checks:

- `SHA256SUMS` names every asset in the folder and every hash matches.
- The export zip unpacks, and every port of `vcpkg.json` is present in the export's status file at its override version.
- `vcpkg-list.txt`, `LICENSES.md` and `BUILD-INFO.txt` exist; `LICENSES.md` has one entry per port of the export and one per re-hosted file.
- The Python embeddable zip and the ExifTool zip match the hashes recorded for them in `SHA256SUMS`, and the ExifTool executable answers `-ver` with the version in its file name.
- The GDAL wheel installs into a fresh unpacking of the Python embeddable package, imports with version 3.8.4, reports the GeoTIFF driver, opens a small GeoTIFF written by the check itself, and transforms one coordinate between two coordinate systems through PROJ, with the export's `bin` on `PATH` and `GDAL_DATA` and `PROJ_LIB` set into the export.
- The VC++ runtime zip holds the runtime DLLs the export's binaries import.

The workflow runs it on the runner after every build. The maintainer runs it by hand on a downloaded release before pinning it in the engine.

Unit tests, `tests/test_check_release.py` with `unittest`, run on Linux on every push. They cover the script's parsing and verdicts on a fixture tree: a fake export with two ports and a status file, a sums file with one wrong hash, a licence list with one port missing, a manifest with one override. The parts that need Windows (the wheel import, the ExifTool run) are behind a seam the fixture tests replace with a recorded result. Prior art: the engine's `tests/test_golden_compare.py` and `tests/test_gpu_smoke_modal.py`, which test a CI script's parsing and verdicts on recorded inputs.

Test depth per ticket:

| Ticket | Depth | Proof |
| --- | --- | --- |
| Repository files: `AGENTS.md` rules, `REVIEW.md`, `README.md`, `CHANGELOG.md`, licence | unit | review of the text; the maintainer merges it without the review loop, which needs `REVIEW.md` on the default branch |
| `vcpkg.json`, the triplet, `release.yml` with the export and the artifact upload | workflow | the workflow's run on the pull request branch, started by dispatch |
| `check-release.py` and its fixture tests, `tests.yml` | unit | the unit test run |
| GDAL wheel step and the release check step in `release.yml` | workflow | the workflow's run, with the check green |
| Re-hosted files with publisher hash checks, VC++ runtime zip, `LICENSES.md`, `BUILD-INFO.txt`, `SHA256SUMS`, draft release on tag | workflow | the workflow's run |
| First release | none | the maintainer's tag; the check run by hand on the download |

A pull request that changes `.github/` changes nothing else and lands alone, as in the engine.

## Out of scope

- Windows on ARM, a static triplet, debug binaries.
- CUDA: the CUDA runtime comes from the engine's CUDA job, not from this repository (ADR 0006).
- OpenCV and Ceres ports, unless the engine's probe asks for them.
- A binary cache, a scheduled build, a package registry.
- Signing of any asset. The engine pins hashes; the manifest signature of the task API covers the package.
- Any speed or size claim. `BUILD-INFO.txt` records versions, not durations.
- Changes to the engine's SuperBuild. The engine spec owns them.

## Further notes

- The Python embeddable package is re-hosted byte for byte. The edit of its `._pth` file that lets it import site packages is the engine's package step, so the re-hosted file keeps its publisher's hash.
- ExifTool is Perl software distributed under Perl's terms; the 64-bit Windows zip is a self-contained executable. Its licence goes into `LICENSES.md` with the others.
- The VC++ runtime files are redistributable under the terms of the Visual Studio licence for the redistributable folder; `LICENSES.md` names that term and the version copied.
- vcpkg overrides ignore the baseline's version of a port, which is how GDAL 3.8.4 is pinned under a newer baseline if the baseline is ever moved.
- The hosted runner's six-hour job limit applies. The first dispatch run gives the duration; nothing here claims one.
