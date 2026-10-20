"""VPS: verificacao isolada; NAO publica app, banco ou PDFs."""
import json
import os
from pathlib import Path


def check(root):
    required = [
        "portable_app/server.py",
        "portable_app/app.js",
        "portable_app/pdf_intelligence.py",
        "portable_app/receipt_matching.py",
        "cloud_mobile/src/index.js",
        "tools/CONTAS_INVENTARIAR_PDFS.py",
    ]
    missing = [item for item in required if not (root / item).is_file()]
    return {
        "stage": "preparacao_apenas",
        "domain": "contas-cloud.fazendanovohorizonte.com",
        "ready_to_publish": False,
        "no_listening_ports": True,
        "pdfs_on_vps": False,
        "dns_modified": False,
        "migrated_database": False,
        "required_files_present": not missing,
        "missing_files": missing,
    }


if __name__ == "__main__":
    result = check(Path(os.environ.get("CONTAS_SOURCE_ROOT", "/workspace")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["required_files_present"] else 1)
