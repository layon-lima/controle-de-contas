from pathlib import Path
import re

path = Path("release_src/controle_contas/patches.py")
text = path.read_text(encoding="utf-8")

old_search = re.compile(
    r'def _listar_contas_com_observacao\(self, busca="", filtro="Todas"\):\n.*?\n    return resultado\n\n',
    re.S,
)
new_search = r'''def _interpretar_valor_busca_exata(busca):
    """Interpreta valores monetários digitados na pesquisa."""
    import re

    bruto = str(busca or "").strip()
    if not bruto:
        return None

    tem_prefixo = bruto.casefold().startswith("r$")
    numero = re.sub(r"(?i)^\\s*r\\$\\s*", "", bruto).strip().replace(" ", "")
    if not numero:
        return None

    if not tem_prefixo:
        formato_br = re.fullmatch(r"\\d{1,3}(?:\\.\\d{3})*,\\d{0,2}|\\d+,\\d{0,2}", numero)
        formato_en = re.fullmatch(r"\\d+\\.\\d{1,2}", numero)
        if not (formato_br or formato_en):
            return None

    try:
        normalizado = numero.replace(".", "").replace(",", ".") if "," in numero else numero
        return round(float(normalizado), 2)
    except (TypeError, ValueError):
        return None


def _listar_contas_com_observacao(self, busca="", filtro="Todas"):
    itens = _listar_contas_original(self, "", filtro)
    termo_original = str(busca or "").strip()
    termo = termo_original.casefold()

    if not termo:
        self._patch_ultima_listagem = itens
        return itens

    valor_exato = _interpretar_valor_busca_exata(termo_original)
    if valor_exato is not None:
        resultado = [
            item
            for item in itens
            if round(float(item.get("valor") or 0), 2) == valor_exato
        ]
        self._patch_ultima_listagem = resultado
        return resultado

    resultado = []
    for item in itens:
        texto = " ".join(
            str(item.get(campo) or "")
            for campo in (
                "fornecedor",
                "pagador",
                "documento",
                "referencia",
                "codigo_pagamento",
                "observacoes",
            )
        ).casefold()
        if termo in texto:
            resultado.append(item)

    self._patch_ultima_listagem = resultado
    return resultado

'''
text, n = old_search.subn(new_search, text, count=1)
if n != 1:
    raise RuntimeError(f"Falha ao atualizar busca: {n}")

old_mount = re.compile(
    r'def _montar_ui_compacta_com_obs\(self\):\n.*?\n    except Exception:\n        pass\n\n',
    re.S,
)
new_mount = r'''def _configurar_busca_com_atraso(self) -> None:
    """Evita reconstruir a tabela a cada tecla pressionada."""
    try:
        from PySide6.QtCore import QTimer

        try:
            self.busca.textChanged.disconnect()
        except Exception:
            pass

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(220)
        timer.timeout.connect(self.atualizar_tabela)
        self._patch_timer_busca = timer

        def agendar_busca(_texto=""):
            timer.start()

        self.busca.textChanged.connect(agendar_busca)
    except Exception:
        pass


def _montar_ui_compacta_com_obs(self):
    _montar_ui_original(self)
    _compactar_area_superior(self)
    _configurar_coluna_observacao(self)
    _configurar_busca_com_atraso(self)
    try:
        self.busca.setPlaceholderText(
            "Pesquisar recebedor, valor, observação, documento ou código..."
        )
    except Exception:
        pass

'''
text, n = old_mount.subn(new_mount, text, count=1)
if n != 1:
    raise RuntimeError(f"Falha ao atualizar UI de busca: {n}")

old_table = re.compile(
    r'def _atualizar_tabela_com_obs\(self, \*args, \*\*kwargs\):\n.*?\n    except Exception:\n        pass\n\n',
    re.S,
)
new_table = r'''def _atualizar_tabela_com_obs(self, *args, **kwargs):
    _atualizar_tabela_original(self, *args, **kwargs)
    try:
        tabela = self.tabela
        if tabela.columnCount() < 9:
            _configurar_coluna_observacao(self)

        contas_cache = getattr(self.banco, "_patch_ultima_listagem", []) or []
        contas_por_id = {
            str(conta.get("id")): conta
            for conta in contas_cache
            if conta.get("id") is not None
        }

        for linha in range(tabela.rowCount()):
            id_item = tabela.item(linha, 7)
            if id_item is None:
                continue

            conta = contas_por_id.get(id_item.text())
            if not conta:
                continue

            obs = " ".join(str(conta.get("observacoes") or "").split())
            item = ui.QTableWidgetItem(obs)
            item.setTextAlignment(
                ui.Qt.AlignmentFlag.AlignLeft | ui.Qt.AlignmentFlag.AlignVCenter
            )
            if obs:
                item.setToolTip(obs)

            base = tabela.item(linha, 0)
            if base is not None:
                try:
                    item.setBackground(base.background())
                    item.setForeground(base.foreground())
                except Exception:
                    pass
            tabela.setItem(linha, 8, item)
    except Exception:
        pass

'''
text, n = old_table.subn(new_table, text, count=1)
if n != 1:
    raise RuntimeError(f"Falha ao otimizar tabela: {n}")

path.write_text(text, encoding="utf-8")
print("Pesquisa 1.4.6 aplicada.")
