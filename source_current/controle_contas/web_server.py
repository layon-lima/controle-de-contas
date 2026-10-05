from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import winreg
from datetime import date, datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


HOST = "127.0.0.1"
PORT = 8765
DIAS_PROXIMA = 5
DOMINIO_MOBILE = "https://contas.fazendanovohorizonte.com"
TUNNEL_NAME = "controle-contas"

LOCALAPPDATA = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
)
APP_DATA_DIR = LOCALAPPDATA / "ControleContas"
DB_PATH = APP_DATA_DIR / "data" / "controle_contas.db"
CONFIG_PATH = APP_DATA_DIR / "web_mobile_config.json"
ACCESS_PATH = APP_DATA_DIR / "ACESSO_MOBILE.txt"
LOG_PATH = APP_DATA_DIR / "web_mobile.log"
CLOUDFLARED_LOG_PATH = APP_DATA_DIR / "cloudflared_mobile.log"

_ui_integrated = False


def _web_dir() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return root / "mobile_site"
    return Path(__file__).resolve().parents[1] / "mobile_site"


def _log(message: str) -> None:
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            stamp = datetime.now().isoformat(timespec="seconds")
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _load_or_create_config() -> dict:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

    config: dict = {}
    if CONFIG_PATH.is_file():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            config = {}

    token = str(config.get("view_token") or "").strip()
    if len(token) < 32:
        token = secrets.token_urlsafe(40)

    domain = str(config.get("domain") or DOMINIO_MOBILE).strip().rstrip("/")
    config = {
        "domain": domain,
        "view_token": token,
        "port": PORT,
        "tunnel": TUNNEL_NAME,
    }

    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    link = f"{domain}/#{token}"
    ACCESS_PATH.write_text(
        "CONTROLE DE CONTAS - ACESSO MOBILE\n\n"
        f"LINK PRIVATIVO:\n{link}\n\n"
        "IMPORTANTE:\n"
        "Quem tiver este link consegue visualizar as contas e os PDFs anexados.\n"
        "Não compartilhe publicamente.\n",
        encoding="utf-8",
    )
    return config


def get_private_link() -> str:
    try:
        from .cloud_sync import get_cloud_private_link
        cloud_link = get_cloud_private_link()
        if cloud_link:
            return cloud_link
    except Exception:
        pass

    cfg = _load_or_create_config()
    return f"{cfg['domain']}/#{cfg['view_token']}"


def _secure_equal(a: str, b: str) -> bool:
    try:
        return secrets.compare_digest(str(a or ""), str(b or ""))
    except Exception:
        return False


def _status_conta(vencimento: str, data_pagamento: str | None) -> str:
    if str(data_pagamento or "").strip():
        return "Paga"

    try:
        due = date.fromisoformat(str(vencimento)[:10])
        days = (due - date.today()).days
    except Exception:
        return "Em dia"

    if days < 0:
        return "Vencida"
    if days <= DIAS_PROXIMA:
        return "Próxima"
    return "Em dia"


def _connect() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Banco de dados não encontrado: {DB_PATH}")

    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _pdf_for_account(account_id: int) -> Path | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT pdf_conta FROM contas WHERE id = ?",
            (int(account_id),),
        ).fetchone()

    if not row:
        return None

    raw = str(row["pdf_conta"] or "").strip()
    if not raw:
        return None

    path = Path(raw)
    try:
        path = path.resolve()
    except OSError:
        pass

    return path if path.is_file() and path.suffix.lower() == ".pdf" else None



def _pdf_metadata(account_id: int) -> dict:
    path = _pdf_for_account(account_id)
    if path is None:
        raise FileNotFoundError("PDF não encontrado para esta conta.")

    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF não está disponível para visualizar o PDF.") from exc

    document = None
    try:
        document = fitz.open(str(path))
        return {
            "paginas": int(document.page_count),
            "nome": path.name,
        }
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass


def _render_pdf_page(account_id: int, page_number: int) -> bytes:
    path = _pdf_for_account(account_id)
    if path is None:
        raise FileNotFoundError("PDF não encontrado para esta conta.")

    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF não está disponível para visualizar o PDF.") from exc

    document = None
    try:
        document = fitz.open(str(path))
        page_index = int(page_number) - 1
        if page_index < 0 or page_index >= document.page_count:
            raise IndexError("Página inválida.")

        page = document.load_page(page_index)

        # Renderiza com boa nitidez para celular sem depender do visualizador
        # PDF nativo do navegador.
        pix = page.get_pixmap(matrix=fitz.Matrix(1.85, 1.85), alpha=False)
        return pix.tobytes("png")
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass


def _list_accounts() -> list[dict]:
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
            ORDER BY
                CASE WHEN p.id IS NULL THEN 0 ELSE 1 END,
                c.vencimento ASC,
                c.id DESC
            """
        ).fetchall()

    accounts: list[dict] = []
    for row in rows:
        item = dict(row)
        raw_pdf = str(item.pop("pdf_conta", "") or "").strip()
        item["tem_pdf"] = bool(raw_pdf and Path(raw_pdf).is_file())
        item["recebedor"] = str(item.get("fornecedor") or "")
        item["status"] = _status_conta(
            str(item.get("vencimento") or ""),
            item.get("data_pagamento"),
        )
        accounts.append(item)
    return accounts


def _db_info() -> dict:
    stat = DB_PATH.stat()
    return {
        "ultima_atualizacao": datetime.fromtimestamp(
            stat.st_mtime
        ).isoformat(timespec="seconds"),
        "tamanho_bytes": stat.st_size,
    }


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_web_dir()), **kwargs)

    def log_message(self, fmt, *args):
        _log("HTTP " + (fmt % args))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        cfg = _load_or_create_config()
        received = self.headers.get("X-View-Token", "")
        return _secure_equal(received, cfg["view_token"])

    def _send_pdf(self, account_id: int) -> None:
        path = _pdf_for_account(account_id)
        if path is None:
            self._json({"ok": False, "erro": "PDF não encontrado para esta conta."}, 404)
            return

        try:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                f'inline; filename="conta-{int(account_id)}.pdf"',
            )
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            _log(f"Erro ao enviar PDF {account_id}: {type(exc).__name__}: {exc}")

    def _send_pdf_page(self, account_id: int, page_number: int) -> None:
        try:
            data = _render_pdf_page(account_id, page_number)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", "inline")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError as exc:
            self._json({"ok": False, "erro": str(exc)}, 404)
        except IndexError as exc:
            self._json({"ok": False, "erro": str(exc)}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            _log(
                f"Erro ao renderizar PDF {account_id}, página {page_number}: "
                f"{type(exc).__name__}: {exc}"
            )
            self._json(
                {"ok": False, "erro": "Não foi possível renderizar esta página."},
                500,
            )

    def do_GET(self):
        route = urlparse(self.path).path

        if route.startswith("/api/"):
            if not self._authorized():
                self._json({"ok": False, "erro": "Acesso não autorizado."}, 401)
                return

            if route == "/api/contas":
                try:
                    accounts = _list_accounts()
                    self._json({
                        "ok": True,
                        "quantidade": len(accounts),
                        "contas": accounts,
                        "meta": _db_info(),
                    })
                except Exception as exc:
                    _log(f"Erro API contas: {type(exc).__name__}: {exc}")
                    self._json({
                        "ok": False,
                        "erro": f"{type(exc).__name__}: {exc}",
                    }, 500)
                return

            if route == "/api/status":
                try:
                    self._json({
                        "ok": True,
                        "quantidade": len(_list_accounts()),
                        **_db_info(),
                    })
                except Exception as exc:
                    self._json({
                        "ok": False,
                        "erro": f"{type(exc).__name__}: {exc}",
                    }, 500)
                return

            if route.startswith("/api/pdf/"):
                parts = [part for part in route.strip("/").split("/") if part]
                # /api/pdf/{id}
                # /api/pdf/{id}/meta
                # /api/pdf/{id}/page/{n}
                try:
                    if len(parts) < 3:
                        raise ValueError
                    account_id = int(parts[2])
                except ValueError:
                    self._json({"ok": False, "erro": "ID de conta inválido."}, 400)
                    return

                if len(parts) == 4 and parts[3] == "meta":
                    try:
                        self._json({
                            "ok": True,
                            **_pdf_metadata(account_id),
                        })
                    except FileNotFoundError as exc:
                        self._json({"ok": False, "erro": str(exc)}, 404)
                    except Exception as exc:
                        _log(
                            f"Erro ao ler metadados PDF {account_id}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        self._json({
                            "ok": False,
                            "erro": "Não foi possível preparar o documento.",
                        }, 500)
                    return

                if len(parts) == 5 and parts[3] == "page":
                    try:
                        page_number = int(parts[4])
                    except ValueError:
                        self._json({"ok": False, "erro": "Página inválida."}, 400)
                        return
                    self._send_pdf_page(account_id, page_number)
                    return

                if len(parts) == 3:
                    # Mantido para compatibilidade / download direto.
                    self._send_pdf(account_id)
                    return

                self._json({"ok": False, "erro": "Rota de PDF inválida."}, 404)
                return

            self._json({"ok": False, "erro": "Rota não encontrada."}, 404)
            return

        return super().do_GET()


def _port_is_busy() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((HOST, PORT)) == 0


def _cloudflared_running() -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "cloudflared.exe" in (result.stdout or "").lower()
    except Exception:
        return False


def _find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found

    candidates = [
        Path.home() / ".cloudflared" / "cloudflared.exe",
        LOCALAPPDATA / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "cloudflared"
        / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "cloudflared"
        / "cloudflared.exe",
    ]

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def start_cloudflare_tunnel_if_needed() -> bool:
    if os.name != "nt":
        return False
    if _cloudflared_running():
        _log("Cloudflared já está em execução.")
        return True

    exe = _find_cloudflared()
    if not exe:
        _log("Cloudflared não encontrado. O servidor local continua disponível.")
        return False

    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_file = CLOUDFLARED_LOG_PATH.open("a", encoding="utf-8")
        subprocess.Popen(
            [exe, "tunnel", "run", TUNNEL_NAME],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=_detached_creation_flags(),
            close_fds=True,
        )
        _log(f"Cloudflared iniciado para o túnel '{TUNNEL_NAME}'.")
        return True
    except Exception as exc:
        _log(f"Falha ao iniciar cloudflared: {type(exc).__name__}: {exc}")
        return False


def run_mobile_server_forever() -> None:
    _load_or_create_config()

    if _port_is_busy():
        _log(f"Servidor mobile já ativo na porta {PORT}; processo auxiliar encerrado.")
        return

    threading.Thread(
        target=start_cloudflare_tunnel_if_needed,
        name="ControleContasTunnelStarter",
        daemon=True,
    ).start()

    try:
        server = ThreadingHTTPServer((HOST, PORT), _Handler)
        _log(f"Servidor web independente iniciado em http://{HOST}:{PORT}")
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _log(f"Servidor web independente falhou: {type(exc).__name__}: {exc}")


def _background_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--mobile-server"]

    app_py = Path(__file__).resolve().parents[1] / "app.py"
    python_exe = Path(sys.executable).resolve()

    # No desenvolvimento usa pythonw.exe: sem janela de CMD e independente
    # do terminal do VS Code.
    pythonw = python_exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else python_exe
    return [str(runner), str(app_py), "--mobile-server"]


def _detached_creation_flags() -> int:
    if os.name != "nt":
        return 0

    flags = 0
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    # Quando permitido pelo Windows, evita que o processo auxiliar permaneça
    # preso ao job/processo do terminal que iniciou o app.
    flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    return flags


def ensure_mobile_backend() -> bool:
    _load_or_create_config()

    if _port_is_busy():
        return True

    try:
        subprocess.Popen(
            _background_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
            cwd=str(Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]),
        )
        _log("Processo web mobile independente solicitado.")
        return True
    except Exception as exc:
        _log(f"Falha ao iniciar processo web independente: {type(exc).__name__}: {exc}")
        return False


def ensure_mobile_autostart() -> None:
    """Inicia o backend mobile automaticamente no login do Windows."""
    if os.name != "nt":
        return

    try:
        command = subprocess.list2cmdline(_background_command())
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                "ControleContasMobile",
                0,
                winreg.REG_SZ,
                command,
            )
        _log(
            "Inicialização automática do mobile registrada no Windows "
            "(sem janela de terminal)."
        )
    except Exception as exc:
        _log(
            f"Falha ao registrar inicialização automática: "
            f"{type(exc).__name__}: {exc}"
        )


def integrate_mobile_button() -> None:
    global _ui_integrated
    if _ui_integrated:
        return

    try:
        from . import ui
    except Exception as exc:
        _log(f"Não foi possível integrar botão mobile: {exc}")
        return

    original_init = ui.JanelaPrincipal.__init__

    def init_with_mobile(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        try:
            header = self.findChild(ui.QFrame, "headerPanel")
            layout = header.layout() if header else None
            if layout is None:
                return

            button = ui.QPushButton("📱  Acesso mobile")
            button.setObjectName("secondaryButton")
            button.setToolTip(
                "Copia o link privativo do painel mobile para a área de transferência."
            )

            def show_mobile_access():
                ensure_mobile_backend()
                link = get_private_link()
                ui.QApplication.clipboard().setText(link)
                ui.QMessageBox.information(
                    self,
                    "Acesso mobile",
                    "O link privativo foi copiado para a área de transferência.\n\n"
                    f"{link}\n\n"
                    "O painel continua ativo mesmo depois de fechar esta janela, "
                    "desde que o computador permaneça ligado.",
                )

            button.clicked.connect(show_mobile_access)

            new_account_index = -1
            for i in range(layout.count()):
                item = layout.itemAt(i)
                widget = item.widget() if item else None
                if isinstance(widget, ui.QPushButton) and "Nova conta" in widget.text():
                    new_account_index = i
                    break

            if new_account_index >= 0:
                layout.insertWidget(new_account_index, button)
            else:
                layout.addWidget(button)

            self._web_mobile_button = button
        except Exception as exc:
            _log(f"Falha ao adicionar botão mobile: {type(exc).__name__}: {exc}")

    ui.JanelaPrincipal.__init__ = init_with_mobile
    _ui_integrated = True
