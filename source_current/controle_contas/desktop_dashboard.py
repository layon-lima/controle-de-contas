"""Apresentação da janela desktop. Reaproveita os controles e métodos existentes.

Não implementa regras de contas, PDFs, pagamentos ou sincronização.
Não altera a estrutura do banco. Instala uma única vez sobre a JanelaPrincipal.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import json
import os

from PySide6.QtCore import Qt, QSize, QRect, QPoint, QTimer, QObject, QEvent
from PySide6 import QtSvg  # inclui plugin de SVG no build PyInstaller
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget, QMenu,
    QHeaderView, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication,
)


DASHBOARD_STYLE = """
/* Controle de Contas — visual desktop consistente, sem alterar a navegação. */
QMainWindow { background: #f3f6fa; }
QMainWindow > QWidget {
    font-family: 'Segoe UI'; font-size: 12px; color: #233248;
}
QFrame#headerPanel {
    background: #ffffff;
    border: 1px solid #e0e7ef;
    border-radius: 12px;
}
QLabel { color: #26374c; }
QPushButton {
    color: #354861;
    background: #ffffff;
    border: 1px solid #dce5ee;
    border-radius: 8px;
    min-height: 34px;
    padding: 0 13px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    background: #f3f7fc;
    color: #193c67;
    border-color: #bacfe7;
}
QPushButton:pressed { background: #e9f1f9; border-color: #86abd6; }
QPushButton:focus { border: 1px solid #6b9ed8; }
QPushButton:disabled {
    color: #a3adba; background: #f6f8fb; border-color: #e9edf2;
}
QPushButton#primaryButton {
    color: #ffffff; background: #12805c;
    border: 1px solid #12805c;
    font-weight: 700;
}
QPushButton#primaryButton:hover {
    color: #ffffff; background: #0d7151; border-color: #0d7151;
}
QPushButton#primaryButton:pressed { background: #095d43; }
QPushButton#bulkPrimary {
    background: #edf7f2; color: #167455;
    border-color: #cfe6d9;
}
QPushButton#dangerButton { color: #a63e47; border-color: #f0d5d8; }
QLineEdit, QComboBox {
    background: #ffffff;
    border: 1px solid #dce5ee;
    border-radius: 8px;
    min-height: 34px;
    padding: 0 11px;
    color: #25374e;
    selection-background-color: #dfeaf7;
}
QLineEdit:hover, QComboBox:hover { border-color: #bfd0e2; }
QLineEdit:focus, QComboBox:focus { border-color: #4f8bc8; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #dce5ee;
    selection-background-color: #edf3fa;
    selection-color: #243f5e;
    outline: none;
}
QFrame#bulkPaymentBar {
    background: #ffffff;
    border: 1px solid #e2e9f1;
    border-radius: 9px;
}
QFrame#bulkPaymentBar QLabel { color: #728195; }
QFrame#bottomNavigation {
    background: #ffffff;
    border: 1px solid #e2e9f1;
    border-radius: 12px;
}
QPushButton#bottomNavButton {
    background: transparent;
    color: #687a8d;
    border: none;
    border-radius: 8px;
    font-size: 12px;
    min-height: 44px;
    font-weight: 600;
    text-align: left;
    padding: 0 13px;
}
QPushButton#bottomNavButton:hover { background: #f2f6fb; color: #264c77; }
QPushButton#bottomNavButton[active="true"] {
    background: #eaf2fb;
    color: #235b96;
    font-weight: 700;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #ffffff;
    border: 1px solid #e0e7ef;
    border-radius: 10px;
    gridline-color: #e7edf4;
    selection-background-color: #ecf4fc;
    selection-color: #20364e;
    font-family: 'Segoe UI';
    font-size: 12px;
    outline: none;
}
QTableWidget::item {
    border-right: 1px solid #e5ebf2;
    border-bottom: 1px solid #edf1f5;
    color: #34445a;
    padding: 5px 9px;
}
QTableWidget::item:hover { background: #f7faff; }
QTableWidget::item:selected { background: #eaf2fc; color: #1a3d66; }
QHeaderView::section {
    background: #f8fafc;
    color: #66788e;
    border: none;
    border-right: 1px solid #c8d5e4;
    border-bottom: 1px solid #dbe4ee;
    padding: 8px 10px;
    font-size: 11px;
    font-weight: 700;
}
QHeaderView::section:hover {
    background: #edf4fc;
    color: #315c87;
    border-right: 2px solid #6395c9;
}
QHeaderView::section:pressed {
    background: #e2eef9;
    border-right: 2px solid #377ab9;
}
QTableWidget QToolButton {
    background: #ffffff;
    color: #52708f;
    border: 1px solid #e1e8f0;
    border-radius: 7px;
    padding: 0;
}
QTableWidget QToolButton:hover {
    background: #ecf4fd;
    border-color: #aac7e6;
}
QTableWidget QToolButton:pressed {
    background: #dcecfb;
    border-color: #75a5d5;
}
QTableWidget QToolButton:disabled {
    background: #f4f8fb;
    border-color: #d5e3f0;
}
QTableWidget QToolButton#sendPaymentIcon {
    background: #f3f8fe; border-color: #dce9f8;
}
QTableWidget QToolButton#sendPaymentIcon:hover {
    background: #e7f2ff; border-color: #9dbfe4;
}
QTableWidget QToolButton#registerPaymentIcon {
    background: #f1faf5; border-color: #d6eadf;
}
QTableWidget QToolButton#registerPaymentIcon:hover {
    background: #e3f6ec; border-color: #a3d5bc;
}
QMenu {
    background: #ffffff; color: #263b53;
    border: 1px solid #dce5ee;
    padding: 5px;
}
QMenu::item { padding: 8px 20px; border-radius: 5px; }
QMenu::item:selected { background: #eef4fb; color: #184f85; }
QToolTip {
    background: #1e2e43; color: #ffffff;
    border: 1px solid #1e2e43;
    padding: 5px 8px; font-size: 11px;
}
QScrollBar:vertical { background: #f7f9fb; width: 9px; }
QScrollBar::handle:vertical {
    background: #cad6e3; min-height: 28px; border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #a7bfd6; }
QScrollBar:horizontal { background: #f7f9fb; height: 9px; }
QScrollBar::handle:horizontal { background: #cad6e3; border-radius: 4px; }
QScrollBar::handle:horizontal:hover { background: #a7bfd6; }
"""


# Icones SVG autorais versionados em disco; nunca glyphs improvisados no runtime.
_ICON_DIR = Path('assets') / 'ui_icons'


def _asset_icon(name):
    roots = [Path(__file__).resolve().parents[1]]
    if getattr(sys, '_MEIPASS', None):
        roots.insert(0, Path(sys._MEIPASS))
    roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        path = root / _ICON_DIR / (name + '.svg')
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()



class DueDateDelegate(QStyledItemDelegate):
    """Desenha o vencimento UMA vez: preserva o texto original no item.

    O widget transparente anterior sobrepunha a propria data que a tabela
    ainda pintava por baixo. Uma unica delegate evita sobreposicao, mantém
    busca, selecao, ordenacao, tooltip e dados sem alteracoes.
    """

    @staticmethod
    def date_context(raw_text):
        try:
            due = datetime.strptime(str(raw_text or "").strip(), "%d/%m/%Y").date()
        except (TypeError, ValueError):
            return None
        delta = (due - datetime.now().date()).days
        if delta < -1:
            description = f"{-delta} dias atrás"
        elif delta == -1:
            description = "Ontem"
        elif delta == 0:
            description = "Hoje"
        elif delta == 1:
            description = "Amanhã"
        else:
            description = f"Em {delta} dias"
        tint = "#d43a42" if delta < 0 else (
            "#bf820e" if delta <= 1 else "#657d9d"
        )
        return description, tint

    def paint(self, painter, option, index):
        raw = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        context = self.date_context(raw)
        if context is None:
            super().paint(painter, option, index)
            return

        painter.save()
        try:
            # Deixe o proprio estilo Qt desenhar selecao e fundo, SEM texto.
            # Assim a data original nao aparece atras da nossa apresentacao.
            cell = QStyleOptionViewItem(option)
            self.initStyleOption(cell, index)
            cell.text = ""
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, cell,
                              painter, option.widget)

            rect = option.rect.adjusted(8, 3, -6, -3)
            if rect.width() <= 0 or rect.height() <= 0:
                return
            painter.setClipRect(option.rect)
            line = max(13, rect.height() // 2)

            main_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
            painter.setFont(main_font)
            painter.setPen(QColor("#263e60"))
            main = painter.fontMetrics().elidedText(raw, Qt.TextElideMode.ElideRight,
                                                   rect.width())
            painter.drawText(QRect(rect.x(), rect.y(), rect.width(), line),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             main)

            secondary, color = context
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            painter.setPen(QColor(color))
            caption = painter.fontMetrics().elidedText(
                secondary, Qt.TextElideMode.ElideRight, rect.width()
            )
            painter.drawText(QRect(rect.x(), rect.y() + line,
                                   rect.width(), max(0, rect.height() - line)),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             caption)
        finally:
            painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if self.date_context(index.data(Qt.ItemDataRole.DisplayRole)) is not None:
            size.setHeight(max(size.height(), 48))
        return size


class _ResponsiveKpiWatcher(QObject):
    """Mantém a grade de indicadores responsiva sem tocar nos dados do app."""

    def __init__(self, dashboard):
        super().__init__(dashboard.window)
        self.dashboard = dashboard

    def eventFilter(self, watched, event):
        if (watched is self.dashboard.window.centralWidget()
                and event.type() == QEvent.Type.Resize):
            # Adiado para não modificar layout dentro da pilha de resize do Qt.
            QTimer.singleShot(0, self.dashboard._layout_kpis)
        return False


class DesktopDashboard:
    """Somente composição visual; nunca substitui fluxo de pagamento ou PDF."""

    def __init__(self, window):
        self.window = window
        self._buttons = {}
        self._paid_month_card = None
        self._paid_month_value = None
        self._paid_month_count = None
        self._install()
        from .desktop_features import install_desktop_features
        install_desktop_features(self)

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
        self._relocate_kpis_to_header()
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
            search.setMinimumWidth(140)
            search.setMaximumWidth(650)
            search.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )

        brand = QWidget(header)
        brand_lay = QHBoxLayout(brand)
        brand_lay.setContentsMargins(0, 0, 0, 0)
        brand_lay.setSpacing(10)

        square = QLabel(brand)
        square.setObjectName("dashboardBrandIcon")
        square.setFixedSize(44, 44)
        square.setAlignment(Qt.AlignmentFlag.AlignCenter)
        square.setPixmap(_asset_icon("brand").pixmap(QSize(44, 44)))
        brand_lay.addWidget(square)

        brand_text = QWidget(brand)
        text_layout = QVBoxLayout(brand_text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        title = QLabel("Controle de Contas", brand_text)
        title.setStyleSheet("color:#18283d;font-size:18px;font-weight:700;")
        subtitle = QLabel("Gestão de contas a pagar e pagamentos", brand_text)
        subtitle.setStyleSheet("color:#8191a4;font-size:10px;font-weight:400;")
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)
        brand_lay.addWidget(brand_text)
        lay.insertWidget(0, brand)
        if search is not None:
            search.setPlaceholderText(
                "Pesquisar fornecedor, valor, descrição ou vencimento..."
            )

        importer = QPushButton("Importar PDF", header)
        importer.setIcon(_asset_icon("upload"))
        importer.setIconSize(QSize(18, 18))
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
            new_button.setText("Nova conta")
            new_button.setIcon(_asset_icon("plus"))
            new_button.setIconSize(QSize(17, 17))
            new_button.setMinimumWidth(130)
            new_button.setMinimumHeight(39)
        mobile = self._find_action("Acesso mobile")
        if mobile:
            mobile.setText("Acesso mobile")
            mobile.setToolTip("Acesso mobile")
            mobile.setIcon(_asset_icon("smartphone"))
            mobile.setIconSize(QSize(18, 18))
            self._buttons["mobile"] = mobile

        # A pasta já possui seu comando real em Configurações; não ocupa
        # também espaço duplicado no cabeçalho compacto.
        folder = getattr(w, "_patch_btn_pasta", None)
        if folder:
            folder.hide()

        drag = getattr(w, "area_drop", None)
        if drag:
            drag.setMaximumHeight(32)
            drag.setMinimumHeight(30)
            drag.setStyleSheet(
                "background:#f8fafc;border:1px dashed #d6e2ee;"
                "border-radius:7px;color:#8795a6;font-size:10px;"
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
            ("#ffffff", "#fbebed", "#bf3945", "overdue"),
            ("#ffffff", "#fff3df", "#a76b17", "upcoming"),
            ("#ffffff", "#e8f5ee", "#137453", "ontime"),
            ("#ffffff", "#eaf2fc", "#326ca8", "paid"),
        ]
        for index, (_key, card) in enumerate(cards[:4]):
            if card is None:
                continue
            bg, icon_bg, accent, icon_name = colors[index]
            card.setMinimumHeight(57)
            card.setMaximumHeight(64)
            card.setObjectName(f"dashboardKpi{index}")
            card.setStyleSheet(
                f"QFrame#dashboardKpi{index} {{"
                f"background:{bg};border:1px solid #e0e7ef;"
                f"border-left:3px solid {accent};border-radius:10px;"
                "}"
                "QFrame QLabel{background:transparent;border:0;}"
            )
            existing_layout = card.layout()
            if existing_layout:
                existing_layout.setContentsMargins(52, 4, 9, 4)
                existing_layout.setSpacing(1)
            for label in card.findChildren(QLabel):
                text = str(label.text() or "").strip()
                # Não herdar fontes enormes do layout legado: com quatro
                # indicadores, o valor extrapolava a área disponível.
                is_count = text.isdigit()
                is_amount = "R$" in text
                if text in ("●", "•", "·"):
                    label.hide()
                    continue
                size = 16 if is_count else 10 if is_amount else 9
                label.setFont(QFont(
                    "Segoe UI", size,
                    QFont.Weight.Bold if is_count else QFont.Weight.DemiBold
                    if is_amount else QFont.Weight.Medium
                ))
                label.setMinimumWidth(0)
                label.setWordWrap(False)
                label.setStyleSheet(
                    f"background:transparent;border:0;"
                    f"color:{accent if is_count else '#52657c' if is_amount else '#7a8a9c'};"
                )
            icon = QLabel(card)
            icon.setObjectName("dashboardKpiIcon")
            icon.setGeometry(8, 10, 38, 38)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setPixmap(_asset_icon(icon_name).pixmap(QSize(21, 21)))
            icon.setStyleSheet(f"background:{icon_bg};border:0;border-radius:10px;")
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

    def _relocate_kpis_to_header(self):
        """Indicadores abaixo do cabeçalho, em grade fluida 4/2/1 colunas.

        O cabeçalho horizontal anterior obrigava quatro cards, marca e ações
        a ocupar uma única linha, impossível em janelas pequenas.
        """
        central = self.window.centralWidget()
        outer = central.layout() if central is not None else None
        if outer is None:
            raise RuntimeError("Layout central não encontrado.")
        strip = QWidget(central)
        strip.setObjectName("dashboardKpiStrip")
        strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(strip)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(6)
        self._kpi_cards = []
        for card in list(getattr(self.window, "cards", {}).values())[:4]:
            if card is None:
                continue
            previous = card.parentWidget()
            if previous is not None and previous.layout() is not None:
                previous.layout().removeWidget(card)
            card.setParent(strip)
            card.setMinimumSize(0, 57)
            card.setMaximumHeight(62)
            card.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            body = card.layout()
            if body is not None:
                body.setContentsMargins(49, 3, 7, 3)
                body.setSpacing(1)
            icon = card.findChild(QLabel, "dashboardKpiIcon")
            if icon is not None:
                icon.setGeometry(6, 10, 36, 36)
            self._kpi_cards.append(card)
            card.show()
        self._kpi_strip = strip
        self._kpi_grid = grid
        self._kpi_columns = None
        header = self.window.findChild(QFrame, "headerPanel")
        index = outer.indexOf(header) if header is not None else -1
        outer.insertWidget(index + 1 if index >= 0 else 0, strip)
        self._buttons["kpis"] = strip
        self._kpi_watcher = _ResponsiveKpiWatcher(self)
        central.installEventFilter(self._kpi_watcher)
        self._layout_kpis()

    def _layout_kpis(self):
        strip = getattr(self, "_kpi_strip", None)
        if strip is None:
            return
        width = max(0, self.window.centralWidget().width() - 32)
        count = 4 if width >= 1130 else 2 if width >= 460 else 1
        self._adapt_header_to_width(width)
        if count == self._kpi_columns:
            return
        self._kpi_columns = count
        grid = self._kpi_grid
        for card in self._kpi_cards:
            grid.removeWidget(card)
        for index, card in enumerate(self._kpi_cards):
            grid.addWidget(card, index // count, index % count)
        # Uma coluna deve sempre ocupar o espaço livre, sem distorcer os labels.
        for column in range(4):
            grid.setColumnStretch(column, 1 if column < count else 0)
            grid.setColumnMinimumWidth(column, 0)
        rows = (len(self._kpi_cards) + count - 1) // count
        strip.setFixedHeight(rows * 62 + max(0, rows - 1) * 6)
        strip.updateGeometry()

    def _adapt_header_to_width(self, width):
        """Mantém operações acessíveis quando a janela fica estreita."""
        compact = width < 890
        for key, title in (("import", "Importar PDF"), ("mobile", "Acesso mobile")):
            button = self._buttons.get(key)
            if button is None:
                continue
            button.setText("" if compact else title)
            button.setToolTip(title)
            button.setMinimumWidth(38 if compact else 80)
            button.setMaximumWidth(42 if compact else 16777215)
        features = getattr(self, "_desktop_features", None)
        if features is None:
            return
        filt = getattr(self.window, "filtro", None)
        tags = getattr(features, "tag_filter", None)
        search = getattr(self.window, "busca", None)
        narrow = width < 760
        if filt is not None:
            filt.setFixedWidth(105 if narrow else 135)
        if tags is not None:
            tags.setFixedWidth(120 if narrow else 170)
        if search is not None:
            search.setMinimumWidth(95 if narrow else 260)
        title = getattr(features, "_filter_title", None)
        if title is not None:
            title.setVisible(not narrow)

    def refresh_paid_month(self):
        if self._paid_month_card is None:
            return
        try:
            month = datetime.now().strftime("%Y-%m")
            with self.window.banco.conexao() as conn:
                row = conn.execute(
                    "WITH latest AS "
                    "(SELECT conta_id, MAX(id) AS payment_id "
                    " FROM pagamentos GROUP BY conta_id) "
                    "SELECT COUNT(*) AS quantidade, "
                    "COALESCE(SUM(COALESCE(p.valor_pago, c.valor, 0)), 0) AS total "
                    "FROM latest l "
                    "JOIN pagamentos p ON p.id=l.payment_id "
                    "JOIN contas c ON c.id=l.conta_id "
                    "WHERE SUBSTR(p.data_pagamento, 1, 7) = ?",
                    (month,),
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
        nav.setFixedSize(294, 57)
        row = QHBoxLayout(nav)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(5)
        for key, caption, icon_name in (
            ("accounts", "Em aberto", "accounts"),
            ("payments", "Pagas", "payments"),
        ):
            button = QPushButton(caption, nav)
            button.setIcon(_asset_icon(icon_name))
            button.setIconSize(QSize(18, 18))
            button.setObjectName("bottomNavButton")
            button.setProperty("dashboardAction", True)
            button.setFixedSize(135, 46)
            button.clicked.connect(lambda _=False, mode=key: self._navigate(mode))
            row.addWidget(button)
            self._buttons[key] = button
        layout.addWidget(nav, 0, Qt.AlignmentFlag.AlignHCenter)
        self._buttons["bottom"] = nav
        self._navigate("accounts")

    def _navigate(self, mode):
        target = 1 if mode == "payments" else 0
        self.window.abas.setCurrentIndex(target)
        for card in getattr(self.window, "cards", {}).values():
            card.show()
        for key in ("accounts", "payments"):
            button = self._buttons.get(key)
            if button:
                button.setProperty("active", key == ("payments" if target else "accounts"))
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
        """Mantem indices logicos das rotinas de pagamento, muda so a visualizacao."""
        table = self.window.tabela
        if table.columnCount() < 10:
            table.setColumnCount(10)
        table.setHorizontalHeaderItem(8, QTableWidgetItem("Observações"))
        table.setHorizontalHeaderItem(9, QTableWidgetItem("Ações"))
        table.setColumnHidden(6, True)  # PDF interno
        table.setColumnHidden(7, True)  # ID interno
        table.setColumnHidden(8, False) # Observacoes obrigatorias
        table.setIconSize(QSize(28, 28))
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(50)
        # A coluna tem UMA fonte de renderizacao; sem QWidget transparente
        # por cima do texto original do QTableWidgetItem.
        table.setItemDelegateForColumn(1, DueDateDelegate(table))
        table.setShowGrid(True)
        table.setAlternatingRowColors(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.setWordWrap(False)
        header = table.horizontalHeader()
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for logical in (0, 8, 1, 2, 3, 5, 4, 9):
            title = table.horizontalHeaderItem(logical)
            if title is not None:
                title.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
        for position, logical in enumerate((0, 8, 1, 2, 3, 5, 4, 9)):
            current = header.visualIndex(logical)
            if current >= 0 and current != position:
                header.moveSection(current, position)
        # Redimensionamento interativo com mínimo próprio por coluna.
        # Ações precisa mostrar os 6 ícones de 33px e seus intervalos.
        self._minimum_column_widths = {
            0: 150, 8: 125, 1: 135, 2: 105,
            3: 100, 5: 110, 4: 110, 9: 228,
        }
        widths = {0: 230, 8: 205, 1: 160, 2: 115,
                  3: 100, 5: 110, 4: 120, 9: 252}
        self._columns_file = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
            / "ControleContas" / "desktop_columns.json"
        )
        try:
            stored = json.loads(self._columns_file.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for key, value in stored.items():
                    column = int(key)
                    if (column in widths and isinstance(value, int)
                            and not isinstance(value, bool) and 0 < value <= 1600):
                        widths[column] = max(
                            self._minimum_column_widths[column], value
                        )
        except (OSError, ValueError, TypeError):
            pass
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(min(self._minimum_column_widths.values()))
        self._adjusting_column_width = True
        for column, width in widths.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(
                column, max(self._minimum_column_widths[column], width)
            )
        self._adjusting_column_width = False
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._save_widths_timer = QTimer(self.window)
        self._save_widths_timer.setSingleShot(True)
        self._save_widths_timer.setInterval(350)
        self._save_widths_timer.timeout.connect(self._save_column_widths)
        header.sectionResized.connect(self._on_column_resized)
        table.setStyleSheet(DASHBOARD_STYLE[DASHBOARD_STYLE.index("QTableWidget {"):])
        self.decorate_rows()

    def _on_column_resized(self, logical, _old_size, new_size):
        """Impede colunas invisíveis, mesmo com tamanho salvo antigo."""
        if self._adjusting_column_width:
            return
        minimum = self._minimum_column_widths.get(logical)
        if minimum is not None and new_size < minimum:
            self._adjusting_column_width = True
            try:
                self.window.tabela.setColumnWidth(logical, minimum)
            finally:
                self._adjusting_column_width = False
        self._save_widths_timer.start()

    def _save_column_widths(self):
        try:
            widths = {
                str(column): max(
                    self._minimum_column_widths[column],
                    self.window.tabela.columnWidth(column),
                )
                for column in (0, 8, 1, 2, 3, 5, 4, 9)
            }
            path = self._columns_file
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(widths), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass

    def _compact_filter_region(self, bulk):
        """Nao deixar a regiao de filtros consumir a altura da listagem."""
        parent = self.window.tabela.parentWidget()
        listing = parent.layout() if parent else None
        if listing is None:
            return
        listing.setContentsMargins(10, 6, 10, 8)
        listing.setSpacing(4)
        bar_index = listing.indexOf(bulk)
        if bar_index >= 0:
            for index in range(bar_index):
                item = listing.itemAt(index)
                if item is None:
                    continue
                widget = item.widget()
                nested = item.layout()
                if widget is not None and widget is not self.window.tabela:
                    widget.setMinimumHeight(0)
                    widget.setMaximumHeight(45)
                    widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(),
                                         QSizePolicy.Policy.Fixed)
                    nested = widget.layout()
                if nested is not None:
                    nested.setContentsMargins(4, 2, 4, 2)
                    nested.setSpacing(4)
        if hasattr(listing, "setStretch"):
            for index in range(listing.count()):
                listing.setStretch(index, 0)
            table_index = listing.indexOf(self.window.tabela)
            if table_index >= 0:
                listing.setStretch(table_index, 1)

    def _rearrange_actions(self):
        w = self.window
        bulk = getattr(w, "_bulk_bar", None)
        if bulk is None or bulk.layout() is None:
            return

        layout = bulk.layout()
        # Descarta a paleta legada aplicada diretamente à barra:
        # senão ela tem prioridade sobre o tema moderno do QMainWindow.
        bulk.setStyleSheet("")
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(5)
        bulk.setFixedHeight(42)
        self._compact_filter_region(bulk)

        payment = self._find_action("Registrar pagamento")
        if payment is not None:
            payment.hide()

        clear = getattr(w, "_bulk_clear_button", None)
        if clear:
            clear.setText("Limpar seleção")
            clear.setIcon(_asset_icon("trash"))
            clear.setIconSize(QSize(16, 16))
        select_all = getattr(w, "_bulk_select_all_button", None)
        if select_all:
            select_all.setIcon(_asset_icon("ontime"))
            select_all.setIconSize(QSize(16, 16))
        suggest = getattr(w, "_bulk_suggest_button", None)
        if suggest:
            suggest.setText("Sugerir pagamento")
            suggest.setIcon(_asset_icon("send"))
            suggest.setIconSize(QSize(16, 16))
        sent = getattr(w, "_bulk_sent_button", None)
        if sent:
            sent.setText("Marcar como enviado")
            sent.setIcon(_asset_icon("paid"))
            sent.setIconSize(QSize(16, 16))

        # Mantém os outros handlers originais no app; botões no rodapé serão
        # acessados pelas ações por linha, sem criar novas regras de negócio.
        pool = QWidget(w)
        pool.hide()
        for text in ("Editar", "Enviar para pagamento", "Abrir conta",
                     "Abrir comprovante", "Excluir"):
            button = self._find_action(text)
            if button:
                if text == "Enviar para pagamento":
                    # Manter referencia ao botão ORIGINAL. Seu clicked carrega
                    # o fluxo de preparação/copia dos dados de pagamento.
                    self._buttons["original_send"] = button
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

    def _show_action_feedback(self, message, result="success"):
        """Aviso discreto, sem modal e sem cobrir a área de trabalho."""
        root = self.window.centralWidget()
        if root is None:
            return
        notice = getattr(self, "_action_notice", None)
        if notice is None:
            notice = QLabel(root)
            notice.setObjectName("dashboardActionFeedback")
            notice.setAlignment(Qt.AlignmentFlag.AlignVCenter |
                                Qt.AlignmentFlag.AlignLeft)
            notice.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._action_notice = notice
            timer = QTimer(root)
            timer.setSingleShot(True)
            timer.timeout.connect(notice.hide)
            self._action_notice_timer = timer
        bg, fg, border, symbol = {
            "success": ("#eef7f0", "#236044", "#c7dece", "✓"),
            "waiting": ("#eff5fb", "#315e8a", "#d0ddea", "·"),
            "error": ("#fff3f3", "#9e3440", "#edd0d2", "!"),
        }.get(result, ("#eff5fb", "#315e8a", "#d0ddea", "·"))
        width = max(125, min(284, root.width() - 20))
        message = str(message or "")
        notice.setToolTip(message)
        notice.setText(
            symbol + "  " + notice.fontMetrics().elidedText(
                message, Qt.TextElideMode.ElideRight, max(50, width - 35)
            )
        )
        notice.setStyleSheet(
            f"QLabel#dashboardActionFeedback{{background:{bg};color:{fg};"
            f"border:1px solid {border};border-radius:6px;"
            "font-family:'Segoe UI';font-size:10px;"
            "font-weight:600;padding:2px 8px;}"
        )
        # Sempre pequeno e junto ao rodapé, sem sobrepor cards nem filtros.
        notice.setGeometry(max(4, root.width() - width - 12),
                           max(0, root.height() - 96), width, 28)
        notice.show()
        notice.raise_()
        self._action_notice_timer.start(2100)

    def _dispatch_row_action(self, button, row, action):
        """Feedback imediato no ícone e resultado após chamar a ação real."""
        try:
            button.setEnabled(False)
            button.setStyleSheet(
                "QToolButton{background:#e6f2ff;border:1px solid #79ace8;"
                "border-radius:7px;}"
            )
            button.setToolTip("Executando: " + action)
        except RuntimeError:
            return
        self._show_action_feedback(action + "…", "waiting")

        def execute():
            try:
                result = self._activate_row(row, action)
            except Exception as exc:
                failure = str(exc)
                result = False
            else:
                failure = ""
            if result is False:
                color = "#fee4e7"
                msg = failure or (action + " não concluído.")
                state = "error"
            elif result is None:
                color = "#e6f2ff"
                msg = {
                    "Registrar pagamento": "Registro de pagamento aberto. Confirme na janela.",
                    "TAGs": "Gerenciador de TAGs acionado.",
                    "Editar": "Edição da conta acionada.",
                    "Excluir": "Exclusão: aguardando sua decisão.",
                    "Enviar para pagamento": "Aguardando cópia dos dados para pagamento.",
                }.get(action, action + " acionado.")
                state = "waiting"
            else:
                color = "#e0f7e8"
                msg = {
                    "Enviar para pagamento": "Dados copiados e envio registrado.",
                    "Sugerir para pagamento": "Conta sugerida para pagamento.",
                    "Registrar pagamento": "Pagamento registrado.",
                    "TAGs": "TAGs salvas na conta.",
                    "Abrir conta": "PDF da conta aberto.",
                    "Abrir comprovante": "Comprovante aberto.",
                }.get(action, action + " concluído.")
                state = "success"
            self._show_action_feedback(msg, state)
            try:
                button.setEnabled(True)
                button.setStyleSheet(
                    f"QToolButton{{background:{color};border:1px solid "
                    "#b6cce0;border-radius:7px;}"
                )
                button.setToolTip(msg)
                QTimer.singleShot(2300, lambda: self._reset_action_icon(button))
            except RuntimeError:
                pass

        # O Qt repinta o estado de processamento antes de executar a rotina.
        QTimer.singleShot(0, execute)

    @staticmethod
    def _reset_action_icon(button):
        try:
            button.setStyleSheet("")
        except RuntimeError:
            pass

    def _initials_icon(self, name):
        words = [part for part in str(name or "").split() if part]
        letters = (
            (words[0][:1] + words[1][:1]) if len(words) >= 2
            else str(name or "?")[:2]
        ).upper()
        palette = ["#5c7ca6", "#4b8d88", "#7174a1", "#63857b", "#8b7595"]
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
                supplier.setForeground(QColor("#25364c"))
                supplier.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            for column in (1, 2, 4, 5):
                item = table.item(row, column)
                if item:
                    item.setFont(QFont("Segoe UI", 9))
                    item.setForeground(QColor("#45576d"))
                    if column == 2:
                        item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            date_item = table.item(row, 1)
            if date_item:
                # Preserva o QTableWidgetItem como fonte unica do vencimento.
                # Tooltip complementa, sem instalar widgets que repintam a data.
                date_text = date_item.text().strip()
                context = DueDateDelegate.date_context(date_text)
                date_item.setToolTip(
                    f"{date_text} — {context[0]}" if context else date_text
                )

            status = table.item(row, 3)
            if status:
                status.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                colors = {
                    "Vencida": ("#fcecef", "#ac3843"),
                    "Próxima": ("#fff3dc", "#98651b"),
                    "Em dia": ("#e9f6ee", "#267251"),
                    "Paga": ("#e9f6ee", "#267251"),
                }
                bg, fg = colors.get(status.text(), ("#eef2f7", "#536780"))
                status.setBackground(QColor(bg))
                status.setForeground(QColor(fg))

                # O item original mantém classificação/ordenação; o label
                # é apenas a apresentação arredondada vista no mockup.
                pill_wrap = QWidget(table)
                pill_wrap.setObjectName("dashboardStatusCell")
                pill_wrap.setStyleSheet(
                    "QWidget#dashboardStatusCell{"
                    "border-right:1px solid #dbe5f0;background:transparent;}"
                )
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
                    "font-size:10px;font-weight:700;padding:0 8px;"
                )
                pill_layout.addWidget(pill)
                pill_layout.addStretch(1)
                table.setCellWidget(row, 3, pill_wrap)

            # Texto completo da observacao permanece no item e no tooltip.
            observation = table.item(row, 8)
            if observation is not None:
                observation.setForeground(QColor("#65768a"))
                observation.setFont(QFont("Segoe UI", 9))
                observation.setToolTip(observation.text())

            # Ações existentes no contexto da linha; evita botões duplicados.
            cell = QWidget(table)
            cell.setObjectName("dashboardRowActions")
            cell.setStyleSheet(
                "QWidget#dashboardRowActions{"
                "border-right:1px solid #dbe5f0;background:transparent;}"
            )
            actions = QHBoxLayout(cell)
            actions.setContentsMargins(2, 2, 2, 2)
            actions.setSpacing(3)
            for icon_name, action, tooltip in (
                ("document", "Abrir conta", "Abrir PDF da conta"),
                ("suggest", "Sugerir para pagamento", "Sugerir no mobile"),
                ("sent", "Enviar para pagamento", "Copiar dados e registrar envio, com ou sem PDF"),
                ("paid", "Registrar pagamento", "Registrar pagamento desta conta"),
                ("tag", "TAGs", "Gerenciar TAGs desta conta"),
                ("more", "menu", "Outras ações"),
            ):
                button = QToolButton(cell)
                button.setIcon(_asset_icon(icon_name))
                button.setIconSize(QSize(17, 17))
                button.setToolTip(tooltip)
                button.setFixedSize(33, 29)
                if action == "Enviar para pagamento":
                    button.setObjectName("sendPaymentIcon")
                elif action == "Registrar pagamento":
                    button.setObjectName("registerPaymentIcon")
                if action == "menu":
                    menu = QMenu(button)
                    for label, asset in (
                        ("Editar", "edit"),
                        ("Abrir comprovante", "receipt"),
                        ("Excluir", "trash"),
                    ):
                        menu_action = menu.addAction(_asset_icon(asset), label)
                        menu_action.triggered.connect(
                            lambda _=False, r=row, a=label: self._activate_row(r, a)
                        )
                    button.setMenu(menu)
                    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                else:
                    button.clicked.connect(
                        lambda _=False, b=button, r=row, a=action:
                            self._dispatch_row_action(b, r, a)
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
