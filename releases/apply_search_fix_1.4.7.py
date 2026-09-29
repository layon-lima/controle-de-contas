from pathlib import Path

path = Path('release_src/controle_contas/patches.py')
text = path.read_text(encoding='utf-8')

helper = r'''
def _interpretar_valor_busca_exata(busca):
    """Retorna um valor monetário quando a consulta representa dinheiro exato."""
    bruto = str(busca or "").strip()
    if not bruto:
        return None

    tem_prefixo = bruto.casefold().startswith("r$")
    numero = re.sub(r"(?i)^\s*r\$\s*", "", bruto).strip().replace(" ", "")
    if not numero:
        return None

    # Sem R$, só tratamos como valor quando existe separador decimal claro.
    # Assim uma busca simples como "10" ainda pode localizar documento/código.
    if not tem_prefixo:
        formato_br = re.fullmatch(r"\d{1,3}(?:\.\d{3})*,\d{0,2}|\d+,\d{0,2}", numero)
        formato_en = re.fullmatch(r"\d+\.\d{1,2}", numero)
        if not (formato_br or formato_en):
            return None

    try:
        if "," in numero:
            normalizado = numero.replace(".", "").replace(",", ".")
        else:
            normalizado = numero
        return round(float(normalizado), 2)
    except (TypeError, ValueError):
        return None


'''

marker = 'def _conta_bate_busca(item: dict, consulta: str, pagamento: dict | None = None) -> bool:\n'
if helper.strip() not in text:
    pos = text.index(marker)
    text = text[:pos] + helper + text[pos:]

start = text.index('def _conta_bate_busca(')
end = text.index('\ndef _montar_ui_compacta_com_obs', start)
new_matcher = r'''def _conta_bate_busca(
    item: dict,
    consulta: str,
    pagamento: dict | None = None,
    valor_exato=None,
) -> bool:
    if valor_exato is None:
        valor_exato = _interpretar_valor_busca_exata(consulta)

    if valor_exato is not None:
        try:
            return round(float(item.get("valor") or 0), 2) == valor_exato
        except (TypeError, ValueError):
            return False

    consulta = _normalizar_busca_global(consulta)
    if not consulta:
        return True

    texto = _texto_pesquisavel_conta(item, pagamento)
    tokens = [t for t in consulta.split() if t]
    return bool(tokens) and all(token in texto for token in tokens)

'''
text = text[:start] + new_matcher + text[end+1:]

start = text.index('def _montar_ui_compacta_com_obs(self):')
end = text.index('\ndef _atualizar_tabela_com_obs', start)
new_mount = r'''def _montar_ui_compacta_com_obs(self):
    _montar_ui_original(self)
    _compactar_area_superior(self)
    _configurar_coluna_observacao(self)

    try:
        from PySide6.QtCore import QTimer

        self.busca.setPlaceholderText(
            "Pesquisar qualquer informação da conta ou valor exato..."
        )
        self.busca.setClearButtonEnabled(True)

        try:
            self.busca.textChanged.disconnect()
        except Exception:
            pass
        try:
            self.busca.returnPressed.disconnect()
        except Exception:
            pass

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(220)
        timer.timeout.connect(self.atualizar_tabela)
        self._patch_timer_busca = timer

        self.busca.textChanged.connect(lambda _texto: timer.start())
        self.busca.returnPressed.connect(lambda: self.atualizar_tabela())
    except Exception:
        pass

'''
text = text[:start] + new_mount + text[end+1:]

old = '    pagamentos = _pagamentos_para_busca(self.banco) if consulta else {}\n\n    if consulta:\n'
new = (
    '    valor_exato = _interpretar_valor_busca_exata(consulta)\n'
    '    pagamentos = (\n'
    '        _pagamentos_para_busca(self.banco)\n'
    '        if consulta and valor_exato is None\n'
    '        else {}\n'
    '    )\n\n'
    '    if consulta:\n'
)
if old not in text:
    raise RuntimeError('Trecho de carregamento de pagamentos não encontrado.')
text = text.replace(old, new, 1)

old_call = '''            if _conta_bate_busca(\n                conta,\n                consulta,\n                pagamentos.get(conta_id, {}),\n            ):\n'''
new_call = '''            if _conta_bate_busca(\n                conta,\n                consulta,\n                pagamentos.get(conta_id, {}),\n                valor_exato,\n            ):\n'''
if old_call not in text:
    raise RuntimeError('Chamada da busca não encontrada.')
text = text.replace(old_call, new_call, 1)

path.write_text(text, encoding='utf-8')
print('Pesquisa 1.4.7 aplicada com sucesso.')
