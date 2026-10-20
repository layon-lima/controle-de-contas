#!/usr/bin/env python3
"""Troca HTTPS do Contas 3 SOMENTE no Caddy, SEM REINICIAR o aplicativo.

Preserva o app original, SQLite, PDFs, R2, Worker, GFARM e Pluviometros.
Mantem HTTPS e autenticação. Testa listagem real antes de cortar o dominio.
Se houver falha, reverte exclusivamente o bloco gerenciado do Caddy.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

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


def active_app_origin() -> str:
    """Consulta a origem esperada pelo processo ATUAL, sem reinicia-lo."""
    envs = old.docker_info(WEB_CONTAINER).get("Config", {}).get("Env", [])
    origins = [s.split("=", 1)[1] for s in envs if s.startswith("CONTAS_PUBLIC_ORIGIN=")]
    if len(origins) != 1 or origins[0] not in {
        "https://" + OLD_DOMAIN, "https://" + DOMAIN,
        "http://127.0.0.1:18769",
    }:
        fail("Origem ativa desconhecida; nao vou mudar o proxy sem confirmacao.")
    return origins[0]


def protected_site(hostname: str, hashed: str, app_origin: str | None = None) -> str:
    """Conserva a validacao Origin original mesmo sem mudar/reiniciar o app.

    O Caddy ja exige login e o app continua exigindo X-Local-Token por sessao.
    """
    origin = app_origin or ("https://" + OLD_DOMAIN)
    if origin not in {"https://" + OLD_DOMAIN, "https://" + DOMAIN,
                      "http://127.0.0.1:18769"}:
        fail("Origem proxy nao reconhecida.")
    return (
        hostname + " {\n" +
        "    encode zstd gzip\n"
        "    basic_auth {\n"
        "        contas " + hashed + "\n"
        "    }\n"
        "    reverse_proxy contas3-backend:8767 {\n"
        "        header_up -Authorization\n"
        "        header_up Origin " + origin + "\n"
        "    }\n"
        "}\n"
    )


def check_public_data() -> int:
    """Conferir login + token de sessao e lista real pelo DOMINIO DEFINITIVO."""
    auth = json.loads(AUTH.read_text(encoding="utf-8"))
    basic = "Basic " + base64.b64encode(
        ("contas:" + auth["password"]).encode("utf-8")
    ).decode("ascii")
    headers = {"Authorization": basic, "User-Agent": "ControleContas-VPS-DNS-Cutover"}
    try:
        req = Request("https://" + DOMAIN + "/", headers=headers)
        with urlopen(req, timeout=15) as response:
            html = response.read(250000).decode("utf-8")
        token = re.search(r'window\.__CONTAS_TOKEN__=("[^"]+");', html)
        if not token:
            fail("A interface web abriu sem o token de sessao original.")
        headers["X-Local-Token"] = json.loads(token.group(1))
        req = Request("https://" + DOMAIN + "/api/accounts", headers=headers)
        with urlopen(req, timeout=15) as response:
            payload = json.load(response)
        if not payload.get("ok") or not isinstance(payload.get("accounts"), list):
            fail("API de contas ainda nao funciona pelo dominio novo.")
        if not payload["accounts"]:
            fail("API nao retornou as contas existentes: abortando troca.")
        return len(payload["accounts"])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        fail("Nova URL ainda nao carrega contas: " + type(exc).__name__)


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
    if not ACTIVE_DB.is_file() or ACTIVE_DB.stat().st_size < 1024:
        fail("Banco original ausente; nenhuma rota alterada.")
    if not old.docker_info(WEB_CONTAINER).get("State", {}).get("Running"):
        fail("App original nao esta rodando. Nao vou reinicia-lo.")
    dns_preflight()
    _saved, _old_origin, network = get_origin()
    app_origin = active_app_origin()
    caddy = old.docker_info(CADDY_CONTAINER)
    if not caddy.get("State", {}).get("Running"):
        fail("Caddy nao esta rodando.")
    if network not in caddy["NetworkSettings"]["Networks"]:
        fail("Rede salva do Caddy nao esta conectada.")
    # Nao conectar redes ou recriar containers: o app pode estar funcional.
    web = old.docker_info(WEB_CONTAINER)
    if network not in web["NetworkSettings"]["Networks"]:
        fail("Proxy Caddy sem acesso ao app na rede existente; revisar antes.")
    caddyfile = old.host_caddyfile(caddy)
    previous_caddy = caddyfile.read_text(encoding="utf-8")
    before, after, previous_block = caddy_managed_block(previous_caddy)
    hashed = auth_hash()
    first_block = (
        protected_site(DOMAIN, hashed, app_origin) + "\n"
        + protected_site(OLD_DOMAIN, hashed, app_origin)
    )
    final_block = (
        protected_site(DOMAIN, hashed, app_origin) + "\n"
        + OLD_DOMAIN + " {\n"
        + "    redir https://" + DOMAIN + "{uri} 302\n"
        + "}\n"
    )
    change_started = False
    try:
        print("1/3: Ativando HTTPS definitivo SEM REINICIAR o app...")
        change_started = True
        set_caddy(caddyfile, replace_block(before, after, first_block))
        print("2/3: Conferindo certificado, autenticacao e CONTAS REAIS...")
        await_tls()
        total = check_public_data()
        print("OK:", total, "contas acessiveis no dominio definitivo.")
        print("3/3: Redirecionando o endereco provisorio...")
        set_caddy(caddyfile, replace_block(before, after, final_block))
        if not tls_probe()[0]:
            fail("HTTPS final nao confirmou autenticacao obrigatoria.")
    except Exception:
        if change_started:
            print("Falha na troca do dominio: restaurando SOMENTE o Caddy antigo.", file=sys.stderr)
            try:
                set_caddy(caddyfile, previous_caddy)
                print("Rota antiga restaurada; app nunca foi reiniciado.", file=sys.stderr)
            except Exception:
                print("AVISO: nao consegui restaurar Caddy. App permanece intacto.", file=sys.stderr)
        raise
    print("===== DOMINIO DEFINITIVO ATIVO =====")
    print("NOVO: https://" + DOMAIN + "/")
    print("ANTIGO: redireciona para o novo.")
    print("BANCO, APLICATIVO, MOBILE, OCR, GFARM, PLUV, R2: NAO REINICIADOS.")
    print("USUARIO E SENHA: os mesmos da publicacao anterior.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("MUDANCA NAO CONCLUIDA:", str(exc), file=sys.stderr)
        sys.exit(1)
