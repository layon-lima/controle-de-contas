"""Cadastro permanente de funcionários e associação mensal de holerites.

Mantém o motor de contas/pagamentos existente. As tabelas adicionais são criadas
SOMENTE na primeira gravação aprovada pelo usuário, dentro do mesmo SQLite.
Nunca gera automaticamente uma nova conta para o próximo mês.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import unicodedata

HOLERITE_WORDS = re.compile(
    r"\b(?:HOLERITE(?:S)?|CONTRA[\s-]?CHEQUE(?:S)?|"
    r"DEMONSTRATIVO\s+DE\s+(?:PAGAMENTO|SALARIO)|"
    r"RECIBO\s+DE\s+(?:PAGAMENTO\s+DE\s+)?SALARIO|"
    r"FOLHA\s+DE\s+PAGAMENTO)\b",
    re.IGNORECASE,
)
MONTH_YEAR = re.compile(
    r"(?:COMPETENCIA|REFERENCIA|MES\s*/?\s*ANO)"
    r"\s*:?\s*(0?[1-9]|1[0-2])[/.-](20\d{2})\b",
    re.IGNORECASE,
)
EMPLOYEE_NAME = re.compile(
    r"(?im)^[ \\t]*(?:NOME\s+(?:DO|DA)\s+"
    r"(?:EMPREGADO|FUNCIONARIO|COLABORADOR|TRABALHADOR|RECEBEDOR)|"
    r"FUNCIONARIO|EMPREGADO|COLABORADOR|TRABALHADOR)"
    r"[ \\t]*[:\\-]?[ \\t]*"
    r"([A-Z][A-Z' .-]{4,90})[ \\t]*$"
)
EMPLOYEE_NAME_NEXT_LINE = re.compile(
    r"(?im)^[ \\t]*(?:FUNCIONARIO|EMPREGADO|COLABORADOR|TRABALHADOR|"
    r"NOME\s+(?:DO|DA)\s+(?:EMPREGADO|FUNCIONARIO|TRABALHADOR))"
    r"[ \\t]*:?[ \\t]*\\n[ \\t]*([A-Z][A-Z' .-]{4,90})[ \\t]*$"
)
SIMPLE_NAME = re.compile(
    r"(?im)^[ \\t]*NOME[ \\t]*[:\\-][ \\t]*([A-Z][A-Z' .-]{4,90})[ \\t]*$"
)


def simplify(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(c for c in normalized if not unicodedata.combining(c)).upper().split()
    )


def tables_exist(conn):
    found = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('holerite_funcionarios', 'holerite_lancamentos')"
        )
    }
    return len(found) == 2


def bootstrap(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS holerite_funcionarios (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             nome TEXT NOT NULL,
             nome_chave TEXT NOT NULL UNIQUE,
             chave_pix TEXT NOT NULL DEFAULT '',
             criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS holerite_lancamentos (
             conta_id INTEGER PRIMARY KEY REFERENCES contas(id) ON DELETE CASCADE,
             funcionario_id INTEGER NOT NULL REFERENCES holerite_funcionarios(id),
             competencia TEXT NOT NULL,
             origem TEXT NOT NULL DEFAULT 'manual',
             atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_holerite_funcionario_competencia "
        "ON holerite_lancamentos(funcionario_id, competencia)"
    )


def validate_name(name):
    name = " ".join(str(name or "").strip().split())
    if len(name) < 3 or len(name) > 150:
        raise ValueError("Informe o nome completo do funcionário (3 a 150 caracteres).")
    return name


def validate_pix(value):
    value = str(value or "").strip()
    if len(value) > 150 or any(ord(c) < 32 for c in value):
        raise ValueError("A chave Pix deve ter até 150 caracteres.")
    return value


def validate_competencia(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value):
        raise ValueError("Competência do holerite inválida. Use ano e mês.")
    return value


def get_employees(conn):
    if not tables_exist(conn):
        return []
    return [
        dict(row) for row in conn.execute(
            """SELECT f.id, f.nome, f.chave_pix,
                COUNT(h.conta_id) AS total_holerites
               FROM holerite_funcionarios f
               LEFT JOIN holerite_lancamentos h ON h.funcionario_id=f.id
               GROUP BY f.id ORDER BY f.nome COLLATE NOCASE"""
        )
    ]


def linked_accounts(conn):
    if not tables_exist(conn):
        return {}
    return {
        int(row["conta_id"]): dict(row)
        for row in conn.execute(
            """SELECT h.conta_id, h.funcionario_id, h.competencia,
                      f.nome AS funcionario_nome,
                      f.chave_pix AS pix_padrao
               FROM holerite_lancamentos h
               JOIN holerite_funcionarios f ON f.id=h.funcionario_id"""
        )
    }


def linked_one(conn, account_id):
    if not tables_exist(conn):
        return None
    row = conn.execute(
        """SELECT h.funcionario_id, h.competencia,
                  f.nome AS funcionario_nome, f.chave_pix AS pix_padrao
           FROM holerite_lancamentos h
           JOIN holerite_funcionarios f ON f.id=h.funcionario_id
           WHERE h.conta_id=?""", (account_id,)
    ).fetchone()
    return dict(row) if row else None


def save_employee(conn, data):
    if not isinstance(data, dict):
        raise ValueError("Dados do funcionário inválidos.")
    name = validate_name(data.get("nome"))
    normalized = simplify(name)
    key = validate_pix(data.get("chave_pix"))
    employee_id = data.get("id")
    bootstrap(conn)
    if employee_id not in (None, ""):
        try:
            employee_id = int(employee_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Funcionário inválido.") from exc
        if employee_id < 1 or not conn.execute(
            "SELECT 1 FROM holerite_funcionarios WHERE id=?", (employee_id,)
        ).fetchone():
            raise ValueError("Funcionário não encontrado.")
        conflict = conn.execute(
            "SELECT id FROM holerite_funcionarios WHERE nome_chave=? AND id<>?",
            (normalized, employee_id)
        ).fetchone()
        if conflict:
            raise ValueError("Já existe outro funcionário com esse nome.")
        conn.execute(
            """UPDATE holerite_funcionarios
               SET nome=?, nome_chave=?, chave_pix=?, atualizado_em=CURRENT_TIMESTAMP
               WHERE id=?""", (name, normalized, key, employee_id)
        )
        return employee_id
    found = conn.execute(
        "SELECT id FROM holerite_funcionarios WHERE nome_chave=?",
        (normalized,)
    ).fetchone()
    if found:
        raise ValueError("Funcionário já cadastrado. Edite o cadastro existente.")
    return int(conn.execute(
        "INSERT INTO holerite_funcionarios(nome,nome_chave,chave_pix) VALUES(?,?,?)",
        (name, normalized, key)
    ).lastrowid)


def _employee_for_assignment(conn, data):
    employee_id = data.get("funcionario_id")
    if employee_id not in (None, ""):
        try:
            employee_id = int(employee_id)
        except (ValueError, TypeError) as exc:
            raise ValueError("Funcionário selecionado é inválido.") from exc
        row = conn.execute(
            "SELECT id FROM holerite_funcionarios WHERE id=?", (employee_id,)
        ).fetchone()
        if not row:
            raise ValueError("Funcionário não encontrado. Atualize o cadastro.")
        return employee_id
    name = validate_name(data.get("funcionario_nome"))
    found = conn.execute(
        "SELECT id FROM holerite_funcionarios WHERE nome_chave=?",
        (simplify(name),)
    ).fetchone()
    if found:
        return int(found[0])
    pix = validate_pix(data.get("pix_padrao"))
    return int(conn.execute(
        "INSERT INTO holerite_funcionarios(nome,nome_chave,chave_pix) VALUES(?,?,?)",
        (name, simplify(name), pix)
    ).lastrowid)


def assign(conn, account_id, data):
    """Explicit choice from the account modal; atomic with account INSERT/UPDATE."""
    if "holerite" not in data:
        return
    enabled = str(data.get("holerite") or "").strip().lower() in ("sim", "true", "1")
    if not enabled:
        if tables_exist(conn):
            conn.execute("DELETE FROM holerite_lancamentos WHERE conta_id=?", (account_id,))
        return
    competence = validate_competencia(data.get("competencia"))
    bootstrap(conn)
    employee_id = _employee_for_assignment(conn, data)
    # Não bloquear contracheques extras na mesma competência (13º, férias etc.);
    # são lançamentos independentes no motor financeiro existente.
    conn.execute(
        """INSERT INTO holerite_lancamentos
               (conta_id,funcionario_id,competencia,origem)
           VALUES(?,?,?,?)
           ON CONFLICT(conta_id) DO UPDATE SET
               funcionario_id=excluded.funcionario_id,
               competencia=excluded.competencia,
               origem=excluded.origem,
               atualizado_em=CURRENT_TIMESTAMP""",
        (account_id, employee_id, competence, "revisado")
    )


def detect_from_document(filename="", path: Path | None = None, fields=None):
    """Sugestão, nunca cadastramento ou vínculo automático silencioso."""
    fields = fields or {}
    name = str(filename or "")
    raw = name + "\n" + str(fields.get("observacoes") or "")
    source = "nome do arquivo"
    if path:
        try:
            import fitz
            with fitz.open(str(path)) as doc:
                extracted = "\n".join(
                    (doc[i].get_text("text", sort=True) or "")[:15_000]
                    for i in range(min(len(doc), 2))
                )
                raw += "\n" + extracted
                source = "conteúdo do PDF"
                if len(extracted.strip()) < 65 and not HOLERITE_WORDS.search(
                    simplify(raw)
                ) and doc.page_count:
                    # Contracheque escaneado: tentar OCR local somente na
                    # primeira pagina, em resolucao limitada e com timeout.
                    # A ausencia do Tesseract nao bloqueia a importacao.
                    try:
                        import subprocess
                        first = doc.load_page(0)
                        pixels = max(first.rect.width * first.rect.height, 1)
                        scale = min(1.65, (8_000_000 / pixels) ** .5)
                        image = first.get_pixmap(
                            matrix=fitz.Matrix(scale, scale), alpha=False
                        ).tobytes("png")
                        result = subprocess.run(
                            ["tesseract", "stdin", "stdout", "-l", "por+eng"],
                            input=image, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=7,
                            check=False
                        )
                        if result.returncode == 0 and result.stdout:
                            raw += "\n" + result.stdout[:20_000].decode(
                                "utf-8", errors="replace"
                            )
                            source = "OCR local do PDF"
                    except (OSError, RuntimeError, subprocess.TimeoutExpired):
                        pass
        except (OSError, RuntimeError, ValueError):
            pass
    # Não achatar as linhas ANTES de procurar FUNCIONARIO/NOME DO EMPREGADO.
    # EMPLOYEE_NAME usa ^ e $ para evitar capturar a empresa ou outros campos
    # do contracheque. O antigo simplify(raw) apagava os \n e fazia o teste
    # de reconhecimento falhar, bloqueando o build Docker da VPS.
    lines = "\n".join(simplify(line) for line in raw.splitlines())
    detected = bool(HOLERITE_WORDS.search(lines))
    match_name = (EMPLOYEE_NAME.search(lines) or
                  EMPLOYEE_NAME_NEXT_LINE.search(lines)) if detected else None
    if detected and not match_name:
        # Um campo NOME isolado é ambíguo quando há mais de um (empresa,
        # funcionário, pagador). Só sugerir quando for o único.
        names = list(SIMPLE_NAME.finditer(lines))
        match_name = names[0] if len(names) == 1 else None
    suggestion = " ".join(match_name.group(1).split()).strip(" -.") if match_name else ""
    # Não confundir empregador com recebedor caso haja ambiguidade.
    if any(term in simplify(suggestion) for term in ("CNPJ", "CPF", "MATRICULA", "SALARIO")):
        suggestion = ""
    competence = ""
    match_month = MONTH_YEAR.search(lines) if detected else None
    if match_month:
        competence = f"{match_month.group(2)}-{int(match_month.group(1)):02d}"
    return {
        "detected": detected,
        "funcionario_sugestao": suggestion,
        "competencia_sugestao": competence,
        "origem_sugestao": source,
    }


def guess_existing(info):
    """Sinaliza antigos holerites que ainda não foram associados a funcionário."""
    return bool(HOLERITE_WORDS.search(simplify(
        str(info.get("observacoes") or "") + " " + str(info.get("referencia") or "")
    )))
