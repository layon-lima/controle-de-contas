"""Testes sem instalação para o botão único do painel."""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller import server


class OneClickDesktopUpdateTests(unittest.TestCase):
    def test_sync_before_build_confirms_exact_commit(self):
        sha = "a" * 40
        commands = []
        def run(cmd, **kwargs):
            commands.append(cmd)
            response = {
                "branch": "develop",
                "pull": "Already up to date.",
                "HEAD": sha,
                "origin/develop": sha,
            }
            if cmd[1:3] == ["branch", "--show-current"]:
                output = response["branch"]
            elif cmd[1:] == ["pull", "--ff-only", "origin", "develop"]:
                output = response["pull"]
            elif cmd[1:] == ["rev-parse", "HEAD"]:
                output = response["HEAD"]
            elif cmd[1:] == ["rev-parse", "refs/remotes/origin/develop"]:
                output = response["origin/develop"]
            else:
                self.fail("Unexpected command: " + repr(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr="")

        with patch.object(server, "_run", side_effect=run):
            log = io.StringIO()
            result = server._sync_develop_before_desktop_build(log)
        self.assertEqual(result, sha)
        self.assertEqual(len(commands), 4)
        self.assertEqual(commands[1], ["git", "pull", "--ff-only", "origin", "develop"])
        self.assertIn("Fonte confirmada", log.getvalue())

    def test_failed_sync_blocks_installation(self):
        seen = []
        def run(cmd, **kwargs):
            seen.append(cmd)
            if cmd[1:3] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="develop", stderr="")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Sem acesso ao GitHub")
        with patch.object(server, "_run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "Sincronizando automaticamente"):
                server._sync_develop_before_desktop_build(io.StringIO())
        self.assertEqual(len(seen), 2)

    def test_divergent_commits_block_installation(self):
        outputs = ["develop", "Already up to date.", "a" * 40, "b" * 40]
        with patch.object(
            server, "_run", side_effect=[
                subprocess.CompletedProcess(["git"], 0, stdout=x, stderr="")
                for x in outputs
            ]
        ):
            with self.assertRaisesRegex(RuntimeError, "divergiu"):
                server._sync_develop_before_desktop_build(io.StringIO())

    def test_locked_previous_updater_never_blocks_new_build(self):
        """Regressão: Windows não permite apagar um instalador em execução."""
        script = (
            Path(__file__).resolve().parents[1]
            / "tools" / "BUILD_AND_INSTALL_DESKTOP.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Remove-Item -LiteralPath $PreferredWork", script)
        self.assertIn("catch {", script)
        self.assertIn('NewGuid().ToString', script)
        self.assertIn("$Work = Join-Path ([System.IO.Path]::GetTempPath())", script)
        self.assertIn("$LaunchExe = Join-Path $LaunchFolder", script)
        self.assertIn("Copy-Item -LiteralPath $Updater -Destination $LaunchExe", script)
        self.assertIn("Get-FileHash -LiteralPath $LaunchExe", script)
        self.assertIn("Start-Process -FilePath $LaunchExe", script)
        self.assertNotIn("Start-Process -FilePath $Updater", script)
        self.assertLess(
            script.index("Get-FileHash -LiteralPath $LaunchExe"),
            script.index("Start-Process -FilePath $LaunchExe"),
        )

    def test_job_never_starts_installer_when_sync_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(server, "APP_DATA_DIR", root), \
                 patch.object(server, "JOB_LOG_PATH", root / "job.log"), \
                 patch.object(server, "_sync_develop_before_desktop_build",
                              side_effect=RuntimeError("GitHub indisponível")), \
                 patch.object(server.subprocess, "Popen") as start_installer:
                server._run_logged_command(
                    "local_desktop_update", ["powershell", "-File", "build.ps1"], root
                )
                start_installer.assert_not_called()
                self.assertIs(server._job_snapshot()["ok"], False)
                self.assertIn("INSTALAÇÃO CANCELADA", (root / "job.log").read_text(
                    encoding="utf-8"
                ))


if __name__ == "__main__":
    unittest.main()
