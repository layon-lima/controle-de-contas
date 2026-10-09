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

from PySide6.QtWidgets import QApplication, QDockWidget, QFrame, QHBoxLayout
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
assert window.tabela.columnCount() >= 10, "Faltou coluna de ações"
assert window.tabela.horizontalHeaderItem(9).text() == "Ações", "Coluna de ações errada"
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
assert window._desktop_dashboard._paid_month_card is not None, "Card de pagas não reaproveitado"
assert window._desktop_dashboard._paid_month_count is not None, "Quantidade do card não reconhecida"
assert window._desktop_dashboard._paid_month_value is not None, "Valor do card não reconhecido"

dash = window._desktop_dashboard
dash.decorate_rows()
dash._navigate("payments")
assert window.abas.currentIndex() == 1, "Pagamentos não navega à aba existente"
dash._navigate("accounts")
assert window.abas.currentIndex() == 0, "Contas não navega à aba existente"
dash._navigate("overview")
print("BOTTOM NAV OK")

window.close()
app.quit()
print("DASHBOARD UI SMOKE PASS")
