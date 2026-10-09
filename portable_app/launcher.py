"""Atualiza o servidor portátil em execução sem matar aplicativos instalados."""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
import webbrowser

import server  # módulo irmão; funciona sem instalar o pacote no Python


def restart_portable_if_running():
    base = f"http://{server.HOST}:{server.PORT}"
    with socket.socket() as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((server.HOST, server.PORT))
        except OSError:
            return
    try:
        with urllib.request.urlopen(base + "/", timeout=2) as response:
            html = response.read(250_000).decode("utf-8", errors="replace")
        if "<title>Controle de Contas</title>" not in html:
            raise RuntimeError("A porta já está ocupada por outro serviço.")
        m = re.search(r'window\.__CONTAS_TOKEN__=("[^"]+")', html)
        if m is None:
            raise RuntimeError("Servidor antigo não suporta reinício automático.")
        token = json.loads(m.group(1))
        body = b"{}"
        request = urllib.request.Request(
            base + "/api/control/stop", data=body, method="POST",
            headers={"X-Local-Token": token,
                     "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=3) as reply:
            result = json.load(reply)
            if not result.get("ok"):
                raise RuntimeError("O servidor portátil não confirmou a saída.")
    except (urllib.error.URLError, ValueError, RuntimeError) as exc:
        print("Servidor existente não foi desligado:", exc)
        webbrowser.open(base)
        raise SystemExit(1) from exc

    for _ in range(60):
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((server.HOST, server.PORT)) != 0:
                return
        time.sleep(0.15)
    raise RuntimeError("Servidor anterior demorou demais para encerrar.")


if __name__ == "__main__":
    restart_portable_if_running()
    server.main()
