from __future__ import annotations

import os
import re
import json
import hashlib
import shutil
import sys
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from . import pdf_reader
from . import paths
from . import ui


# ---------------------------------------------------------------------------
# OCR PARA PDF ESCANEADO / HÍBRIDO
# ---------------------------------------------------------------------------

_extrair_texto_original = pdf_reader._extrair_texto
_analisar_pdf_original = pdf_reader.analisar_pdf
_OCR_CACHE: dict[tuple[str, int, int], dict[str, str]] = {}
_HASH_CACHE: dict[tuple[str, int, int], str] = {}

_ROTULOS_RECEBEDOR = (
    ("beneficiario",),
    ("beneficiaria",),
    ("recebedor",),
    ("favorecido",),
    ("cedente",),
    ("fornecedor",),
    ("prestador",),
    ("emitente",),
    ("razao", "social", "beneficiario"),
)

_ROTULOS_PAGADOR = (
    ("nome", "do", "pagador"),
    ("pagador",),
    ("sacado",),
    ("cliente",),
    ("consumidor",),
    ("titular",),
    ("razao", "social", "pagador"),
)

_ROTULOS_RECEBEDOR_TEXTO = (
    "beneficiario", "beneficiaria", "recebedor", "favorecido", "cedente",
    "fornecedor", "prestador", "emitente",
)
_ROTULOS_PAGADOR_TEXTO = (
    "pagador", "nome do pagador", "sacado", "cliente", "consumidor", "titular",
)

_CORTES_NOME = {
    "cpf", "cnpj", "documento", "endereco", "endereco:", "cep", "cidade",
    "agencia", "conta", "banco", "vencimento", "valor", "codigo", "linha",
    "nosso", "data", "carteira", "especie", "aceite", "numero", "referencia",
}


def _normalizar_ocr(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.casefold()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _localizar_tesseract() -> str | None:
    candidatos: list[Path] = []

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


def _chave_cache(caminho: str | Path) -> tuple[str, int, int]:
    p = Path(caminho).resolve()
    try:
        st = p.stat()
        return str(p), int(st.st_mtime_ns), int(st.st_size)
    except OSError:
        return str(p), 0, 0


def _idiomas_disponiveis(pytesseract) -> list[str | None]:
    try:
        disponiveis = set(pytesseract.get_languages(config=""))
    except Exception:
        disponiveis = set()

    ordem: list[str | None] = []
    if "por" in disponiveis and "eng" in disponiveis:
        ordem.append("por+eng")
    if "por" in disponiveis:
        ordem.append("por")
    if "eng" in disponiveis:
        ordem.append("eng")
    ordem.append(None)

    saida: list[str | None] = []
    for item in ordem:
        if item not in saida:
            saida.append(item)
    return saida


def _token_parecido(a: str, b: str) -> bool:
    a = _normalizar_ocr(a)
    b = _normalizar_ocr(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5:
        return SequenceMatcher(None, a, b).ratio() >= 0.78
    return False


def _achar_rotulo(tokens: list[str], rotulos: tuple[tuple[str, ...], ...]):
    normalizados = [_normalizar_ocr(t) for t in tokens]
    for rotulo in sorted(rotulos, key=len, reverse=True):
        n = len(rotulo)
        for i in range(0, len(tokens) - n + 1):
            if all(_token_parecido(normalizados[i + j], rotulo[j]) for j in range(n)):
                return i, i + n
    return None


def _limpar_nome_ocr(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto or "").strip()

    # OCR costuma transformar bordas de tabelas/caixas em caracteres como
    # ], [, |, }, {, > etc. Se eles vierem antes do nome, removemos todo
    # lixo inicial até o primeiro caractere alfanumérico real.
    texto = re.sub(r"^[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+", "", texto).strip()

    # Às vezes uma borda vertical vira uma letra/dígito isolado antes de um
    # separador, por exemplo "J | RODES...". Esse primeiro caractere é ruído.
    texto = re.sub(
        r"^[A-Za-z0-9]\s*[|¦]\s*(?=[A-Za-zÀ-ÖØ-öø-ÿ]{2,})",
        "",
        texto,
    ).strip()
    if not texto:
        return ""

    # Alguns boletos colocam data/valor e o nome na mesma linha visual.
    # Ex.: "01/09/2026 | RODES ENG TRANSPORTES CNPJ".
    # A data e os separadores à esquerda não pertencem ao nome.
    texto = re.sub(
        r"^(?:(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:[|:;\-–—]\s*)?)+",
        "",
        texto,
    ).strip(" :-|;,.\t[]{}()<>\\/")

    # Remove rótulos que às vezes vêm grudados antes do valor real do campo.
    texto = re.sub(
        r"^(?:(?:BENEFICI[AÁ]RIO|BENEFICI[AÁ]RIA|RECEBEDOR|FAVORECIDO|CEDENTE|FORNECEDOR|PRESTADOR|EMITENTE|PAGADOR|SACADO|CLIENTE|CONSUMIDOR|TITULAR|NOME\s+DO\s+PAGADOR|RAZ[AÃ]O\s+SOCIAL)\s*[:|;\-–—]?\s*)+",
        "",
        texto,
        flags=re.IGNORECASE,
    ).strip(" :-|;,.\t[]{}()<>\\/")

    # Tudo que vier depois de identificadores/campos típicos pertence a outro campo.
    partes = texto.split()
    mantidos: list[str] = []
    for token in partes:
        norm = _normalizar_ocr(token)
        if mantidos and norm in _CORTES_NOME:
            break
        mantidos.append(token)
    texto = " ".join(mantidos).strip(" :-|;,.\t[]{}()<>\\/")

    texto = re.sub(
        r"\s+(?:CPF|CNPJ|CEP|ENDERE[CÇ]O|VENCIMENTO|VALOR|AG[EÊ]NCIA|CONTA|DOCUMENTO|BANCO)\b.*$",
        "",
        texto,
        flags=re.IGNORECASE,
    ).strip(" :-|;,.\t[]{}()<>\\/")

    # Segunda limpeza defensiva depois dos recortes acima. Isso resolve
    # casos como "] | RODES ENG TRANSPORTES" sem alterar o nome real.
    texto = re.sub(r"^[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+", "", texto).strip()
    texto = re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ&/().,'’+\-]+$", "", texto).strip()

    if len(texto) < 3 or len(texto) > 120:
        return ""
    letras = sum(ch.isalpha() for ch in texto)
    digitos = sum(ch.isdigit() for ch in texto)
    if letras < 3 or letras < digitos:
        return ""

    norm = _normalizar_ocr(texto)
    if norm in _ROTULOS_RECEBEDOR_TEXTO or norm in _ROTULOS_PAGADOR_TEXTO:
        return ""
    if any(x in norm for x in ("linha digitavel", "codigo de barras", "data de vencimento", "valor do documento")):
        return ""
    return texto


def _pontuar_nome(texto: str, confianca: float, mesma_linha: bool, distancia: int) -> float:
    if not texto:
        return -999.0
    norm = _normalizar_ocr(texto)
    score = max(0.0, min(100.0, confianca)) * 0.55
    score += 34 if mesma_linha else max(12, 28 - 6 * distancia)
    if any(s in f" {norm} " for s in (" ltda ", " eireli ", " s a ", " sa ", " me ", " mei ")):
        score += 8
    if 5 <= len(texto) <= 80:
        score += 6
    return score


def _extrair_nome_ancorado(linhas: list[dict], rotulos: tuple[tuple[str, ...], ...]):
    candidatos: list[tuple[float, str]] = []

    for idx, linha in enumerate(linhas):
        tokens = linha["tokens"]
        achado = _achar_rotulo(tokens, rotulos)
        if not achado:
            continue
        _, fim = achado

        # 1) Valor à direita do rótulo na mesma linha.
        direita = _limpar_nome_ocr(" ".join(tokens[fim:]))
        if direita:
            candidatos.append(
                (_pontuar_nome(direita, linha["conf"], True, 0), direita)
            )

        # 2) Muitos boletos põem o nome na linha imediatamente abaixo.
        for distancia in (1, 2, 3):
            j = idx + distancia
            if j >= len(linhas):
                break
            prox = linhas[j]
            if prox["pagina"] != linha["pagina"]:
                break

            # Não atravessa para uma região muito distante da página.
            limite_vertical = max(linha["altura"] * 5.0, 125)
            if prox["top"] - linha["bottom"] > limite_vertical * distancia:
                break

            if _achar_rotulo(prox["tokens"], _ROTULOS_RECEBEDOR) or _achar_rotulo(prox["tokens"], _ROTULOS_PAGADOR):
                break

            candidato = _limpar_nome_ocr(prox["texto"])
            if candidato:
                candidatos.append(
                    (_pontuar_nome(candidato, prox["conf"], False, distancia), candidato)
                )
                # A primeira linha útil abaixo costuma ser o valor correto.
                break

    if not candidatos:
        return ""
    candidatos.sort(key=lambda x: x[0], reverse=True)
    melhor_score, melhor = candidatos[0]
    return melhor if melhor_score >= 45 else ""


def _linhas_do_data(data: dict, pagina_num: int) -> list[dict]:
    grupos: dict[tuple[int, int, int], list[dict]] = {}
    total = len(data.get("text", []))
    for i in range(total):
        txt = str(data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1.0
        if conf < 0:
            continue

        chave = (
            int(data.get("block_num", [0] * total)[i]),
            int(data.get("par_num", [0] * total)[i]),
            int(data.get("line_num", [0] * total)[i]),
        )
        grupos.setdefault(chave, []).append(
            {
                "texto": txt,
                "left": int(data["left"][i]),
                "top": int(data["top"][i]),
                "width": int(data["width"][i]),
                "height": int(data["height"][i]),
                "conf": conf,
            }
        )

    linhas: list[dict] = []
    for palavras in grupos.values():
        palavras.sort(key=lambda w: w["left"])
        texto = " ".join(w["texto"] for w in palavras).strip()
        if not texto:
            continue
        top = min(w["top"] for w in palavras)
        bottom = max(w["top"] + w["height"] for w in palavras)
        confs = [w["conf"] for w in palavras if w["conf"] >= 0]
        linhas.append(
            {
                "pagina": pagina_num,
                "texto": texto,
                "tokens": [w["texto"] for w in palavras],
                "left": min(w["left"] for w in palavras),
                "top": top,
                "bottom": bottom,
                "altura": max(1, bottom - top),
                "conf": sum(confs) / len(confs) if confs else 0.0,
            }
        )

    linhas.sort(key=lambda l: (l["pagina"], l["top"], l["left"]))
    return linhas


def _ocr_documento(caminho: str | Path) -> dict[str, str]:
    chave = _chave_cache(caminho)
    if chave in _OCR_CACHE:
        return dict(_OCR_CACHE[chave])

    vazio = {"texto": "", "recebedor": "", "pagador": ""}
    try:
        import cv2
        import fitz
        import numpy as np
        import pytesseract
        from pytesseract import Output
    except Exception:
        return vazio

    executavel = _localizar_tesseract()
    if not executavel:
        return vazio
    pytesseract.pytesseract.tesseract_cmd = executavel

    idiomas = _idiomas_disponiveis(pytesseract)
    textos: list[str] = []
    linhas_todas: list[dict] = []
    documento = None

    try:
        documento = fitz.open(str(caminho))
        for numero_pagina, pagina in enumerate(documento, start=1):
            # ~300-330 DPI. Ajuda bastante em letras pequenas de boletos.
            pixmap = pagina.get_pixmap(matrix=fitz.Matrix(4.0, 4.0), alpha=False)
            imagem = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            if pixmap.n == 4:
                imagem = cv2.cvtColor(imagem, cv2.COLOR_RGBA2BGR)
            else:
                imagem = cv2.cvtColor(imagem, cv2.COLOR_RGB2BGR)

            cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
            cinza = cv2.fastNlMeansDenoising(cinza, None, 7, 7, 21)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            melhorada = clahe.apply(cinza)
            blur = cv2.GaussianBlur(melhorada, (0, 0), 1.1)
            nitida = cv2.addWeighted(melhorada, 1.65, blur, -0.65, 0)
            binaria = cv2.adaptiveThreshold(
                nitida,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                35,
                13,
            )

            # V4 fazia quatro chamadas ao Tesseract por página: duas para
            # texto e duas para posições. image_to_data já devolve texto +
            # coordenadas, então reaproveitamos a mesma leitura. Mantém as
            # duas estratégias (PSM 6 e 11), mas corta pela metade o trabalho.
            idioma_funcional = None
            data_principal = None
            for idioma in idiomas:
                try:
                    kwargs = {
                        "config": "--oem 3 --psm 6",
                        "output_type": Output.DICT,
                    }
                    if idioma:
                        kwargs["lang"] = idioma
                    candidato = pytesseract.image_to_data(nitida, **kwargs)
                    linhas = _linhas_do_data(candidato, numero_pagina)
                    if linhas:
                        idioma_funcional = idioma
                        data_principal = candidato
                        linhas_todas.extend(linhas)
                        textos.extend(linha["texto"] for linha in linhas)
                        break
                except Exception:
                    continue

            # Segunda e última passagem: texto esparso. Ajuda em campos
            # isolados/colunas sem repetir uma terceira ou quarta execução.
            try:
                kwargs = {
                    "config": "--oem 3 --psm 11",
                    "output_type": Output.DICT,
                }
                if idioma_funcional:
                    kwargs["lang"] = idioma_funcional
                data_esparso = pytesseract.image_to_data(binaria, **kwargs)
                linhas = _linhas_do_data(data_esparso, numero_pagina)
                linhas_todas.extend(linhas)
                textos.extend(linha["texto"] for linha in linhas)
            except Exception:
                pass
    except Exception:
        return vazio
    finally:
        if documento is not None:
            try:
                documento.close()
            except Exception:
                pass

    # Remove linhas duplicadas das duas passagens mantendo a ordem.
    vistas: set[str] = set()
    texto_unico: list[str] = []
    for bloco in textos:
        for linha in bloco.splitlines():
            linha = re.sub(r"\s+", " ", linha).strip()
            chave_linha = _normalizar_ocr(linha)
            if linha and chave_linha and chave_linha not in vistas:
                vistas.add(chave_linha)
                texto_unico.append(linha)

    # As passagens PSM 6 e PSM 11 podem devolver a mesma linha duas vezes.
    # Deduplicar evita que um rótulo repetido esconda a linha com o nome logo abaixo.
    linhas_unicas: list[dict] = []
    vistas_linhas: set[tuple[int, str]] = set()
    for linha in linhas_todas:
        chave_linha = (int(linha["pagina"]), _normalizar_ocr(linha["texto"]))
        if not chave_linha[1] or chave_linha in vistas_linhas:
            continue
        vistas_linhas.add(chave_linha)
        linhas_unicas.append(linha)
    linhas_unicas.sort(key=lambda l: (l["pagina"], l["top"], l["left"]))

    resultado = {
        "texto": "\n".join(texto_unico),
        "recebedor": _extrair_nome_ancorado(linhas_unicas, _ROTULOS_RECEBEDOR),
        "pagador": _extrair_nome_ancorado(linhas_unicas, _ROTULOS_PAGADOR),
    }
    _OCR_CACHE.clear()  # evita crescimento indefinido; mantém só o documento atual
    _OCR_CACHE[chave] = dict(resultado)
    return resultado


def _texto_suficiente(texto: str) -> bool:
    compactado = re.sub(r"\s+", "", texto or "")
    return len(compactado) >= 80


def _precisa_ocr_complementar(texto: str) -> bool:
    if not _texto_suficiente(texto):
        return True
    norm = _normalizar_ocr(texto)
    tem_recebedor = any(rotulo in norm for rotulo in _ROTULOS_RECEBEDOR_TEXTO)
    tem_pagador = any(rotulo in norm for rotulo in _ROTULOS_PAGADOR_TEXTO)
    # PDF híbrido: pode trazer linha digitável/data como texto, mas deixar os
    # nomes apenas na imagem. Nesse caso também fazemos OCR complementar.
    return not (tem_recebedor and tem_pagador)


def _ocr_pdf(caminho: str | Path) -> str:
    return _ocr_documento(caminho).get("texto", "")


def _extrair_texto_com_ocr(caminho: str | Path) -> str:
    texto = ""
    try:
        texto = _extrair_texto_original(caminho) or ""
    except Exception:
        texto = ""

    if not _precisa_ocr_complementar(texto):
        return texto

    texto_ocr = _ocr_pdf(caminho)
    if not texto_ocr.strip():
        return texto
    if texto.strip():
        return f"{texto}\n{texto_ocr}"
    return texto_ocr


def _analisar_pdf_com_ocr_melhorado(caminho: str | Path):
    dados = _analisar_pdf_original(caminho)
    if not isinstance(dados, dict):
        return dados

    # Mesmo quando o leitor original encontra um nome, ele pode capturar a linha
    # inteira do boleto. Antes de decidir se precisa chamar o OCR pesado, limpamos
    # os valores existentes. Isso corrige casos como:
    #   01/09/2026 | RODES ENG TRANSPORTES CNPJ
    # para:
    #   RODES ENG TRANSPORTES
    recebedor_atual = str(dados.get("recebedor") or dados.get("fornecedor") or "").strip()
    recebedor_limpo = _limpar_nome_ocr(recebedor_atual)
    if recebedor_limpo:
        dados["recebedor"] = recebedor_limpo
        dados["fornecedor"] = recebedor_limpo

    pagador_atual = str(dados.get("pagador") or "").strip()
    pagador_limpo = _limpar_nome_ocr(pagador_atual)
    if pagador_limpo:
        dados["pagador"] = pagador_limpo

    inseguros = [str(x) for x in dados.get("campos_inseguros", [])]
    falta_recebedor = not str(dados.get("recebedor") or dados.get("fornecedor") or "").strip() or "recebedor" in inseguros
    falta_pagador = not str(dados.get("pagador") or "").strip() or "pagador" in inseguros

    if not (falta_recebedor or falta_pagador):
        return dados

    ocr = _ocr_documento(caminho)
    preenchidos: list[str] = []

    if falta_recebedor and ocr.get("recebedor"):
        dados["recebedor"] = ocr["recebedor"]
        dados["fornecedor"] = ocr["recebedor"]
        preenchidos.append("recebedor")

    if falta_pagador and ocr.get("pagador"):
        dados["pagador"] = ocr["pagador"]
        preenchidos.append("pagador")

    if preenchidos:
        dados["campos_inseguros"] = [x for x in inseguros if x not in preenchidos]
        confianca = dados.get("confianca")
        if isinstance(confianca, dict):
            for campo in preenchidos:
                confianca[campo] = max(float(confianca.get(campo, 0) or 0), 0.90)
        motivos = dados.get("motivos")
        if isinstance(motivos, dict):
            for campo in preenchidos:
                motivos[campo] = "nome localizado por OCR posicional junto ao rótulo do campo"

    return dados


# ---------------------------------------------------------------------------
# VALIDAÇÃO DE DUPLICIDADE REFORÇADA
# ---------------------------------------------------------------------------

_localizar_duplicatas_original = ui.Banco.localizar_duplicatas


def _hash_arquivo(caminho: str | Path) -> str:
    if not caminho:
        return ""
    try:
        p = Path(caminho).resolve()
        st = p.stat()
        if not p.is_file():
            return ""
        chave = (str(p), int(st.st_mtime_ns), int(st.st_size))
        if chave in _HASH_CACHE:
            return _HASH_CACHE[chave]

        h = hashlib.sha256()
        with p.open("rb") as arquivo:
            for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
                h.update(bloco)
        digest = h.hexdigest()
        # Cache pequeno: suficiente para o PDF atual e os candidatos recentes.
        if len(_HASH_CACHE) > 128:
            _HASH_CACHE.clear()
        _HASH_CACHE[chave] = digest
        return digest
    except (OSError, ValueError):
        return ""


def _normalizar_codigo_duplicata(valor) -> str:
    return "".join(ch for ch in str(valor or "").upper() if ch.isalnum())


def _normalizar_nome_duplicata(valor) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).casefold()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    tokens = [t for t in texto.split() if t]

    # S/A, S.A., LTDA etc. variam bastante no OCR e não devem ser o motivo
    # para deixar passar o mesmo documento.
    corporativos = {"sa", "ltda", "eireli", "epp", "mei", "me"}
    compactados: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and tokens[i] == "s" and tokens[i + 1] == "a":
            i += 2
            continue
        if tokens[i] in corporativos:
            i += 1
            continue
        compactados.append(tokens[i])
        i += 1
    return " ".join(compactados)


def _nomes_equivalentes(a, b) -> bool:
    a = _normalizar_nome_duplicata(a)
    b = _normalizar_nome_duplicata(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) >= 8 and (a in b or b in a):
        return True

    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    uniao = ta | tb
    jaccard = len(ta & tb) / len(uniao) if uniao else 0.0
    return ratio >= 0.90 or (ratio >= 0.82 and jaccard >= 0.72)


def _mesmo_pdf_por_conteudo(novo: str | Path, existente: str | Path) -> bool:
    if not novo or not existente:
        return False
    try:
        pn = Path(novo)
        pe = Path(existente)
        if not pn.is_file() or not pe.is_file():
            return False
        # Evita calcular SHA-256 de arquivos que já diferem pelo tamanho.
        if pn.stat().st_size != pe.stat().st_size:
            return False
    except OSError:
        return False

    hn = _hash_arquivo(pn)
    he = _hash_arquivo(pe)
    return bool(hn and he and hn == he)


def _localizar_duplicatas_reforcado(self, dados, ignorar_id=None):
    # Mantém todas as regras originais primeiro.
    encontradas = list(_localizar_duplicatas_original(self, dados, ignorar_id) or [])
    ids = {int(c["id"]) for c in encontradas if c.get("id") is not None}

    novo_pdf = str(dados.get("pdf_conta") or "")
    novo_codigo = _normalizar_codigo_duplicata(dados.get("codigo_pagamento"))
    novo_recebedor = dados.get("fornecedor") or dados.get("recebedor") or ""
    novo_valor = round(float(dados.get("valor") or 0), 2)
    novo_vencimento = str(dados.get("vencimento") or "")
    nova_referencia = str(dados.get("referencia") or "").strip().casefold()

    for conta in self.listar_contas():
        try:
            conta_id = int(conta["id"])
        except Exception:
            continue
        if ignorar_id is not None and conta_id == int(ignorar_id):
            continue
        if conta_id in ids:
            continue

        # Regra mais forte: é literalmente o mesmo arquivo PDF, byte por byte.
        if _mesmo_pdf_por_conteudo(novo_pdf, str(conta.get("pdf_conta") or "")):
            encontradas.append(conta)
            ids.add(conta_id)
            continue

        # Código/linha digitável/PIX idêntico continua sendo prova forte mesmo
        # se o OCR escrever o nome do recebedor de maneira um pouco diferente.
        codigo_existente = _normalizar_codigo_duplicata(conta.get("codigo_pagamento"))
        if (
            len(novo_codigo) >= 20
            and len(codigo_existente) >= 20
            and novo_codigo == codigo_existente
        ):
            encontradas.append(conta)
            ids.add(conta_id)
            continue

        # Fallback para OCR: valor + vencimento precisam ser exatos; o nome
        # pode variar apenas o suficiente para cobrir pontuação/sufixos/erros
        # pequenos de reconhecimento. A referência, quando existe nos dois,
        # também precisa bater.
        try:
            mesmo_valor = round(float(conta.get("valor") or 0), 2) == novo_valor
        except Exception:
            mesmo_valor = False
        mesmo_vencimento = str(conta.get("vencimento") or "") == novo_vencimento
        ref_existente = str(conta.get("referencia") or "").strip().casefold()
        referencia_compativel = (
            not nova_referencia
            or not ref_existente
            or nova_referencia == ref_existente
        )

        if (
            mesmo_valor
            and mesmo_vencimento
            and referencia_compativel
            and _nomes_equivalentes(novo_recebedor, conta.get("fornecedor") or "")
        ):
            encontradas.append(conta)
            ids.add(conta_id)

    return encontradas


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
# ORGANIZAÇÃO DE DOCUMENTOS POR VENCIMENTO
# ---------------------------------------------------------------------------

_CONFIG_DOCUMENTOS = paths.APP_DATA_DIR / "config_documentos.json"
_MESES = (
    "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
)

_dados_conta_original = ui.ContaDialog.dados
_validar_conta_original = ui.ContaDialog.validar
_dados_pagamento_original = ui.PagamentoDialog.dados
_validar_pagamento_original = ui.PagamentoDialog.validar
_banco_adicionar_conta_original = ui.Banco.adicionar_conta
_banco_atualizar_conta_original = ui.Banco.atualizar_conta
_banco_registrar_pagamento_original = ui.Banco.registrar_pagamento
_janela_init_original = ui.JanelaPrincipal.__init__
_excluir_conta_original = ui.JanelaPrincipal.excluir_conta


def _ler_pasta_base() -> Path | None:
    try:
        if not _CONFIG_DOCUMENTOS.is_file():
            return None
        dados = json.loads(_CONFIG_DOCUMENTOS.read_text(encoding="utf-8"))
        valor = str(dados.get("pasta_base") or "").strip()
        return Path(valor) if valor else None
    except Exception:
        return None


def _salvar_pasta_base(pasta: Path) -> None:
    paths.APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_DOCUMENTOS.write_text(
        json.dumps({"pasta_base": str(pasta)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalizar_base_escolhida(pasta: Path) -> Path:
    # Se o usuário apontar por engano para "BOLETOS 2026", guardamos o pai.
    if re.fullmatch(r"BOLETOS\s+\d{4}", pasta.name.strip(), flags=re.IGNORECASE):
        return pasta.parent
    return pasta


def _selecionar_pasta_base(parent=None) -> Path | None:
    atual = _ler_pasta_base()
    inicial = str(atual or (Path.home() / "Documents"))
    escolhido = ui.QFileDialog.getExistingDirectory(
        parent,
        "Escolha a pasta-base dos boletos",
        inicial,
    )
    if not escolhido:
        return None

    pasta = _normalizar_base_escolhida(Path(escolhido).resolve())
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        _salvar_pasta_base(pasta)
    except OSError as exc:
        ui.QMessageBox.critical(
            parent,
            "Pasta indisponível",
            "Não foi possível usar essa pasta para organizar os documentos.\n\n"
            f"Detalhe: {exc}",
        )
        return None
    return pasta


def _obter_pasta_base(parent=None, perguntar: bool = True) -> Path | None:
    pasta = _ler_pasta_base()
    if pasta:
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            return pasta
        except OSError:
            pass
    if perguntar:
        return _selecionar_pasta_base(parent)
    return None


def _pasta_do_vencimento(base: Path, vencimento: str) -> Path:
    try:
        data = datetime.strptime(str(vencimento), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("O vencimento da conta não é uma data válida.") from exc

    pasta = base / f"BOLETOS {data.year}" / f"{data.month:02d} - {_MESES[data.month - 1]}"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _nome_documento(nome_base: str, fallback: str = "DOCUMENTO") -> str:
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(nome_base or ""))
    nome = " ".join(nome.split()).strip().rstrip(".")
    if nome.casefold().endswith(".pdf"):
        nome = nome[:-4].rstrip()
    nome = nome[:120].strip() or fallback
    return f"{nome}.pdf"


def _destino_disponivel(pasta: Path, nome_arquivo: str, origem: Path | None = None) -> Path:
    destino = pasta / nome_arquivo
    try:
        if origem and destino.exists() and destino.resolve() == origem.resolve():
            return destino
    except OSError:
        pass

    if not destino.exists():
        return destino

    base = destino.stem
    ext = destino.suffix or ".pdf"
    contador = 1
    while True:
        candidato = pasta / f"{base} ({contador}){ext}"
        if not candidato.exists():
            return candidato
        contador += 1


def _mover_pdf_cobranca(
    origem: str | Path,
    vencimento: str,
    observacoes: str,
    fornecedor: str,
    parent=None,
) -> tuple[str, str]:
    """Move o PDF para ANO/MÊS. Retorna (destino, origem_original)."""
    if not origem:
        return "", ""

    src = Path(origem)
    if not src.is_file():
        return str(src), str(src)

    base = _obter_pasta_base(parent, perguntar=True)
    if base is None:
        raise RuntimeError("Seleção da pasta de documentos cancelada.")

    pasta = _pasta_do_vencimento(base, vencimento)
    nome = _nome_documento(observacoes, fornecedor or src.stem or "BOLETO")
    destino = _destino_disponivel(pasta, nome, src)

    try:
        if src.resolve() == destino.resolve():
            return str(destino), str(src)
    except OSError:
        pass

    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(destino))
    return str(destino), str(src)


def _desfazer_movimento(destino: str, origem: str) -> None:
    if not destino or not origem or destino == origem:
        return
    dst = Path(destino)
    src = Path(origem)
    try:
        if dst.is_file() and not src.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
    except OSError:
        pass


def _dados_conta_sem_copia(self, copiar_pdf=True):
    # A cópia antiga é desativada. O Banco move o arquivo somente depois que
    # o usuário confirma o cadastro/edição e organiza pelo vencimento.
    return _dados_conta_original(self, copiar_pdf=False)


def _validar_conta_com_pasta(self):
    # Se existe PDF, garantimos antes do accept() que a pasta-base foi definida.
    # Cancelar o seletor mantém o formulário aberto e não move nenhum arquivo.
    if getattr(self, "pdf_origem", "") and _obter_pasta_base(self, perguntar=False) is None:
        if _selecionar_pasta_base(self) is None:
            return
    return _validar_conta_original(self)


def _banco_adicionar_conta_organizada(self, dados):
    dados_novos = dict(dados)
    origem = str(dados_novos.get("pdf_conta") or "")
    destino = origem

    if origem:
        destino, origem_original = _mover_pdf_cobranca(
            origem,
            str(dados_novos.get("vencimento") or ""),
            str(dados_novos.get("observacoes") or ""),
            str(dados_novos.get("fornecedor") or ""),
            ui.QApplication.activeWindow(),
        )
        dados_novos["pdf_conta"] = destino
    else:
        origem_original = ""

    try:
        return _banco_adicionar_conta_original(self, dados_novos)
    except Exception:
        _desfazer_movimento(destino, origem_original)
        raise


def _banco_atualizar_conta_organizada(self, conta_id, dados):
    dados_novos = dict(dados)
    origem = str(dados_novos.get("pdf_conta") or "")
    destino = origem

    if origem:
        destino, origem_original = _mover_pdf_cobranca(
            origem,
            str(dados_novos.get("vencimento") or ""),
            str(dados_novos.get("observacoes") or ""),
            str(dados_novos.get("fornecedor") or ""),
            ui.QApplication.activeWindow(),
        )
        dados_novos["pdf_conta"] = destino
    else:
        origem_original = ""

    try:
        return _banco_atualizar_conta_original(self, conta_id, dados_novos)
    except Exception:
        _desfazer_movimento(destino, origem_original)
        raise


def _dados_pagamento_sem_copia(self):
    dados = self.dados_parciais() or {}
    resultado, detalhes = ui.comparar_pagamento(self.conta, dados)
    return {
        **dados,
        "pdf_comprovante": str(getattr(self, "pdf_origem", "") or ""),
        "resultado_conferencia": resultado,
        "detalhes_conferencia": detalhes,
    }


def _validar_pagamento_com_pasta(self):
    if getattr(self, "pdf_origem", "") and _obter_pasta_base(self, perguntar=False) is None:
        if _selecionar_pasta_base(self) is None:
            return
    return _validar_pagamento_original(self)


def _atualizar_caminho_pdf_conta(banco, conta_id: int, caminho: str) -> None:
    with banco.conexao() as conn:
        conn.execute(
            "UPDATE contas SET pdf_conta = ?, atualizado_em = ? WHERE id = ?",
            (caminho, datetime.now().isoformat(timespec="seconds"), int(conta_id)),
        )


def _mesclar_pdfs_atomico(pdf_conta: Path, comprovante: Path) -> tuple[Path, Path]:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF não está disponível para mesclar os PDFs.") from exc

    backup = pdf_conta.with_name(f".{pdf_conta.stem}.backup_pagamento.pdf")
    temp = pdf_conta.with_name(f".{pdf_conta.stem}.mesclando.pdf")
    contador = 1
    while backup.exists() or temp.exists():
        backup = pdf_conta.with_name(f".{pdf_conta.stem}.backup_pagamento_{contador}.pdf")
        temp = pdf_conta.with_name(f".{pdf_conta.stem}.mesclando_{contador}.pdf")
        contador += 1

    shutil.copy2(pdf_conta, backup)
    saida = None
    doc_conta = None
    doc_comprovante = None
    try:
        doc_conta = fitz.open(str(pdf_conta))
        doc_comprovante = fitz.open(str(comprovante))
        paginas_esperadas = doc_conta.page_count + doc_comprovante.page_count

        saida = fitz.open()
        saida.insert_pdf(doc_conta)
        saida.insert_pdf(doc_comprovante)
        saida.save(str(temp), garbage=4, deflate=True)
        saida.close()
        saida = None
        doc_conta.close()
        doc_conta = None
        doc_comprovante.close()
        doc_comprovante = None

        teste = fitz.open(str(temp))
        paginas_gravadas = teste.page_count
        teste.close()
        if paginas_gravadas != paginas_esperadas:
            raise RuntimeError("A quantidade de páginas do PDF mesclado não confere.")

        os.replace(str(temp), str(pdf_conta))
        return pdf_conta, backup
    except Exception:
        for doc in (saida, doc_conta, doc_comprovante):
            try:
                if doc is not None:
                    doc.close()
            except Exception:
                pass
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        try:
            if backup.exists():
                os.replace(str(backup), str(pdf_conta))
        except OSError:
            pass
        raise


def _banco_registrar_pagamento_mesclado(self, conta_id, dados):
    dados_novos = dict(dados)
    comprovante_txt = str(dados_novos.get("pdf_comprovante") or "").strip()
    if not comprovante_txt:
        return _banco_registrar_pagamento_original(self, conta_id, dados_novos)

    comprovante = Path(comprovante_txt)
    if not comprovante.is_file():
        return _banco_registrar_pagamento_original(self, conta_id, dados_novos)

    conta = self.buscar_conta(int(conta_id))
    if not conta:
        return _banco_registrar_pagamento_original(self, conta_id, dados_novos)

    parent = ui.QApplication.activeWindow()
    pdf_conta_txt = str(conta.get("pdf_conta") or "").strip()
    pdf_conta = Path(pdf_conta_txt) if pdf_conta_txt else None
    origem_conta = pdf_conta_txt
    conta_movida = False
    comprovante_movido = False
    comprovante_destino = ""
    comprovante_origem = ""
    backup = None

    try:
        # Contas antigas também passam a ser organizadas na nova estrutura no
        # primeiro pagamento/edição em que forem tocadas.
        if pdf_conta and pdf_conta.is_file():
            destino_conta, origem_real = _mover_pdf_cobranca(
                pdf_conta,
                str(conta.get("vencimento") or ""),
                str(conta.get("observacoes") or ""),
                str(conta.get("fornecedor") or ""),
                parent,
            )
            pdf_conta = Path(destino_conta)
            conta_movida = destino_conta != origem_real
            if destino_conta != pdf_conta_txt:
                _atualizar_caminho_pdf_conta(self, int(conta_id), destino_conta)

        if pdf_conta and pdf_conta.is_file():
            try:
                mesmo_arquivo = pdf_conta.resolve() == comprovante.resolve()
            except OSError:
                mesmo_arquivo = False

            if not mesmo_arquivo:
                pdf_conta, backup = _mesclar_pdfs_atomico(pdf_conta, comprovante)
            dados_novos["pdf_comprovante"] = str(pdf_conta)
        else:
            # Conta sem PDF: o comprovante vira o documento único daquela conta.
            destino, origem_comprovante = _mover_pdf_cobranca(
                comprovante,
                str(conta.get("vencimento") or ""),
                str(conta.get("observacoes") or "") or str(conta.get("fornecedor") or ""),
                str(conta.get("fornecedor") or ""),
                parent,
            )
            comprovante_destino = destino
            comprovante_origem = origem_comprovante
            comprovante_movido = destino != origem_comprovante
            pdf_conta = Path(destino)
            _atualizar_caminho_pdf_conta(self, int(conta_id), destino)
            dados_novos["pdf_comprovante"] = destino
            comprovante = Path(origem_comprovante)

        resultado = _banco_registrar_pagamento_original(self, conta_id, dados_novos)

        # Só removemos o comprovante avulso depois de o PDF final e o banco
        # estarem confirmados. O conteúdo dele já está dentro do PDF da conta.
        if pdf_conta:
            try:
                if comprovante.is_file() and comprovante.resolve() != pdf_conta.resolve():
                    comprovante.unlink()
            except OSError:
                pass
        if backup:
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
        return resultado

    except Exception:
        if backup and backup.exists() and pdf_conta:
            try:
                os.replace(str(backup), str(pdf_conta))
            except OSError:
                pass
        if conta_movida and pdf_conta and origem_conta:
            _desfazer_movimento(str(pdf_conta), origem_conta)
            try:
                _atualizar_caminho_pdf_conta(self, int(conta_id), origem_conta)
            except Exception:
                pass
        if comprovante_movido and comprovante_destino and comprovante_origem:
            _desfazer_movimento(comprovante_destino, comprovante_origem)
            try:
                _atualizar_caminho_pdf_conta(self, int(conta_id), origem_conta)
            except Exception:
                pass
        raise


def _configurar_pasta_documentos(self):
    atual = _ler_pasta_base()
    nova = _selecionar_pasta_base(self)
    if nova is None:
        return
    self._patch_btn_pasta.setToolTip(f"Pasta-base atual:\n{nova}")
    if atual and atual != nova:
        ui.QMessageBox.information(
            self,
            "Pasta alterada",
            "A nova pasta-base foi salva. Novos documentos e documentos alterados "
            "passarão a usar esse local. Os arquivos antigos não foram movidos automaticamente.",
        )
    else:
        ui.QMessageBox.information(
            self,
            "Pasta configurada",
            "Pasta-base salva. O sistema criará automaticamente BOLETOS ANO e as pastas dos meses.",
        )


def _janela_init_com_pasta(self, *args, **kwargs):
    _janela_init_original(self, *args, **kwargs)

    try:
        header = self.findChild(ui.QFrame, "headerPanel")
        layout = header.layout() if header else None
        if layout is None:
            return

        botao = ui.QPushButton("📁  Pasta de boletos")
        botao.setObjectName("secondaryButton")
        pasta = _ler_pasta_base()
        botao.setToolTip(
            f"Pasta-base atual:\n{pasta}" if pasta else
            "Escolha onde o sistema deve criar as pastas BOLETOS ANO / MÊS."
        )
        botao.clicked.connect(self._patch_configurar_pasta_documentos)
        self._patch_btn_pasta = botao

        indice_nova = -1
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, ui.QPushButton) and "Nova conta" in widget.text():
                indice_nova = i
                break
        if indice_nova >= 0:
            layout.insertWidget(indice_nova, botao)
        else:
            layout.addWidget(botao)
    except Exception:
        pass


def _excluir_conta_mensagem_atualizada(self):
    conta = self.conta_selecionada()
    if not conta:
        return
    resposta = ui.QMessageBox.question(
        self,
        "Excluir conta",
        f"Excluir definitivamente a conta de {conta['fornecedor']}?\n\n"
        "O PDF organizado permanecerá na pasta de boletos.",
    )
    if resposta == ui.QMessageBox.StandardButton.Yes:
        self.banco.excluir_conta(int(conta["id"]))
        self.atualizar()


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
    pdf_reader.analisar_pdf = _analisar_pdf_com_ocr_melhorado
    # ui importou analisar_pdf diretamente no módulo, então atualizamos também
    # essa referência para que o fallback posicional de nomes seja utilizado.
    ui.analisar_pdf = _analisar_pdf_com_ocr_melhorado

    ui.Banco.localizar_duplicatas = _localizar_duplicatas_reforcado
    ui.Banco.adicionar_conta = _banco_adicionar_conta_organizada
    ui.Banco.atualizar_conta = _banco_atualizar_conta_organizada
    ui.Banco.registrar_pagamento = _banco_registrar_pagamento_mesclado

    ui.ContaDialog.__init__ = _init_conta_preservando_edicoes
    ui.ContaDialog.carregar_pdf = _carregar_pdf_preservando_campos
    ui.ContaDialog.dados = _dados_conta_sem_copia
    ui.ContaDialog.validar = _validar_conta_com_pasta

    ui.PagamentoDialog.dados = _dados_pagamento_sem_copia
    ui.PagamentoDialog.validar = _validar_pagamento_com_pasta

    ui.JanelaPrincipal.__init__ = _janela_init_com_pasta
    ui.JanelaPrincipal._patch_configurar_pasta_documentos = _configurar_pasta_documentos
    ui.JanelaPrincipal.nova_conta = _nova_conta_nao_modal
    ui.JanelaPrincipal.editar_conta = _editar_conta_nao_modal
    ui.JanelaPrincipal.processar_pdf_solto = _processar_pdf_solto_nao_modal
    ui.JanelaPrincipal.excluir_conta = _excluir_conta_mensagem_atualizada

    _aplicado = True
