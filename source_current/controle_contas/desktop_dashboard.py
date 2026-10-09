"""Apresentação da janela desktop. Reaproveita os controles e métodos existentes.

Não implementa regras de contas, PDFs, pagamentos ou sincronização.
Não altera a estrutura do banco. Instala uma única vez sobre a JanelaPrincipal.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget, QMenu,
    QHeaderView,
)


DASHBOARD_STYLE = """
QMainWindow { background: #f4f8fc; }
QMainWindow > QWidget { font-family: 'Segoe UI'; font-size: 12px; color: #182b48; }
QFrame#headerPanel {
    background: #ffffff; border: 1px solid #e5edf6;
    border-radius: 14px;
}
QPushButton {
    color: #263f61; background: #ffffff;
    border: 1px solid #dce6f0; border-radius: 9px;
    min-height: 33px; padding: 0 12px; font-weight: 600;
}
QPushButton:hover { background: #eff6ff; border-color: #bcd1eb; }
QPushButton:disabled { color: #97a6b9; background: #f5f7f9; border-color: #e2e8ef; }
QPushButton#primaryButton {
    background: #078b59; border-color: #078b59; color: #ffffff;
    font-weight: 800;
}
QPushButton#primaryButton:hover { background: #067a4e; }
QPushButton#bulkPrimary {
    background: #e9f8f1; border: 1px solid #c4e9d4;
    color: #10774f; font-weight: 800;
}
QPushButton#dangerButton { color: #bb343d; border-color: #f3c8cd; }
QLineEdit, QComboBox {
    background: white; border: 1px solid #d8e3f0; border-radius: 10px;
    min-height: 35px; padding: 0 12px; color: #2b425e;
    selection-background-color: #e0edff;
}
QLineEdit:focus, QComboBox:focus { border-color: #5b98ed; }
QFrame#bulkPaymentBar {
    background: #ffffff; border: 0px; border-radius: 0;
}
QFrame#bulkPaymentBar QLabel { color: #687e99; }
QFrame#bulkPaymentBar QPushButton {
    min-height: 34px; padding: 0 10px;
    border: 1px solid #dde7f0; border-radius: 9px;
    color: #253d5d; background: white; font-weight: 700;
}
QFrame#bulkPaymentBar QPushButton#bulkPrimary {
    background: #e7f7ee; color: #0d8255; border-color: #b9e7cd;
}
QFrame#bulkPaymentBar QPushButton#primaryButton {
    background: #078b59; border-color: #078b59;
    color: #ffffff; font-weight: 800;
}
QFrame#bottomNavigation {
    background: #ffffff; border: 1px solid #dfeaf4;
    border-radius: 15px;
}
QPushButton#bottomNavButton {
    color: #4a6180; background: transparent; border: 0;
    border-radius: 11px; min-height: 52px;
    text-align: left; padding: 0 12px; font-size: 12px;
    font-weight: 650;
}
QPushButton#bottomNavButton:hover { background: #f1f6fc; }
QPushButton#bottomNavButton[active="true"] {
    background: #e6f1ff; color: #1769dc; font-weight: 800;
}
QTableWidget {
    background: #ffffff; alternate-background-color: #ffffff;
    border: 1px solid #e4ecf4; border-radius: 12px;
    gridline-color: #f0f3f8;
    selection-background-color: #e8f2fe;
    selection-color: #172b44;
    font-family: 'Segoe UI'; font-size: 12px;
}
QTableWidget::item {
    border-bottom: 1px solid #eff3f8; padding: 5px 7px;
    color: #203654;
}
QTableWidget::item:selected { background: #e8f2fe; color: #172b44; }
QHeaderView::section {
    background: #f2f7fb; color: #58708f; border: 0;
    border-bottom: 1px solid #e5edf5; padding: 8px;
    font-weight: 700; font-size: 11px;
}
QTableWidget QToolButton {
    border: 1px solid #dce7f1; border-radius: 7px;
    background: #ffffff; color: #24456b;
    font-size: 15px; padding: 0;
}
QTableWidget QToolButton:hover { background: #eaf3ff; border-color: #9fc4f5; }
QScrollBar:vertical { background: #f9fbfe; width: 10px; }
QScrollBar::handle:vertical { background: #c6d4e4; min-height: 35px; border-radius: 5px; }
"""


class DesktopDashboard:
    """Somente composição visual; nunca substitui fluxo de pagamento ou PDF."""

    def __init__(self, window):
        self.window = window
        self._buttons = {}
        self._paid_month_card = None
        self._paid_month_value = None
        self._paid_month_count = None
        self._install()

    def _find_action(self, text):
        for button in self.window.findChildren(QPushButton):
            if button.property("dashboardAction") or button.objectName().startswith("nav"):
                continue
            if text.casefold() in str(button.text() or "").casefold():
                if button.objectName() in {"detailEditAccount", "detailOpenReceipt"}:
                    continue
                return button
        return None

    def _click_original(self, text):
        button = self._find_action(text)
        if button is not None and button.isEnabled():
            button.click()

    def _install(self):
        w = self.window
        if getattr(w, "_desktop_dashboard", None) is not None:
            return
        w._desktop_dashboard = self
        w.setStyleSheet(DASHBOARD_STYLE)
        central = w.centralWidget()
        if central and central.layout():
            central.layout().setContentsMargins(16, 12, 16, 10)
            central.layout().setSpacing(8)
        self._remove_old_sidebar()
        self._build_header()
        self._style_cards()
        self._build_bottom_navigation()
        self._configure_account_table()
        self._rearrange_actions()

    def _remove_old_sidebar(self):
        from PySide6.QtWidgets import QDockWidget

        w = self.window
        old = w.findChild(QDockWidget, "modernNavigation")
        if old is not None:
            w.removeDockWidget(old)
            old.deleteLater()
        self.window._modern_shell_installed = False

    def _build_header(self):
        w = self.window
        header = w.findChild(QFrame, "headerPanel")
        if header is None or header.layout() is None:
            raise RuntimeError("Cabeçalho original não localizado.")
        lay = header.layout()
        header.setFixedHeight(76)
        lay.setContentsMargins(14, 7, 14, 7)
        lay.setSpacing(10)

        # A busca é o campo original: preserva debounce, busca exata e callbacks.
        search = getattr(w, "busca", None)
        if search is not None:
            previous = search.parentWidget()
            if previous and previous.layout():
                previous.layout().removeWidget(search)
            search.setParent(header)
            search.setMinimumWidth(220)
            search.setMaximumWidth(650)
            search.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )

        brand = QWidget(header)
        brand_lay = QHBoxLayout(brand)
        brand_lay.setContentsMargins(0, 0, 0, 0)
        brand_lay.setSpacing(10)

        square = QLabel("▣", brand)
        square.setFixedSize(46, 46)
        square.setAlignment(Qt.AlignmentFlag.AlignCenter)
        square.setStyleSheet(
            "background:#237cf0;color:white;border-radius:12px;"
            "font-size:24px;font-weight:900;"
        )
        brand_lay.addWidget(square)

        brand_text = QWidget(brand)
        text_layout = QVBoxLayout(brand_text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        title = QLabel("Controle de Contas", brand_text)
        title.setStyleSheet("color:#101d39;font-size:19px;font-weight:850;")
        subtitle = QLabel("Gestão de contas a pagar e pagamentos", brand_text)
        subtitle.setStyleSheet("color:#8797ac;font-size:10px;")
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)
        brand_lay.addWidget(brand_text)
        lay.insertWidget(0, brand)
        if search is not None:
            lay.insertWidget(1, search, 1)
            search.setPlaceholderText(
                "Pesquisar fornecedor, valor, descrição ou vencimento..."
            )

        importer = QPushButton("⇧  Importar PDF", header)
        importer.setToolTip("Importar boleto, conta ou comprovante PDF.")
        importer.setObjectName("dashboardImport")
        importer.setProperty("dashboardAction", True)
        importer.clicked.connect(self._import_pdf)
        new_button = self._find_action("Nova conta")
        location = lay.indexOf(new_button) if new_button else lay.count()
        lay.insertWidget(max(0, location), importer)
        self._buttons["import"] = importer

        # Reestilizar botões reais, sem criar um segundo Nova conta.
        if new_button:
            new_button.setObjectName("primaryButton")
            new_button.setMinimumWidth(130)
            new_button.setMinimumHeight(39)
        mobile = self._find_action("Acesso mobile")
        if mobile:
            mobile.setText("▯  Acesso mobile")

        # A pasta já possui seu comando real em Configurações; não ocupa
        # também espaço duplicado no cabeçalho compacto.
        folder = getattr(w, "_patch_btn_pasta", None)
        if folder:
            folder.hide()

        drag = getattr(w, "area_drop", None)
        if drag:
            drag.setMaximumHeight(27)
            drag.setMinimumHeight(23)
            drag.setStyleSheet(
                "background:#fbfdff;border:1px dashed #dee7f2;"
                "border-radius:8px;color:#8190a5;font-size:10px;"
            )

    def _import_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self.window, "Importar documentos", str(Path.home()), "Arquivos PDF (*.pdf)"
        )
        for path in paths:
            self.window.processar_pdf_solto(path)

    def _style_cards(self):
        # O app já possui os quatro cards; não criamos cartões/contadores
        # duplicados. A altura compacta mantém mais linhas visíveis.
        cards = list(getattr(self.window, "cards", {}).items())
        colors = [
            ("#fff5f5", "#fee6e8", "#db2938", "◷"),
            ("#fffbf3", "#fff0d6", "#c9880b", "▦"),
            ("#f3fcf8", "#daf7e8", "#137b55", "✓"),
            ("#f4f8ff", "#e1edff", "#2470d8", "▥"),
        ]
        for index, (_key, card) in enumerate(cards[:4]):
            if card is None:
                continue
            bg, icon_bg, accent, symbol = colors[index]
            card.setMinimumHeight(72)
            card.setMaximumHeight(78)
            card.setObjectName(f"dashboardKpi{index}")
            card.setStyleSheet(
                f"QFrame#dashboardKpi{index} {{"
                f"background:{bg};border:1px solid #e9eff5;border-radius:14px;"
                "}"
                "QFrame QLabel{background:transparent;border:0;}"
            )
            existing_layout = card.layout()
            if existing_layout:
                existing_layout.setContentsMargins(62, 5, 10, 5)
                existing_layout.setSpacing(0)
            for label in card.findChildren(QLabel):
                label.setFont(QFont("Segoe UI", max(9, label.font().pointSize())))
                label.setStyleSheet(
                    f"background:transparent;border:0;color:{accent};"
                    + ("font-weight:800;" if label.text().strip().isdigit() else "")
                )
            icon = QLabel(symbol, card)
            icon.setObjectName("dashboardKpiIcon")
            icon.setGeometry(10, 12, 45, 45)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet(
                f"background:{icon_bg};color:{accent};font-size:23px;"
                "font-weight:850;border-radius:12px;"
            )
            icon.raise_()

            if index == 3:
                self._paid_month_card = card
                for label in card.findChildren(QLabel):
                    text = str(label.text() or "").strip()
                    if "paga" in text.casefold():
                        label.setText("PAGAS (ESTE MÊS)")
                    elif text.isdigit():
                        self._paid_month_count = label
                    elif "R$" in text:
                        self._paid_month_value = label

        self.refresh_paid_month()

    def refresh_paid_month(self):
        if self._paid_month_card is None:
            return
        try:
            month = datetime.now().strftime("%Y-%m")
            with self.window.banco.conexao() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS quantidade, "
                    "COALESCE(SUM(COALESCE(valor_pago, valor, 0)),0) AS total "
                    "FROM contas WHERE data_pagamento LIKE ?",
                    (month + "%",),
                ).fetchone()
            count = int(row["quantidade"])
            amount = float(row["total"])
            formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            self._paid_month_count.setText(str(count))
            self._paid_month_value.setText("R$ " + formatted)
        except Exception:
            self._paid_month_count.setText("—")
            self._paid_month_value.setText("Sem dados")

    def _build_bottom_navigation(self):
        w = self.window
        central = w.centralWidget()
        layout = central.layout() if central else None
        if layout is None:
            raise RuntimeError("Layout principal indisponível para navegação inferior.")

        # Não exibir abas duplicadas — o QTabWidget existente continua operante.
        # A versão legada usa QTabBar diretamente (não QTabWidget).
        # Escondemos apenas as abas visuais: o índice ainda dirige a lista.
        w.abas.hide()

        nav = QFrame(central)
        nav.setObjectName("bottomNavigation")
        nav.setFixedHeight(64)
        row = QHBoxLayout(nav)
        row.setContentsMargins(10, 5, 10, 5)
        row.setSpacing(6)

        items = (
            ("overview", "⌂   Visão geral", lambda: self._navigate("overview")),
            ("accounts", "▤   Contas", lambda: self._navigate("accounts")),
            ("payments", "▣   Pagamentos", lambda: self._navigate("payments")),
            ("reports", "▥   Relatórios", self._reports),
            ("settings", "⚙   Configurações", self._settings),
        )
        for key, caption, callback in items:
            button = QPushButton(caption, nav)
            button.setObjectName("bottomNavButton")
            button.setProperty("dashboardAction", True)
            button.clicked.connect(callback)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            row.addWidget(button, 1)
            self._buttons[key] = button
        layout.addWidget(nav, 0)
        self._buttons["bottom"] = nav
        self._navigate("overview")

    def _navigate(self, mode):
        w = self.window
        if mode == "payments":
            w.abas.setCurrentIndex(1)
        else:
            w.abas.setCurrentIndex(0)
        # Contas e Pagamentos priorizam a listagem e maximizam a área útil.
        show_summary = mode == "overview"
        for card in getattr(w, "cards", {}).values():
            card.setVisible(show_summary)
        if self._paid_month_card:
            self._paid_month_card.setVisible(show_summary)
        for key in ("overview", "accounts", "payments"):
            button = self._buttons.get(key)
            if button:
                button.setProperty("active", key == mode)
                button.style().unpolish(button)
                button.style().polish(button)

    def _reports(self):
        # Não inventa uma área de gráficos sem dados; entrega um resumo real.
        self.refresh_paid_month()
        paid = (
            self._paid_month_count.text()
            if self._paid_month_count else "indisponível"
        )
        QMessageBox.information(
            self.window,
            "Relatório de pagamentos",
            "Contas pagas neste mês: " + paid
            + "\n\nUse a guia Pagamentos para consultar cada lançamento.",
        )

    def _settings(self):
        button = getattr(self.window, "_patch_btn_pasta", None)
        if button is not None:
            button.click()
        else:
            QMessageBox.information(
                self.window, "Configurações",
                "A configuração da pasta de documentos está indisponível.",
            )

    def _configure_account_table(self):
        table = self.window.tabela
        if table.columnCount() < 10:
            table.setColumnCount(10)
        table.setHorizontalHeaderItem(9, QTableWidgetItem("Ações"))
        table.setColumnHidden(6, True)
        table.setColumnHidden(7, True)
        table.setColumnHidden(8, True)
        table.setIconSize(QPixmap(28, 28).size())
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(40)
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        header = table.horizontalHeader()
        order = (0, 1, 2, 3, 5, 4, 9)
        for pos, logical in enumerate(order):
            visual = header.visualIndex(logical)
            if visual >= 0 and visual != pos:
                header.moveSection(visual, pos)

        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4, 5):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(9, 124)
        table.setStyleSheet(DASHBOARD_STYLE[DASHBOARD_STYLE.index("QTableWidget {"):])
        self.decorate_rows()

    def _rearrange_actions(self):
        w = self.window
        bulk = getattr(w, "_bulk_bar", None)
        if bulk is None or bulk.layout() is None:
            return

        layout = bulk.layout()
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(7)
        bulk.setFixedHeight(51)

        # Move o botão REAL do rodapé para a barra superior, mantendo
        # seu handler, regras de validação e permissões originais.
        payment = self._find_action("Registrar pagamento")
        if payment is not None:
            parent = payment.parentWidget()
            if parent and parent.layout():
                parent.layout().removeWidget(payment)
            payment.setParent(bulk)
            payment.setObjectName("primaryButton")
            payment.setMinimumWidth(165)
            layout.insertWidget(max(1, layout.count() - 1), payment)

        clear = getattr(w, "_bulk_clear_button", None)
        if clear:
            clear.setText("Limpar seleção")
        suggest = getattr(w, "_bulk_suggest_button", None)
        if suggest:
            suggest.setText("Sugerir pagamento")
        sent = getattr(w, "_bulk_sent_button", None)
        if sent:
            sent.setText("Marcar como enviado")

        # Mantém os outros handlers originais no app; botões no rodapé serão
        # acessados pelas ações por linha, sem criar novas regras de negócio.
        pool = QWidget(w)
        pool.hide()
        for text in ("Editar", "Enviar para pagamento", "Abrir conta",
                     "Abrir comprovante", "Excluir"):
            button = self._find_action(text)
            if button:
                parent = button.parentWidget()
                if parent and parent.layout():
                    parent.layout().removeWidget(button)
                button.setParent(pool)
                button.hide()
        self._buttons["action_pool"] = pool

    def _activate_row(self, row, action):
        table = self.window.tabela
        if row >= table.rowCount():
            return
        table.setCurrentCell(row, 0)
        table.selectRow(row)
        if action == "Editar":
            self._click_original("Editar")
        else:
            self._click_original(action)

    def _initials_icon(self, name):
        words = [part for part in str(name or "").split() if part]
        letters = (
            (words[0][:1] + words[1][:1]) if len(words) >= 2
            else str(name or "?")[:2]
        ).upper()
        palette = ["#287cf0", "#1fa477", "#9774eb", "#f06a76", "#d79a2d"]
        seed = sum(ord(ch) for ch in name)
        color = QColor(palette[seed % len(palette)])
        pix = QPixmap(30, 30)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 28, 28)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, letters)
        painter.end()
        return QIcon(pix)

    def decorate_rows(self):
        table = self.window.tabela
        if table.columnCount() < 10:
            return
        for row in range(table.rowCount()):
            supplier = table.item(row, 0)
            if supplier:
                supplier.setIcon(self._initials_icon(supplier.text()))
                supplier.setForeground(QColor("#1c3251"))
                supplier.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            for column in (1, 2, 4, 5):
                item = table.item(row, column)
                if item:
                    item.setFont(QFont("Segoe UI", 9))
                    item.setForeground(QColor("#263f61"))
                    if column == 2:
                        item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            status = table.item(row, 3)
            if status:
                status.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                colors = {
                    "Vencida": ("#fff0f1", "#be2330"),
                    "Próxima": ("#fff5dd", "#a76900"),
                    "Em dia": ("#e7f8ee", "#08794c"),
                    "Paga": ("#e7f8ee", "#08794c"),
                }
                bg, fg = colors.get(status.text(), ("#f1f5f9", "#536780"))
                status.setBackground(QColor(bg))
                status.setForeground(QColor(fg))

                # O item original mantém classificação/ordenação; o label
                # é apenas a apresentação arredondada vista no mockup.
                pill_wrap = QWidget(table)
                pill_wrap.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                pill_layout = QHBoxLayout(pill_wrap)
                pill_layout.setContentsMargins(5, 3, 5, 3)
                pill_layout.setSpacing(0)
                pill = QLabel(status.text(), pill_wrap)
                pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pill.setMinimumWidth(78)
                pill.setFixedHeight(24)
                pill.setStyleSheet(
                    f"background:{bg};color:{fg};border:0;border-radius:12px;"
                    "font-size:11px;font-weight:700;padding:0 8px;"
                )
                pill_layout.addWidget(pill)
                pill_layout.addStretch(1)
                table.setCellWidget(row, 3, pill_wrap)

            # Ações existentes no contexto da linha; evita botões duplicados.
            cell = QWidget(table)
            actions = QHBoxLayout(cell)
            actions.setContentsMargins(2, 2, 2, 2)
            actions.setSpacing(5)
            for glyph, action, tooltip in (
                ("▤", "Abrir conta", "Abrir o PDF da conta"),
                ("➤", "Enviar para pagamento", "Enviar esta conta para pagamento"),
                ("⋯", "menu", "Mais ações"),
            ):
                button = QToolButton(cell)
                button.setText(glyph)
                button.setToolTip(tooltip)
                button.setFixedSize(33, 29)
                if action == "menu":
                    menu = QMenu(button)
                    for label in ("Editar", "Abrir comprovante", "Excluir"):
                        menu_action = menu.addAction(label)
                        menu_action.triggered.connect(
                            lambda _=False, r=row, a=label: self._activate_row(r, a)
                        )
                    button.setMenu(menu)
                    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                else:
                    button.clicked.connect(
                        lambda _=False, r=row, a=action: self._activate_row(r, a)
                    )
                actions.addWidget(button)
            table.setCellWidget(row, 9, cell)
        self.refresh_paid_month()


def install_dashboard(window):
    """Instala a UI aprovada uma vez; atualizações usam o mesmo objeto."""
    current = getattr(window, "_desktop_dashboard", None)
    if current is None:
        return DesktopDashboard(window)
    return current
