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
    def test_panel_opens_web_portable_not_retired_desktop_exe(self):
        with patch.object(server, "_portable_online", side_effect=[False, True]), \
             patch.object(server, "_portable_command", return_value=["python", "-u"]), \
             patch.object(server.subprocess, "Popen") as process, \
             patch.object(server, "_find_app_executable") as legacy:
            process.return_value.poll.return_value = None
            ok, message = server._start_app()
        self.assertTrue(ok)
        self.assertIn("web", message)
        legacy.assert_not_called()
        args = process.call_args.args[0]
        self.assertIn("portable_app", args[-1])
        self.assertTrue(args[-1].endswith("launcher.py"))

    def test_panel_opens_browser_without_starting_another_server(self):
        with patch.object(server, "_portable_online", return_value=True), \
             patch.object(server.webbrowser, "open") as browser, \
             patch.object(server.subprocess, "Popen") as start:
            ok, _ = server._start_app()
        self.assertTrue(ok)
        browser.assert_called_once_with("http://127.0.0.1:8767/")
        start.assert_not_called()

    def test_panel_frontend_has_independent_open_button(self):
        page = (server.ROOT / "controller" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="openAppBtn"', page)
        self.assertIn("request('/api/app/start'", page)


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

    def test_already_closed_app_does_not_abort_completed_build(self):
        """A ausencia do processo e normal; Inno Setup faz o fechamento."""
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools" / "BUILD_AND_INSTALL_DESKTOP.ps1").read_text(
            encoding="utf-8"
        )
        installer = (
            root / "source_current" / "Controle_de_Contas_1.4.5.iss"
        ).read_text(encoding="utf-8")
        # Este comando antigo emitia NativeCommandError no Windows PowerShell
        # quando o aplicativo ja estava fechado.
        self.assertNotIn('taskkill /IM "Controle de Contas.exe"', script)
        self.assertIn("Start-Process -FilePath $LaunchExe", script)
        self.assertIn("CloseApplications=yes", installer)
        self.assertIn("function PrepareToInstall", installer)
        self.assertIn(
            "o instalador cuidara disso automaticamente", script
        )

    def test_self_update_restarts_powershell_before_build(self):
        """Se git pull mudar o próprio PS1, o processo antigo não pode compilar."""
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools" / "BUILD_AND_INSTALL_DESKTOP.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("$LoadedScriptHash", script)
        self.assertIn("$DiskScriptHash", script)
        self.assertIn("if ($LoadedScriptHash -ne $DiskScriptHash)", script)
        self.assertIn("& powershell.exe @RestartArgs", script)
        self.assertLess(
            script.index("git -C $Root pull --ff-only origin develop"),
            script.index("if ($LoadedScriptHash -ne $DiskScriptHash)"),
        )
        self.assertLess(
            script.index("& powershell.exe @RestartArgs"),
            script.index("=== Controle de Contas $Version - build local ==="),
        )
        self.assertNotIn('taskkill /IM "Controle de Contas.exe"', script)

    def test_single_button_panel_and_legacy_compatibility(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "controller" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(html.count("<button "), 2)
        self.assertIn('id="runBtn"', html)
        self.assertIn('id="copyBtn"', html)
        self.assertIn('role="progressbar"', html)
        self.assertIn('id="serverStatus"', html)
        self.assertNotIn('<pre', html)
        self.assertNotIn('id="jobLog"', html)
        self.assertNotIn('id="logs"', html)
        self.assertIn("async function startLegacy()", html)
        self.assertIn("async function runAll()", html)
        self.assertIn("async function copyLog()", html)
        self.assertIn("post('/api/update/full'", html.replace(
            "request('/api/update/full'", "post('/api/update/full'"
        ))
        start = html.index("async function startLegacy(){")
        end = html.index("async function runAll(){", start)
        legacy = html[start:end]
        self.assertLess(
            legacy.index("request('/api/panel/pull'"),
            legacy.index("request('/api/update/local-desktop'"),
        )
        self.assertIn("request('/api/diagnostics')", html)
        self.assertIn("request('/api/mobile/deploy'", html)

    def test_full_pipeline_waits_for_desktop_and_mobile(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            called = []
            class FakeProcess:
                def wait(self):
                    return 0
            def popen(args, **kwargs):
                called.append(str(args[-1]))
                return FakeProcess()
            fake_state = {
                "running": False, "kind": "", "message": "",
                "stage": "Aguardando", "progress": 0, "ok": None,
                "started_at": "", "finished_at": "",
            }
            with patch.object(server, "ROOT", root), \
                 patch.object(server, "APP_DATA_DIR", dest), \
                 patch.object(server, "JOB_LOG_PATH", dest / "job.log"), \
                 patch.object(server, "_JOB_STATE", fake_state), \
                 patch.object(server, "_sync_develop_before_desktop_build",
                              return_value="a" * 40), \
                 patch.object(server.subprocess, "Popen", side_effect=popen):
                server._run_full_update()
                snap = server._job_snapshot()
                self.assertTrue(snap["ok"])
                self.assertEqual(snap["progress"], 100)
                self.assertEqual(len(called), 2)
                self.assertIn("UPDATE_AND_OPEN_PORTABLE.ps1", called[0])
                self.assertIn("DEPLOY_MOBILE_PERMANENT_ACCESS.ps1", called[1])
                self.assertIn("Portátil e mobile", server._diagnostic_log())

    def test_desktop_failure_skips_mobile_and_never_reaches_100(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tempdir:
            dest = Path(tempdir)
            calls = []
            class FailedProcess:
                def wait(self):
                    return 1
            def popen(args, **kwargs):
                calls.append(str(args[-1]))
                return FailedProcess()
            fake_state = {
                "running": False, "kind": "", "message": "",
                "stage": "Aguardando", "progress": 0, "ok": None,
                "started_at": "", "finished_at": "",
            }
            with patch.object(server, "ROOT", root), \
                 patch.object(server, "APP_DATA_DIR", dest), \
                 patch.object(server, "JOB_LOG_PATH", dest / "job.log"), \
                 patch.object(server, "_JOB_STATE", fake_state), \
                 patch.object(server, "_sync_develop_before_desktop_build",
                              return_value="a" * 40), \
                 patch.object(server.subprocess, "Popen", side_effect=popen):
                server._run_full_update()
                state = server._job_snapshot()
                self.assertFalse(state["ok"])
                self.assertLess(state["progress"], 100)
                self.assertEqual(len(calls), 1)
                self.assertIn("A publicação mobile não foi iniciada.", state["message"])

    def test_real_progress_markers_and_installer_wait(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools" / "BUILD_AND_INSTALL_DESKTOP.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('CC_PROGRESS|30|', script)
        self.assertIn('CC_PROGRESS|70|', script)
        self.assertIn('CC_PROGRESS|88|', script)
        self.assertIn('-PassThru -ErrorAction Stop', script)
        self.assertIn('$Installation.WaitForExit()', script)
        self.assertIn('$Installation.ExitCode -ne 0', script)
        self.assertIn('"/VERYSILENT"', script)
        self.assertIn('"/SUPPRESSMSGBOXES"', script)
        self.assertIn('"/NORESTART"', script)
        self.assertIn('"/CLOSEAPPLICATIONS"', script)
        self.assertIn('/LOG=', script)
        self.assertIn("Get-Content -LiteralPath $InstallerLog -Tail 70", script)
        self.assertNotIn('Start-Process -FilePath $LaunchExe -WorkingDirectory', script)
        self.assertIn('/api/update/full', (
            root / "controller" / "server.py"
        ).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tempdir:
            log = Path(tempdir) / "job.log"
            log.write_text("CC_PROGRESS|70|Montando instalador\\n", encoding="utf-8")
            state = {
                "running": True, "kind": "full_update", "message": "Executando",
                "stage": "Inicio", "progress": 15, "ok": None,
            }
            with patch.object(server, "JOB_LOG_PATH", log), \
                 patch.object(server, "_JOB_STATE", state):
                snap = server._job_snapshot()
                self.assertEqual(snap["progress"], 70)
                self.assertIn("Montando", snap["stage"])
                with log.open("a", encoding="utf-8") as file:
                    file.write("x" * 120_000)
                persisted = server._job_snapshot()
                self.assertEqual(persisted["progress"], 70)

    def test_one_button_no_longer_runs_installer_for_portable(self):
        """Painel antigo e novo atualizam o portátil, sem substituir o EXE."""
        root = Path(__file__).resolve().parents[1]
        legacy = (root / "tools" / "BUILD_AND_INSTALL_DESKTOP.ps1").read_text(
            encoding="utf-8"
        )
        portable = (root / "tools" / "UPDATE_AND_OPEN_PORTABLE.ps1").read_text(
            encoding="utf-8"
        )
        backend = (root / "controller" / "server.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (-not $LegacyDesktop)", legacy)
        self.assertIn("UPDATE_AND_OPEN_PORTABLE.ps1", legacy)
        self.assertIn('if ($LASTEXITCODE -ne 0)', legacy)
        self.assertIn('$Installation.WaitForExit()', legacy)  # instalador legado preservado
        self.assertIn('UPDATE_AND_OPEN_PORTABLE.ps1', backend)
        self.assertIn('CONTROLLER_VERSION = "0.5.0"', backend)
        self.assertNotIn('BUILD_AND_INSTALL_DESKTOP.ps1"\n            mobile_script', backend)
        self.assertIn("unittest", portable)
        self.assertIn("http://127.0.0.1:8767/health", portable)
        self.assertIn('$Response.revision -eq $TargetCommit', portable)
        self.assertIn('CC_PROGRESS|', portable)
        self.assertNotIn('PyInstaller', portable)
        self.assertNotIn('Inno Setup', portable)

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
