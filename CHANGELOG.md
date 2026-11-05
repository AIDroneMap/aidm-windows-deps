## Unreleased

- Reported unsupported ZIP compression as a failed release check and continued with the remaining verdicts. Evidence: `test_unsupported_zip_compression_reports_failure_and_continues` in `tests/test_check_release.py` checks the command output and exit status.
- Added the release checker and its Linux fixture tests for asset hashes, export ports and versions, licences, recorded Windows results and imported runtime DLLs. Evidence: `tests/test_check_release.py`, run by the `Unit tests` check in `tests.yml`.
- Added repository rules, the review mandate, a README and the AGPL-3.0 licence for the repository's own files. Evidence: review of the text against [issue #3](https://github.com/AIDroneMap/aidm-windows-deps/issues/3) and the [first release spec](docs/specs/0001-first-deps-release.md).
- Recorded the origin of the first manifest's planned port list and overrides: the list published by ODX for its 3.8.4 line. The manifest will be written for AIDM, with GDAL 3.8.4 added as a port, as specified in [spec #1](https://github.com/AIDroneMap/aidm-windows-deps/issues/1). No dependency build is included in this change.
