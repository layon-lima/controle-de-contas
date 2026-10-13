from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

CONTROLLER_VERSION = "0.5.1"
ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCALAPPDATA = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
)
APP_DATA_DIR = LOCALAPPDATA / "ControleContas"
CONFIG_PATH = APP_DATA_DIR / "controller.json"
SYNC_LOG_PATH = APP_DATA_DIR / "cloud_sync.log"

DEFAULT_CONFIG = {
    "repo_full_name": "layon-lima/controle-de-contas",
    "host": "127.0.0.1",
    "port": 8766,
    "app_executable": "",
    "auto_open_browser": True,
}


def _load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.is_file():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
    except Exception:
        pass
    return cfg


def _save_default_config_if_missing() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _run(cmd: list[str], timeout: int = 15, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _find_app_executable(cfg: dict) -> Path | None:
    configured = str(cfg.get("app_executable") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))

    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_programs = LOCALAPPDATA / "Programs"
    candidates.extend(
        [
            program_files / "Controle de Contas" / "Controle de Contas.exe",
            local_programs / "Controle de Contas" / "Controle de Contas.exe",
            ROOT / "dist" / "Controle de Contas" / "Controle de Contas.exe",
        ]
    )

    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


def _process_running(image_name: str) -> bool:
    if os.name != "nt":
        return False
    try:
        result = _run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            timeout=8,
        )
        return image_name.casefold() in result.stdout.casefold()
    except Exception:
        return False


def _sync_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 18766), timeout=0.25):
            return True
    except OSError:
        return False


def _installed_version(exe: Path | None) -> str:
    if not exe or os.name != "nt":
        return ""
    try:
        escaped = str(exe).replace("'", "''")
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Item '{escaped}').VersionInfo.FileVersion",
        ]
        result = _run(cmd, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _tail(path: Path, max_lines: int = 120) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def _gh_available() -> bool:
    return shutil.which("gh") is not None


_GITHUB_CACHE = {"repo": "", "at": 0.0, "data": None}
_GITHUB_CACHE_LOCK = threading.Lock()


def _github_snapshot(repo: str, force: bool = False) -> dict:
    now = time.time()
    with _GITHUB_CACHE_LOCK:
        cached = _GITHUB_CACHE.get("data")
        if (
            not force
            and cached is not None
            and _GITHUB_CACHE.get("repo") == repo
            and now - float(_GITHUB_CACHE.get("at") or 0) < 30
        ):
            return dict(cached)
    data = {
        "available": False,
        "repo": repo,
        "main_sha": "",
        "main_message": "",
        "develop_sha": "",
        "develop_message": "",
        "develop_check_status": "",
        "develop_check_url": "",
        "latest_build_version": "",
        "latest_build_run_id": None,
        "latest_build_status": "",
        "latest_build_url": "",
        "latest_release_version": "",
        "latest_release_tag": "",
        "latest_release_url": "",
        "latest_release_asset": "",
        "error": "",
    }

    if not _gh_available():
        data["error"] = "GitHub CLI (gh) não encontrado."
        with _GITHUB_CACHE_LOCK:
            _GITHUB_CACHE.update({"repo": repo, "at": time.time(), "data": dict(data)})
        return data

    try:
        commit = _run(["gh", "api", f"repos/{repo}/commits/main"], timeout=15)
        if commit.returncode != 0:
            data["error"] = commit.stderr.strip() or "Falha ao consultar GitHub."
            return data

        commit_json = json.loads(commit.stdout)
        data["main_sha"] = str(commit_json.get("sha") or "")[:10]
        data["main_message"] = (
            commit_json.get("commit", {}).get("message", "").splitlines()[0]
        )

        develop = _run(["gh", "api", f"repos/{repo}/commits/develop"], timeout=15)
        if develop.returncode == 0:
            develop_json = json.loads(develop.stdout)
            data["develop_sha"] = str(develop_json.get("sha") or "")[:10]
            data["develop_message"] = (
                develop_json.get("commit", {}).get("message", "").splitlines()[0]
            )

        # O build aprovado pode ter sido produzido em uma branch de release/PR,
        # então não limitamos a consulta à main.
        runs = _run(
            [
                "gh",
                "api",
                f"repos/{repo}/actions/runs?status=success&per_page=100",
            ],
            timeout=25,
        )
        if runs.returncode == 0:
            runs_json = json.loads(runs.stdout)
            candidates = []
            for run in runs_json.get("workflow_runs", []):
                name = str(run.get("name") or "")
                match = re.search(r"Build updater\s+(\d+\.\d+\.\d+)", name, re.I)
                if not match:
                    continue
                version = match.group(1)
                candidates.append((tuple(int(x) for x in version.split(".")), version, run))

            if candidates:
                _key, version, run = max(candidates, key=lambda item: item[0])
                data["latest_build_version"] = version
                data["latest_build_run_id"] = run.get("id")
                data["latest_build_status"] = run.get("conclusion") or run.get("status") or ""
                data["latest_build_url"] = run.get("html_url") or ""

        releases = _run(
            ["gh", "api", f"repos/{repo}/releases?per_page=30"],
            timeout=20,
        )
        if releases.returncode == 0:
            releases_json = json.loads(releases.stdout)
            release_candidates = []
            for release in releases_json if isinstance(releases_json, list) else []:
                if release.get("draft"):
                    continue
                tag = str(release.get("tag_name") or "")
                match = re.fullmatch(r"v?(\d+\.\d+\.\d+)", tag, re.I)
                if not match:
                    continue
                version = match.group(1)
                wanted_asset = f"ATUALIZADOR_CONTROLE_DE_CONTAS_{version}.exe"
                asset = next(
                    (
                        item for item in (release.get("assets") or [])
                        if str(item.get("name") or "") == wanted_asset
                    ),
                    None,
                )
                if not asset:
                    continue
                release_candidates.append(
                    (
                        tuple(int(x) for x in version.split(".")),
                        version,
                        tag,
                        release,
                        asset,
                    )
                )

            if release_candidates:
                _key, version, tag, release, asset = max(
                    release_candidates,
                    key=lambda item: item[0],
                )
                data["latest_release_version"] = version
                data["latest_release_tag"] = tag
                data["latest_release_url"] = release.get("html_url") or ""
                data["latest_release_asset"] = asset.get("name") or ""

        develop_runs = _run(
            [
                "gh",
                "api",
                f"repos/{repo}/actions/runs?branch=develop&per_page=20",
            ],
            timeout=20,
        )
        if develop_runs.returncode == 0:
            develop_runs_json = json.loads(develop_runs.stdout)
            for run in develop_runs_json.get("workflow_runs", []):
                if str(run.get("name") or "") != "Development checks":
                    continue
                data["develop_check_status"] = (
                    run.get("conclusion") or run.get("status") or ""
                )
                data["develop_check_url"] = run.get("html_url") or ""
                break

        data["available"] = True
        with _GITHUB_CACHE_LOCK:
            _GITHUB_CACHE.update({"repo": repo, "at": time.time(), "data": dict(data)})
        return data
    except Exception as exc:
        data["error"] = f"{type(exc).__name__}: {exc}"
        with _GITHUB_CACHE_LOCK:
            _GITHUB_CACHE.update({"repo": repo, "at": time.time(), "data": dict(data)})
        return data


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value or "")
    return tuple(int(x) for x in numbers[:4]) if numbers else (0,)


def _status_payload() -> dict:
    cfg = _load_config()
    exe = _find_app_executable(cfg)
    installed = _installed_version(exe)
    github = _github_snapshot(str(cfg.get("repo_full_name") or DEFAULT_CONFIG["repo_full_name"]))
    build_version = str(github.get("latest_build_version") or "")
    release_version = str(github.get("latest_release_version") or "")
    latest = max(
        (value for value in (build_version, release_version) if value),
        key=_version_tuple,
        default="",
    )

    return {
        "controller_version": CONTROLLER_VERSION,
        "installed_version": installed,
        "latest_version": latest,
        "update_known": bool(latest),
        "update_available": bool(
            installed and latest and _version_tuple(latest) > _version_tuple(installed)
        ),
        "app_running": _portable_online(),
        "sync_running": _sync_running(),
        "app_executable": str(exe) if exe else "",
        "cloud_log_path": str(SYNC_LOG_PATH),
        "cloud_log_tail": _tail(SYNC_LOG_PATH, 12),
        "github": github,
        "config_path": str(CONFIG_PATH),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


PORTABLE_URL = "http://127.0.0.1:8767/"


def _portable_online() -> bool:
    """Somente localhost; nao consultar banco, rede externa ou arquivos do usuario."""
    try:
        with urllib.request.urlopen(PORTABLE_URL + "health", timeout=0.75) as response:
            payload = json.load(response)
        return bool(payload.get("online"))
    except (OSError, ValueError, TypeError):
        return False


def _portable_command() -> list[str] | None:
    """Priorizar Python 3.13 instalado; nao depender do antigo EXE."""
    python313 = LOCALAPPDATA / "Programs" / "Python" / "Python313" / "python.exe"
    if python313.is_file():
        return [str(python313), "-u"]
    py = shutil.which("py")
    if py:
        return [py, "-3.13", "-u"]
    if sys.version_info[:2] == (3, 13) and sys.executable:
        return [sys.executable, "-u"]
    return None


def _start_app() -> tuple[bool, str]:
    """Abrir a versao web principal, mesmo se o atualizador falhou nos testes."""
    if _portable_online():
        webbrowser.open(PORTABLE_URL)
        return True, "Controle de Contas web aberto."

    launcher = ROOT / "portable_app" / "launcher.py"
    if not launcher.is_file():
        return False, "Iniciador web nao encontrado. Sincronize os arquivos do projeto."
    command = _portable_command()
    if not command:
        return False, "Python 3.13 nao encontrado. Verifique a instalacao do Python."

    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_path = APP_DATA_DIR / "panel_open_portable.log"
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [*command, str(launcher)],
                cwd=str(ROOT), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
            )
        # A interface web e aberta pelo proprio launcher quando esta pronta.
        # Nao executar Git pull, suite de testes, atualizador nem EXE antigo.
        for _ in range(16):
            if _portable_online():
                return True, "Controle de Contas web iniciado."
            if process.poll() is not None:
                return False, ("O aplicativo web encerrou na inicializacao. "
                               "Verifique o log " + str(log_path))
            time.sleep(0.4)
        return True, "Controle de Contas web iniciando; aguarde alguns segundos."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "Falha ao iniciar a versao web: " + str(exc)


def _stop_app() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Ação disponível apenas no Windows."
    try:
        result = _run(
            ["taskkill", "/IM", "Controle de Contas.exe", "/T", "/F"],
            timeout=12,
        )
        if result.returncode in (0, 128):
            return True, "Controle de Contas fechado."
        return False, result.stderr.strip() or result.stdout.strip()
    except Exception as exc:
        return False, f"Falha ao fechar: {exc}"


def _restart_app() -> tuple[bool, str]:
    _stop_app()
    time.sleep(0.8)
    return _start_app()


_JOB_LOCK = threading.Lock()
_JOB_STATE = {
    "running": False,
    "kind": "",
    "message": "Pronto para atualizar.",
    "stage": "Aguardando",
    "progress": 0,
    "ok": None,
    "started_at": "",
    "finished_at": "",
}
JOB_LOG_PATH = APP_DATA_DIR / "controller_job.log"


def _set_job_state(**values) -> None:
    with _JOB_LOCK:
        _JOB_STATE.update(values)


def _recent_log_text(path: Path, limit: int = 128_000) -> str:
    """Lê apenas o final do log, sem carregar gigabytes de output do build."""
    try:
        with path.open("rb") as file:
            length = file.seek(0, 2)
            file.seek(max(0, length - limit))
            raw = file.read()
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _job_snapshot() -> dict:
    with _JOB_LOCK:
        data = dict(_JOB_STATE)
    log = _recent_log_text(JOB_LOG_PATH, 96_000)
    # A porcentagem corresponde a marcos confirmados, não a tempo estimado.
    milestones = list(re.finditer(r"CC_PROGRESS\|(\d{1,3})\|([^\r\n]+)", log))
    if milestones and data.get("running"):
        latest = milestones[-1]
        pct = min(99, max(0, int(latest.group(1))))
        if pct >= int(data.get("progress") or 0):
            data["progress"] = pct
            data["stage"] = latest.group(2).strip()
    if data.get("running") and data.get("kind") == "full_update":
        mobile_stages = {
            "1": (90, "Preparando publicação mobile"),
            "2": (92, "Autenticando no Cloudflare"),
            "3": (94, "Validando migrações mobile"),
            "4": (97, "Publicando aplicativo mobile"),
            "5": (99, "Verificando servidor mobile"),
        }
        if int(data.get("progress") or 0) >= 89:
            for match in re.finditer(r"\[([1-5])/5\]", log):
                p, stage = mobile_stages[match.group(1)]
                if p >= int(data["progress"]):
                    data["progress"], data["stage"] = p, stage
    data["progress"] = min(100, max(0, int(data.get("progress") or 0)))
    # Memoriza o último marco atingido: logs longos de compilação podem
    # fazer as linhas antigas saírem dos últimos 96 KB do arquivo.
    if data.get("running"):
        with _JOB_LOCK:
            if _JOB_STATE.get("running"):
                if data["progress"] >= int(_JOB_STATE.get("progress") or 0):
                    _JOB_STATE["progress"] = data["progress"]
                    _JOB_STATE["stage"] = data["stage"]
    # Mantém compatibilidade com consumidores antigos deste endpoint.
    data["log_tail"] = log.splitlines()[-80:]
    return data


def _diagnostic_log() -> str:
    snapshot = _job_snapshot()
    lines = [
        "CONTROLE DE CONTAS - DIAGNOSTICO DO PAINEL",
        "Resultado: " + ("EM ANDAMENTO" if snapshot["running"] else
                          "CONCLUIDO" if snapshot.get("ok") is True else
                          "FALHOU" if snapshot.get("ok") is False else "NAO EXECUTADO"),
        "Etapa: " + str(snapshot.get("stage") or "-"),
        "Progresso por etapas: " + str(snapshot["progress"]) + "%",
        "Mensagem: " + str(snapshot.get("message") or "-"),
        "Inicio: " + str(snapshot.get("started_at") or "-"),
        "Fim: " + str(snapshot.get("finished_at") or "-"),
        "",
        "LOG DO PROCESSO (com final preservado):",
    ]
    try:
        size = JOB_LOG_PATH.stat().st_size
    except OSError:
        size = 0
    if size > 200_000:
        lines.append("[Log grande: incluindo início e últimos 180 KB]")
        with JOB_LOG_PATH.open("rb") as file:
            beginning = file.read(12_000).decode("utf-8", errors="replace")
        lines.append(beginning)
        lines.append("\n[... conteúdo intermediário omitido ...]\n")
        lines.append(_recent_log_text(JOB_LOG_PATH, 180_000))
    else:
        lines.append(_recent_log_text(JOB_LOG_PATH, 200_000))
    return "\n".join(lines)


def _sync_develop_before_desktop_build(log) -> str:
    """Atualiza a fonte local e confere a revisão ANTES de construir o EXE.

    Chamado pela tarefa do painel; nunca exige que o usuário execute Git
    ou PowerShell manualmente. Não força reset nem sobrescreve alterações locais.
    """
    def step(command: list[str], description: str, timeout: int = 120):
        log.write("\n" + description + "\n")
        log.flush()
        result = _run(command, timeout=timeout, cwd=ROOT)
        output = "\n".join(
            value.strip() for value in (result.stdout, result.stderr)
            if value and value.strip()
        )
        if output:
            log.write(output + "\n")
        log.flush()
        if result.returncode != 0:
            raise RuntimeError(
                description + " falhou. " + (output or "Verifique a conexão com o GitHub.")
            )
        return result.stdout.strip()

    branch = step(
        ["git", "branch", "--show-current"],
        "Verificando a branch local...",
        timeout=20,
    )
    if branch != "develop":
        raise RuntimeError(
            "O projeto está na branch '" + (branch or "(desconhecida)") +
            "', mas o aplicativo usa develop. "
            "A instalação foi interrompida sem sobrescrever arquivos locais."
        )
    step(
        ["git", "pull", "--ff-only", "origin", "develop"],
        "Sincronizando automaticamente a develop do GitHub...",
    )
    local = step(["git", "rev-parse", "HEAD"], "Verificando revisão local...", 20)
    remote = step(
        ["git", "rev-parse", "refs/remotes/origin/develop"],
        "Verificando revisão remota...", 20,
    )
    if not local or local != remote:
        raise RuntimeError(
            "A versão local divergiu da develop; nenhuma instalação foi iniciada."
        )
    log.write("\nFonte confirmada em " + local[:10] + ". Iniciando testes e instalação...\n")
    log.flush()
    return local


def _run_logged_command(kind: str, cmd: list[str], cwd: Path) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _set_job_state(
        running=True,
        kind=kind,
        message="Executando...",
        ok=None,
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        finished_at="",
    )

    try:
        with JOB_LOG_PATH.open("w", encoding="utf-8", errors="replace") as log:
            log.write("$ " + " ".join(cmd) + "\n\n")
            log.flush()
            # O botão Instalar faz pull + checagem de revisão + build.
            # Se o pull falhar, não executamos sequer o PowerShell de instalação.
            if kind == "local_desktop_update":
                try:
                    _sync_develop_before_desktop_build(log)
                except Exception as exc:
                    log.write("\nINSTALAÇÃO CANCELADA: " + str(exc) + "\n")
                    log.flush()
                    raise
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            code = proc.wait()

        if code == 0:
            _set_job_state(
                running=False,
                ok=True,
                message="Concluído com sucesso.",
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        else:
            _set_job_state(
                running=False,
                ok=False,
                message=f"Processo terminou com código {code}.",
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
    except Exception as exc:
        _set_job_state(
            running=False,
            ok=False,
            message=f"{type(exc).__name__}: {exc}",
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )


def _run_full_update() -> None:
    """Único botão: sincronizar -> testar/abrir portátil -> publicar mobile."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _set_job_state(
        running=True, kind="full_update", ok=None,
        progress=2, stage="Sincronizando com GitHub",
        message="Atualização em andamento.",
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        finished_at="",
    )
    stage = "Sincronização"
    try:
        with JOB_LOG_PATH.open("w", encoding="utf-8", errors="replace") as log:
            log.write("ATUALIZACAO COMPLETA: GITHUB > TESTES > PORTATIL > MOBILE\n")
            log.flush()
            _sync_develop_before_desktop_build(log)
            _set_job_state(progress=15, stage="Código sincronizado")
            portable_script = ROOT / "tools" / "UPDATE_AND_OPEN_PORTABLE.ps1"
            mobile_script = ROOT / "tools" / "DEPLOY_MOBILE_PERMANENT_ACCESS.ps1"
            if not portable_script.is_file():
                raise FileNotFoundError("Atualizador portátil não encontrado.")
            if not mobile_script.is_file():
                raise FileNotFoundError("Script de publicação mobile não encontrado.")
            def run_script(script: Path, label: str):
                log.write("\n" + label + "\n")
                log.flush()
                process = subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(script)],
                    cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return process.wait()

            stage = "Atualização do aplicativo portátil"
            if run_script(portable_script, "ETAPA: VALIDANDO E ABRINDO O PORTÁTIL") != 0:
                raise RuntimeError(
                    "A validação ou abertura do portátil falhou. "
                    "O aplicativo instalado permanece inalterado. "
                    "A publicação mobile não foi iniciada."
                )
            _set_job_state(progress=89, stage="Preparando publicação mobile")
            stage = "Publicação mobile"
            if run_script(mobile_script, "ETAPA: PUBLICANDO MOBILE") != 0:
                raise RuntimeError(
                    "O portátil foi aberto, mas a publicação mobile falhou. "
                    "Use Copiar log para enviar o erro."
                )
            log.write("\nATUALIZACAO COMPLETA: portatil e mobile finalizados.\n")
            log.flush()
        _set_job_state(
            running=False, ok=True, progress=100,
            stage="Concluído", message="Portátil e mobile atualizados com sucesso.",
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
    except Exception as exc:
        description = f"{stage}: {exc}"
        try:
            with JOB_LOG_PATH.open("a", encoding="utf-8") as log:
                log.write("\nFALHA: " + description + "\n")
        except OSError:
            pass
        # Nunca marca 100% ou sucesso depois de uma falha intermediária.
        _set_job_state(
            running=False, ok=False, stage="Falhou em: " + stage,
            message=description,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )


def _start_full_update() -> tuple[bool, str]:
    with _JOB_LOCK:
        if _JOB_STATE.get("running"):
            return False, "Já há uma atualização em andamento."
        _JOB_STATE.update(
            running=True, kind="full_update", ok=None,
            progress=1, stage="Preparando",
            message="Iniciando atualização completa...",
        )
    try:
        threading.Thread(target=_run_full_update, daemon=True).start()
    except Exception as exc:
        _set_job_state(
            running=False, ok=False, stage="Falha ao iniciar", message=str(exc)
        )
        return False, "Não foi possível iniciar a atualização: " + str(exc)
    return True, "Sincronização, abertura do portátil e publicação mobile iniciadas."


def _start_mobile_deploy() -> tuple[bool, str]:
    with _JOB_LOCK:
        if _JOB_STATE.get("running"):
            return False, "Já existe uma operação em andamento no painel."

    script = ROOT / "tools" / "DEPLOY_MOBILE_PERMANENT_ACCESS.ps1"
    if not script.is_file():
        return False, "Script de publicação mobile ainda não está disponível localmente."

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    threading.Thread(
        target=_run_logged_command,
        args=("mobile_deploy", cmd, ROOT),
        daemon=True,
    ).start()
    return True, "Publicação do mobile iniciada. O painel mostrará o andamento."


def _pull_develop() -> tuple[bool, str]:
    # Evita pull concorrente enquanto o build de desktop já sincroniza a fonte.
    with _JOB_LOCK:
        if _JOB_STATE.get("running"):
            return False, "Uma operação está em andamento. Aguarde no painel."
    # --ff-only evita merges inesperados na cópia local.
    try:
        result = _run(["git", "pull", "--ff-only", "origin", "develop"],
                      timeout=90, cwd=ROOT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Falha na sincronizacao: {exc}"
    if result.returncode != 0:
        return False, ((result.stderr or result.stdout or "").strip()
                       or "Falha ao atualizar a copia local.")
    with _GITHUB_CACHE_LOCK:
        _GITHUB_CACHE.update({"repo": "", "at": 0.0, "data": None})
    return True, ((result.stdout.strip() or "Arquivos atualizados.")
                  + " O servidor permanece ativo. Reinicie pelo atalho apenas "
                    "se houver atualizacoes no backend Python.")


def _start_local_desktop_update() -> tuple[bool, str]:
    # Reserva a tarefa ANTES de criar a thread, evitando clique duplo.
    with _JOB_LOCK:
        if _JOB_STATE.get("running"):
            return False, "Já existe uma operação em andamento no painel."
        script = ROOT / "tools" / "BUILD_AND_INSTALL_DESKTOP.ps1"
        if not script.is_file():
            return False, "O instalador local não foi encontrado. Nenhum arquivo foi alterado."
        _JOB_STATE.update(
            running=True, kind="local_desktop_update", ok=None,
            message="Aguardando sincronização automática com o GitHub...",
        )

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    try:
        threading.Thread(
            target=_run_logged_command,
            args=("local_desktop_update", cmd, ROOT),
            daemon=True,
        ).start()
    except Exception as exc:
        _set_job_state(running=False, ok=False,
                       message="Não foi possível iniciar a atualização: " + str(exc))
        return False, "Não foi possível iniciar a atualização: " + str(exc)
    return True, "Sincronização, testes e instalação iniciados em uma única operação."


def _install_latest_update() -> tuple[bool, str]:
    cfg = _load_config()
    repo = str(cfg.get("repo_full_name") or DEFAULT_CONFIG["repo_full_name"])
    github = _github_snapshot(repo, force=True)

    if not github.get("available"):
        return False, github.get("error") or "GitHub indisponível."

    build_version = str(github.get("latest_build_version") or "")
    release_version = str(github.get("latest_release_version") or "")
    version = max(
        (value for value in (build_version, release_version) if value),
        key=_version_tuple,
        default="",
    )
    if not version:
        return False, "Nenhum atualizador aprovado foi encontrado no GitHub."

    tmp_root = Path(tempfile.mkdtemp(prefix="controle_contas_update_"))

    try:
        result = None

        if (
            release_version == version
            and github.get("latest_release_tag")
            and github.get("latest_release_asset")
        ):
            result = _run(
                [
                    "gh",
                    "release",
                    "download",
                    str(github["latest_release_tag"]),
                    "--repo",
                    repo,
                    "--pattern",
                    str(github["latest_release_asset"]),
                    "--dir",
                    str(tmp_root),
                ],
                timeout=180,
            )

        if (
            (result is None or result.returncode != 0)
            and build_version == version
            and github.get("latest_build_run_id")
        ):
            artifact = f"ATUALIZADOR_CONTROLE_DE_CONTAS_{version}"
            result = _run(
                [
                    "gh",
                    "run",
                    "download",
                    str(github["latest_build_run_id"]),
                    "--repo",
                    repo,
                    "--name",
                    artifact,
                    "--dir",
                    str(tmp_root),
                ],
                timeout=180,
            )

        if result is None or result.returncode != 0:
            message = ""
            if result is not None:
                message = result.stderr.strip() or result.stdout.strip()
            return False, message or "Falha ao baixar o atualizador."

        exe_candidates = list(tmp_root.rglob("ATUALIZADOR_CONTROLE_DE_CONTAS_*.exe"))
        if not exe_candidates:
            exe_candidates = list(tmp_root.rglob("*.exe"))
        if not exe_candidates:
            return False, "O atualizador foi baixado, mas o instalador .exe não foi encontrado."

        updater = exe_candidates[0]
        _stop_app()
        subprocess.Popen(
            [str(updater)],
            cwd=str(updater.parent),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return True, f"Atualizador {version} iniciado."
    except Exception as exc:
        return False, f"Falha ao atualizar: {type(exc).__name__}: {exc}"


class Handler(BaseHTTPRequestHandler):
    server_version = "ControleContasController/" + CONTROLLER_VERSION

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({
                "ok": True,
                "controller_version": CONTROLLER_VERSION,
                "port": int(_load_config().get("port") or DEFAULT_CONFIG["port"]),
            })
            return
        if path == "/api/status":
            self._json(_status_payload())
            return
        if path == "/api/logs":
            self._json({"lines": _tail(SYNC_LOG_PATH, 250)})
            return
        if path == "/api/job":
            self._json(_job_snapshot())
            return
        if path == "/api/diagnostics":
            self._json({"text": _diagnostic_log()})
            return
        if path in ("/", "/index.html"):
            self._static(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        actions = {
            "/api/app/start": _start_app,
            "/api/app/stop": _stop_app,
            "/api/app/restart": _restart_app,
            "/api/update/install": _install_latest_update,
            "/api/update/local-desktop": _start_local_desktop_update,
            "/api/update/full": _start_full_update,
            "/api/mobile/deploy": _start_mobile_deploy,
            "/api/panel/pull": _pull_develop,
        }
        action = actions.get(path)
        if not action:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        ok, message = action()
        self._json(
            {"ok": ok, "message": message},
            HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
        )
        # Mantem a API local ativa apos sincronizar com o GitHub.

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    _save_default_config_if_missing()
    cfg = _load_config()
    host = str(cfg.get("host") or "127.0.0.1")
    port = int(cfg.get("port") or 8765)

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"

    print(f"Painel Controle de Contas: {url}")
    if bool(cfg.get("auto_open_browser", True)) and "--no-browser" not in sys.argv:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
