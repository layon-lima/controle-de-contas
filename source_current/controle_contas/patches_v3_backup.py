from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from . import pdf_reader
from . import ui


# ---------------------------------------------------------------------------
# OCR PARA PDF ESCANEADO
# ---------------------------------------------------------------------------

_extrair_texto_original = pdf_reader._extrair_texto


def _localizar_tesseract() -> str | None:
    candidatos: list[Path] = []

    # Tesseract portátil empacotado junto do aplicativo/projeto.
    raiz_execucao = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    candidatos.extend(
        [
            raiz_execucao / "tesseract" / "tesseract.exe",
            Path(__file__).resolve().parents[1] / "tesseract" / "tesseract.exe",
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ]
    )

    no_path = shutil.which("tesseract")
    if no_path:
        candidatos.insert(0, Path(no_path))

    for candidato in candidatos:
        if candidato.is_file():
            return str(candidato)
    return None


def _texto_suficiente(texto: str) -> bool:
    # PDFs digitais continuam no caminho rápido. OCR só entra quando a camada
    # de texto é ausente ou claramente pequena demais para uma conta/boleto.
    compactado = re.sub(r"\s+", "", texto or "")
    return len(compactado) >= 80


def _ocr_pdf(caminho: str | Path) -> str:
    try:
        import cv2
        import fitz
        import numpy as np
        import pytesseract
    except Exception:
        return ""

    executavel = _localizar_tesseract()
    if not executavel:
        return ""

    pytesseract.pytesseract.tesseract_cmd = executavel
    partes: list[str] = []
    documento = None

    try:
        documento = fitz.open(str(caminho))
        for pagina in documento:
            pixmap = pagina.get_pixmap(matrix=fitz.Matrix(3.0, 3.0), alpha=False)
            imagem = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )

            if pixmap.n == 4:
                imagem = cv2.cvtColor(imagem, cv2.COLOR_RGBA2BGR)
            else:
                imagem = cv2.cvtColor(imagem, cv2.COLOR_RGB2BGR)

            cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
            cinza = cv2.fastNlMeansDenoising(cinza, None, 10, 7, 21)
            _, binaria = cv2.threshold(
                cinza, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )

            texto_pagina = ""
            # Prefere português + inglês. Se o pacote de idioma português não
            # estiver disponível, tenta inglês; por último deixa o Tesseract
            # usar o idioma padrão instalado.
            for idioma in ("por+eng", "eng", None):
                try:
                    kwargs = {"config": "--oem 3 --psm 3"}
                    if idioma:
                        kwargs["lang"] = idioma
                    texto_pagina = pytesseract.image_to_string(binaria, **kwargs)
                    if texto_pagina.strip():
                        break
                except Exception:
                    texto_pagina = ""

            if texto_pagina.strip():
                partes.append(texto_pagina.strip())
    except Exception:
        return ""
    finally:
        if documento is not None:
            try:
                documento.close()
            except Exception:
                pass

    return "\n".join(partes)


def _extrair_texto_com_ocr(caminho: str | Path) -> str:
    texto = ""
    try:
        texto = _extrair_texto_original(caminho) or ""
    except Exception:
        texto = ""

    if _texto_suficiente(texto):
        return texto

    texto_ocr = _ocr_pdf(caminho)
    if not texto_ocr.strip():
        return texto

    if texto.strip():
        return f"{texto}\n{texto_ocr}"
    return texto_ocr


# ---------------------------------------------------------------------------
# CADASTRO: NÃO APAGAR O QUE O USUÁRIO JÁ PREENCHEU
# ---------------------------------------------------------------------------

_init_conta_original = ui.ContaDialog.__init__


def _init_conta_preservando_edicoes(
    self,
    parent=None,
    conta=None,
    pdf_inicial=None,
    dados_pdf=None,
    banco=None,
):
    # Durante o __init__ original um PDF inicial pode ser carregado. Nesse
    # momento ainda não houve edição manual do usuário.
    self._patch_inicializando = True
    self._patch_vencimento_manual = bool((conta or {}).get("vencimento"))
    self._patch_tipo_manual = bool((conta or {}).get("tipo_pagamento"))
    # Uma observação já existente em uma conta salva é considerada escolha
    # manual do usuário e nunca deve ser substituída pelo nome de outro PDF.
    self._patch_observacao_manual = bool((conta or {}).get("observacoes"))
    self._patch_observacao_automatica = ""

    _init_conta_original(
        self,
        parent=parent,
        conta=conta,
        pdf_inicial=pdf_inicial,
        dados_pdf=dados_pdf,
        banco=banco,
    )

    self._patch_inicializando = False

    def marcou_vencimento(*_):
        if not getattr(self, "_patch_carregando_pdf", False):
            self._patch_vencimento_manual = True

    def marcou_tipo(*_):
        if not getattr(self, "_patch_carregando_pdf", False):
            self._patch_tipo_manual = True

    def marcou_observacao(*_):
        # O preenchimento automático ao carregar o PDF não conta como edição.
        # Qualquer mudança feita fora desse momento passa a ser a fonte oficial
        # do nome do PDF que será copiado para a pasta de documentos.
        if not getattr(self, "_patch_carregando_pdf", False):
            self._patch_observacao_manual = True

    self.vencimento.dateChanged.connect(marcou_vencimento)
    self.tipo.currentTextChanged.connect(marcou_tipo)
    self.observacoes.textChanged.connect(marcou_observacao)


def _carregar_pdf_preservando_campos(self, caminho, dados_prontos=None):
    self.pdf_origem = caminho

    # Confirmação visual clara de que o PDF foi realmente anexado ao cadastro.
    nome_pdf = Path(caminho).name
    self.lbl_pdf.setText(f"✓ PDF CARREGADO — {nome_pdf}")
    self.lbl_pdf.setToolTip(str(Path(caminho)))
    self.lbl_pdf.setStyleSheet(
        "QLabel {"
        " background-color: #E8F5E9;"
        " color: #1B5E20;"
        " border: 1px solid #81C784;"
        " border-radius: 6px;"
        " padding: 7px 10px;"
        " font-weight: 600;"
        "}"
    )
    self.lbl_pdf.setVisible(True)

    ui.QApplication.setOverrideCursor(ui.Qt.CursorShape.WaitCursor)
    self._patch_carregando_pdf = True

    try:
        dados = dados_prontos or ui.analisar_pdf(caminho)

        # A Observação começa com o nome original do PDF. Se o usuário alterar
        # esse texto, ContaDialog.dados() já usa a observação como nome-base do
        # PDF copiado ao salvar a conta. Portanto:
        #   arquivo original: BOLETO 123.pdf
        #   observação automática: BOLETO 123.pdf
        #   usuário altera para: ENERGIA SETEMBRO
        #   arquivo importado: ENERGIA SETEMBRO.pdf
        observacao_atual = self.observacoes.toPlainText().strip()
        observacao_auto_anterior = getattr(self, "_patch_observacao_automatica", "")
        pode_atualizar_observacao = (
            not getattr(self, "_patch_observacao_manual", False)
            or not observacao_atual
            or (
                observacao_auto_anterior
                and observacao_atual == observacao_auto_anterior
            )
        )
        if pode_atualizar_observacao:
            self.observacoes.setPlainText(nome_pdf)
            self._patch_observacao_automatica = nome_pdf
            self._patch_observacao_manual = False

        recebedor = str(dados.get("recebedor") or dados.get("fornecedor") or "")
        pagador = str(dados.get("pagador") or "")
        referencia = str(dados.get("referencia") or "")
        codigo_lido = str(dados.get("codigo_pagamento") or "")
        tipo_lido = str(dados.get("tipo_pagamento") or "")

        # Texto digitado pelo usuário sempre vence o que veio do PDF.
        if not self.fornecedor.text().strip() and recebedor:
            self.fornecedor.setText(recebedor)
        if not self.pagador.text().strip() and pagador:
            self.pagador.setText(pagador)
        if not self.referencia.text().strip() and referencia:
            self.referencia.setText(referencia)

        # Mantém compatibilidade com a versão recuperada.
        if not getattr(self, "documento_extraido", ""):
            self.documento_extraido = str(dados.get("documento") or "")

        valor_lido = dados.get("valor") or 0
        if self.valor.valor() <= 0 and valor_lido:
            self.valor.set_valor(valor_lido)

        vencimento_lido = dados.get("vencimento")
        if vencimento_lido and not getattr(self, "_patch_vencimento_manual", False):
            data = ui.QDate.fromString(str(vencimento_lido), "yyyy-MM-dd")
            if data.isValid():
                self.vencimento.setDate(data)

        if tipo_lido and not getattr(self, "_patch_tipo_manual", False):
            self.tipo.setCurrentText(tipo_lido)

        if not self.codigo.toPlainText().strip() and codigo_lido:
            self.codigo.setPlainText(codigo_lido)

        self.btn_copiar_codigo_exato.setEnabled(bool(self.codigo.toPlainText().strip()))

        inseguros = [str(c) for c in dados.get("campos_inseguros", [])]
        if inseguros:
            nomes = {
                "recebedor": "Recebedor",
                "pagador": "Pagador",
                "vencimento": "Vencimento",
                "valor": "Valor",
                "codigo_pagamento": "Código de pagamento",
                "referencia": "Referência",
            }
            faltando = ", ".join(nomes.get(c, c) for c in inseguros)
            self.aviso_leitura.setText(
                "Leitura concluída com segurança. Não houve confirmação suficiente para: "
                f"{faltando}. Esses campos foram deixados vazios para evitar cadastro incorreto."
            )
        else:
            self.aviso_leitura.setText(
                "Leitura confirmada: os campos extraídos passaram pelas validações automáticas."
            )

    except Exception as exc:
        ui.QMessageBox.warning(
            self,
            "Não foi possível ler o PDF",
            "O arquivo foi selecionado, mas os dados devem ser preenchidos manualmente.\n\n"
            f"Detalhe: {exc}",
        )
    finally:
        self._patch_carregando_pdf = False
        ui.QApplication.restoreOverrideCursor()


# ---------------------------------------------------------------------------
# JANELA DE CADASTRO NÃO MODAL
# ---------------------------------------------------------------------------


def _manter_dialogo_vivo(janela, dialogo):
    dialogos = getattr(janela, "_patch_dialogos_conta", None)
    if dialogos is None:
        dialogos = []
        janela._patch_dialogos_conta = dialogos

    dialogos.append(dialogo)
    dialogo.setModal(False)
    dialogo.setWindowModality(ui.Qt.WindowModality.NonModal)
    dialogo.setAttribute(ui.Qt.WidgetAttribute.WA_DeleteOnClose, True)

    def remover(*_):
        try:
            dialogos.remove(dialogo)
        except ValueError:
            pass

    dialogo.destroyed.connect(remover)
    dialogo.show()
    dialogo.raise_()
    dialogo.activateWindow()


def _nova_conta_nao_modal(self):
    dialog = ui.ContaDialog(self, banco=self.banco)

    def salvar():
        self.banco.adicionar_conta(dialog.dados())
        self.atualizar()

    dialog.accepted.connect(salvar)
    _manter_dialogo_vivo(self, dialog)


def _editar_conta_nao_modal(self):
    conta = self.conta_selecionada()
    if not conta:
        return

    dialog = ui.ContaDialog(self, conta, banco=self.banco)

    def salvar():
        self.banco.atualizar_conta(int(conta["id"]), dialog.dados())
        self.atualizar()

    dialog.accepted.connect(salvar)
    _manter_dialogo_vivo(self, dialog)


def _processar_pdf_solto_nao_modal(self, caminho):
    ui.QApplication.setOverrideCursor(ui.Qt.CursorShape.WaitCursor)
    try:
        dados = ui.analisar_pdf(caminho)
    except Exception as exc:
        ui.QMessageBox.warning(
            self,
            "Não foi possível analisar",
            f"{Path(caminho).name}\n\n{exc}",
        )
        return
    finally:
        # Só restaura aqui se ainda houver cursor de espera. O Qt tolera a
        # chamada mesmo quando a exceção já restaurou acima.
        try:
            ui.QApplication.restoreOverrideCursor()
        except Exception:
            pass

    tipo = dados.get("tipo_documento", "desconhecido")
    if tipo == "desconhecido":
        resposta = ui.QMessageBox.question(
            self,
            "Tipo não identificado",
            "Não consegui identificar automaticamente "
            f"{Path(caminho).name}.\n\nEste PDF é um comprovante de pagamento?",
            ui.QMessageBox.StandardButton.Yes
            | ui.QMessageBox.StandardButton.No
            | ui.QMessageBox.StandardButton.Cancel,
        )
        if resposta == ui.QMessageBox.StandardButton.Cancel:
            return
        tipo = (
            "comprovante"
            if resposta == ui.QMessageBox.StandardButton.Yes
            else "conta"
        )

    if tipo == "comprovante":
        self._importar_comprovante(caminho, dados)
        return

    dialog = ui.ContaDialog(
        self,
        pdf_inicial=caminho,
        dados_pdf=dados,
        banco=self.banco,
    )

    def salvar():
        self.banco.adicionar_conta(dialog.dados())
        self.atualizar()

    dialog.accepted.connect(salvar)
    _manter_dialogo_vivo(self, dialog)


_aplicado = False


def aplicar_patches():
    global _aplicado
    if _aplicado:
        return

    # analisar_pdf() consulta _extrair_texto no namespace do próprio módulo,
    # então trocar esta função já adiciona OCR sem reescrever toda a lógica de
    # validação que foi recuperada do executável.
    pdf_reader._extrair_texto = _extrair_texto_com_ocr

    # ui importou analisar_pdf por referência, mas a função usa o namespace de
    # pdf_reader internamente; portanto continua aproveitando o patch de OCR.
    ui.ContaDialog.__init__ = _init_conta_preservando_edicoes
    ui.ContaDialog.carregar_pdf = _carregar_pdf_preservando_campos

    ui.JanelaPrincipal.nova_conta = _nova_conta_nao_modal
    ui.JanelaPrincipal.editar_conta = _editar_conta_nao_modal
    ui.JanelaPrincipal.processar_pdf_solto = _processar_pdf_solto_nao_modal

    _aplicado = True
