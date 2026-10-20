#!/usr/bin/env python3
"""Publica o Controle de Contas ORIGINAL via Caddy HTTPS com autenticacao.

Uso na VPS: python3 deploy/vps/publicar_web_original.py
Nao publica nenhuma porta nova, nao edita bancos nem mexe em GFARM/PLUV.
Reusa o Caddy existente e preserva todos os blocos de sites.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/vps/compose.portable-web.yml"
DATA = Path("/opt/apps/contas-runtime")
SETTINGS = DATA / "public-site.env"
DB = DATA / "data/controle_contas.db"
CADDY = "pluv-https-caddy-1"
WEB = "contas-web-original-contas-web-1"
START = "# BEGIN CONTAS3 SECURE HTTPS MANAGED"
END = "# END CONTAS3 SECURE HTTPS MANAGED"


class Abort(Exception):
    pass


def cmd(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(list(args), input=input_text, text=True, capture_output=True, timeout=95)
    if result.returncode:
        # stderr pode conter detalhes de credenciais da configuracao de outros sites.
        # Nao imprimir logs brutos de containers de terceiros.
        raise Abort("comando nao concluido: " + " ".join(str(x) for x in args[:4]) +
                    " (codigo " + str(result.returncode) + ")")
    return result.stdout.strip()


def docker_info(container: str) -> dict:
    data = json.loads(cmd("docker", "inspect", container))
    if not data:
        raise Abort("container nao encontrado: " + container)
    return data[0]


def host_caddyfile(caddy: dict) -> Path:
    for mount in caddy["Mounts"]:
        if mount.get("Type") != "bind":
            continue
        dest = mount["Destination"].rstrip("/")
        source = Path(mount["Source"])
        if dest == "/etc/caddy/Caddyfile" and source.is_file():
            return source
        if dest == "/etc/caddy" and (source / "Caddyfile").is_file():
            return source / "Caddyfile"
    raise Abort("Caddyfile nao esta em volume bind do host. Nada foi alterado.")


def pick_network(caddy: dict) -> str:
    nets = caddy["NetworkSettings"]["Networks"]
    names = sorted(n for n in nets if n not in {"bridge", "host", "none"})
    if not names:
        raise Abort("Caddy sem rede Docker de projeto.")
    return next((n for n in names if "pluv" in n.lower()), names[0])


def domain_name() -> str:
    explicit = os.environ.get("CONTAS_PUBLIC_HOST", "").strip().lower()
    if explicit:
        host = explicit
    else:
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=12) as response:
                ip = response.read(80).decode("ascii").strip()
            if not re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", ip):
                raise ValueError("IPv4 inesperado")
        except (OSError, ValueError) as exc:
            raise Abort("Nao consegui identificar o IP da VPS: " + type(exc).__name__)
        host = "contas." + ip.replace(".", "-") + ".sslip.io"
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{1,235})[a-z0-9]", host):
        raise Abort("Dominio publico invalido.")
    return host


def replacement(config: str, block: str, domain: str) -> str:
    if START in config or END in config:
        if config.count(START) != 1 or config.count(END) != 1:
            raise Abort("Marcadores do Controle de Contas inconsistentes no Caddyfile.")
        a = config.index(START)
        b = config.index(END) + len(END)
        return config[:a] + block + config[b:]
    if re.search(r"(?m)^\s*" + re.escape(domain) + r"\s*\{", config):
        raise Abort("Este dominio ja tem uma rota no Caddy. Nao sobrescrevi.")
    return config.rstrip() + "\n\n" + block + "\n"


def main() -> int:
    try:
        if not DB.is_file() or DB.stat().st_size < 1024:
            raise Abort("Banco real na VPS nao encontrado. Nenhuma publicacao.")
        if not COMPOSE.is_file():
            raise Abort("Arquivo de implantacao ausente; atualize a main.")
        if not docker_info(WEB).get("State", {}).get("Running"):
            raise Abort("App original nao esta rodando. Inicie-o antes de publicar.")
        caddy = docker_info(CADDY)
        if not caddy.get("State", {}).get("Running"):
            raise Abort("Caddy nao esta rodando.")
        path = host_caddyfile(caddy)
        network = pick_network(caddy)
        domain = domain_name()
        origin = "https://" + domain
        # O mesmo servidor web existente. Aplicar somente a origem HTTPS.
        env = dict(os.environ)
        env["CONTAS_PUBLIC_ORIGIN"] = origin
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "up", "-d",
             "--no-deps", "--force-recreate", "contas-web"],
            env=env, check=True, timeout=95,
        )
        web = docker_info(WEB)
        if not web.get("State", {}).get("Running"):
            raise Abort("Aplicativo nao reiniciou corretamente.")
        if network not in web["NetworkSettings"]["Networks"]:
            cmd("docker", "network", "connect", "--alias", "contas3-backend", network, WEB)

        # Sem login adicional no Caddy, conforme preferencia do usuario.
        block = (
            f"{START}\n"
            f"{domain} {{\n"
            "    encode zstd gzip\n"
            "    reverse_proxy contas3-backend:8767 {\n"
            "        header_up -Authorization\n"
            "    }\n"
            "}\n"
            f"{END}"
        )
        old = path.read_text(encoding="utf-8")
        new = replacement(old, block, domain)
        if new != old:
            # Caddyfile pode estar bind-mounted como arquivo: reescrever NO MESMO
            # inode para a configuracao nova aparecer dentro do container.
            try:
                with path.open("w", encoding="utf-8") as handle:
                    handle.write(new)
                cmd("docker", "exec", CADDY, "caddy", "validate",
                    "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
                cmd("docker", "exec", CADDY, "caddy", "reload",
                    "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
            except BaseException:
                with path.open("w", encoding="utf-8") as handle:
                    handle.write(old)
                try:
                    cmd("docker", "exec", CADDY, "caddy", "reload",
                        "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
                except Exception:
                    pass
                raise
        # Persistir a origem para futuras chamadas a INICIAR_WEB_ORIGINAL.sh.
        temp = SETTINGS.with_suffix(".env.tmp")
        temp.write_text("CONTAS_PUBLIC_ORIGIN=" + origin + "\n"
                        "CONTAS_CADDY_NETWORK=" + network + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, SETTINGS)
        # Validar HTTPS de verdade sem precisar de DNS local ou credenciais.
        # HTTP 200 confirma certificado funcional e interface web acessivel.
        https_ok = False
        for attempt in range(7):
            probe = subprocess.run(
                ["curl", "--noproxy", "*", "--resolve",
                 domain + ":443:127.0.0.1", "-sS", "-o", "/dev/null",
                 "-w", "%{http_code}", "--connect-timeout", "5",
                 "--max-time", "10", origin + "/"],
                capture_output=True, text=True, timeout=13,
            )
            if probe.returncode == 0 and probe.stdout.strip() == "200":
                https_ok = True
                break
            time.sleep(4)
        if https_ok:
            print("TESTE HTTPS: OK (sem tela extra de usuario/senha)")
        else:
            print("AVISO: HTTPS ainda nao confirmou HTTP 200. O Caddy pode estar emitindo certificado.")
        print("\n===== CONTAS 3 — ACESSO HTTPS CONFIGURADO =====")
        print("ENDERECO:", origin)
        print("ACESSO: sem tela extra de usuario/senha.")
        print("As contas e pagamentos permanecem no banco original transferido.")
        print("GFARM e PLUVIOMETROS: rotas preservadas; nenhum container deles foi reiniciado.")
        print("Aguarde ate 1 minuto para o certificado HTTPS ser emitido.")
        return 0
    except (Abort, OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        print("PUBLICACAO NAO CONCLUIDA:", str(exc), file=sys.stderr)
        print("Nao execute comandos de limpeza. Envie apenas o erro, sem senhas.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
