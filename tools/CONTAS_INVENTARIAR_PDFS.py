#!/usr/bin/env python3
"""Inventario somente leitura dos PDFs locais. Nenhum documento e copiado."""
import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def paths_default():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("Execute no computador Windows original (LOCALAPPDATA ausente).")
    return (Path(local) / "ControleContas" / "data" / "controle_contas.db",
            Path.home() / "Documents" / "BOLETOS")


def inside(child, parent):
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(db, root, hash_pdfs=False):
    db, root = Path(db), Path(root)
    if not db.is_file():
        raise FileNotFoundError("Banco original ausente; nada sera criado: " + str(db))
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        accounts = conn.execute("SELECT id,pdf_conta FROM contas").fetchall()
        payments = conn.execute("SELECT conta_id,pdf_comprovante FROM pagamentos").fetchall()
    references = (
        [("boleto", int(a["id"]), a["pdf_conta"]) for a in accounts]
        + [("comprovante", int(p["conta_id"]), p["pdf_comprovante"]) for p in payments]
    )
    entries, cache, by_year = [], {}, Counter()
    for kind, account_id, raw in references:
        raw = str(raw or "").strip()
        if not raw:
            continue
        file = Path(raw)
        key = os.path.normcase(os.path.abspath(file))
        if key not in cache:
            exists = file.is_file()
            record = {
                "original_path": raw,
                "exists": exists,
                "under_official_root": inside(file, root),
                "bytes": file.stat().st_size if exists else None,
            }
            if exists and hash_pdfs:
                record["sha256"] = file_sha256(file)
            cache[key] = record
        entry = {"account_id": account_id, "kind": kind, **cache[key]}
        entries.append(entry)
        if entry["under_official_root"]:
            for part in file.parts:
                if part.upper().startswith("BOLETOS ") and part[8:].isdigit():
                    by_year[part.upper()] += 1
                    break
    return {
        "type": "controle-contas-pdf-inventory-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "readonly": True,
        "pdfs_copied_to_vps": 0,
        "database": str(db),
        "official_root": str(root),
        "accounts": len(accounts),
        "payments": len(payments),
        "references": len(entries),
        "unique_files": len(cache),
        "missing": sum(not e["exists"] for e in entries),
        "outside_official_root": sum(not e["under_official_root"] for e in entries),
        "by_year": dict(sorted(by_year.items())),
        "entries": entries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--hash", action="store_true")
    parser.add_argument("--output", type=Path, help="Relatorio somente neste PC")
    args = parser.parse_args()
    if args.db is None or args.root is None:
        default_db, default_root = paths_default()
    else:
        default_db, default_root = args.db, args.root
    result = inventory(args.db or default_db, args.root or default_root, args.hash)
    print(json.dumps({k: v for k, v in result.items() if k != "entries"},
                     ensure_ascii=False, indent=2))
    if args.output:
        output = args.output.expanduser()
        if output.suffix.lower() != ".json":
            parser.error("Use arquivo .json")
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_name(output.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, output)
        print("Inventario privado salvo no PC: " + str(output))
    return 2 if result["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
