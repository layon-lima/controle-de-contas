"""Criar/consultar job de teste apenas na VPS, sem imprimir credenciais."""
import argparse
import json
import secrets
from pathlib import Path
from urllib.request import Request, urlopen

ENV = Path(__file__).with_name("broker.env")
URL = "http://127.0.0.1:18767"


def token_admin():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONTAS_BROKER_ADMIN_TOKEN="):
            key = line.partition("=")[2].strip()
            if len(key) >= 32 and not key.startswith("SUBSTITUA"):
                return key
    raise RuntimeError("Token de administracao nao configurado.")


def request(path, body=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Bearer " + token_admin()}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = Request(URL + path, data=payload, headers=headers,
                  method="POST" if payload is not None else "GET")
    with urlopen(req, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    op = parser.add_mutually_exclusive_group(required=True)
    op.add_argument("--conta", type=int)
    op.add_argument("--consultar", type=str)
    args = parser.parse_args()
    if args.conta is not None:
        if not 0 < args.conta <= 2147483647:
            parser.error("Conta invalida.")
        job = request("/internal/jobs", {
            "request_key": "teste-vps-" + secrets.token_urlsafe(16),
            "account_id": args.conta, "operation": "pdf_status",
        })["job"]
        print(json.dumps({"id": job["id"], "status": job["status"]}))
    else:
        key = args.consultar
        if not key or not 20 <= len(key) <= 80 or any(
                not (c.isalnum() and c.isascii() or c in "_-") for c in key):
            parser.error("ID de pedido invalido.")
        print(json.dumps(request("/internal/jobs/" + key), ensure_ascii=False))


if __name__ == "__main__":
    main()
