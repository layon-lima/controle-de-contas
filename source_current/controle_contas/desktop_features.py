"""Fluxos desktop: documentos locais, pagamento em lote e TAGs persistentes.

Não mexe na estrutura existente de contas/pagamentos. TAGs ficam em tabelas auxiliares.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from types import MethodType

from PySide6.QtCore import Qt, QDate, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)


class DesktopFeatures:
    def __init__(self, dashboard):
        self.dashboard = dashboard
        self.window = dashboard.window
        self.tag_filter = None
        self._tag_map = {}
        self._build_tag_tables()
        self._install_toolbar()
        self._patch_row_dispatch()
        self._patch_decorate()
        self.window.atualizar_tabela()

    def _build_tag_tables(self):
        with self.window.banco.conexao() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cc_tags "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL COLLATE NOCASE UNIQUE)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cc_account_tags "
                "(account_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, "
                "PRIMARY KEY(account_id,tag_id))"
            )

    def _ids(self):
        return sorted(int(x) for x in getattr(self.window, "_bulk_checked_ids", set()))

    def _row_id(self, row):
        try:
            item = self.window.tabela.item(int(row), 7)
            return int(item.text()) if item else None
        except (ValueError, TypeError, AttributeError):
            return None

    def _account(self, account_id):
        return self.window.banco.buscar_conta(int(account_id)) or {}

    def _install_toolbar(self):
        w = self.window
        bulk = w._bulk_bar
        lay = bulk.layout()
        bulk.setFixedHeight(42)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(5)

        # Apenas controles de trabalho: sem seleção total, limpar ou marcar enviado.
        for name in ("_bulk_select_all_button", "_bulk_sent_button",
                     "_bulk_clear_button"):
            button = getattr(w, name, None)
            if button is not None:
                button.hide()

        # Reaproveita o seletor de status original, com sinal/filtro intactos.
        filt = getattr(w, "filtro", None)
        if filt is not None:
            parent = filt.parentWidget()
            if parent is not None and parent.layout() is not None:
                parent.layout().removeWidget(filt)
            filt.setParent(bulk)
            filt.setFixedWidth(138)
            filt.setFixedHeight(32)
            filt.setToolTip("Filtrar por situação")
            lay.insertWidget(0, filt)

        tag_combo = QComboBox(bulk)
        tag_combo.setObjectName("desktopTagFilter")
        tag_combo.setFixedWidth(156)
        tag_combo.setFixedHeight(32)
        lay.insertWidget(1, tag_combo)
        self.tag_filter = tag_combo
        self._refresh_tags()
        tag_combo.currentIndexChanged.connect(self._filter_tags)

        tag_button = QPushButton("TAGs", bulk)
        tag_button.setObjectName("desktopTagsButton")
        tag_button.setToolTip("Criar e atribuir TAGs às contas selecionadas")
        tag_button.clicked.connect(lambda: self.manage_tags(self._ids()))
        lay.insertWidget(max(0, lay.count()-1), tag_button)

        pay_button = QPushButton("Pagar selecionadas", bulk)
        pay_button.setObjectName("desktopPayBatchButton")
        pay_button.setToolTip("Registrar pagamentos sem exigir comprovante PDF")
        pay_button.clicked.connect(self.pay_selected)
        lay.insertWidget(max(0, lay.count()-1), pay_button)

        # Refaz a conexão das sugestões: a rota remota antiga precisa ser
        # diagnosticada e uma chamada não pode ser contada como sucesso sem OK.
        suggest = getattr(w, "_bulk_suggest_button", None)
        if suggest:
            try:
                suggest.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            suggest.clicked.connect(self.suggest_checked)
        # A antiga faixa de filtros ficava alta e praticamente vazia.
        parent = w.tabela.parentWidget()
        layout = parent.layout() if parent else None
        if layout:
            layout.setSpacing(3)
            layout.setContentsMargins(7, 4, 7, 6)
            position = layout.indexOf(bulk)
            for i in range(position):
                it = layout.itemAt(i)
                widget = it.widget() if it else None
                if widget and widget is not w.tabela:
                    widget.hide()
                nested = it.layout() if it else None
                if nested:
                    nested.setContentsMargins(0, 0, 0, 0)
                    nested.setSpacing(0)

    def _tags(self):
        with self.window.banco.conexao() as conn:
            return [(int(r["id"]), str(r["name"])) for r in conn.execute(
                "SELECT id,name FROM cc_tags ORDER BY name COLLATE NOCASE"
            )]

    def _refresh_tags(self):
        if not self.tag_filter:
            return
        current = self.tag_filter.currentData()
        self.tag_filter.blockSignals(True)
        try:
            self.tag_filter.clear()
            self.tag_filter.addItem("Todas as TAGs", None)
            for tag_id, name in self._tags():
                self.tag_filter.addItem(name, tag_id)
            idx = self.tag_filter.findData(current)
            self.tag_filter.setCurrentIndex(max(0, idx))
        finally:
            self.tag_filter.blockSignals(False)

    def _load_tag_map(self):
        with self.window.banco.conexao() as conn:
            rows = conn.execute(
                "SELECT x.account_id, t.id, t.name FROM cc_account_tags x "
                "JOIN cc_tags t ON t.id=x.tag_id "
                "JOIN contas c ON c.id=x.account_id ORDER BY t.name COLLATE NOCASE"
            ).fetchall()
        tags = {}
        for r in rows:
            tags.setdefault(int(r["account_id"]), []).append(
                (int(r["id"]), str(r["name"]))
            )
        return tags

    def _filter_tags(self, *_):
        table = self.window.tabela
        tag_id = self.tag_filter.currentData() if self.tag_filter else None
        for row in range(table.rowCount()):
            ids = [tag for tag, _ in self._tag_map.get(self._row_id(row), ())]
            table.setRowHidden(row, tag_id is not None and tag_id not in ids)

    def _patch_decorate(self):
        original = self.dashboard.decorate_rows
        def decorate(dashboard):
            original()
            tags = self._load_tag_map()
            self._tag_map = tags
            table = self.window.tabela
            # Aplica somente ao item visual, sem modificar observações no banco.
            role = Qt.ItemDataRole.UserRole + 17
            for row in range(table.rowCount()):
                item = table.item(row, 8)
                if item is None:
                    continue
                raw = item.data(role)
                if raw is None:
                    raw = item.text()
                    item.setData(role, raw)
                names = [name for _, name in tags.get(self._row_id(row), ())]
                visual = str(raw or "")
                if names:
                    visual += ("  ·  " if visual else "") + ", ".join(
                        "#" + name for name in names
                    )
                item.setText(visual)
                item.setToolTip(str(raw or "") + (
                    "\nTAGs: " + ", ".join(names) if names else ""
                ))
            self._filter_tags()
        self.dashboard.decorate_rows = MethodType(decorate, self.dashboard)

    def manage_tags(self, account_ids):
        ids = sorted({int(x) for x in account_ids if int(x) > 0})
        if not ids:
            QMessageBox.information(
                self.window, "TAGs", "Marque uma conta ou abra TAGs no menu da linha."
            )
            return
        dialog = QDialog(self.window)
        dialog.setWindowTitle("TAGs das contas")
        dialog.resize(420, 430)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"Atribuir TAGs a {len(ids)} conta(s). TAGs já existentes "
            "são preservadas em operações em lote."
        ))
        listing = QListWidget(dialog)
        layout.addWidget(listing)
        with self.window.banco.conexao() as conn:
            existing = {int(r["tag_id"]) for r in conn.execute(
                "SELECT tag_id FROM cc_account_tags WHERE account_id=?",
                (ids[0],)
            )} if len(ids) == 1 else set()
        def fill():
            listing.clear()
            for tag_id, name in self._tags():
                item = QListWidgetItem(name, listing)
                item.setData(Qt.ItemDataRole.UserRole, tag_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if tag_id in existing
                    else Qt.CheckState.Unchecked
                )
        fill()
        add = QPushButton("Criar TAG", dialog)
        layout.addWidget(add)
        def create():
            name, ok = QInputDialog.getText(dialog, "Nova TAG", "Nome da TAG:")
            name = " ".join(name.split())
            if not ok or not name:
                return
            if len(name) > 60:
                QMessageBox.warning(dialog, "TAG", "Máximo de 60 caracteres.")
                return
            with self.window.banco.conexao() as conn:
                conn.execute("INSERT OR IGNORE INTO cc_tags(name) VALUES(?)", (name,))
                row = conn.execute(
                    "SELECT id FROM cc_tags WHERE name=? COLLATE NOCASE", (name,)
                ).fetchone()
            if row:
                existing.add(int(row["id"]))
            fill()
        add.clicked.connect(create)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel, parent=dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [int(listing.item(i).data(Qt.ItemDataRole.UserRole))
                    for i in range(listing.count())
                    if listing.item(i).checkState() == Qt.CheckState.Checked]
        with self.window.banco.conexao() as conn:
            if len(ids) == 1:
                conn.execute("DELETE FROM cc_account_tags WHERE account_id=?", (ids[0],))
            for account_id in ids:
                for tag_id in selected:
                    conn.execute(
                        "INSERT OR IGNORE INTO cc_account_tags(account_id,tag_id) "
                        "VALUES(?,?)", (account_id, tag_id)
                    )
        self._refresh_tags()
        self.window.atualizar_tabela()

    def _patch_row_dispatch(self):
        def action(dashboard, row, what):
            table = self.window.tabela
            if row < 0 or row >= table.rowCount():
                return
            table.setCurrentCell(row, 0)
            table.selectRow(row)
            account_id = self._row_id(row)
            if not account_id:
                return
            if what == "Abrir conta":
                self.open_pdf(account_id)
            elif what == "Abrir comprovante":
                self.open_pdf(account_id, receipt=True)
            elif what == "Enviar para pagamento":
                self.suggest([account_id])
            elif what == "TAGs":
                self.manage_tags([account_id])
            elif what == "Marcar como enviado":
                self.mark_sent(account_id)
            elif what == "Editar":
                self.window.editar_conta()
            elif what == "Excluir":
                self.window.excluir_conta()
        self.dashboard._activate_row = MethodType(action, self.dashboard)

    def open_pdf(self, account_id, receipt=False):
        account = self._account(account_id)
        if not account:
            QMessageBox.warning(self.window, "PDF", "Conta não encontrada.")
            return
        raw = str(account.get("pdf_conta") or "")
        if receipt:
            with self.window.banco.conexao() as conn:
                row = conn.execute(
                    "SELECT pdf_comprovante FROM pagamentos WHERE conta_id=? "
                    "ORDER BY id DESC LIMIT 1", (account_id,)
                ).fetchone()
            raw = str(row["pdf_comprovante"] or "") if row else ""
        path = Path(raw) if raw else None
        if path is None or not path.is_file():
            QMessageBox.information(
                self.window, "PDF indisponível",
                ("Comprovante ainda não anexado." if receipt
                 else "PDF da conta não encontrado.")
                + ("\nCaminho cadastrado: " + raw if raw else "")
            )
            return
        try:
            if os.name == "nt":
                os.startfile(str(path.resolve()))
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
                raise OSError("Nenhum leitor de PDF disponível.")
        except OSError as exc:
            QMessageBox.warning(self.window, "Falha ao abrir o PDF", str(exc))

    def mark_sent(self, account_id):
        stamp = datetime.now().isoformat(timespec="seconds")
        with self.window.banco.conexao() as conn:
            conn.execute(
                "UPDATE contas SET enviado_pagamento_em="
                "COALESCE(enviado_pagamento_em,?),atualizado_em=? WHERE id=?",
                (stamp, stamp, int(account_id))
            )
        self.window.atualizar_tabela()

    def suggest_checked(self):
        ids = self._ids()
        if not ids:
            return
        self.suggest(ids)

    def suggest(self, ids):
        from .cloud_sync import (
            cloud_enabled, suggest_payment, suggest_payments, sync_once
        )
        if not cloud_enabled():
            QMessageBox.warning(self.window, "Nuvem", "Acesso mobile não configurado.")
            return
        try:
            # Rotas existentes no código: /api/desktop/suggest-batch e /suggest/:id.
            # Se a versão publicada estiver antiga, mostra a causa em vez
            # de inventar um sucesso.
            try:
                response = suggest_payments(ids)
                if response.get("ok") is False or int(response.get("quantidade", -1)) != len(ids):
                    raise RuntimeError("A nuvem não confirmou todas as contas.")
            except Exception as exc:
                if "Conta não encontrada" in str(exc):
                    sync_once()
                elif "Rota não encontrada" not in str(exc):
                    raise
                for account_id in ids:
                    result = suggest_payment(account_id)
                    if result.get("ok") is False:
                        raise RuntimeError(str(result.get("erro") or "Falha remota."))
            QMessageBox.information(
                self.window, "Sugestões confirmadas",
                f"{len(ids)} conta(s) sugerida(s) para pagamento."
            )
            self.window.atualizar_tabela()
        except Exception as exc:
            msg = str(exc)
            if "Rota não encontrada" in msg:
                msg += ("\n\nO Worker Cloudflare publicado não contém essas rotas; "
                        "publique a versão atual do backend mobile.")
            QMessageBox.warning(self.window, "Sugestões não enviadas", msg)

    def pay_selected(self):
        ids = self._ids()
        if not ids:
            QMessageBox.information(
                self.window, "Pagamento em lote",
                "Marque as contas usando as caixas à esquerda."
            )
            return
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Pagar contas selecionadas")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"Registrar {len(ids)} pagamento(s) pelo valor integral, "
            "sem exigir PDF do comprovante?"
        ))
        layout.addWidget(QLabel("Data do pagamento:"))
        date_field = QDateEdit(QDate.currentDate(), dialog)
        date_field.setDisplayFormat("dd/MM/yyyy")
        date_field.setCalendarPopup(True)
        layout.addWidget(date_field)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel, parent=dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        date_paid = date_field.date().toString("yyyy-MM-dd")
        paid, errors = [], []
        for account_id in ids:
            try:
                with self.window.banco.conexao() as conn:
                    before = conn.execute(
                        "SELECT 1 FROM pagamentos WHERE conta_id=? "
                        "AND data_pagamento IS NOT NULL AND TRIM(data_pagamento) <> ''",
                        (account_id,)
                    ).fetchone()
                if before:
                    errors.append(f"{account_id}: já paga")
                    continue
                acc = self._account(account_id)
                if not acc:
                    raise ValueError("Conta não localizada.")
                payload = {
                    "valor_pago": float(acc.get("valor") or 0),
                    "data_pagamento": date_paid,
                    "beneficiario": str(acc.get("fornecedor") or ""),
                    "documento_beneficiario": str(acc.get("documento") or ""),
                    "pdf_comprovante": "",
                    "resultado_conferencia": "Sem comprovante",
                    "detalhes_conferencia": "Pagamento manual; documento pendente.",
                }
                self.window.banco.registrar_pagamento(account_id, payload)
                with self.window.banco.conexao() as conn:
                    check = conn.execute(
                        "SELECT 1 FROM pagamentos WHERE conta_id=? "
                        "AND data_pagamento IS NOT NULL AND TRIM(data_pagamento) <> ''",
                        (account_id,)
                    ).fetchone()
                if not check:
                    raise RuntimeError("Pagamento não gravado no histórico.")
                paid.append(account_id)
            except Exception as exc:
                errors.append(f"{account_id}: {exc}")
        self.window._bulk_checked_ids = set()
        if hasattr(self.window, "atualizar"):
            self.window.atualizar()
        else:
            self.window.atualizar_tabela()
        summary = f"{len(paid)} pagamento(s) confirmado(s)."
        if errors:
            summary += "\n\nNão registrados:\n" + "\n".join(errors[:15])
            QMessageBox.warning(self.window, "Resultado", summary)
        else:
            QMessageBox.information(self.window, "Resultado", summary)


def install_desktop_features(dashboard):
    if getattr(dashboard, "_desktop_features", None) is None:
        dashboard._desktop_features = DesktopFeatures(dashboard)
    return dashboard._desktop_features
