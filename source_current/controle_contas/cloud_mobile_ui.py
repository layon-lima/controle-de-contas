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


def integrate_cloud_mobile_button() -> None:
    global _ui_integrated
    if _ui_integrated:
        return

    from PySide6.QtCore import QTimer
    from . import ui
    from .cloud_sync import (
        cloud_enabled,
        get_cloud_private_link,
        pull_cloud_activity_once,
        suggest_payment,
    )

    original_init = ui.JanelaPrincipal.__init__
    original_update_table = ui.JanelaPrincipal.atualizar_tabela

    def apply_activity_markers(self) -> None:
        try:
            activity = _activity_map()
            table = self.tabela
            for row in range(table.rowCount()):
                id_item = table.item(row, 7)
                if id_item is None:
                    continue
                try:
                    account_id = int(id_item.text())
                except Exception:
                    continue
                info = activity.get(account_id) or {}
                copied = _is_future(info.get("copied_until"))
                suggested = _is_future(info.get("suggested_until"))

                envio_item = table.item(row, 4)
                if envio_item is None:
                    continue

                original_text = str(envio_item.data(ui.Qt.ItemDataRole.UserRole) or "")
                if not original_text:
                    original_text = envio_item.text()
                    envio_item.setData(ui.Qt.ItemDataRole.UserRole, original_text)

                if copied:
                    when = _format_activity_time(info.get("copied_at"))
                    until = _format_activity_time(info.get("copied_until"))
                    envio_item.setText("⚠ CÓDIGO COPIADO")
                    envio_item.setBackground(ui.QColor("#fff0db"))
                    envio_item.setForeground(ui.QColor("#9a5a00"))
                    envio_item.setFont(ui.QFont("Arial", 10, ui.QFont.Weight.Bold))
                    envio_item.setToolTip(
                        "Código de pagamento copiado no mobile"
                        + (f" em {when}" if when else "")
                        + (f". Sinalização até {until}." if until else ".")
                        + "\nIsso NÃO significa que a conta já foi paga."
                    )
                elif suggested:
                    until = _format_activity_time(info.get("suggested_until"))
                    envio_item.setText("💡 SUGERIDA")
                    envio_item.setBackground(ui.QColor("#fff7ed"))
                    envio_item.setForeground(ui.QColor("#b76500"))
                    envio_item.setFont(ui.QFont("Arial", 10, ui.QFont.Weight.DemiBold))
                    envio_item.setToolTip(
                        "Sugestão de pagamento ativa no mobile"
                        + (f" até {until}." if until else ".")
                    )
                else:
                    envio_item.setText(original_text)
                    envio_item.setToolTip("")
        except Exception:
            pass

    def update_table_with_activity(self, *args, **kwargs):
        result = original_update_table(self, *args, **kwargs)
        apply_activity_markers(self)
        return result

    def init_with_cloud_mobile(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        try:
            header = self.findChild(ui.QFrame, "headerPanel")
            layout = header.layout() if header else None
            if layout is None:
                return

            button = ui.QPushButton("📱  Acesso mobile")
            button.setObjectName("secondaryButton")
            button.setToolTip("Copia o link privativo do painel mobile em nuvem.")

            suggest_button = ui.QPushButton("💡  Sugerir pagamento")
            suggest_button.setObjectName("secondaryButton")
            suggest_button.setToolTip(
                "Destaca a conta selecionada no mobile por 3 horas."
            )

            def show_mobile_access():
                if not cloud_enabled():
                    ui.QMessageBox.warning(
                        self,
                        "Acesso mobile",
                        "A sincronização em nuvem ainda não está configurada neste computador.",
                    )
                    return

                link = get_cloud_private_link()
                if not link:
                    ui.QMessageBox.warning(
                        self,
                        "Acesso mobile",
                        "Não foi possível localizar o link privativo do mobile.",
                    )
                    return

                ui.QApplication.clipboard().setText(link)
                ui.QMessageBox.information(
                    self,
                    "Acesso mobile",
                    "O link privativo foi copiado para a área de transferência.\n\n"
                    "O mobile usa a última versão sincronizada na nuvem e continua "
                    "disponível mesmo quando este computador estiver desligado.\n\n"
                    "Não compartilhe o link publicamente.",
                )

            def suggest_selected_account():
                if not cloud_enabled():
                    ui.QMessageBox.warning(
                        self,
                        "Sugestão de pagamento",
                        "A sincronização em nuvem ainda não está configurada neste computador.",
                    )
                    return

                account = self.conta_selecionada()
                if not account:
                    return
                if str(account.get("status") or "") == "Paga":
                    ui.QMessageBox.information(
                        self,
                        "Sugestão de pagamento",
                        "Essa conta já está paga.",
                    )
                    return

                response = ui.QMessageBox.question(
                    self,
                    "Sugestão de pagamento",
                    "Sugerir esta conta para pagamento no mobile por 3 horas?\n\n"
                    f"{account.get('fornecedor') or 'Conta'} — "
                    f"R$ {float(account.get('valor') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                    ui.QMessageBox.StandardButton.Yes | ui.QMessageBox.StandardButton.No,
                )
                if response != ui.QMessageBox.StandardButton.Yes:
                    return

                try:
                    result = suggest_payment(int(account["id"]))
                    until = _format_activity_time(result.get("suggested_until"))
                    apply_activity_markers(self)
                    ui.QMessageBox.information(
                        self,
                        "Sugestão enviada",
                        "A conta foi destacada no mobile por 3 horas."
                        + (f"\n\nAtiva até {until}." if until else ""),
                    )
                except Exception as exc:
                    ui.QMessageBox.warning(
                        self,
                        "Sugestão de pagamento",
                        f"Não foi possível enviar a sugestão.\n\n{exc}",
                    )

            button.clicked.connect(show_mobile_access)
            suggest_button.clicked.connect(suggest_selected_account)

            new_account_index = -1
            for i in range(layout.count()):
                item = layout.itemAt(i)
                widget = item.widget() if item else None
                if isinstance(widget, ui.QPushButton) and "Nova conta" in widget.text():
                    new_account_index = i
                    break

            if new_account_index >= 0:
                layout.insertWidget(new_account_index, button)
                layout.insertWidget(new_account_index + 1, suggest_button)
            else:
                layout.addWidget(button)
                layout.addWidget(suggest_button)

            self._cloud_mobile_button = button
            self._cloud_suggest_button = suggest_button

            # O sincronizador consulta os eventos em segundo plano. A interface
            # apenas relê o cache local para nunca travar por causa de rede.
            try:
                pull_cloud_activity_once()
            except Exception:
                pass
            apply_activity_markers(self)

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
