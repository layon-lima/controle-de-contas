"""Transfere o banco REAL do Controle de Contas do Windows para a VPS por SSH.

SQLite backup() cria snapshot apenas em memoria para incluir transacoes WAL.
Nao grava segunda copia no PC. Na VPS valida SHA256, integridade e contagens
antes da instalacao atomica. Nunca sobrescreve o banco se ele ja existir.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys

REMOTE_PROGRAM = r"""
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

DEST = Path('/opt/apps/contas-runtime/data/controle_contas.db')
EXPECTED_HASH = sys.argv[1]
EXPECTED_ACCOUNTS = int(sys.argv[2])
EXPECTED_PAYMENTS = int(sys.argv[3])

def fail(msg):
    raise SystemExit('ERRO VPS: ' + msg)

if DEST.exists():
    fail('banco de destino ja existe; nao vou substituir.')
if not DEST.parent.is_dir():
    fail('pasta /opt/apps/contas-runtime/data nao encontrada; prepare o app primeiro.')

fd, temp_path = tempfile.mkstemp(prefix='.transferencia-contas-', suffix='.db', dir=str(DEST.parent))
temporary = Path(temp_path)
try:
    with os.fdopen(fd, 'wb') as output:
        digest = hashlib.sha256()
        while True:
            chunk = sys.stdin.buffer.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    if digest.hexdigest() != EXPECTED_HASH:
        fail('a integridade SHA256 da transferencia nao confere.')
    with sqlite3.connect(temporary.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            fail('SQLite nao passou na verificacao de integridade.')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'contas', 'pagamentos'}.issubset(tables):
            fail('tabelas contas e pagamentos nao encontradas.')
        accounts = db.execute('SELECT COUNT(*) FROM contas').fetchone()[0]
        payments = db.execute('SELECT COUNT(*) FROM pagamentos').fetchone()[0]
        if (accounts, payments) != (EXPECTED_ACCOUNTS, EXPECTED_PAYMENTS):
            fail('contagens nao conferem com o banco do Windows.')
    # hardlink no Linux: operacao atomica que NUNCA substitui destino existente
    try:
        os.link(temporary, DEST)
    except FileExistsError:
        fail('destino passou a existir; operacao cancelada sem substituir.')
    print('TRANSFERENCIA CONFERIDA: OK')
    print('Contas:', accounts)
    print('Pagamentos:', payments)
    print('Banco na VPS:', DEST)
    print('Banco do Windows: INALTERADO')
finally:
    temporary.unlink(missing_ok=True)
"""


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+", sys.argv[1]):
        print("USO: py CONTAS_TRANSFERIR_BANCO_VPS.py root@ENDERECO_DA_VPS", file=sys.stderr)
        return 2
    if not shutil.which("ssh"):
        print("ERRO: cliente SSH do Windows nao encontrado.", file=sys.stderr)
        return 2
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ControleContas" / "data"
    src_path = root / "controle_contas.db"
    if not src_path.is_file():
        print("ERRO: banco original nao encontrado em " + str(src_path), file=sys.stderr)
        return 2

    print("Lendo o banco do Windows sem modifica-lo...")
    try:
        uri = src_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=30) as original:
            snapshot = sqlite3.connect(":memory:")
            try:
                original.backup(snapshot, pages=256, sleep=0.05)
                if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Banco original inconsistente. Transferencia cancelada.")
                names = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"contas", "pagamentos"}.issubset(names):
                    raise RuntimeError("Tabelas contas e pagamentos nao encontradas.")
                n_accounts = snapshot.execute("SELECT COUNT(*) FROM contas").fetchone()[0]
                n_payments = snapshot.execute("SELECT COUNT(*) FROM pagamentos").fetchone()[0]
                if not n_accounts:
                    raise RuntimeError("Banco sem contas. Nao vou instalar uma base vazia.")
                if not hasattr(snapshot, "serialize"):
                    raise RuntimeError("Este Python nao suporta SQLite serialize().")
                data = snapshot.serialize()
            finally:
                snapshot.close()
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        print("ERRO LOCAL: " + str(exc), file=sys.stderr)
        return 1

    sha256 = hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(REMOTE_PROGRAM.encode("utf-8")).decode("ascii")
    remote_python = "import base64; exec(base64.b64decode(" + repr(encoded) + "))"
    remote_command = ("python3 -c " + shlex.quote(remote_python) + " "
                      + shlex.quote(sha256) + " " + str(n_accounts) + " " + str(n_payments))
    print(f"Contas: {n_accounts} | Pagamentos: {n_payments}")
    print("Enviando banco diretamente para a VPS via SSH...")
    try:
        cmd = ["ssh", "-T", "-o", "ConnectTimeout=15", sys.argv[1], remote_command]
        done = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("ERRO SSH: " + str(exc), file=sys.stderr)
        return 1
    if done.returncode:
        msg = done.stderr.decode("utf-8", errors="replace").strip()
        if not msg:
            msg = done.stdout.decode("utf-8", errors="replace").strip()
        print("ERRO DE TRANSFERENCIA: " + msg[-2000:], file=sys.stderr)
        return 1
    print(done.stdout.decode("utf-8", errors="replace").strip())
    print("PROXIMO PASSO na VPS: bash deploy/vps/INICIAR_WEB_ORIGINAL.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
