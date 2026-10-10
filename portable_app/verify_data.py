"""Auditoria SOMENTE LEITURA do banco existente antes da substituição do desktop.

Não altera schema, não abre transação de escrita, não move PDFs e não lê
conteúdo sensível; relata contagens e referências quebradas.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

APPDIR = Path(os.environ.get("LOCALAPPDATA") or
              str(Path.home() / "AppData" / "Local")) / "ControleContas"
DB = APPDIR / "data" / "controle_contas.db"
REQUIRED = {
    "contas": {"id", "fornecedor", "valor", "vencimento", "pdf_conta",
               "codigo_pagamento", "enviado_pagamento_em", "observacoes"},
    "pagamentos": {"id", "conta_id", "data_pagamento", "pdf_comprovante"},
}


def audit() -> dict:
    if not DB.is_file():
        raise FileNotFoundError("Banco original não encontrado: " + str(DB))
    with closing(sqlite3.connect(DB.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=15)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        checks = conn.execute("PRAGMA quick_check").fetchall()
        if any(r[0] != "ok" for r in checks):
            raise RuntimeError("SQLite reportou problemas de integridade. Não prosseguir.")
        for table, cols in REQUIRED.items():
            names = {r["name"] for r in conn.execute("PRAGMA table_info(" + table + ")")}
            missing = cols - names
            if missing:
                raise RuntimeError(
                    f"Banco não compatível: {table} sem colunas " + ", ".join(sorted(missing))
                )
        total = int(conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0])
        paid = int(conn.execute(
            "SELECT COUNT(DISTINCT conta_id) FROM pagamentos "
            "WHERE data_pagamento IS NOT NULL AND TRIM(data_pagamento)<>''"
        ).fetchone()[0])
        orphan = int(conn.execute(
            "SELECT COUNT(*) FROM pagamentos p "
            "LEFT JOIN contas c ON c.id=p.conta_id WHERE c.id IS NULL"
        ).fetchone()[0])
        paths = [
            (row["id"], str(row["pdf_conta"] or "").strip())
            for row in conn.execute("SELECT id,pdf_conta FROM contas")
        ]
    linked = [(id, p) for id, p in paths if p]
    broken = [id for id, p in linked if not Path(p).is_file()]
    return {
        "database_integrity": "ok",
        "accounts": total,
        "paid_accounts": paid,
        "account_pdf_references": len(linked),
        "account_pdfs_missing": len(broken),
        "missing_pdf_account_ids": broken[:20],
        "orphan_payment_records": orphan,
        "no_database_changes": True,
    }


if __name__ == "__main__":
    result = audit()
    print("VALIDACAO BANCO: " + json.dumps(result, ensure_ascii=False))
    if result["account_pdfs_missing"]:
        print("AVISO: alguns PDFs já cadastrados não existem no caminho original.")
    if result["orphan_payment_records"]:
        print("AVISO: existem pagamentos órfãos anteriores à migração.")
