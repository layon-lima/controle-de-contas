"""Adaptador MOBILE do Controle de Contas: usa O MESMO SQLite da web VPS.

Somente leitura de contas abertas. Nenhum D1 ou sincronizacao do banco antigo.
O estado da etiqueta 'copiado' e temporario e nao altera pagamentos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unicodedata
from threading import RLock

COPIED = {}
LOCK = RLock()

# Qualquer referencia textual a DAVI oculta a conta APENAS no mobile.
# Nao incluir caminhos internos de PDF: uma pasta geral do Windows com esse
# nome poderia ocultar indevidamente todas as contas.
MOBILE_TEXT_FIELDS = (
    "fornecedor", "documento", "pagador", "referencia",
    "codigo_pagamento", "tipo_pagamento", "observacoes",
    "beneficiario", "documento_beneficiario",
    "resultado_conferencia", "detalhes_conferencia",
)


def _contains_davi(value):
    if value is None:
        return False
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "davi" in text.casefold()


def _hidden_on_mobile(item):
    return any(_contains_davi(item.get(field)) for field in MOBILE_TEXT_FIELDS)


def mobile_account_visible(conn, account_id):
    """Revalida no banco antes de qualquer operacao por ID no mobile."""
    row = conn.execute(
        """
        SELECT c.fornecedor, c.documento, c.pagador, c.referencia,
               c.codigo_pagamento, c.tipo_pagamento, c.observacoes,
               p.beneficiario, p.documento_beneficiario,
               p.resultado_conferencia, p.detalhes_conferencia
        FROM contas c
        LEFT JOIN pagamentos p ON p.id=(
            SELECT p2.id FROM pagamentos p2
            WHERE p2.conta_id=c.id ORDER BY p2.id DESC LIMIT 1
        )
        WHERE c.id=?
          AND NOT EXISTS (
              SELECT 1 FROM pagamentos paid
              WHERE paid.conta_id=c.id
                AND paid.data_pagamento IS NOT NULL
                AND TRIM(paid.data_pagamento)<>''
          )
        """,
        (account_id,),
    ).fetchone()
    if row is None or _hidden_on_mobile(dict(row)):
        return False
    # Inclui qualquer anotacao de pagamento anterior, nao somente a ultima.
    for payment in conn.execute(
        """SELECT beneficiario, documento_beneficiario,
                  resultado_conferencia, detalhes_conferencia
           FROM pagamentos WHERE conta_id=?""",
        (account_id,),
    ):
        if _hidden_on_mobile(dict(payment)):
            return False
    return True


def listing(conn, status_fn, has_remote_pdf=None):
    rows = conn.execute(
        """
        SELECT c.id, c.fornecedor, c.documento, c.pagador, c.referencia,
               c.valor, c.vencimento, c.codigo_pagamento, c.tipo_pagamento,
               c.observacoes, c.criado_em, c.atualizado_em,
               c.enviado_pagamento_em, p.data_pagamento, p.valor_pago,
               p.beneficiario, p.documento_beneficiario,
               p.resultado_conferencia, p.detalhes_conferencia
        FROM contas c
        LEFT JOIN pagamentos p ON p.id=(
            SELECT p2.id FROM pagamentos p2
            WHERE p2.conta_id=c.id ORDER BY p2.id DESC LIMIT 1
        )
        WHERE NOT EXISTS (
            SELECT 1 FROM pagamentos paid
            WHERE paid.conta_id=c.id
              AND paid.data_pagamento IS NOT NULL
              AND TRIM(paid.data_pagamento)<>''
        )
        ORDER BY c.vencimento ASC, c.id DESC
        """
    ).fetchall()

    # A anotacao mais antiga de um pagamento tambem pode mencionar DAVI;
    # ler em lote para evitar uma consulta por conta na listagem do mobile.
    hidden_from_payments = {
        int(payment["conta_id"])
        for payment in conn.execute(
            """SELECT conta_id, beneficiario, documento_beneficiario,
                      resultado_conferencia, detalhes_conferencia
               FROM pagamentos"""
        )
        if _hidden_on_mobile(dict(payment))
    }

    now = datetime.now(timezone.utc)
    items = []
    with LOCK:
        for account_id, until in list(COPIED.items()):
            if until <= now:
                COPIED.pop(account_id, None)
        for row in rows:
            item = dict(row)
            if int(item["id"]) in hidden_from_payments or _hidden_on_mobile(item):
                continue
            if str(item.get("data_pagamento") or "").strip():
                continue
            item["recebedor"] = str(item.get("fornecedor") or "")
            item["status"] = status_fn(item.get("vencimento"), item.get("data_pagamento"))
            if item["status"] == "Paga":
                continue
            # Quando o R2 estiver configurado, o proprio VPS confirma
            # a existencia do PDF da conta aberta.
            item["tem_pdf"] = bool(has_remote_pdf(int(item["id"]))) if has_remote_pdf else False
            item["sugestao_pagamento"] = False
            item["sugerido_em"] = None
            item["sugestao_ate"] = None
            expiry = COPIED.get(int(item["id"]))
            item["codigo_copiado_recente"] = bool(expiry and expiry > now)
            item["codigo_copiado_em"] = None
            item["codigo_copiado_ate"] = expiry.isoformat() if expiry and expiry > now else None
            items.append(item)

    return {
        "ok": True,
        "quantidade": len(items),
        "contas": items,
        "meta": {"ultima_atualizacao": now.isoformat()},
    }


def copied(conn, account_id):
    if not mobile_account_visible(conn, account_id):
        return None
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(minutes=15)
    with LOCK:
        COPIED[int(account_id)] = expiry
    return {
        "ok": True,
        "account_id": int(account_id),
        "copied_at": now.isoformat(),
        "copied_until": expiry.isoformat(),
        "mobile_copied_until": expiry.isoformat(),
    }
