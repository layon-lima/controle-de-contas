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
    QApplication, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QInputDialog, QLabel,
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
        """Faixa única e compacta: filtros discretos + busca junto à tabela."""
        w = self.window
        bulk = w._bulk_bar
        lay = bulk.layout()
        bulk.setFixedHeight(48)
        bulk.setStyleSheet(
            "QFrame#bulkPaymentBar{background:#f8fbff;"
            "border:1px solid #dfebf6;border-radius:12px;}"
            "QFrame#bulkPaymentBar QLabel{color:#657b99;font-size:10px;"
            "font-weight:800;border:0;}"
            "QFrame#bulkPaymentBar QComboBox{background:#ffffff;"
            "border:1px solid #dbe7f4;border-radius:8px;"
            "min-height:30px;padding:0 8px;color:#294566;font-weight:650;}"
        )
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(7)
        for i in range(lay.count() - 1, -1, -1):
            item = lay.itemAt(i)
            if item.spacerItem() is not None:
                lay.takeAt(i)
        # As operações agora são exclusivamente por linha.
        for name in (
            "_bulk_select_all_button", "_bulk_sent_button", "_bulk_clear_button",
            "_bulk_suggest_button", "_bulk_count_label",
        ):
            old = getattr(w, name, None)
            if old:
                old.hide()
        # O botão original de pagamento foi ocultado pelo dashboard.
        # O próprio método registrar_pagamento continua disponível por linha.
        title = QLabel("FILTROS", bulk)
        lay.insertWidget(0, title)
        filt = getattr(w, "filtro", None)
        if filt is not None:
            parent = filt.parentWidget()
            if parent and parent.layout():
                parent.layout().removeWidget(filt)
            filt.setParent(bulk)
            filt.setFixedSize(135, 34)
            filt.setToolTip("Situação da conta")
            lay.insertWidget(1, filt)
        tags = QComboBox(bulk)
        tags.setObjectName("desktopTagFilter")
        tags.setFixedSize(170, 34)
        tags.setToolTip("Filtrar contas por TAG")
        lay.insertWidget(2, tags)
        self.tag_filter = tags
        self._refresh_tags()
        tags.currentIndexChanged.connect(self._filter_tags)
        search = getattr(w, "busca", None)
        if search is not None:
            parent = search.parentWidget()
            if parent and parent.layout():
                parent.layout().removeWidget(search)
            search.setParent(bulk)
            search.setMinimumWidth(260)
            search.setMaximumWidth(16777215)
            search.setFixedHeight(35)
            search.setObjectName("desktopSearch")
            search.setPlaceholderText("Pesquisar fornecedor, valor, descrição ou vencimento...")
            lay.addWidget(search, 1)
            search.show()

        parent = w.tabela.parentWidget()
        listing = parent.layout() if parent else None
        if listing:
            listing.setSpacing(4)
            listing.setContentsMargins(7, 4, 7, 6)
            position = listing.indexOf(bulk)
            for index in range(max(position, 0)):
                item = listing.itemAt(index)
                widget = item.widget() if item else None
                if widget is not None and widget is not w.tabela:
                    widget.hide()
                nested = item.layout() if item else None
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
            elif what == "Sugerir para pagamento":
                self.suggest([account_id])
            elif what == "Enviar para pagamento":
                return self.send_for_payment(account_id)
            elif what == "Registrar pagamento":
                self.window.registrar_pagamento()
            elif what == "TAGs":
                self.manage_tags([account_id])
            elif what == "Marcar como enviado":
                return self.send_for_payment(account_id)
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

    def _record_sent(self, account_id):
        """A data só é gravada depois que os dados foram copiados."""
        stamp = datetime.now().isoformat(timespec="seconds")
        with self.window.banco.conexao() as conn:
            result = conn.execute(
                "UPDATE contas SET enviado_pagamento_em="
                "COALESCE(enviado_pagamento_em,?),atualizado_em=? WHERE id=?",
                (stamp, stamp, int(account_id)),
            )
            if result.rowcount != 1:
                raise RuntimeError("A conta não foi encontrada ao gravar o envio.")

    def mark_sent(self, account_id):
        # Compatibilidade: chamadas externas usam o mesmo fluxo de cópia.
        return self.send_for_payment(account_id)

    def send_for_payment(self, account_id):
        """Executa o botão original de envio, que prepara/copia os dados.

        Não simula o envio atualizando somente a coluna de data: essa era
        a causa do botão aparentemente funcionar sem abrir o fluxo real.
        """
        account = self._account(account_id)
        if not account:
            QMessageBox.warning(self.window, "Enviar para pagamento",
                                "Conta não encontrada.")
            return False
        original = self.dashboard._buttons.get("original_send")
        if original is None:
            original = self.dashboard._find_action("Enviar para pagamento")
        if original is None or not original.isEnabled():
            QMessageBox.warning(
                self.window, "Enviar para pagamento",
                "A rotina original de copiar dados para pagamento está indisponível."
            )
            return False

        clipboard = QApplication.clipboard()
        before = clipboard.text() if clipboard is not None else ""
        previous_stamp = str(account.get("enviado_pagamento_em") or "")
        # A linha correta já foi selecionada por _patch_row_dispatch.
        # O handler original controla o texto e a apresentação de dados.
        original.click()

        after = clipboard.text() if clipboard is not None else ""
        copied = bool(after.strip()) and after != before
        # Copiar novamente o mesmo código é válido: o clipboard pode
        # continuar idêntico à cópia anterior.
        code = str(account.get("codigo_pagamento") or "").strip()
        if not copied and code and code in after:
            copied = True

        current_stamp = str(
            self._account(account_id).get("enviado_pagamento_em") or ""
        )
        if copied or (current_stamp and current_stamp != previous_stamp):
            if not current_stamp:
                self._record_sent(account_id)
            self.window.atualizar_tabela()
            self._flash("Dados de pagamento copiados. Envio registrado.")
            return True

        # A rotina original pode abrir uma tela para o usuário copiar
        # manualmente. Nesse caso, não confirmar envio antes da cópia.
        opened = QApplication.activeModalWidget()
        if opened is None:
            opened = next(
                (dlg for dlg in self.window.findChildren(QDialog)
                 if dlg.isVisible()), None
            )
        if opened is not None:
            self._flash("Dados de pagamento abertos. Copie para concluir o envio.")
            return None
        QMessageBox.warning(
            self.window, "Envio não concluído",
            "A rotina de envio foi acionada, mas não confirmou a cópia dos dados. "
            "A conta não foi marcada como enviada."
        )
        return False

    def _flash(self, message):
        status = self.window.statusBar() if hasattr(self.window, "statusBar") else None
        if status:
            status.showMessage(message, 4200)

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
            self._flash(f"{len(ids)} conta(s) sugerida(s) para pagamento.")
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
