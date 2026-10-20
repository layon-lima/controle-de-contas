#!/usr/bin/env python3
"""Migra URL do Controle de Contas para o dominio definitivo COM TESTE E REVERSAO.

Nao altera o banco, R2, Worker, containers GFARM/PLUV nem o aplicativo em codigo.
Executa somente na VPS apos registro DNS A em modo 'DNS only' apontar para ela.

Fase 1: adiciona dominio novo mantendo o SSLIP com funcionamento inalterado.
Fase 2: confere HTTPS/TLS real do dominio novo com 401 protegido.
Fase 3: atualiza apenas a origem HTTP do container do Controle de Contas.
Fase 4: transforma o dominio antigo em redirect 302. Preserva outros sites.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import publicar_web_original as old

DOMAIN = "contas-cloud.fazendanovohorizonte.com"
IP_EXPECTED = "187.127.63.29"
OLD_DOMAIN = "contas.187-127-63-29.sslip.io"
SETTINGS = Path("/opt/apps/contas-runtime/public-site.env")
AUTH = Path("/opt/apps/contas-private/public-auth.json")
CADDY_CONTAINER = "pluv-https-caddy-1"
WEB_CONTAINER = "contas-web-original-contas-web-1"
COMPOSE = Path(__file__).with_name("compose.portable-web.yml")
ACTIVE_DB = Path("/opt/apps/contas-runtime/data/controle_contas.db")


def fail(message):
    raise RuntimeError(message)


def run(argv: list[str], timeout=90, input_text=None) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, input=input_text, timeout=timeout)
    if result.returncode != 0:
        # Nao devolver logs com senhas/configuracoes de outros apps.
        fail("comando falhou: " + " ".join(argv[:3]) + " (status " + str(result.returncode) + ")")
    return result.stdout.strip()


def get_origin() -> tuple[str, str, str]:
    if not SETTINGS.is_file():
        fail("Configuracao da publicacao atual nao encontrada.")
    contents = SETTINGS.read_text(encoding="utf-8")
    origin = re.search(r"^CONTAS_PUBLIC_ORIGIN=(https://[a-zA-Z0-9.-]+)$", contents, re.M)
    network = re.search(r"^CONTAS_CADDY_NETWORK=([a-zA-Z0-9_.-]+)$", contents, re.M)
    if origin is None or network is None:
        fail("Configuracao HTTPS atual invalida; nada alterado.")
    if origin.group(1) not in {"https://" + OLD_DOMAIN, "https://" + DOMAIN}:
        fail("Publicacao atual usa outro dominio; nada alterado.")
    return contents, origin.group(1), network.group(1)


def dns_preflight():
    try:
        resolved = {r[4][0] for r in socket.getaddrinfo(DOMAIN, 443, socket.AF_INET, socket.SOCK_STREAM)}
    except OSError:
        fail("DNS A ainda nao resolvido. Cloudflare > DNS > Adicionar registro A 'contas-cloud' -> " +
             IP_EXPECTED + ", DNS only (nuvem cinza).")
    if IP_EXPECTED not in resolved:
        fail("DNS nao aponta diretamente para a VPS (" + IP_EXPECTED +
             "). Ajuste o registro A para DNS only (nuvem cinza) nesta etapa.")
    print("DNS: OK, apontando para a VPS.")


def caddy_managed_block(config: str) -> tuple[str, str, str]:
    if config.count(old.START) != 1 or config.count(old.END) != 1:
        fail("Nao localizei o bloco unico gerenciado do CONTAS 3 no Caddyfile.")
    i = config.index(old.START)
    j = config.index(old.END) + len(old.END)
    original_block = config[i:j]
    if OLD_DOMAIN not in original_block:
        fail("Bloco atual nao corresponde ao dominio provisório; nao alterei.")
    # Se alguem criou rota fora do bloco gerenciado, nao mexer nela.
    if DOMAIN in (config[:i] + config[j:]):
        fail("Dominio definitivo ja consta de outro bloco do Caddy; revisar manualmente.")
    return config[:i], config[j:], original_block


def set_caddy(caddyfile: Path, content: str):
    # Escrever no inode existente preserva bind mount Caddyfile.
    caddyfile.write_text(content, encoding="utf-8")
    run(["docker", "exec", CADDY_CONTAINER, "caddy", "validate",
         "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"])
    run(["docker", "exec", CADDY_CONTAINER, "caddy", "reload",
         "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"])


def auth_hash() -> str:
    if not AUTH.is_file():
        fail("Credenciais da publicacao anterior nao encontradas; nada alterado.")
    auth = json.loads(AUTH.read_text(encoding="utf-8"))
    if auth.get("user") != "contas" or len(auth.get("password", "")) < 20:
        fail("Credenciais HTTPS atuais invalidas; nada alterado.")
    hashed = run(["docker", "exec", "-i", CADDY_CONTAINER, "caddy",
                  "hash-password", "--algorithm", "bcrypt"],
                 input_text=auth["password"] + "\n")
    if not re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", hashed):
        fail("Falha ao criar hash HTTPS.")
    return hashed


def protected_site(hostname: str, hashed: str) -> str:
    return (
        hostname + " {\n" +
        "    encode zstd gzip\n"
        "    basic_auth {\n"
        "        contas " + hashed + "\n"
        "    }\n"
        "    reverse_proxy contas3-backend:8767 {\n"
        "        header_up -Authorization\n"
        "    }\n"
        "}\n"
    )


def replace_block(before: str, after: str, block: str) -> str:
    return before + old.START + "\n" + block + old.END + after


def tls_probe() -> tuple[bool, str]:
    # Sem --insecure: certificado deve ser valido.
    check = subprocess.run(
        ["curl", "--noproxy", "*", "--max-time", "10",
         "--resolve", DOMAIN + ":443:127.0.0.1", "-sS", "-o", "/dev/null",
         "-w", "%{http_code}", "https://" + DOMAIN + "/"],
        capture_output=True, text=True, timeout=13
    )
    direct_ok = check.returncode == 0 and check.stdout.strip() == "401"
    if not direct_ok:
        return False, check.stdout.strip()
    # Validar tambem acesso DNS REAL (evita dominio ainda vinculado a Worker).
    public = subprocess.run(
        ["curl", "--noproxy", "*", "--max-time", "10", "-sS",
         "-o", "/dev/null", "-w", "%{http_code}", "https://" + DOMAIN + "/"],
        capture_output=True, text=True, timeout=13
    )
    return public.returncode == 0 and public.stdout.strip() == "401", public.stdout.strip()


def await_tls():
    for i in range(24):
        ok, response = tls_probe()
        if ok:
            return
        time.sleep(5)
    fail("Certificado HTTPS do dominio novo ainda nao validou. "
         "O endereco provisório sera preservado.")


def check_web(expected_origin: str):
    c = old.docker_info(WEB_CONTAINER)
    if not c.get("State", {}).get("Running"):
        fail("App do Controle de Contas nao iniciou.")
    active_env = c.get("Config", {}).get("Env", [])
    if "CONTAS_PUBLIC_ORIGIN=" + expected_origin not in active_env:
        fail("Origem HTTPS ativa do aplicativo nao foi atualizada corretamente.")
    try:
        with urlopen("http://127.0.0.1:18769/health", timeout=5) as response:
            obj = json.load(response)
        if obj.get("online") is not True:
            fail("Aplicativo nao respondeu a validacao de saude.")
    except OSError as exc:
        fail("Aplicativo nao respondeu na porta local 18769: " + type(exc).__name__)


def restart_web(origin: str, network: str):
    env = dict(os.environ)
    env["CONTAS_PUBLIC_ORIGIN"] = origin
    run(["docker", "compose", "-f", str(COMPOSE), "up", "-d",
         "--no-deps", "--force-recreate", "contas-web"], timeout=140)
    c = old.docker_info(WEB_CONTAINER)
    if network not in c["NetworkSettings"]["Networks"]:
        run(["docker", "network", "connect", "--alias", "contas3-backend",
             network, WEB_CONTAINER])
    last_problem = "Inicializacao ainda em andamento"
    for attempt in range(30):
        try:
            check_web(origin)
            return
        except RuntimeError as exc:
            last_problem = str(exc)
            time.sleep(3)
    state = old.docker_info(WEB_CONTAINER).get("State", {})
    fail(
        "Aplicativo nao retomou a saude; container="
        + str(state.get("Status", "desconhecido"))
        + ", exit=" + str(state.get("ExitCode", "desconhecido"))
        + ", health=" + str((state.get("Health") or {}).get("Status", "indisponivel"))
        + ", ultimo teste: " + last_problem
    )


def replace_settings(old_text: str, original: str, updated: str):
    text = old_text.replace("CONTAS_PUBLIC_ORIGIN=" + original,
                            "CONTAS_PUBLIC_ORIGIN=" + updated)
    if text == old_text and original != updated:
        fail("Nao foi possivel atualizar a origem HTTPS salva.")
    temp = SETTINGS.with_suffix(".env.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temp, SETTINGS)
    finally:
        temp.unlink(missing_ok=True)


def main():
    db = ACTIVE_DB
    if not db.is_file() or db.stat().st_size < 1024:
        fail("Banco original da VPS ausente; operacao cancelada.")
    if old.docker_info(WEB_CONTAINER).get("State", {}).get("Running") is not True:
        fail("Aplicativo original nao esta rodando.")
    dns_preflight()
    previous_env, previous_origin, network = get_origin()
    if not old.docker_info(CADDY_CONTAINER).get("State", {}).get("Running"):
        fail("Caddy nao esta rodando.")
    caddyfile = old.host_caddyfile(old.docker_info(CADDY_CONTAINER))
    previous_caddy = caddyfile.read_text(encoding="utf-8")
    before, after, previous_block = caddy_managed_block(previous_caddy)
    hashed = auth_hash()
    first_block = protected_site(DOMAIN, hashed) + "\n" + protected_site(OLD_DOMAIN, hashed)
    final_block = protected_site(DOMAIN, hashed) + (
        "\n" + OLD_DOMAIN + " {\n" +
        "    redir https://" + DOMAIN + "{uri} 302\n"
        "}\n"
    )
    if previous_origin == "https://" + DOMAIN:
        print("Dominio definitivo ja registrado no aplicativo. Verificando HTTPS...")
        if tls_probe()[0]:
            print("DOMINIO DEFINITIVO: OK — https://" + DOMAIN + "/")
            return
    changed_caddy = False
    changed_app = False
    try:
        print("1/4: Habilitando ambos os dominios, sem desligar o antigo...")
        changed_caddy = True
        set_caddy(caddyfile, replace_block(before, after, first_block))

        print("2/4: Validando certificado HTTPS do dominio definitivo...")
        await_tls()
        print("HTTPS novo: OK, autenticação obrigatória.")

        print("3/4: Atualizando apenas o endereco publico do app...")
        changed_app = True
        restart_web("https://" + DOMAIN, network)
        replace_settings(previous_env, previous_origin, "https://" + DOMAIN)

        print("4/4: Redirecionando temporariamente o endereco antigo...")
        set_caddy(caddyfile, replace_block(before, after, final_block))
        if not tls_probe()[0]:
            fail("A validacao HTTPS final falhou.")
    except Exception:
        print("Falha ao migrar: tentando restaurar o endereco anterior...", file=sys.stderr)
        if changed_caddy:
            try:
                set_caddy(caddyfile, previous_caddy)
            except Exception:
                print("AVISO: falha ao restaurar Caddy; verificar manualmente.", file=sys.stderr)
        if changed_app:
            try:
                # Se a falha ocorreu antes de salvar o novo dominio, o arquivo
                # AINDA esta no dominio antigo: nunca bloquear a recuperacao
                # por causa disso. Reverter origem do app SEMPRE.
                saved = SETTINGS.read_text(encoding="utf-8")
                target = "CONTAS_PUBLIC_ORIGIN=https://" + DOMAIN
                original = "CONTAS_PUBLIC_ORIGIN=" + previous_origin
                if target in saved:
                    replace_settings(saved, "https://" + DOMAIN, previous_origin)
                elif original not in saved:
                    print("AVISO: origem salva nao corresponde ao dominio anterior.", file=sys.stderr)
                print("Restaurando container do Controle de Contas anterior...", file=sys.stderr)
                restart_web(previous_origin, network)
                print("Container anterior restaurado.", file=sys.stderr)
            except Exception as recover_exc:
                print("AVISO: falha ao restaurar origem do app: "
                      + str(recover_exc), file=sys.stderr)
        raise

    print("===== DOMINIO DEFINITIVO ATIVO =====")
    print("NOVO:", "https://" + DOMAIN + "/")
    print("ANTIGO: redireciona para o novo.")
    print("USUARIO E SENHA: os mesmos da publicacao anterior.")
    print("BANCO: inalterado. GFARM, PLUV e R2: inalterados.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("MUDANCA NAO CONCLUIDA:", str(exc), file=sys.stderr)
        sys.exit(1)
