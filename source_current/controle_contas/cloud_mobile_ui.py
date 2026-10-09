from __future__ import annotations

import os
from datetime import datetime

_ui_integrated = False


def cleanup_legacy_mobile_autostart() -> None:
    """Remove apenas o autostart antigo do servidor/túnel local, não dados do usuário."""
    if os.name != "nt":
        return

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, "ControleContasMobile")
            except FileNotFoundError:
                pass
    except Exception:
        pass


def _activity_map() -> dict[int, dict]:
    from .cloud_sync import load_cloud_activity

    payload = load_cloud_activity()
    result: dict[int, dict] = {}
    for item in payload.get("activities") or []:
        try:
            result[int(item.get("account_id"))] = dict(item)
        except Exception:
            continue
    return result


def _is_future(value) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.timestamp() > datetime.now().astimezone().timestamp()
    except Exception:
        return False


def _format_activity_time(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return text


def _money_br(value) -> str:
    try:
        text = f"{float(value or 0):,.2f}"
        return "R$ " + text.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def integrate_cloud_mobile_button() -> None:
    global _ui_integrated
    if _ui_integrated:
        return

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QDockWidget,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    from . import ui
    from .cloud_sync import (
        cloud_enabled,
        get_cloud_private_link,
        pull_cloud_activity_once,
        suggest_payments,
    )

    original_init = ui.JanelaPrincipal.__init__
    original_update_table = ui.JanelaPrincipal.atualizar_tabela

    visible_columns = (0, 1, 2, 3, 4, 5)

    def row_account_id(self, row: int) -> int | None:
        try:
            item = self.tabela.item(row, 7)
            return int(item.text()) if item is not None else None
        except Exception:
            return None

    def checked_ids(self) -> list[int]:
        selected = set(getattr(self, "_bulk_checked_ids", set()))
        return sorted(int(x) for x in selected)

    def update_bulk_state(self) -> None:
        ids = checked_ids(self)
        count = len(ids)
        label = getattr(self, "_bulk_count_label", None)
        bar = getattr(self, "_bulk_bar", None)
        suggest = getattr(self, "_bulk_suggest_button", None)
        sent = getattr(self, "_bulk_sent_button", None)
        clear = getattr(self, "_bulk_clear_button", None)

        if label is not None:
            label.setText(f"{count} selecionada" + ("" if count == 1 else "s"))
        if suggest is not None:
            suggest.setText(f"Sugerir pagamento ({count})")
            suggest.setEnabled(count > 0)
        if sent is not None:
            sent.setText(f"Marcar como enviado ({count})")
            sent.setEnabled(count > 0)
        if clear is not None:
            clear.setEnabled(count > 0)
        if bar is not None:
            bar.setVisible(True)

    def refresh_detail_panel(self, row: int | None = None) -> None:
        dock = getattr(self, "_cloud_detail_dock", None)
        if dock is None or not dock.isVisible():
            return

        if row is None:
            row = self.tabela.currentRow()
        account_id = row_account_id(self, int(row))
        if not account_id:
            return

        try:
            account = self.banco.buscar_conta(int(account_id)) or {}
        except Exception:
            account = {}

        activity = _activity_map().get(account_id) or {}

        receiver = str(account.get("fornecedor") or "")
        value = _money_br(account.get("valor"))
        due = ui.data_br(account.get("vencimento"))
        status_item = self.tabela.item(int(row), 3)
        status = status_item.text() if status_item is not None else ""

        suggested = _format_activity_time(activity.get("suggested_at")) or "—"
        sent = (
            ui.data_hora_br(account.get("enviado_pagamento_em"))
            if account.get("enviado_pagamento_em")
            else "—"
        )
        copied = _format_activity_time(activity.get("copied_at")) or "—"

        self._detail_receiver.setText(receiver or "Conta")
        self._detail_value.setText(value)
        self._detail_due.setText(f"Vencimento  {due or '—'}")
        self._detail_status.setText(status or "—")
        self._detail_suggested.setText(f"Sugerido para pagamento\n{suggested}")
        self._detail_sent.setText(f"Enviado para pagamento\n{sent}")
        self._detail_copied.setText(f"Última cópia do código\n{copied}")

        if _is_future(activity.get("copied_until")):
            self._detail_copied.setStyleSheet(
                "padding:10px 12px;border-radius:10px;"
                "background:#f4edff;color:#6d37b8;font-weight:700;"
            )
        else:
            self._detail_copied.setStyleSheet(
                "padding:10px 12px;border-radius:10px;"
                "background:#f6f8f7;color:#52655c;font-weight:600;"
            )

    def configure_table(self) -> None:
        table = self.tabela
        if table.columnCount() < 9:
            return

        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(False)
        # A grade visível é gerida pelo dashboard e deve sobreviver
        # aos refreshes periódicos de atividade/sincronização.
        table.setShowGrid(bool(getattr(self, "_desktop_dashboard", None)))
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(50)

        headers = {
            0: "Fornecedor",
            1: "Vencimento",
            2: "Valor",
            3: "Status",
            4: "Enviado em",
            5: "Sugerido em",
            8: "Observações",
        }
        for logical, title in headers.items():
            item = table.horizontalHeaderItem(logical)
            if item is None:
                item = ui.QTableWidgetItem(title)
                table.setHorizontalHeaderItem(logical, item)
            else:
                item.setText(title)

        header = table.horizontalHeader()
        order = (
            [0, 8, 1, 2, 3, 5, 4, 9]
            if getattr(self, "_desktop_dashboard", None)
            else [0, 8, 1, 2, 5, 4, 3]
        )
        for target, logical in enumerate(order):
            current = header.visualIndex(logical)
            if current >= 0 and current != target:
                header.moveSection(current, target)

        table.setColumnHidden(6, True)
        table.setColumnHidden(7, True)
        table.setColumnHidden(8, False)

        # Dashboard dimensiona o Vencimento incluindo a segunda linha.
        if getattr(self, "_desktop_dashboard", None) is None:
            header.setSectionResizeMode(0, ui.QHeaderView.ResizeMode.Stretch)
            for logical in (1, 2, 3, 4, 5):
                header.setSectionResizeMode(logical, ui.QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(8, ui.QHeaderView.ResizeMode.Stretch)

        if getattr(self, "_desktop_dashboard", None) is None:
            table.setStyleSheet(
                """
                QTableWidget {
                    background:#ffffff;
                    alternate-background-color:#ffffff;
                    border:1px solid #e4ebe7;
                    border-radius:12px;
                    gridline-color:transparent;
                    selection-background-color:#e9f7f0;
                    selection-color:#173127;
                    font-size:12px;
                }
                QTableWidget::item {
                    border-bottom:1px solid #eef2f0;
                    padding:7px 8px;
                }
                QTableWidget::item:selected {
                    background:#e9f7f0;
                    color:#173127;
                }
                QHeaderView::section {
                    background:#f7f9f8;
                    color:#64756d;
                    border:0;
                    border-bottom:1px solid #e4ebe7;
                    padding:9px 8px;
                    font-size:11px;
                    font-weight:700;
                }
                """
            )
    
    def apply_activity_markers(self) -> None:
        try:
            table = self.tabela
            configure_table(self)
            activity = _activity_map()
            checked = set(getattr(self, "_bulk_checked_ids", set()))
            visible_ids: set[int] = set()

            table.blockSignals(True)
            try:
                for row in range(table.rowCount()):
                    account_id = row_account_id(self, row)
                    if not account_id:
                        continue
                    visible_ids.add(account_id)

                    supplier = table.item(row, 0)
                    if supplier is not None:
                        # A versão desktop usa ações individuais por conta;
                        # não exibe mais caixas de seleção sem função.
                        if getattr(self, "_desktop_dashboard", None) is not None:
                            supplier.setFlags(
                                supplier.flags() & ~Qt.ItemFlag.ItemIsUserCheckable
                            )
                        else:
                            supplier.setFlags(
                                supplier.flags() | Qt.ItemFlag.ItemIsUserCheckable
                            )
                            supplier.setCheckState(
                                Qt.CheckState.Checked
                                if account_id in checked
                                else Qt.CheckState.Unchecked
                            )
                        supplier.setFont(QFont("Arial", 10, QFont.Weight.DemiBold))

                    info = activity.get(account_id) or {}
                    suggested_item = table.item(row, 5)
                    if suggested_item is None:
                        suggested_item = ui.QTableWidgetItem("")
                        table.setItem(row, 5, suggested_item)

                    suggested_at = _format_activity_time(info.get("suggested_at"))
                    suggested_item.setText(suggested_at or "—")
                    suggested_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                        if getattr(self, "_desktop_dashboard", None)
                        else Qt.AlignmentFlag.AlignCenter
                    )
                    suggested_item.setToolTip(
                        f"Sugerido para pagamento em {suggested_at}"
                        if suggested_at
                        else "Ainda não sugerido para pagamento."
                    )

                    sent_item = table.item(row, 4)
                    if sent_item is not None and sent_item.text().strip().casefold() == "não enviada":
                        sent_item.setText("—")

                    status_item = table.item(row, 3)
                    status = status_item.text() if status_item is not None else ""
                    if status_item is not None:
                        palette = {
                            "Vencida": ("#fff0f0", "#b33939"),
                            "Próxima": ("#fff7e5", "#9a6700"),
                            "Em dia": ("#eef6ff", "#2f6792"),
                            "Paga": ("#eaf8ef", "#28744d"),
                        }
                        bg, fg = palette.get(status, ("#f4f6f5", "#53655d"))
                        status_item.setBackground(QColor(bg))
                        status_item.setForeground(QColor(fg))
                        status_item.setFont(QFont("Arial", 9, QFont.Weight.Bold))

                    # A cópia continua registrada por 24 h para o desktop,
                    # mas não ocupa uma coluna extra: aparece no detalhe/tooltip.
                    if supplier is not None:
                        copied_at = _format_activity_time(info.get("copied_at"))
                        if copied_at:
                            supplier.setToolTip(
                                "Última cópia do código no mobile: " + copied_at
                            )
                        else:
                            supplier.setToolTip("")
            finally:
                table.blockSignals(False)

            checked.intersection_update(visible_ids)
            self._bulk_checked_ids = checked
            update_bulk_state(self)
            refresh_detail_panel(self)
        except Exception:
            pass

    def update_table_with_activity(self, *args, **kwargs):
        result = original_update_table(self, *args, **kwargs)
        apply_activity_markers(self)
        dashboard = getattr(self, "_desktop_dashboard", None)
        if dashboard is not None:
            dashboard.decorate_rows()
        return result

    def on_item_changed(self, item) -> None:
        try:
            if item.column() != 0:
                return
            account_id = row_account_id(self, item.row())
            if not account_id:
                return
            selected = set(getattr(self, "_bulk_checked_ids", set()))
            if item.checkState() == Qt.CheckState.Checked:
                selected.add(account_id)
            else:
                selected.discard(account_id)
            self._bulk_checked_ids = selected
            update_bulk_state(self)
        except Exception:
            pass

    def select_all_visible(self) -> None:
        selected: set[int] = set()
        for row in range(self.tabela.rowCount()):
            account_id = row_account_id(self, row)
            status_item = self.tabela.item(row, 3)
            status = status_item.text().strip() if status_item is not None else ""
            if account_id and status != "Paga":
                selected.add(account_id)
        self._bulk_checked_ids = selected
        apply_activity_markers(self)

    def clear_checked(self) -> None:
        self._bulk_checked_ids = set()
        apply_activity_markers(self)

    def suggest_checked(self) -> None:
        ids = checked_ids(self)
        if not ids:
            return
        if not cloud_enabled():
            QMessageBox.warning(
                self,
                "Sugestão de pagamento",
                "A sincronização em nuvem ainda não está configurada neste computador.",
            )
            return

        response = QMessageBox.question(
            self,
            "Sugerir pagamentos",
            f"Sugerir {len(ids)} conta" + ("" if len(ids) == 1 else "s")
            + " para pagamento no mobile por 3 horas?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        try:
            result = suggest_payments(ids)
            count = int(result.get("quantidade") or len(ids))
            self._bulk_checked_ids = set()
            apply_activity_markers(self)
            QMessageBox.information(
                self,
                "Sugestões enviadas",
                f"{count} conta" + ("" if count == 1 else "s")
                + " sugerida" + ("" if count == 1 else "s")
                + " para pagamento.",
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Sugestão de pagamento",
                f"Não foi possível enviar as sugestões.\n\n{exc}",
            )

    def mark_checked_sent(self) -> None:
        """Compatibilidade: nunca marcar como enviado sem copiar a conta."""
        ids = checked_ids(self)
        if not ids:
            return
        # O desktop agora opera envio individual pela linha. Copiar vários
        # lançamentos de uma vez deixaria somente o último no clipboard,
        # embora todos fossem marcados como enviados.
        dashboard = getattr(self, "_desktop_dashboard", None)
        features = getattr(dashboard, "_desktop_features", None)
        if features is None:
            return
        if len(ids) != 1:
            QMessageBox.information(
                self, "Envio individual",
                "Envie uma conta de cada vez pelo ícone da própria linha."
            )
            return
        features.send_for_payment(ids[0])

    def install_bulk_bar(self) -> None:
        if getattr(self, "_bulk_bar", None) is not None:
            return

        table = self.tabela
        parent = table.parentWidget()
        parent_layout = parent.layout() if parent else None
        if parent_layout is None:
            return

        bar = QFrame(parent)
        bar.setObjectName("bulkPaymentBar")
        bar.setStyleSheet(
            """
            QFrame#bulkPaymentBar {
                background:#f8fbf9;
                border:1px solid #e0e9e4;
                border-radius:12px;
            }
            QFrame#bulkPaymentBar QLabel {
                color:#274238;
                font-weight:700;
            }
            QFrame#bulkPaymentBar QPushButton {
                min-height:32px;
                border:1px solid #d9e5de;
                border-radius:9px;
                padding:0 12px;
                background:#ffffff;
                color:#274238;
                font-weight:700;
            }
            QFrame#bulkPaymentBar QPushButton#bulkPrimary {
                background:#0b6b45;
                color:#ffffff;
                border-color:#0b6b45;
            }
            """
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(8)

        count = QLabel("0 selecionadas", bar)
        lay.addWidget(count)
        lay.addStretch(1)

        suggest = QPushButton("Sugerir pagamento (0)", bar)
        suggest.setObjectName("bulkPrimary")
        sent = QPushButton("Marcar como enviado (0)", bar)
        select_all = QPushButton("Selecionar todos", bar)
        clear = QPushButton("Limpar seleção", bar)

        suggest.clicked.connect(lambda: suggest_checked(self))
        sent.clicked.connect(lambda: mark_checked_sent(self))
        select_all.clicked.connect(lambda: select_all_visible(self))
        clear.clicked.connect(lambda: clear_checked(self))

        lay.addWidget(select_all)
        lay.addWidget(suggest)
        lay.addWidget(sent)
        lay.addWidget(clear)

        try:
            index = parent_layout.indexOf(table)
            if hasattr(parent_layout, "insertWidget") and index >= 0:
                parent_layout.insertWidget(index, bar)
            else:
                parent_layout.addWidget(bar)
        except Exception:
            parent_layout.addWidget(bar)

        self._bulk_bar = bar
        self._bulk_count_label = count
        self._bulk_suggest_button = suggest
        self._bulk_sent_button = sent
        self._bulk_clear_button = clear
        self._bulk_select_all_button = select_all
        bar.show()

    def install_detail_dock(self) -> None:
        if getattr(self, "_cloud_detail_dock", None) is not None:
            return

        try:
            dock = QDockWidget("Detalhes da conta", self)
            dock.setObjectName("paymentDetailDock")
            dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
            dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
            dock.setStyleSheet(
                """
                QDockWidget {
                    color:#203049;
                    font-weight:800;
                }
                QDockWidget::title {
                    background:#ffffff;
                    border:1px solid #e4eaf2;
                    padding:11px 12px;
                    text-align:left;
                }
                """
            )

            body = QWidget(dock)
            layout = QVBoxLayout(body)
            layout.setContentsMargins(18, 16, 18, 18)
            layout.setSpacing(10)

            receiver = QLabel("Conta", body)
            receiver.setWordWrap(True)
            receiver.setStyleSheet("font-size:17px;font-weight:800;color:#183329;")
            value = QLabel("R$ 0,00", body)
            value.setStyleSheet("font-size:24px;font-weight:900;color:#10251c;")

            meta = QHBoxLayout()
            due = QLabel("Vencimento  —", body)
            status = QLabel("—", body)
            due.setStyleSheet("color:#66776f;font-size:12px;")
            status.setStyleSheet(
                "padding:5px 9px;border-radius:9px;background:#eef6ff;"
                "color:#2d658f;font-weight:800;"
            )
            meta.addWidget(due)
            meta.addStretch(1)
            meta.addWidget(status)

            title = QLabel("Linha do tempo", body)
            title.setStyleSheet("margin-top:8px;font-weight:800;color:#274238;")

            suggested = QLabel("Sugerido para pagamento\n—", body)
            sent = QLabel("Enviado para pagamento\n—", body)
            copied = QLabel("Última cópia do código\n—", body)
            for label in (suggested, sent):
                label.setStyleSheet(
                    "padding:10px 12px;border-radius:10px;"
                    "background:#f6f8f7;color:#52655c;font-weight:600;"
                )

            layout.addWidget(receiver)
            layout.addWidget(value)
            layout.addLayout(meta)
            layout.addWidget(title)
            layout.addWidget(suggested)
            layout.addWidget(sent)
            layout.addWidget(copied)
            layout.addStretch(1)

            open_receipt = QPushButton("Abrir comprovante", body)
            open_receipt.setObjectName("detailOpenReceipt")
            open_receipt.setStyleSheet(
                "min-height:38px;border:1px solid #9bbcff;color:#1769e0;"
                "background:#ffffff;border-radius:9px;font-weight:700;"
            )
            edit_account = QPushButton("Editar conta", body)
            edit_account.setObjectName("detailEditAccount")
            edit_account.setStyleSheet(
                "min-height:40px;border:1px solid #1f6feb;color:#ffffff;"
                "background:#1f6feb;border-radius:9px;font-weight:800;"
            )
            open_receipt.clicked.connect(
                lambda: getattr(self, "_desktop_dashboard", None)
                and self._desktop_dashboard._click_original("Abrir comprovante")
            )
            edit_account.clicked.connect(
                lambda: getattr(self, "_desktop_dashboard", None)
                and self._desktop_dashboard._click_original("Editar")
            )
            layout.addWidget(open_receipt)
            layout.addWidget(edit_account)

            dock.setWidget(body)
            dock.setMinimumWidth(315)
            dock.setMaximumWidth(360)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            dock.hide()

            self._cloud_detail_dock = dock
            self._detail_receiver = receiver
            self._detail_value = value
            self._detail_due = due
            self._detail_status = status
            self._detail_suggested = suggested
            self._detail_sent = sent
            self._detail_copied = copied

            def open_row_detail(row, _column):
                dock.show()
                refresh_detail_panel(self, row)

            self.tabela.cellClicked.connect(open_row_detail)
        except Exception:
            pass

    def init_with_cloud_mobile(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        self._bulk_checked_ids = set()

        try:
            install_bulk_bar(self)
            # O painel lateral foi eliminado da experiencia desktop.

            if not getattr(self, "_cloud_table_connected", False):
                self.tabela.itemChanged.connect(lambda item: on_item_changed(self, item))
                self._cloud_table_connected = True

            header = self.findChild(ui.QFrame, "headerPanel")
            layout = header.layout() if header else None
            if layout is not None:
                button = ui.QPushButton("📱  Acesso mobile")
                button.setObjectName("secondaryButton")
                button.setToolTip("Copia o link privativo do painel mobile em nuvem.")

                def show_mobile_access():
                    if not cloud_enabled():
                        QMessageBox.warning(
                            self,
                            "Acesso mobile",
                            "A sincronização em nuvem ainda não está configurada neste computador.",
                        )
                        return

                    link = get_cloud_private_link()
                    if not link:
                        QMessageBox.warning(
                            self,
                            "Acesso mobile",
                            "Não foi possível localizar o link privativo do mobile.",
                        )
                        return

                    ui.QApplication.clipboard().setText(link)
                    QMessageBox.information(
                        self,
                        "Acesso mobile",
                        "O link privativo foi copiado para a área de transferência.",
                    )

                button.clicked.connect(show_mobile_access)

                new_account_index = -1
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    widget = item.widget() if item else None
                    if isinstance(widget, ui.QPushButton) and "Nova conta" in widget.text():
                        new_account_index = i
                        break

                if new_account_index >= 0:
                    layout.insertWidget(new_account_index, button)
                else:
                    layout.addWidget(button)

                self._cloud_mobile_button = button

            try:
                pull_cloud_activity_once()
            except Exception:
                pass

            apply_activity_markers(self)
            from .desktop_dashboard import install_dashboard
            install_dashboard(self)

            timer = QTimer(self)
            timer.setInterval(5000)
            timer.timeout.connect(lambda: apply_activity_markers(self))
            timer.start()
            self._cloud_activity_timer = timer
        except Exception:
            pass

    ui.JanelaPrincipal.atualizar_tabela = update_table_with_activity
    ui.JanelaPrincipal.__init__ = init_with_cloud_mobile
    _ui_integrated = True
