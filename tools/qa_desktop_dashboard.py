"""Smoke test do dashboard PySide6 real, com widgets originais e dados temporários."""
import os
import sys
import tempfile
from pathlib import Path

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
print("KPI4", bool(window.findChild(QFrame, "dashboardPaidMonth")))

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
