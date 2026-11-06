#!/usr/bin/env python3
"""Configura o envio oficial WhatsApp Cloud API sem expor o token no terminal.

PREREQUISITO: coexistencia ativada na Meta/parceiro e templates
PAYMENT_REQUEST de Pix e/ou boleto aprovados para o seu numero.
Nao registra o numero na Meta nem cria credenciais por conta propria.
"""
import getpass
import json
import os
import re
from pathlib import Path

ROOT = Path(os.environ.get("CONTAS_APP_DIR", "/opt/apps/contas-runtime"))
DEST = ROOT / "whatsapp-api.json"


def main():
    print("WHATSAPP CLOUD API - CONTROLE DE CONTAS")
    print("Esta etapa requer o ID do numero, token e modelo(s) aprovados na Meta.")
    current = {}
    try:
        current = json.loads(DEST.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        pass
    phone = input("Phone Number ID da Meta (nao e o numero de telefone): ").strip()
    if not phone:
        phone = str(current.get("phone_number_id") or "")
    if not re.fullmatch(r"[0-9]{5,25}", phone):
        raise SystemExit("ERRO: Phone Number ID deve conter apenas numeros.")
    token = getpass.getpass("Token de acesso permanente (nao aparece na tela): ").strip()
    if not token:
        token = str(current.get("access_token") or "")
    if len(token) < 20:
        raise SystemExit("ERRO: token de acesso invalido.")
    print("Os modelos devem ter 3 campos no corpo: recebedor, valor, descricao.")
    print("E um botao PAYMENT_REQUEST para Pix ou boleto, aprovado pela Meta.")
    boleto = input("Nome do template aprovado para BOLETO (Enter se nao tiver): ").strip()
    pix = input("Nome do template aprovado para PIX (Enter se nao tiver): ").strip()
    if not boleto and not pix:
        boleto = str(current.get("template_boleto") or "")
        pix = str(current.get("template_pix") or "")
    if not boleto and not pix:
        raise SystemExit("ERRO: informe pelo menos um modelo aprovado.")
    for name in (boleto, pix):
        if name and not re.fullmatch(r"[a-z0-9_]{1,128}", name):
            raise SystemExit("ERRO: nome do modelo deve ter letras minusculas, digitos ou _.") 
    version = input("Versao Meta Graph API [v26.0]: ").strip() or "v26.0"
    if not re.fullmatch(r"v[0-9]{1,2}[.][0-9]{1,2}", version):
        raise SystemExit("ERRO: versao da API invalida.")
    config = {
        "enabled": True,
        "phone_number_id": phone,
        "access_token": token,
        "api_version": version,
        "template_boleto": boleto,
        "template_pix": pix,
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_name(DEST.name + ".tmp")
    try:
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, DEST)
    finally:
        tmp.unlink(missing_ok=True)
    print("Configuracao salva com permissoes privadas em:", DEST)
    print("Nenhum envio foi disparado.")


if __name__ == "__main__":
    main()
