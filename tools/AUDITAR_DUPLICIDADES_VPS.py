#!/usr/bin/env python3
"""Audita duplicidades do Controle de Contas sem modificar o SQLite.

Uso na VPS:
  python3 tools/AUDITAR_DUPLICIDADES_VPS.py

Saida:
- IDENTIFICADOR REPETIDO: boleto bancario forte ou PIX EMV do mesmo valor/data.
- SUSPEITA: registros iguais em fornecedor, pagador, referencia, valor e vencimento.

O relatorio NAO remove nada, NAO altera o banco e NAO imprime codigos de pagamento.
"""
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path("/opt/apps/contas-runtime/data/controle_contas.db")


def normalize(text):
    return " ".join(str(text or "").upper().split())


def payment_code(value):
    return "".join(ch for ch in str(value or "") if ch.isalnum())


def normalize_amount(value):
    return round(float(value or 0), 2)


def main():
    if not DB.is_file():
        raise SystemExit("ERRO: banco da VPS nao encontrado. Nenhum arquivo alterado.")
    with sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id,fornecedor,pagador,referencia,valor,vencimento,
                      codigo_pagamento FROM contas ORDER BY id"""
        ).fetchall()

    by_code = defaultdict(list)
    by_business = defaultdict(list)
    for r in rows:
        code = payment_code(r["codigo_pagamento"])
        amount = normalize_amount(r["valor"])
        due = str(r["vencimento"] or "")[:10]
        if code.isdigit() and len(code) in (44, 46, 47, 48):
            by_code[("boleto", code)].append(r)
        elif code.startswith("000201") and len(code) >= 50:
            by_code[("pix_emv", code, amount, due)].append(r)

        # Grupo suspeito, nao prova de duplicidade: duas notas podem ter
        # fornecedor, vencimento e valor iguais. Exigir referencia preenchida.
        ref = normalize(r["referencia"])
        supplier = normalize(r["fornecedor"])
        payer = normalize(r["pagador"])
        if ref and supplier and amount > 0 and due:
            by_business[(supplier, payer, ref, amount, due)].append(r)

    strong = [items for items in by_code.values() if len(items) > 1]
    suspects = [items for items in by_business.values() if len(items) > 1]
    print("AUDITORIA SOMENTE LEITURA - CONTROLE DE CONTAS")
    print("Total de contas no banco:", len(rows))
    print("Grupos com identificador forte repetido:", len(strong))
    print("Grupos suspeitos por dados cadastrais:", len(suspects))
    for label, groups in (("IDENTIFICADOR REPETIDO", strong),
                          ("SUSPEITA CADASTRAL (CONFERIR)", suspects)):
        for items in groups:
            print("\n" + label)
            for row in items:
                print("  ID", row["id"], "|", row["fornecedor"],
                      "| R$", f'{float(row["valor"] or 0):.2f}',
                      "| vence", row["vencimento"],
                      "| referencia", str(row["referencia"] or "")[:90])
    print("\nNenhuma conta ou PDF foi modificado.")


if __name__ == "__main__":
    main()
