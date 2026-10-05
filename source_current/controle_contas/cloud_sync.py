from __future__ import annotations

import atexit
import hashlib
import json
import os
import sqlite3
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


LOCALAPPDATA = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
)
APP_DATA_DIR = LOCALAPPDATA / "ControleContas"
DB_PATH = APP_DATA_DIR / "data" / "controle_contas.db"
CONFIG_PATH = APP_DATA_DIR / "cloud_sync.json"
LOG_PATH = APP_DATA_DIR / "cloud_sync.log"
ACCESS_PATH = APP_DATA_DIR / "ACESSO_MOBILE_CLOUD.txt"
ACTIVITY_PATH = APP_DATA_DIR / "cloud_activity.json"
POLL_SECONDS = 4
ACTIVITY_POLL_SECONDS = 10
SYNC_LOCK_HOST = "127.0.0.1"
SYNC_LOCK_PORT = 18766
SYNC_PROCESS_MARKER = "--cloud-sync-v11"
FULL_SYNC_SECONDS = 900
_PDF_HASH_CACHE: dict[str, tuple[int, int, str]] = {}


def _log(message: str) -> None:
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def load_cloud_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def cloud_enabled() -> bool:
    cfg = load_cloud_config()
    return bool(
        cfg.get("enabled")
        and str(cfg.get("base_url") or "").startswith("https://")
        and str(cfg.get("sync_token") or "")
        and str(cfg.get("view_token") or "")
    )


def get_cloud_private_link() -> str:
    cfg = load_cloud_config()
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    token = str(cfg.get("view_token") or "").strip()
    if not base or not token:
        return ""
    return f"{base}/#{token}"


def _connect() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Banco de dados não encontrado: {DB_PATH}")
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = str(path)
    cached = _PDF_HASH_CACHE.get(key)
    signature = (int(stat.st_mtime_ns), int(stat.st_size))
    if cached and cached[0] == signature[0] and cached[1] == signature[1]:
        return cached[2]

    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    digest = h.hexdigest()
    _PDF_HASH_CACHE[key] = (signature[0], signature[1], digest)
    return digest


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return 0, 0


def _database_signature() -> tuple[int, ...]:
    wal = Path(str(DB_PATH) + "-wal")
    shm = Path(str(DB_PATH) + "-shm")
    journal = Path(str(DB_PATH) + "-journal")
    values: list[int] = []
    for path in (DB_PATH, wal, shm, journal):
        values.extend(_file_signature(path))
    return tuple(values)


def _snapshot() -> tuple[list[dict], dict[int, Path]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.fornecedor,
                c.documento,
                c.pagador,
                c.referencia,
                c.valor,
                c.vencimento,
                c.codigo_pagamento,
                c.tipo_pagamento,
                c.observacoes,
                c.criado_em,
                c.atualizado_em,
                c.enviado_pagamento_em,
                c.pdf_conta,
                p.valor_pago,
                p.data_pagamento,
                p.beneficiario,
                p.documento_beneficiario,
                p.resultado_conferencia,
                p.detalhes_conferencia
            FROM contas c
            LEFT JOIN pagamentos p
              ON p.id = (
                    SELECT p2.id
                    FROM pagamentos p2
                    WHERE p2.conta_id = c.id
                    ORDER BY p2.id DESC
                    LIMIT 1
                )
            ORDER BY c.id ASC
            """
        ).fetchall()

    accounts: list[dict] = []
    pdf_paths: dict[int, Path] = {}

    for row in rows:
        item = dict(row)
        account_id = int(item["id"])
        raw_pdf = str(item.pop("pdf_conta", "") or "").strip()
        pdf_hash = ""
        pdf_name = ""
        if raw_pdf:
            p = Path(raw_pdf)
            try:
                p = p.resolve()
            except OSError:
                pass
            if p.is_file() and p.suffix.lower() == ".pdf":
                try:
                    pdf_hash = _sha256(p)
                    pdf_name = p.name
                    pdf_paths[account_id] = p
                except Exception as exc:
                    _log(f"Falha ao calcular hash do PDF {account_id}: {exc}")

        item["recebedor"] = str(item.get("fornecedor") or "")
        item["pdf_hash"] = pdf_hash
        item["pdf_name"] = pdf_name
        accounts.append(item)

    return accounts, pdf_paths


def _request(
    method: str,
    path: str,
    *,
    token: str,
    data: bytes | None = None,
    content_type: str = "application/json; charset=utf-8",
    extra_headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> tuple[int, bytes, dict]:
    cfg = load_cloud_config()
    base = str(cfg.get("base_url") or "").rstrip("/")
    url = base + path
    headers = {
        "X-Sync-Token": token,
        "User-Agent": "ControleContasDesktop/1.0",
    }
    if data is not None:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return int(response.status), body, dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return int(exc.code), body, dict(exc.headers)


def _json_request(method: str, path: str, payload: dict, token: str, timeout=60) -> dict:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    status, body, _ = _request(
        method,
        path,
        token=token,
        data=data,
        content_type="application/json; charset=utf-8",
        timeout=timeout,
    )
    try:
        decoded = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        decoded = {}
    if status < 200 or status >= 300:
        raise RuntimeError(decoded.get("erro") or f"HTTP {status}")
    return decoded


def _render_pages(pdf_path: Path):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    doc = fitz.open(str(pdf_path))
    try:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            # Boa leitura no celular sem multiplicar demais o tamanho em nuvem.
            pix = page.get_pixmap(matrix=fitz.Matrix(1.55, 1.55), alpha=False)
            try:
                data = pix.tobytes("jpeg", jpg_quality=82)
            except TypeError:
                data = pix.tobytes("jpeg")
            yield index + 1, data
    finally:
        doc.close()


def _upload_pdf(account_id: int, path: Path, expected_hash: str, token: str) -> None:
    page_count = 0
    for page_count, jpeg in _render_pages(path):
        status, body, _ = _request(
            "PUT",
            f"/api/sync/pdf/{account_id}/page/{page_count}",
            token=token,
            data=jpeg,
            content_type="image/jpeg",
            timeout=90,
        )
        if status < 200 or status >= 300:
            raise RuntimeError(f"Falha ao enviar página {page_count}: HTTP {status}")

    with path.open("rb") as f:
        pdf_bytes = f.read()
    status, body, _ = _request(
        "PUT",
        f"/api/sync/pdf/{account_id}",
        token=token,
        data=pdf_bytes,
        content_type="application/pdf",
        extra_headers={
            "X-Pdf-Hash": expected_hash,
            "X-Pdf-Name": path.name,
            "X-Pdf-Pages": str(page_count),
        },
        timeout=120,
    )
    if status < 200 or status >= 300:
        raise RuntimeError(f"Falha ao enviar PDF: HTTP {status}")

    result = _json_request(
        "POST",
        f"/api/sync/pdf/{account_id}/complete",
        {
            "hash": expected_hash,
            "name": path.name,
            "pages": page_count,
        },
        token,
        timeout=60,
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("erro") or "Falha ao concluir PDF")


def _get_json(path: str, token: str, timeout=30) -> dict:
    status, body, _ = _request("GET", path, token=token, timeout=timeout)
    try:
        decoded = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        decoded = {}
    if status < 200 or status >= 300:
        raise RuntimeError(decoded.get("erro") or f"HTTP {status}")
    return decoded


def pull_cloud_activity_once() -> dict:
    cfg = load_cloud_config()
    if not cloud_enabled():
        return {}
    token = str(cfg["sync_token"])
    payload = _get_json("/api/desktop/activity", token, timeout=20)
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        temp = ACTIVITY_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(ACTIVITY_PATH)
    except Exception as exc:
        _log(f"Falha ao salvar atividade mobile: {type(exc).__name__}: {exc}")
    return payload


def load_cloud_activity() -> dict:
    if not ACTIVITY_PATH.is_file():
        return {}
    try:
        data = json.loads(ACTIVITY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def suggest_payments(account_ids: list[int] | tuple[int, ...] | set[int]) -> dict:
    cfg = load_cloud_config()
    if not cloud_enabled():
        raise RuntimeError("Sincronização em nuvem não configurada.")

    ids = sorted({int(account_id) for account_id in account_ids if int(account_id) > 0})
    if not ids:
        return {"ok": True, "quantidade": 0, "account_ids": []}

    token = str(cfg["sync_token"])
    result = _json_request(
        "POST",
        "/api/desktop/suggest-batch",
        {"account_ids": ids},
        token,
        timeout=30,
    )
    try:
        pull_cloud_activity_once()
    except Exception:
        pass
    return result


def suggest_payment(account_id: int) -> dict:
    cfg = load_cloud_config()
    if not cloud_enabled():
        raise RuntimeError("Sincronização em nuvem não configurada.")
    token = str(cfg["sync_token"])
    result = _json_request("POST", f"/api/desktop/suggest/{int(account_id)}", {}, token, timeout=20)
    try:
        pull_cloud_activity_once()
    except Exception:
        pass
    return result


def cancel_payment_suggestion(account_id: int) -> dict:
    cfg = load_cloud_config()
    if not cloud_enabled():
        raise RuntimeError("Sincronização em nuvem não configurada.")
    token = str(cfg["sync_token"])
    status, body, _ = _request("DELETE", f"/api/desktop/suggest/{int(account_id)}", token=token, timeout=20)
    try:
        result = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        result = {}
    if status < 200 or status >= 300:
        raise RuntimeError(result.get("erro") or f"HTTP {status}")
    try:
        pull_cloud_activity_once()
    except Exception:
        pass
    return result


def sync_once() -> bool:
    cfg = load_cloud_config()
    if not cloud_enabled():
        return False
    if not DB_PATH.is_file():
        _log("Sincronização aguardando o banco de dados existir.")
        return False

    token = str(cfg["sync_token"])
    accounts, pdf_paths = _snapshot()
    synced_at = datetime.now().astimezone().isoformat(timespec="seconds")

    response = _json_request(
        "POST",
        "/api/sync/accounts",
        {"accounts": accounts, "synced_at": synced_at},
        token,
        timeout=90,
    )

    need = response.get("need_pdfs") or []
    for raw_id in need:
        account_id = int(raw_id)
        path = pdf_paths.get(account_id)
        if path is None:
            continue
        account = next((a for a in accounts if int(a["id"]) == account_id), None)
        if not account:
            continue
        expected_hash = str(account.get("pdf_hash") or "")
        try:
            _upload_pdf(account_id, path, expected_hash, token)
            _log(f"PDF da conta {account_id} sincronizado: {path.name}")
        except Exception as exc:
            _log(f"Falha no PDF da conta {account_id}: {type(exc).__name__}: {exc}")

    # Atualiza o carimbo somente depois das tentativas de PDF.
    try:
        _json_request(
            "POST",
            "/api/sync/heartbeat",
            {"synced_at": synced_at},
            token,
            timeout=30,
        )
    except Exception:
        pass

    _log(f"Sincronização concluída: {len(accounts)} contas; PDFs solicitados: {len(need)}")
    return True


def _background_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--cloud-sync", SYNC_PROCESS_MARKER]

    app_py = Path(__file__).resolve().parents[1] / "app.py"
    py = Path(sys.executable).resolve()
    pythonw = py.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else py
    return [str(runner), str(app_py), "--cloud-sync", SYNC_PROCESS_MARKER]


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    flags = 0
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    return flags


def _sync_backend_running() -> bool:
    try:
        with socket.create_connection((SYNC_LOCK_HOST, SYNC_LOCK_PORT), timeout=0.25):
            return True
    except OSError:
        return False


def _stop_legacy_sync_processes() -> None:
    if os.name != "nt":
        return

    ps_script = (
        '$marker = "' + SYNC_PROCESS_MARKER + '"; '
        'Get-CimInstance Win32_Process | '
        'Where-Object { '
        '$_.ProcessId -ne $PID -and '
        '$_.CommandLine -and '
        '"$_.CommandLine -match \'(?i)(^|\\\\s)--cloud-sync(\\\\s|$)\' -and "'
        '$_.CommandLine -notlike "*$marker*" '
        '} | ForEach-Object { '
        'Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue '
        '}'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=8,
            check=False,
        )
    except Exception:
        pass


def ensure_cloud_sync_backend() -> None:
    if not cloud_enabled():
        return

    if _sync_backend_running():
        return

    _stop_legacy_sync_processes()

    if _sync_backend_running():
        return

    try:
        subprocess.Popen(
            _background_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
            close_fds=True,
        )
        _log("Sincronizador V11 iniciado em segundo plano.")
    except Exception as exc:
        _log(f"Falha ao iniciar sincronizador V11: {type(exc).__name__}: {exc}")


def ensure_cloud_sync_autostart() -> None:
    if os.name != "nt" or not cloud_enabled():
        return
    try:
        import winreg

        command = subprocess.list2cmdline(_background_command())
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "ControleContasCloudSync", 0, winreg.REG_SZ, command)
        _log("Sincronizador V11 registrado no início do Windows.")
    except Exception as exc:
        _log(f"Falha no autostart da nuvem: {type(exc).__name__}: {exc}")


def _acquire_sync_lock() -> socket.socket | None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        listener.bind((SYNC_LOCK_HOST, SYNC_LOCK_PORT))
        listener.listen(2)
        listener.settimeout(0.5)
        return listener
    except OSError:
        try:
            listener.close()
        except Exception:
            pass
        return None


def run_cloud_sync_forever() -> None:
    listener = _acquire_sync_lock()
    if listener is None:
        _log("Sincronizador V11 não iniciou: já existe uma instância ativa.")
        return

    _log("Sincronizador V11 iniciado.")
    last_signature: tuple[int, ...] | None = None
    last_full = 0.0
    next_allowed_sync = 0.0
    last_activity_pull = 0.0

    try:
        while True:
            try:
                try:
                    conn, _ = listener.accept()
                    conn.close()
                except socket.timeout:
                    pass
                except OSError:
                    pass

                if cloud_enabled() and DB_PATH.is_file():
                    signature = _database_signature()
                    now = time.monotonic()
                    changed = signature != last_signature
                    periodic = (now - last_full) >= FULL_SYNC_SECONDS

                    if (now - last_activity_pull) >= ACTIVITY_POLL_SECONDS:
                        try:
                            pull_cloud_activity_once()
                        except Exception as exc:
                            _log(f"Falha ao consultar atividade mobile: {type(exc).__name__}: {exc}")
                        last_activity_pull = time.monotonic()

                    if (changed or periodic) and now >= next_allowed_sync:
                        time.sleep(1.0)
                        sync_once()
                        last_signature = _database_signature()
                        last_full = time.monotonic()
                        next_allowed_sync = last_full + 2.0

            except Exception as exc:
                _log(f"Erro de sincronização V11: {type(exc).__name__}: {exc}")

            time.sleep(POLL_SECONDS)
    finally:
        try:
            listener.close()
        except Exception:
            pass
