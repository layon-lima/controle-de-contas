"""Smoke test do dashboard PySide6 real, com widgets originais e dados temporários."""
import os
import sys
import tempfile
from pathlib import Path
from PySide6.QtCore import Qt

# Consoles Windows may default to cp1252; keep the test functional and
# escape only characters the console cannot encode (e.g. status bullet).
# This changes only QA log output, not UI data or assertions.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="backslashreplace")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
temp = tempfile.mkdtemp(prefix="cc_ui_qa_")
os.environ["LOCALAPPDATA"] = temp
os.environ["APPDATA"] = temp

from PySide6.QtWidgets import QApplication, QDockWidget, QFrame, QHBoxLayout, QComboBox, QPushButton, QWidget, QHeaderView, QToolButton
from controle_contas.desktop_dashboard import _asset_icon, DueDateDelegate
app = QApplication.instance() or QApplication(sys.argv)

from controle_contas.patches import aplicar_patches
aplicar_patches()
from controle_contas.cloud_mobile_ui import integrate_cloud_mobile_button
integrate_cloud_mobile_button()
from controle_contas import ui

window = ui.JanelaPrincipal()

bottom = window.findChild(QFrame, "bottomNavigation")
assert bottom is not None, "Navegação inferior não foi montada"
assert getattr(window, "_desktop_dashboard", None) is not None, "Chrome novo não instalado"
assert window.findChild(QDockWidget, "modernNavigation") is None, "Menu lateral antigo permanece"
assert window.findChild(QDockWidget, "paymentDetailDock") is None, "Painel de detalhes ainda instalado"
assert window.findChild(QComboBox, "desktopTagFilter") is not None, "Filtro por TAG ausente"
assert window._desktop_dashboard._desktop_features is not None, "Fluxos desktop ausentes"
assert window.tabela.columnCount() >= 10, "Tabela perdeu colunas"
assert window.findChild(QPushButton, "desktopPayBatchButton") is None, "Ação em lote deveria ter sido removida"
assert window.findChild(QPushButton, "desktopTagsButton") is None, "TAGs no topo devem sair"
assert window.tabela.columnCount() >= 10, "Faltou coluna de ações"
assert window.tabela.horizontalHeaderItem(9).text() == "Ações", "Coluna de ações errada"
assert window.tabela.horizontalHeaderItem(8).text() == "Observações", "Observacoes ausentes"
assert not window.tabela.isColumnHidden(8), "Observacoes continuam ocultas"
assert window.tabela.isColumnHidden(7), "ID interno exposto"
assert window.tabela.horizontalHeader().visualIndex(8) == 1, "Observacoes fora de ordem"
assert window.tabela.columnWidth(1) >= 155, "Vencimento estreito"
assert window.tabela.columnWidth(9) >= 120, "Acoes cortadas"
assert isinstance(window.tabela.itemDelegateForColumn(1), DueDateDelegate), (
    "Vencimento continua com renderizacao original/duplicada"
)
assert window.tabela.verticalHeader().defaultSectionSize() >= 48, (
    "Altura insuficiente para data e prazo"
)
for row in range(window.tabela.rowCount()):
    assert window.tabela.cellWidget(row, 1) is None, (
        "Data duplicada: widget transparente sobre QTableWidgetItem"
    )
for name in ("brand","smartphone","upload","plus","overdue","upcoming","ontime",
             "paid","home","accounts","payments","reports","settings",
             "document","send","more","edit","receipt","trash","suggest","sent","tag"):
    asset = _asset_icon(name)
    assert not asset.isNull(), "Icone ausente: " + name
    assert not asset.pixmap(20, 20).isNull(), "Falha ao renderizar SVG: " + name
assert window._bulk_bar.maximumHeight() <= 50, "Faixa de filtros alta"
assert window.busca.parentWidget() is window._bulk_bar, "Busca longe da tabela"
strip = window.findChild(QWidget, "dashboardKpiStrip")
assert strip is not None, "Cards não foram movidos ao cabeçalho"
assert all(c.parentWidget() is strip for c in window.cards.values())
nav_buttons = bottom.findChildren(QPushButton)
assert [b.text() for b in nav_buttons] == ["Em aberto", "Pagas"]
header = window.tabela.horizontalHeader()
for col in (0,1,2,3,4,5,8,9):
    assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
    title = window.tabela.horizontalHeaderItem(col)
    assert title.textAlignment() & Qt.AlignmentFlag.AlignLeft, (
        "O título deve permanecer à esquerda"
    )
assert window.tabela.showGrid(), "As divisórias das colunas não estão visíveis"
assert "border-right: 2px solid" in window.tabela.styleSheet(), (
    "Separadores visíveis no cabeçalho não foram aplicados"
)
minima = window._desktop_dashboard._minimum_column_widths
assert minima[9] >= 225, "Ações deve comportar todos os seis ícones"
for col in (0, 1, 2, 3, 4, 5, 8, 9):
    window.tabela.setColumnWidth(col, 15)
    assert window.tabela.columnWidth(col) >= minima[col], (
        f"A coluna {col} desaparece quando é arrastada"
    )
    assert not window.tabela.isColumnHidden(col), "Coluna visível foi escondida"
window.tabela.setColumnWidth(1, 212)
window._desktop_dashboard._save_column_widths()
import json
settings = Path(temp) / "ControleContas" / "desktop_columns.json"
assert json.loads(settings.read_text(encoding="utf-8"))["1"] == 212
# O app não deve aceitar larguras salvas antigas menores que o limite.
window.tabela.setColumnWidth(9, 10)
window._desktop_dashboard._save_column_widths()
assert json.loads(settings.read_text(encoding="utf-8"))["9"] >= minima[9]
window.tabela.setColumnWidth(1, 160)
assert window.abas.isHidden(), "Barra antiga de abas ainda visível"
assert getattr(window, "busca", None) is not None, "Busca antiga perdida"

print("UI INSTALLED")
print("window", window.size().width(), window.size().height())
print("bottom", bottom.isHidden(), bottom.height())
print("table", window.tabela.columnCount())
print("search parent", window.busca.parentWidget().objectName())
print("tabs hidden", window.abas.isHidden())
for key, card in getattr(window, "cards", {}).items():
    parent=card.parentWidget()
    print("KPI",key,card.maximumHeight(),"layout",
          type(parent.layout()).__name__ if parent and parent.layout() else "NONE")
print("KPI4 reused", bool(window._desktop_dashboard._paid_month_card))
print("KPI4 labels",
      [label.text() for label in window._desktop_dashboard._paid_month_card.findChildren(
          __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel
      )] if window._desktop_dashboard._paid_month_card else [])
print("KPI4 count bound", window._desktop_dashboard._paid_month_count is not None)
print("KPI4 amount bound", window._desktop_dashboard._paid_month_value is not None)
print("OBS visible", not window.tabela.isColumnHidden(8))
print("date width", window.tabela.columnWidth(1))
print("bulk height", window._bulk_bar.height())
assert window._desktop_dashboard._paid_month_card is not None, "Card de pagas não reaproveitado"
assert window._desktop_dashboard._paid_month_count is not None, "Quantidade do card não reconhecida"
assert window._desktop_dashboard._paid_month_value is not None, "Valor do card não reconhecido"

dash = window._desktop_dashboard
dash.decorate_rows()
for row in range(window.tabela.rowCount()):
    assert window.tabela.cellWidget(row, 1) is None, "Widget de data reapareceu"
# Prova o desenho de uma data real sem duplicar o item ou depender do banco.
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
sample = QTableWidget(1, 1)
sample.setItemDelegateForColumn(0, DueDateDelegate(sample))
sample.setItem(0, 0, QTableWidgetItem("09/10/2026"))
sample.resize(220, 100)
sample.show()
app.processEvents()
assert sample.item(0, 0).text() == "09/10/2026", "Vencimento original modificado"
assert not sample.viewport().grab().isNull(), "Delegate nao renderizou"
assert sample.cellWidget(0, 0) is None, "Renderizacao duplicada na celula"
sample.close()
dash._navigate("payments")
assert window.abas.currentIndex() == 1, "Pagamentos não navega à aba existente"
dash._navigate("accounts")
assert window.abas.currentIndex() == 0, "Contas não navega à aba existente"
dash._navigate("accounts")
print("BOTTOM NAV OK")

# Teste isolado: cadastro e baixa de conta SEM PDF usando o Banco real.
# O QA redirecionou LOCALAPPDATA/APPDATA para pasta temporaria no inicio.
from datetime import datetime
with window.banco.conexao() as conn:
    now = datetime.now().isoformat(timespec="seconds")
    cursor = conn.execute(
        "INSERT INTO contas(fornecedor, valor, vencimento, criado_em, atualizado_em) "
        "VALUES(?,?,?,?,?)",
        ("QA PAGAMENTO SEM COMPROVANTE", 123.45, "2026-10-09", now, now)
    )
    qa_id = int(cursor.lastrowid)
    # Reproduz o cadastro real em que a data de envio pode ser string vazia.
    conn.execute("UPDATE contas SET enviado_pagamento_em='' WHERE id=?", (qa_id,))
# O ícone Enviar deve acionar o botão ORIGINAL de copiar dados,
# e só então registrar o envio da conta selecionada.
from PySide6.QtWidgets import QToolButton, QLabel
features = window._desktop_dashboard._desktop_features
original_send = window._desktop_dashboard._buttons.get("original_send")
copy_action = QPushButton("Copiar dados da conta para pagamento", window)
copy_action.clicked.connect(
    lambda: QApplication.clipboard().setText(
        "PAGAMENTO QA: dados da conta copiados para envio"
    )
)
window._desktop_dashboard._buttons["original_send"] = copy_action
QApplication.clipboard().setText("texto anterior")
window.abas.setCurrentIndex(0)
window.atualizar_tabela()
qa_row = next(
    i for i in range(window.tabela.rowCount())
    if window.tabela.item(i, 7)
    and int(window.tabela.item(i, 7).text()) == qa_id
)
assert window._desktop_dashboard._activate_row(
    qa_row, "Enviar para pagamento"
) is True, "O ícone da linha não acionou o botão original"
assert QApplication.clipboard().text().startswith("PAGAMENTO QA:"), (
    "Dados não foram copiados para a área de transferência"
)
with window.banco.conexao() as conn:
    row = conn.execute(
        "SELECT enviado_pagamento_em FROM contas WHERE id=?", (qa_id,)
    ).fetchone()
    assert row and str(row["enviado_pagamento_em"] or "").strip(), (
        "Data de envio não foi registrada com campo inicialmente vazio"
    )
    first_sent_at = str(row["enviado_pagamento_em"])
# O horário original deve ser preservado nas cópias subsequentes.
features._record_sent(qa_id)
with window.banco.conexao() as conn:
    updated = conn.execute(
        "SELECT enviado_pagamento_em FROM contas WHERE id=?", (qa_id,)
    ).fetchone()
    assert updated and updated["enviado_pagamento_em"] == first_sent_at, (
        "Reenvio sobrescreveu indevidamente a data inicial"
    )
window._desktop_dashboard._buttons["original_send"] = original_send
# Ausência de handler herdado não pode deixar a ação inerte.
window._desktop_dashboard._buttons["original_send"] = None
native_lookup = window._desktop_dashboard._find_action
window._desktop_dashboard._find_action = lambda text: None
QApplication.clipboard().setText("texto externo")
assert features.send_for_payment(qa_id) is True, "Fluxo de cópia alternativo falhou"
copied_text = QApplication.clipboard().text()
assert "FORNECEDOR: QA PAGAMENTO SEM COMPROVANTE" in copied_text
assert "VALOR: R$ 123,45" in copied_text
window._desktop_dashboard._find_action = native_lookup
window._desktop_dashboard._buttons["original_send"] = original_send
dashboard = window._desktop_dashboard
dashboard._show_action_feedback("Cópia concluída", "success")
notice = window.findChild(QLabel, "dashboardActionFeedback")
assert notice is not None and "Cópia concluída" in notice.text(), (
    "Feedback visual de sucesso não foi exibido"
)
dashboard._show_action_feedback("Falha de teste", "error")
assert "Falha de teste" in notice.text(), "Feedback visual de erro não funciona"

window.banco.registrar_pagamento(qa_id, {
    "valor_pago": 123.45,
    "data_pagamento": "2026-10-09",
    "beneficiario": "QA PAGAMENTO SEM COMPROVANTE",
    "documento_beneficiario": "",
    "pdf_comprovante": "",
    "resultado_conferencia": "Sem comprovante",
    "detalhes_conferencia": "Teste local",
})
with window.banco.conexao() as conn:
    record = conn.execute(
        "SELECT data_pagamento,pdf_comprovante FROM pagamentos WHERE conta_id=?",
        (qa_id,)
    ).fetchone()
    assert record is not None and record["data_pagamento"] == "2026-10-09", (
        "Pagamento sem PDF nao persistiu"
    )
    assert not record["pdf_comprovante"], "QA deve aceitar comprovante opcional"
    tag = conn.execute(
        "INSERT INTO cc_tags(name) VALUES(?)", ("HOLERITES QA",)
    )
    conn.execute(
        "INSERT INTO cc_account_tags(account_id,tag_id) VALUES(?,?)",
        (qa_id, int(tag.lastrowid))
    )

window._desktop_dashboard._desktop_features._refresh_tags()
window.abas.setCurrentIndex(0)
window.atualizar_tabela()
assert window.tabela.showGrid(), "Atualização desligou a grade"
qa_items = [
    window.tabela.item(i, col)
    for i in range(window.tabela.rowCount())
    for col in (0, 1, 2, 3, 4, 5, 8)
    if window.tabela.item(i, col) is not None
]
assert all(item.textAlignment() & Qt.AlignmentFlag.AlignLeft for item in qa_items), (
    "O texto das células deve ficar preso à esquerda"
)
pending_ids = [
    int(window.tabela.item(i,7).text()) for i in range(window.tabela.rowCount())
    if window.tabela.item(i,7)
]
assert qa_id not in pending_ids, "Conta paga sem PDF ainda aparece como pendente"
window.abas.setCurrentIndex(1)
window.atualizar_tabela()
assert window.tabela.showGrid(), "A aba Pagas perdeu as divisórias"
paid_rows = [
    i for i in range(window.tabela.rowCount())
    if window.tabela.item(i,7) and int(window.tabela.item(i,7).text()) == qa_id
]
assert len(paid_rows) == 1, "Conta paga nao apareceu em Pagamentos"
actions = window.tabela.cellWidget(paid_rows[0],9)
assert actions is not None and len(actions.findChildren(QToolButton)) == 6, "Ações de linha incompletas"
assert ui.PagamentoDialog.validar.__name__ == "_validar_pagamento_com_pasta"
assert ui.PagamentoDialog.dados.__name__ == "_dados_pagamento_sem_copia"
obs = window.tabela.item(paid_rows[0],8)
assert obs is not None and "#HOLERITES QA" in obs.text(), (
    "TAG nao foi apresentada na listagem"
)
print("PAYMENT WITHOUT RECEIPT AND TAGS PASS")

window.close()
app.quit()
print("DASHBOARD UI SMOKE PASS")
