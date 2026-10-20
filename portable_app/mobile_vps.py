"""Adaptador MOBILE do Controle de Contas: usa O MESMO SQLite da web VPS.

Somente leitura de contas abertas. Nenhum D1 ou sincronizacao do banco antigo.
O estado da etiqueta 'copiado' e temporario e nao altera pagamentos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock

COPIED = {}
LOCK = RLock()


def listing(conn, status_fn):
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

    now = datetime.now(timezone.utc)
    items = []
    with LOCK:
        for account_id, until in list(COPIED.items()):
            if until <= now:
                COPIED.pop(account_id, None)
        for row in rows:
            item = dict(row)
            if str(item.get("data_pagamento") or "").strip():
                continue
            item["recebedor"] = str(item.get("fornecedor") or "")
            item["status"] = status_fn(item.get("vencimento"), item.get("data_pagamento"))
            if item["status"] == "Paga":
                continue
            # Nunca anunciar PDF inacessivel: os caminhos Windows ainda
            # precisam de integracao R2/agente para visualizacao na VPS.
            item["tem_pdf"] = False
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
    row = conn.execute(
        """SELECT c.id FROM contas c WHERE c.id=?
           AND NOT EXISTS (
              SELECT 1 FROM pagamentos p WHERE p.conta_id=c.id
                AND p.data_pagamento IS NOT NULL
                AND TRIM(p.data_pagamento)<>''
           )""",
        (account_id,),
    ).fetchone()
    if row is None:
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
