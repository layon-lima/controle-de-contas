"""Smoke test do dashboard PySide6 real, com widgets originais e dados temporários."""
import os
import sys
import tempfile
from pathlib import Path

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

from PySide6.QtWidgets import QApplication, QDockWidget, QFrame, QHBoxLayout, QComboBox, QPushButton
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
assert window.findChild(QPushButton, "desktopPayBatchButton") is not None, "Pagamento em lote ausente"
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
             "document","send","more","edit","receipt","trash"):
    asset = _asset_icon(name)
    assert not asset.isNull(), "Icone ausente: " + name
    assert not asset.pixmap(20, 20).isNull(), "Falha ao renderizar SVG: " + name
assert window._bulk_bar.maximumHeight() <= 45, "Barra de acoes nao compactada"
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
dash._navigate("overview")
print("BOTTOM NAV OK")

window.close()
app.quit()
print("DASHBOARD UI SMOKE PASS")
