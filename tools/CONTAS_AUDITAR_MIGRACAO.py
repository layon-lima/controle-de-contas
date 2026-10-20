#!/usr/bin/env python3
"""CONTAS 3 — auditoria/backup LOCAL para migracao futura à VPS.

Nao transmite dados, nao altera o SQLite original, nao envia PDF ao servidor.
Faz backup SQLite consistente na pasta privada de backups do Windows;
imprime apenas contagens e o hash de verificacao, sem nomes/valores.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

APP = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ControleContas"
SOURCE = APP / "data" / "controle_contas.db"
BACKUPS = APP / "backups"
AUDIT_FILE = APP / "auditoria_migracao_vps.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(
        'PRAGMA table_info("' + name.replace('"', '""') + '")'
    )}


def inventory(conn: sqlite3.Connection) -> dict:
    check = conn.execute("PRAGMA integrity_check").fetchone()
    if not check or check[0] != "ok":
        raise RuntimeError("Backup SQLite nao passou no teste de integridade.")

    required = {"contas", "pagamentos"}
    absent = [name for name in required if not table_exists(conn, name)]
    if absent:
        raise RuntimeError("Tabelas originais ausentes: " + ", ".join(sorted(absent)))

    rows = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name
    """).fetchall()
    counts = {}
    for (name,) in rows:
        quoted = '"' + str(name).replace('"', '""') + '"'
        counts[name] = int(conn.execute("SELECT COUNT(*) FROM " + quoted).fetchone()[0])

    needed = {"contas": {"id"}, "pagamentos": {"id", "conta_id"}}
    for name, columns in needed.items():
        missing = columns - table_columns(conn, name)
        if missing:
            raise RuntimeError("Colunas obrigatorias ausentes em " + name + ": " + ", ".join(sorted(missing)))

    orphan_payments = int(conn.execute("""
        SELECT COUNT(*) FROM pagamentos p
        LEFT JOIN contas c ON c.id = p.conta_id
        WHERE c.id IS NULL
    """).fetchone()[0])

    payment_history = int(conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT conta_id FROM pagamentos GROUP BY conta_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0])

    pdf_counts = {"referenciados": 0, "encontrados": 0, "ausentes": 0}
    if "pdf_conta" in table_columns(conn, "contas"):
        for (value,) in conn.execute("SELECT pdf_conta FROM contas"):
            pdf = str(value or "").strip()
            if not pdf:
                continue
            pdf_counts["referenciados"] += 1
            if Path(pdf).is_file():
                pdf_counts["encontrados"] += 1
            else:
                pdf_counts["ausentes"] += 1

    return {
        "integridade": "ok",
        "tabelas": counts,
        "pagamentos_sem_conta": orphan_payments,
        "contas_com_historico_pagamento_multiplo": payment_history,
        "pdfs": pdf_counts,
    }


def run(source: Path = SOURCE, backups: Path = BACKUPS, report: Path = AUDIT_FILE) -> dict:
    if not source.is_file():
        raise FileNotFoundError("Banco original nao localizado no Windows: " + str(source))
    backups.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = backups / f"antes_migracao_vps_{timestamp}_{secrets.token_hex(4)}.sqlite3"
    target = None
    try:
        # Conexao read-only, com backup online transacional (inclusive WAL).
        uri = source.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=30)) as src:
            target = sqlite3.connect(snapshot, timeout=30)
            with closing(target):
                src.backup(target, pages=200, sleep=0.05)
                result = inventory(target)
                target.commit()
            target = None

        digest = file_sha256(snapshot)
        output = {
            "tipo": "contas3-auditoria-migracao-v1",
            "criado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
            "snapshot_local": str(snapshot),
            "sha256_snapshot": digest,
            **result,
        }
        report.parent.mkdir(parents=True, exist_ok=True)
        tmp = report.with_name(report.name + "." + secrets.token_hex(4) + ".tmp")
        try:
            with tmp.open("x", encoding="utf-8") as handle:
                json.dump(output, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, report)
        finally:
            tmp.unlink(missing_ok=True)
        return output
    except BaseException:
        if target is not None:
            target.close()
        snapshot.unlink(missing_ok=True)
        raise


def main() -> int:
    try:
        result = run()
    except Exception as exc:
        print("AUDITORIA NAO CONCLUIDA: " + str(exc), file=sys.stderr)
        return 1

    print("===== CONTAS 3: AUDITORIA LOCAL PARA MIGRACAO =====")
    print("Backup SQLite consistente: OK (mantido SOMENTE no PC)")
    print("Integridade SQLite:", result["integridade"])
    print("Contas:", result["tabelas"]["contas"])
    print("Pagamentos:", result["tabelas"]["pagamentos"])
    print("Outras tabelas preservadas:", len(result["tabelas"]) - 2)
    print("Pagamentos sem conta:", result["pagamentos_sem_conta"])
    print("Contas com multiplos pagamentos:", result["contas_com_historico_pagamento_multiplo"])
    print("PDFs: referenciados={referenciados} encontrados={encontrados} ausentes={ausentes}".format(**result["pdfs"]))
    print("Hash SHA256 do backup:", result["sha256_snapshot"])
    print("Dados pessoais, pagamentos e PDFs NAO foram enviados pela internet.")
    print("Este passo NAO altera a fonte oficial Windows nem o banco novo na VPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
