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
# Aparência: superfícies, hierarquia tipográfica e estados de interação
# usam uma só linguagem visual. Não alteram o layout nem os dados.
from controle_contas.desktop_dashboard import DASHBOARD_STYLE
assert "QTableWidget::item:hover" in DASHBOARD_STYLE
assert "QHeaderView::section:hover" in DASHBOARD_STYLE
assert "QPushButton:pressed" in DASHBOARD_STYLE
assert "QMenu::item:selected" in DASHBOARD_STYLE
assert "QTableWidget QToolButton#sendPaymentIcon" in DASHBOARD_STYLE
assert "QTableWidget QToolButton#registerPaymentIcon" in DASHBOARD_STYLE
assert "QToolTip" in DASHBOARD_STYLE
assert "background: #f3f6fa" in DASHBOARD_STYLE, "Tema geral não foi aplicado"
assert "gridline-color: #e7edf4" in DASHBOARD_STYLE, (
    "Divisórias perderam consistência visual"
)
for kpi in window._desktop_dashboard._kpi_cards:
    assert "border-left:3px solid" in kpi.styleSheet(), (
        "Cards deveriam usar superfície neutra e uma faixa de status discreta"
    )
assert "background:#ffffff" in window._desktop_dashboard._kpi_cards[0].styleSheet()
assert "#ffffff" in window._bulk_bar.styleSheet(), (
    "Barra de filtros mantém tema diferente"
)
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
# Envio SEM PDF: o conteúdo real precisa ser copiado antes do registro.
# O botão legado não pode ser acionado, pois pressupunha anexo PDF.
from PySide6.QtWidgets import QToolButton, QLabel
features = window._desktop_dashboard._desktop_features
original_send = window._desktop_dashboard._buttons.get("original_send")
legacy = QPushButton("LEGADO", window)
legacy_activated = []
legacy.clicked.connect(lambda: legacy_activated.append(True))
window._desktop_dashboard._buttons["original_send"] = legacy
window.abas.setCurrentIndex(0)
window.atualizar_tabela()
qa_row = next(
    i for i in range(window.tabela.rowCount())
    if window.tabela.item(i, 7)
    and int(window.tabela.item(i, 7).text()) == qa_id
)

# Dados obrigatórios inválidos não podem ser enviados nem gravados.
with window.banco.conexao() as conn:
    conn.execute("UPDATE contas SET valor=0 WHERE id=?", (qa_id,))
assert features.send_for_payment(qa_id) is False, (
    "Conta sem valor foi indevidamente marcada como enviada"
)
with window.banco.conexao() as conn:
    row = conn.execute(
        "SELECT enviado_pagamento_em FROM contas WHERE id=?", (qa_id,)
    ).fetchone()
    assert not str(row["enviado_pagamento_em"] or "").strip()
    conn.execute("UPDATE contas SET valor=123.45 WHERE id=?", (qa_id,))

# Copiar mal-sucedido: jamais gravar uma data nem indicar sucesso.
real_copy = features._copy_payment_payload
features._copy_payment_payload = lambda _message: False
with window.banco.conexao() as conn:
    before = conn.execute(
        "SELECT enviado_pagamento_em FROM contas WHERE id=?", (qa_id,)
    ).fetchone()
assert not str(before["enviado_pagamento_em"] or "").strip()
assert window._desktop_dashboard._activate_row(
    qa_row, "Enviar para pagamento"
) is False, "Uma cópia falha não pode indicar envio concluído"
with window.banco.conexao() as conn:
    failed = conn.execute(
        "SELECT enviado_pagamento_em FROM contas WHERE id=?", (qa_id,)
    ).fetchone()
assert not str(failed["enviado_pagamento_em"] or "").strip(), (
    "Falha na cópia gravou falsamente a data de envio"
)

features._copy_payment_payload = real_copy
QApplication.clipboard().setText("ANTERIOR")
assert window._desktop_dashboard._activate_row(
    qa_row, "Enviar para pagamento"
) is True, "Envio com dados da conta sem PDF deveria funcionar"
copied_text = QApplication.clipboard().text()
assert "FORNECEDOR: QA PAGAMENTO SEM COMPROVANTE" in copied_text
assert "VALOR: R$ 123,45" in copied_text
assert not legacy_activated, "O handler antigo dependente de PDF foi executado"
with window.banco.conexao() as conn:
    row = conn.execute(
        "SELECT enviado_pagamento_em,pdf_conta FROM contas WHERE id=?", (qa_id,)
    ).fetchone()
    assert not row["pdf_conta"], "Conta do teste deveria continuar sem PDF"
    assert row and str(row["enviado_pagamento_em"] or "").strip(), (
        "O envio válido não persistiu no banco"
    )
    first_sent_at = str(row["enviado_pagamento_em"])

# Repetir cópia preserva o momento original de envio.
assert features.send_for_payment(qa_id) is True
with window.banco.conexao() as conn:
    repeated = conn.execute(
        "SELECT enviado_pagamento_em FROM contas WHERE id=?", (qa_id,)
    ).fetchone()
    assert repeated["enviado_pagamento_em"] == first_sent_at
window._desktop_dashboard._buttons["original_send"] = original_send
dashboard = window._desktop_dashboard

# Nenhuma confirmação pode ocupar grande parte da tela.
dashboard._show_action_feedback("Cópia concluída", "success")
notice = window.findChild(QLabel, "dashboardActionFeedback")
assert notice is not None and "Cópia concluída" in notice.text()
assert notice.height() <= 30 and notice.width() <= 290, (
    "O aviso visual voltou a ocupar espaço demais na tela"
)
dashboard._show_action_feedback("Falha de teste", "error")
assert "Falha de teste" in notice.text()

# Quatro cards reais devem se adaptar a largura sem sobrepor o cabeçalho.
strip = window.findChild(QWidget, "dashboardKpiStrip")
assert strip is not None
assert strip.parentWidget() is window.centralWidget(), (
    "Cards foram novamente comprimidos dentro do cabeçalho"
)
grid = strip.layout()
assert isinstance(grid, __import__(
    "PySide6.QtWidgets", fromlist=["QGridLayout"]
).QGridLayout)
for size in (1380, 900, 590):
    window.resize(size, 760)
    app.processEvents()
    dashboard._layout_kpis()
    usable = max(0, window.centralWidget().width() - 32)
    expected_cols = 4 if usable >= 1130 else 2 if usable >= 460 else 1
    assert dashboard._kpi_columns == expected_cols, (
        "A grade não acompanhou o tamanho da janela"
    )
    for index, card in enumerate(dashboard._kpi_cards):
        cell = grid.itemAtPosition(index // expected_cols, index % expected_cols)
        assert cell is not None and cell.widget() is card, (
            "Card desapareceu ou ficou sobre outro após redimensionar"
        )
    if usable < 760:
        assert window._desktop_dashboard._buttons["import"].text() == "", (
            "Importar PDF não ficou compacto em janela estreita"
        )
        assert features.tag_filter.width() <= 125, (
            "Filtro por TAG continua ocupando espaço demais"
        )
window.resize(1380, 840)
app.processEvents()
dashboard._layout_kpis()
# Em offscreen, o QWidget central nem sempre acompanha os pixels solicitados
# a um QMainWindow que não foi exibido. Validar a LARGURA REAL, não o resize.
real_width = max(0, window.centralWidget().width() - 32)
expected_import = "" if real_width < 890 else "Importar PDF"
expected_tag_width = 120 if real_width < 760 else 170
assert dashboard._buttons["import"].text() == expected_import, (
    f"Importar PDF incompatível com largura real de {real_width}px"
)
assert features.tag_filter.width() == expected_tag_width, (
    f"Filtro TAG incompatível com largura real de {real_width}px"
)
# Exercitar as duas transições independentemente da geometria do Qt offscreen:
dashboard._adapt_header_to_width(1380)
assert dashboard._buttons["import"].text() == "Importar PDF", (
    "O texto completo não voltou após expandir a área disponível"
)
assert features.tag_filter.width() == 170
dashboard._adapt_header_to_width(540)
assert dashboard._buttons["import"].text() == ""
assert features.tag_filter.width() == 120
# Devolver a aparência ao tamanho real ao final da simulação.
dashboard._adapt_header_to_width(real_width)

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
assert len(actions.findChildren(QToolButton, "sendPaymentIcon")) == 1, (
    "Ícone Enviar não tem identidade visual própria"
)
assert len(actions.findChildren(QToolButton, "registerPaymentIcon")) == 1, (
    "Ícone Registrar pagamento não tem identidade visual própria"
)
assert ui.PagamentoDialog.validar.__name__ == "_validar_pagamento_com_pasta"
assert ui.PagamentoDialog.dados.__name__ == "_dados_pagamento_sem_copia"
obs = window.tabela.item(paid_rows[0],8)
assert obs is not None and "#HOLERITES QA" in obs.text(), (
    "TAG nao foi apresentada na listagem"
)
print("PAYMENT WITHOUT RECEIPT AND TAGS PASS")

# O observador deve ser destruído com a interface, sem tentar acessar
# C++ QWidget já liberado nos callbacks finais de encerramento do Qt.
from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid
watcher = dashboard._kpi_watcher
assert watcher.parent() is window.centralWidget()
window.close()
window.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()
assert not isValid(window), "Qt não liberou a janela no encerramento do teste"
assert not isValid(watcher), "Observador responsivo ficou vivo após a janela"
app.quit()
print("DASHBOARD UI SMOKE PASS")
