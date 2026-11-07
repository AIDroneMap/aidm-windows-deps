"""Exercise the workflow's draft command without publishing a release."""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


class DraftReleaseTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        step = self.workflow.split("      - name: Create draft from checked assets\n", 1)[1]
        self.script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        assets = self.root / "assets"
        assets.mkdir()
        self.names = ["SHA256SUMS", "aidm-windows-deps.zip", "GDAL-3.8.4.whl"]
        for name in self.names:
            (assets / name).write_text("fixture")
        self.environment = {
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v1.0.0",
            "GITHUB_REF_NAME": "v1.0.0", "GH_REPO": "AIDroneMap/aidm-windows-deps",
        }

    def execute(self, **environment):
        with patch.dict(os.environ, {**self.environment, **environment}), \
                patch("pathlib.Path.iterdir", return_value=iter((self.root / "assets").iterdir())):
            exec(compile(self.script, str(WORKFLOW), "exec"), {})

    def test_tag_creates_only_a_draft_with_all_checked_assets(self):
        with patch("subprocess.run") as run:
            self.execute()
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gh", "release", "create", "v1.0.0"])
        self.assertEqual(command[4:7], sorted(str(self.root / "assets" / name) for name in self.names))
        self.assertIn("--draft", command)
        self.assertIn("--verify-tag", command)
        self.assertEqual(run.call_args.kwargs, {"check": True})

    def test_dispatch_and_branch_push_cannot_create_releases(self):
        for environment in ({"GITHUB_EVENT_NAME": "workflow_dispatch"},
                            {"GITHUB_REF": "refs/heads/main"}):
            with self.subTest(environment=environment), patch("subprocess.run") as run:
                with self.assertRaisesRegex(SystemExit, "Only a version tag push"):
                    self.execute(**environment)
                run.assert_not_called()

    def test_upload_error_fails_the_job(self):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.execute()

    def test_empty_artifact_cannot_create_a_draft(self):
        for path in (self.root / "assets").iterdir():
            path.unlink()
        with patch("subprocess.run") as run:
            with self.assertRaisesRegex(SystemExit, "has no assets"):
                self.execute()
            run.assert_not_called()

    def test_release_check_precedes_upload_and_draft_is_tag_only(self):
        check = self.workflow.index("python scripts/check-release.py assets --manifest vcpkg.json")
        upload = self.workflow.index("      - name: Upload dependency assets")
        self.assertLess(check, upload)
        self.assertIn("if ($LASTEXITCODE -ne 0) { throw 'The release asset check failed.' }",
                      self.workflow[check:upload])
        draft = self.workflow.split("  draft:\n", 1)[1]
        self.assertIn("needs: export", draft)
        self.assertIn("if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')", draft)
        self.assertNotIn("contents: write", self.workflow.split("  draft:\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
