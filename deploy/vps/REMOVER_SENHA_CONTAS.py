#!/usr/bin/env python3
"""Remove APENAS Basic Auth das rotas CONTAS 3 no Caddy existente.

Sem alterar SQLite, PDFs, Worker, R2, DNS, outras rotas ou containers.
A autenticacao foi originalmente adicionada por este projeto.
ATENCAO: sem ela, contas e acoes ficam acessiveis a quem abrir a URL publica.
"""
from __future__ import annotations

import re
import subprocess
import sys

import publicar_web_original as pub


def command(*args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=35)
    if p.returncode:
        raise RuntimeError("Comando falhou: " + " ".join(args[:3]) + " (" + str(p.returncode) + ")")
    return p.stdout.strip()


def main():
    caddy = pub.docker_info(pub.CADDY)
    if not caddy.get("State", {}).get("Running"):
        raise RuntimeError("Caddy indisponivel; nenhum arquivo alterado.")
    path = pub.host_caddyfile(caddy)
    text = path.read_text(encoding="utf-8")
    if text.count(pub.START) != 1 or text.count(pub.END) != 1:
        raise RuntimeError("Marcadores do Controle de Contas inconsistentes; nada alterado.")

    start = text.index(pub.START)
    end = text.index(pub.END, start) + len(pub.END)
    block = text[start:end]
    if "contas3-backend:8767" not in block:
        raise RuntimeError("Nao localizei o proxy original do Contas 3; nada alterado.")
    for other in ("gfarm", "pluv"):
        if "reverse_proxy " + other in block.lower():
            raise RuntimeError("Outro aplicativo aparece no bloco: operacao cancelada.")

    rule = (r"(?m)^    basic_auth \{\n"
            r"        contas \$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}\n"
            r"    \}\n")
    stripped, count = re.subn(rule, "", block)
    if count == 0:
        if "basic_auth" in block or "basicauth" in block:
            raise RuntimeError("Existe outra regra de autenticacao; nao removerei algo desconhecido.")
        print("CONTAS 3: sem tela de usuario/senha — nenhuma mudanca necessaria.")
        return
    if count > 2:
        raise RuntimeError("Numero inesperado de rotas autenticadas. Nada alterado.")

    changed = text[:start] + stripped + text[end:]
    if changed == text:
        raise RuntimeError("Nao foi possivel preparar alteracao.")
    try:
        # Mesmo inode: Caddyfile pode estar montado como arquivo Docker.
        path.write_text(changed, encoding="utf-8")
        command("docker", "exec", pub.CADDY, "caddy", "validate",
                "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        command("docker", "exec", pub.CADDY, "caddy", "reload",
                "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
    except Exception:
        path.write_text(text, encoding="utf-8")
        try:
            command("docker", "exec", pub.CADDY, "caddy", "reload",
                    "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        except Exception:
            pass
        raise

    print("CONTAS 3: TELA DE USUARIO/SENHA REMOVIDA.")
    print("Nenhum aplicativo foi reiniciado e nenhum banco foi alterado.")
    print("ATENCAO: contas e operacoes estao publicas a quem acessar a URL.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERRO AO REMOVER LOGIN:", error, file=sys.stderr)
        raise SystemExit(1)
