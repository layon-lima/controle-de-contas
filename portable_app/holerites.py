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
    r"(?im)^[ \t]*(?:NOME\s+(?:DO|DA)\s+"
    r"(?:EMPREGADO|FUNCIONARIO|COLABORADOR|TRABALHADOR|RECEBEDOR)|"
    r"FUNCIONARIO|EMPREGADO|COLABORADOR|TRABALHADOR)"
    r"[ \t]*[:\-]?[ \t]*"
    r"([A-Z][A-Z' .-]{4,90})[ \t]*$"
)
EMPLOYEE_NAME_NEXT_LINE = re.compile(
    r"(?im)^[ \t]*(?:FUNCIONARIO|EMPREGADO|COLABORADOR|TRABALHADOR|"
    r"NOME\s+(?:DO|DA)\s+(?:EMPREGADO|FUNCIONARIO|TRABALHADOR))"
    r"[ \t]*:?[ \t]*\n[ \t]*([A-Z][A-Z' .-]{4,90})[ \t]*$"
)
SIMPLE_NAME = re.compile(
    r"(?im)^[ \t]*NOME[ \t]*[:\-][ \t]*([A-Z][A-Z' .-]{4,90})[ \t]*$"
)

def simplify(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(c for c in normalized if not unicodedata.combining(c)).upper().split()
    )



# Não aceitar cabeçalhos como se fossem nomes de funcionários.
# Em contracheques as caixas NOME/FUNCIONARIO muitas vezes ficam ao lado
# de "CÓDIGO / DISCRIMINADA / DESCONTOS". A IA e o OCR passam por este crivo.
PAYROLL_BAD_WORDS = frozenset((
    "DISCRIMINADA", "DISCRIMINACAO", "DISCRIMINACAO", "DESCRICAO",
    "VENCIMENTO", "VENCIMENTOS", "DESCONTO", "DESCONTOS", "PROVENTOS",
    "REFERENCIA", "COMPETENCIA", "FUNCIONARIO", "FUNCIONARIOS",
    "EMPREGADO", "EMPREGADOS", "COLABORADOR", "COLABORADORES",
    "RECIBO", "HOLERITE", "CONTRACHEQUE", "PAGAMENTO", "SALARIO",
    "EMPRESA", "EMPREGADOR", "MATRICULA", "ADMISSAO", "CARGO",
    "SETOR", "DEPARTAMENTO", "FUNCAO", "FUNCOES", "CODIGO",
    "CONTA", "BANCO", "BRUTO", "LIQUIDO", "TOTAL", "VALOR",
    "CPF", "CNPJ", "MES", "ANO", "RUBRICA", "TABELA",
    "DATA", "BASE", "INSS", "FGTS", "IRRF", "FOLHA", "DEMONSTRATIVO",
    "DO", "DA", "DE", "DAS", "DOS", "PARA", "AO", "AS", "OS",
))


def valid_employee_name(value):
    """Somente nome de pessoa plausível; nunca confiar cegamente no OCR/IA."""
    name = " ".join(str(value or "").split()).strip(" .-:;,")
    if len(name) < 7 or len(name) > 120 or len(name.split()) < 2:
        return ""
    canonical = simplify(name)
    words = canonical.split()
    if len(words) < 2 or len(words) > 9:
        return ""
    if words[0] in {"DO", "DA", "DE", "DAS", "DOS", "NOME", "CODIGO"}:
        return ""
    if any(w in PAYROLL_BAD_WORDS for w in words):
        # Preposições entre nomes são normais: MARIA DA SILVA.
        blocking = [w for w in words if w not in {"DE", "DA", "DO", "DAS", "DOS"}]
        if any(w in PAYROLL_BAD_WORDS for w in blocking):
            return ""
    if not re.fullmatch(r"[A-Za-zÀ-ÿ' .-]+", name):
        return ""
    substantial = [w for w in words if w not in {"DE", "DA", "DO", "DAS", "DOS"}]
    if len(substantial) < 2 or any(len(w) < 2 for w in substantial):
        return ""
    return name

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
    if not pix:
        raise ValueError(
            "Informe a chave Pix padrão para cadastrar este novo funcionário."
        )
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


def _looks_like_payroll(text):
    """Classifica o CONTEÚDO do documento, nunca seu nome ou observações."""
    body = simplify(text)
    if not body:
        return False
    # Títulos encontrados na página são evidência; títulos de ARQUIVO não são.
    if HOLERITE_WORDS.search(body):
        return True

    # Layouts comuns não escrevem "holerite": trazem rubricas,
    # proventos/descontos, identificação do empregado e salário líquido.
    employee = bool(re.search(
        r"\b(?:FUNCIONARIO|EMPREGADO|COLABORADOR|TRABALHADOR|"
        r"NOME\s+(?:DO|DA)\s+(?:FUNCIONARIO|EMPREGADO|COLABORADOR|"
        r"TRABALHADOR)|MATRICULA|ADMISSAO)\b", body
    ))
    salary = bool(re.search(
        r"\b(?:SALARIO(?:\s+(?:BASE|BRUTO|MENSAL|CONTRATUAL|"
        r"LIQUIDO))?|REMUNERACAO|PROVENTOS|VENCIMENTOS)\b", body
    ))
    has_columns = (
        bool(re.search(r"\b(?:PROVENTOS|VENCIMENTOS)\b", body))
        and bool(re.search(r"\bDESCONTOS?\b", body))
    )
    has_net = bool(re.search(
        r"\b(?:LIQUIDO\s+A\s+RECEBER|TOTAL\s+LIQUIDO|"
        r"SALARIO\s+LIQUIDO|VALOR\s+LIQUIDO)\b", body
    ))
    labour_indicators = (
        r"\bINSS\b", r"\bFGTS\b", r"\bIRRF\b",
        r"\bPIS(?:\s*/\s*PASEP)?\b", r"\bCBO\b",
        r"\b(?:DATA\s+DE\s+)?ADMISSAO\b",
        r"\bHORAS?\s+EXTRAS?\b", r"\b(?:13[ºO]?\s+SALARIO|FERIAS)\b",
        r"\bRUBRICAS?\b",
    )
    labour_count = sum(bool(re.search(p, body)) for p in labour_indicators)
    return bool(
        (employee and has_columns and (salary or labour_count >= 1))
        or (employee and salary and has_net and labour_count >= 1)
        or (has_columns and salary and has_net and labour_count >= 2)
    )


def detect_from_document(filename="", path: Path | None = None, fields=None):
    """Sugere holerite pelo texto/OCR do PDF; não cria contas ou funcionários.

    filename e fields permanecem na assinatura por compatibilidade com a API,
    mas NÃO participam da classificação nem da extração do nome.
    """
    content = ""
    source = "conteúdo do PDF não disponível"
    if path:
        try:
            import fitz
            with fitz.open(str(path)) as doc:
                content = "\n".join(
                    (doc[i].get_text("text", sort=True) or "")[:15_000]
                    for i in range(min(len(doc), 2))
                )
                source = "conteúdo do PDF"
                # A antiga rotina usava o título do arquivo para decidir se
                # tentaria OCR. Um PDF escaneado com nome genérico falhava.
                # Só acionar OCR quando a camada de texto é insuficiente.
                text_length = len(content.strip())
                text_lines = "\n".join(simplify(line) for line in content.splitlines())
                employee_found = any(
                    p.search(text_lines)
                    for p in (EMPLOYEE_NAME, EMPLOYEE_NAME_NEXT_LINE, SIMPLE_NAME)
                )
                payroll_found = _looks_like_payroll(content)
                if (doc.page_count and (
                        (not payroll_found and
                         (text_length < 280 or len(content.split()) < 35))
                        or (payroll_found and text_length < 450
                            and not employee_found))):
                    try:
                        import subprocess
                        first = doc.load_page(0)
                        pixels = max(first.rect.width * first.rect.height, 1)
                        scale = min(2.0, (8_000_000 / pixels) ** .5)
                        image = first.get_pixmap(
                            matrix=fitz.Matrix(scale, scale), alpha=False
                        ).tobytes("png")
                        result = subprocess.run(
                            ["tesseract", "stdin", "stdout", "-l", "por+eng"],
                            input=image, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=9,
                            check=False
                        )
                        if result.returncode == 0 and result.stdout:
                            content += "\n" + result.stdout[:35_000].decode(
                                "utf-8", errors="replace"
                            )
                            source = "OCR local do PDF"
                    except (OSError, RuntimeError, subprocess.TimeoutExpired):
                        pass
        except (OSError, RuntimeError, ValueError):
            pass
    # Manter as linhas evita confundir cabeçalhos e nomes de empregados.
    lines = "\n".join(simplify(line) for line in content.splitlines())
    detected = _looks_like_payroll(content)
    suggestion = ""
    if detected:
        for pattern in (EMPLOYEE_NAME, EMPLOYEE_NAME_NEXT_LINE):
            candidates = [
                valid_employee_name(m.group(1)) for m in pattern.finditer(lines)
            ]
            valid = list(dict.fromkeys(name for name in candidates if name))
            if len(valid) == 1:
                suggestion = valid[0]
                break
        if not suggestion:
            candidates = list(SIMPLE_NAME.finditer(lines))
            if len(candidates) == 1:
                suggestion = valid_employee_name(candidates[0].group(1))
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
