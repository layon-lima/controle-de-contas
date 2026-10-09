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
from .layout_learning_v2 import LayoutLearningV2


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
    ("nome", "pagador"),
    ("pagador",),
    ("sacado",),
    ("nome", "do", "cliente"),
    ("cliente",),
    ("consumidor",),
    ("titular",),
    ("razao", "social", "pagador"),
    ("razao", "social", "cliente"),
)

_ROTULOS_RECEBEDOR_TEXTO = (
    "beneficiario", "beneficiaria", "recebedor", "favorecido", "cedente",
    "fornecedor", "prestador", "emitente",
)
_ROTULOS_PAGADOR_TEXTO = (
    "pagador", "nome do pagador", "nome pagador", "sacado", "cliente",
    "nome do cliente", "consumidor", "titular",
)

_ROTULOS_REFERENCIA = (
    ("referencia",),
    ("mes", "referencia"),
    ("mes", "de", "referencia"),
    ("mes", "ano"),
    ("competencia",),
    ("periodo", "de", "apuracao"),
    ("periodo", "apuracao"),
    ("periodo", "de", "referencia"),
    ("periodo", "referencia"),
    ("numero", "de", "referencia"),
    ("numero", "da", "fatura"),
    ("numero", "fatura"),
    ("seu", "numero"),
)

_FRASES_NAO_NOME = (
    "voce podera",
    "podera ter",
    "fornecimento de",
    "suspensao do",
    "aviso de",
    "valor a pagar",
    "valor total",
    "total a pagar",
    "data de vencimento",
    "mes referencia",
    "mes de referencia",
    "periodo de referencia",
    "consumo faturado",
    "leitura anterior",
    "leitura atual",
    "proxima leitura",
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

    # Em build empacotado, prioriza SEMPRE o Tesseract que acompanha o app.
    # A versao anterior colocava o executavel encontrado no PATH em primeiro
    # lugar; se o Windows tivesse um Tesseract apenas com ENG, o OCR do app
    # ignorava o POR empacotado e regredia em DARF/ITR escaneado.
    no_path = shutil.which("tesseract")
    if no_path:
        candidatos.append(Path(no_path))

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


def _parece_nome_entidade(texto: str) -> bool:
    """Valida se um trecho realmente parece nome de pessoa/empresa.

    Evita aceitar como pagador/recebedor linhas de fatura contendo competencia,
    valores monetarios, avisos ao consumidor ou frases corridas.
    """
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if len(texto) < 3 or len(texto) > 120:
        return False

    norm = _normalizar_ocr(texto)
    if not norm:
        return False

    # Uma referencia/competencia ou data no inicio nao e nome de entidade.
    if re.match(r"^\s*(?:0?[1-9]|1[0-2])\s*[/.-]\s*(?:19|20)\d{2}\b", texto):
        return False
    if re.match(r"^\s*\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*(?:19|20)?\d{2}\b", texto):
        return False

    # Nome de pessoa/empresa nao deve carregar valor monetario no meio.
    if re.search(r"(?:R\$\s*)?\d{1,3}(?:\.\d{3})*,\d{2}\b", texto, re.I):
        return False

    if any(frase in norm for frase in _FRASES_NAO_NOME):
        return False

    # Frases longas com verbos/pronomes tipicos de avisos sao rejeitadas.
    palavras = norm.split()
    if len(palavras) > 12:
        return False
    palavras_frase = {"voce", "podera", "devera", "ter", "sera", "foi", "para", "ate", "apos"}
    if len(set(palavras) & palavras_frase) >= 2:
        return False

    letras = sum(ch.isalpha() for ch in texto)
    digitos = sum(ch.isdigit() for ch in texto)
    if letras < 3 or letras < digitos:
        return False

    # Linhas com pontuacao de frase costumam ser aviso/texto corrido.
    if re.search(r"[.!?]{2,}", texto):
        return False
    return True


def _limpar_referencia_ocr(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto or "").strip(" :-|;,\t[]{}()<>\\/")
    if not texto:
        return ""

    texto = re.sub(
        r"^(?:(?:REFER[EÊ]NCIA|M[EÊ]S\s+DE\s+REFER[EÊ]NCIA|M[EÊ]S\s+REFER[EÊ]NCIA|M[EÊ]S\s*/?\s*ANO|COMPET[EÊ]NCIA|PER[IÍ]ODO\s+DE\s+REFER[EÊ]NCIA|N[ÚU]MERO\s+DE\s+REFER[EÊ]NCIA|N[ÚU]MERO\s+DA\s+FATURA|SEU\s+N[ÚU]MERO)\s*[:|;\-–—]?\s*)+",
        "",
        texto,
        flags=re.IGNORECASE,
    ).strip(" :-|;,\t[]{}()<>\\/")

    # Prioridade: competencia MM/AAAA, formato mais comum no campo Referencia.
    m = re.search(r"(?<!\d)(0?[1-9]|1[0-2])\s*[/._-]\s*((?:19|20)\d{2})(?!\d)", texto)
    if m:
        return f"{int(m.group(1)):02d}/{m.group(2)}"

    # Tambem aceita AAAA-MM e normaliza para MM/AAAA.
    m = re.search(r"(?<!\d)((?:19|20)\d{2})\s*[/._-]\s*(0?[1-9]|1[0-2])(?!\d)", texto)
    if m:
        return f"{int(m.group(2)):02d}/{m.group(1)}"

    # Numero/codigo de referencia explicitamente rotulado.
    candidato = texto.strip(" .,:;|-_")
    if not (3 <= len(candidato) <= 50):
        return ""
    if re.search(r"\b\d{1,2}/\d{1,2}/(?:19|20)?\d{2}\b", candidato):
        return ""
    if re.search(r"(?:R\$\s*)?\d{1,3}(?:\.\d{3})*,\d{2}\b", candidato, re.I):
        return ""
    if not any(ch.isdigit() for ch in candidato):
        return ""
    norm = _normalizar_ocr(candidato)
    if any(frase in norm for frase in _FRASES_NAO_NOME):
        return ""
    return candidato


def _extrair_referencia_ancorada(linhas: list[dict]) -> str:
    candidatos: list[tuple[float, str]] = []
    for idx, linha in enumerate(linhas):
        achado = _achar_rotulo(linha["tokens"], _ROTULOS_REFERENCIA)
        if not achado:
            continue
        _, fim = achado

        direita = _limpar_referencia_ocr(" ".join(linha["tokens"][fim:]))
        if direita:
            candidatos.append((100.0 + float(linha.get("conf", 0)) * 0.1, direita))

        for distancia in (1, 2):
            j = idx + distancia
            if j >= len(linhas):
                break
            prox = linhas[j]
            if prox["pagina"] != linha["pagina"]:
                break
            if prox["top"] - linha["bottom"] > max(linha["altura"] * 5.0, 125) * distancia:
                break
            candidato = _limpar_referencia_ocr(prox["texto"])
            if candidato:
                candidatos.append((75.0 - 8.0 * distancia + float(prox.get("conf", 0)) * 0.1, candidato))
                break

    if not candidatos:
        return ""
    candidatos.sort(key=lambda item: item[0], reverse=True)
    return candidatos[0][1]


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
    if not _parece_nome_entidade(texto):
        return ""
    return texto


def _pontuar_nome(texto: str, confianca: float, mesma_linha: bool, distancia: int) -> float:
    if not texto or not _parece_nome_entidade(texto):
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

    vazio = {"texto": "", "recebedor": "", "pagador": "", "referencia": ""}
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
        "referencia": _extrair_referencia_ancorada(linhas_unicas),
    }
    _OCR_CACHE.clear()  # evita crescimento indefinido; mantém só o documento atual
    _OCR_CACHE[chave] = dict(resultado)
    return resultado


def _enriquecer_darf_ocr(dados: dict, ocr: dict) -> tuple[dict, set[str]]:
    """Confirma campos de DARF quando o PDF e imagem/scaneado.

    Esta regra so entra quando o proprio OCR identifica um Documento de
    Arrecadacao de Receitas Federais. Ela roda depois do aprendizado para que
    uma leitura oficial/deterministica nao possa ser sobrescrita por um perfil
    antigo contaminado.
    """
    texto = str((ocr or {}).get("texto") or "")
    if not texto.strip():
        return dados, set()

    texto_norm = _normalizar_ocr(texto)

    # Nao dependa de uma frase OCR 100% correta para reconhecer DARF.
    # Com Tesseract somente ENG, por exemplo, "arrecadacao" costuma virar
    # "arrecadagao" e "apuracao" vira "apuragao". O layout continua sendo
    # inequivocamente um DARF/ITR; por isso usamos marcadores estruturais com
    # comparacao tolerante token a token.
    tokens_doc = texto_norm.split()

    def tem_frase(*frases: tuple[str, ...]) -> bool:
        return _achar_rotulo(tokens_doc, tuple(frases)) is not None

    marcador_arrecadacao = tem_frase(("documento", "de", "arrecadacao"))
    marcador_receita = tem_frase(("receita", "federal"), ("receitas", "federais"))
    marcador_periodo = tem_frase(("periodo", "de", "apuracao"))
    marcador_composicao = tem_frase(("composicao", "do", "documento", "de", "arrecadacao"))
    marcador_vencimento = tem_frase(("data", "de", "vencimento"), ("pagar", "este", "documento", "ate"), ("pagar", "ate"))
    marcador_itr = (
        "1070" in tokens_doc
        and ("itr" in tokens_doc or tem_frase(("propriedade", "territorial", "rural")))
    )
    marcador_senda = any(_token_parecido(tok, "senda") for tok in tokens_doc)

    marcadores = sum(bool(x) for x in (
        marcador_arrecadacao, marcador_receita, marcador_periodo,
        marcador_composicao, marcador_vencimento, marcador_itr, marcador_senda,
    ))
    marcador_federal_forte = marcador_receita or marcador_itr or marcador_senda

    # Exige um sinal federal forte + pelo menos tres marcadores independentes.
    # Assim toleramos OCR ruim sem transformar boletos comuns em DARF.
    if not (marcador_federal_forte and marcadores >= 3):
        return dados, set()

    linhas = [re.sub(r"\s+", " ", l).strip() for l in texto.splitlines() if l.strip()]
    fortes: set[str] = set()

    # RECEBEDOR: o documento se identifica como arrecadacao de Receitas
    # Federais e a marca Receita Federal tambem aparece no proprio OCR.
    dados["recebedor"] = "RECEITA FEDERAL"
    dados["fornecedor"] = "RECEITA FEDERAL"
    fortes.add("recebedor")

    # PAGADOR: aceita CPF ou CNPJ seguido do nome, tolerando bordas da tabela
    # que o OCR costuma transformar em ], |, } etc.
    doc_re = re.compile(
        r"(?:\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2})"
    )
    pagador = ""
    for linha in linhas:
        m = doc_re.search(linha)
        if not m:
            continue
        cauda = linha[m.end():]
        cauda = re.sub(r"^[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+", "", cauda).strip()
        candidato = _limpar_nome_ocr(cauda)
        if candidato and _parece_nome_entidade(candidato):
            pagador = candidato
            break

    # OCR pode separar CPF e Nome em linhas diferentes. Procura o bloco
    # rotulado "CPF Nome" e considera as proximas linhas antes de desistir.
    if not pagador:
        for i, linha in enumerate(linhas):
            if "cpf nome" not in _normalizar_ocr(linha):
                continue
            bloco = " ".join(linhas[i + 1:i + 4])
            m = doc_re.search(bloco)
            if not m:
                continue
            candidato = _limpar_nome_ocr(bloco[m.end():])
            if candidato and _parece_nome_entidade(candidato):
                pagador = candidato
                break

    if pagador:
        dados["pagador"] = pagador
        fortes.add("pagador")

    # REFERENCIA: no DARF deste layout o Periodo de Apuracao e uma data
    # completa. O campo Referencia do app usa somente MM/AAAA.
    referencia = ""
    for i, linha in enumerate(linhas):
        tokens_linha = _normalizar_ocr(linha).split()
        if _achar_rotulo(tokens_linha, (("periodo", "de", "apuracao"),)) is None:
            continue
        bloco = " ".join(linhas[i:i + 4])
        m = re.search(r"(?<!\d)(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/((?:19|20)\d{2})(?!\d)", bloco)
        if m:
            referencia = f"{int(m.group(2)):02d}/{m.group(3)}"
            break
    if not referencia:
        referencia = _limpar_referencia_ocr(str((ocr or {}).get("referencia") or ""))
    if referencia:
        dados["referencia"] = referencia
        fortes.add("referencia")

    # VENCIMENTO: a segunda passagem do OCR costuma trazer a linha compacta
    # "Pagar ate: DD/MM/AAAA", mesmo quando o quadro superior foi quebrado.
    vencimento = ""
    padroes_venc = (
        r"(?i)Pagar\s+at[eé]\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
        r"(?i)Data\s+de\s+Vencimento\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
    )
    for padrao in padroes_venc:
        m = re.search(padrao, texto)
        if m:
            try:
                vencimento = datetime.strptime(m.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                vencimento = ""
            if vencimento:
                break
    if vencimento:
        dados["vencimento"] = vencimento
        fortes.add("vencimento")

    # VALOR: no rodape destacado do DARF o OCR retorna "Valor: 10,00".
    # Mantemos a regra restrita ao DARF para nao confundir outros numeros.
    valor = None
    for padrao in (
        r"(?i)\bValor\s*[:.\-]?\s*(?:R\$\s*)?([0-9.]+\s*,\s*[0-9]{2})",
        r"(?i)Valor\s+Total\s+do\s+Documento\s*[:\-]?\s*(?:R\$\s*)?([0-9.]+\s*,\s*[0-9]{2})",
    ):
        m = re.search(padrao, texto)
        if m:
            valor = _valor_br_para_float(re.sub(r"\s+", "", m.group(1)))
            if valor is not None and valor > 0:
                break
            valor = None
    if valor is not None and valor > 0:
        dados["valor"] = valor
        fortes.add("valor")

    dados["tipo_documento"] = "conta"

    if fortes:
        inseguros = [str(x) for x in dados.get("campos_inseguros", [])]
        dados["campos_inseguros"] = [x for x in inseguros if x not in fortes]
        confianca = dados.get("confianca")
        if isinstance(confianca, dict):
            for campo in fortes:
                confianca[campo] = max(float(confianca.get(campo, 0) or 0), 0.99)
        motivos = dados.get("motivos")
        if isinstance(motivos, dict):
            for campo in fortes:
                motivos[campo] = "campo confirmado por regra estrutural DARF no OCR"

    return dados, fortes


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



def _valor_br_para_float(texto: str) -> float | None:
    bruto = re.sub(r"[^0-9,.-]", "", str(texto or "")).strip()
    if not bruto:
        return None
    try:
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        return round(float(bruto), 2)
    except ValueError:
        return None


def _extrair_valor_apos_rotulo(linhas: list[str], rotulos: tuple[str, ...], limpador):
    normalizadas = [_normalizar_ocr(l) for l in linhas]
    for i, norm in enumerate(normalizadas):
        for rotulo in rotulos:
            rnorm = _normalizar_ocr(rotulo)
            if rnorm not in norm:
                continue

            # Primeiro tenta o que vier depois do próprio rótulo na mesma linha.
            pos = norm.find(rnorm)
            if pos >= 0:
                # Usa a linha original para preservar pontuação/acentos.
                original = linhas[i]
                m = re.search(re.escape(rotulo), original, flags=re.IGNORECASE)
                if m:
                    candidato = limpador(original[m.end():])
                    if candidato:
                        return candidato

            # Depois tenta as próximas linhas úteis.
            for j in range(i + 1, min(len(linhas), i + 4)):
                candidato = limpador(linhas[j])
                if candidato:
                    return candidato
    return None


def _enriquecer_documento_textual(caminho: str | Path, dados: dict) -> tuple[dict, set[str]]:
    """Extrai campos fortes de guias/faturas que não seguem layout de boleto.

    Dá prioridade a rótulos explícitos presentes na camada de texto do PDF.
    Inclui tratamentos determinísticos para guias governamentais (FGTS Digital,
    DARF/Documento de Arrecadação de Receitas Federais) antes de recorrer ao OCR.
    """
    try:
        texto = _extrair_texto_original(caminho) or ""
    except Exception:
        texto = ""
    if not texto.strip():
        return dados, set()

    linhas = [re.sub(r"\s+", " ", l).strip() for l in texto.splitlines() if l.strip()]
    texto_norm = _normalizar_ocr(texto)
    fortes: set[str] = set()

    def nome_valido(t: str) -> str:
        candidato = _limpar_nome_ocr(t)
        return candidato if candidato and _parece_nome_entidade(candidato) else ""

    # ------------------------------------------------------------------
    # DARF / DOCUMENTO DE ARRECADAÇÃO DE RECEITAS FEDERAIS
    # ------------------------------------------------------------------
    # A extração de texto desses PDFs costuma vir fora da ordem visual:
    # o CNPJ + Razão Social podem aparecer ANTES dos rótulos "CNPJ Razão
    # Social". Por isso não usamos apenas "valor depois do rótulo".
    eh_darf = "documento de arrecadacao de receitas federais" in texto_norm
    if eh_darf:
        # PAGADOR/CONTRIBUINTE: DARF pode ser emitido tanto para CNPJ quanto
        # para CPF. O leitor antigo cobria somente CNPJ; em DARF de pessoa
        # física isso deixava o campo para o fallback, que podia capturar a
        # palavra "este" da frase "Pagar este documento até".
        pagador_darf = ""
        padrao_doc_nome = re.compile(
            r"\b(?:"
            r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}"
            r"|"
            r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"
            r")\s+(.+?)\s*$",
            re.IGNORECASE,
        )
        for linha in linhas:
            m = padrao_doc_nome.search(linha)
            if not m:
                continue
            candidato = nome_valido(m.group(1))
            if candidato:
                pagador_darf = candidato
                break

        # Fallback para CPF/CNPJ e nome quebrados em linhas distintas.
        if not pagador_darf:
            padrao_doc = re.compile(
                r"(?:\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}"
                r"|\d{3}\.?\d{3}\.?\d{3}-?\d{2})"
            )
            for i, linha in enumerate(linhas):
                if re.fullmatch(padrao_doc, linha):
                    for j in range(i + 1, min(len(linhas), i + 4)):
                        candidato = nome_valido(linhas[j])
                        if candidato:
                            pagador_darf = candidato
                            break
                if pagador_darf:
                    break

        if pagador_darf:
            dados["pagador"] = pagador_darf
            fortes.add("pagador")

        # RECEBEDOR: neste tipo de documento a entidade arrecadadora é a
        # Receita Federal. O próprio documento se identifica como Documento de
        # Arrecadação de Receitas Federais e traz a marca Receita Federal.
        dados["recebedor"] = "RECEITA FEDERAL"
        dados["fornecedor"] = "RECEITA FEDERAL"
        fortes.add("recebedor")

        # REFERÊNCIA/PERÍODO DE APURAÇÃO. "PA:08/2026" é o sinal mais forte.
        m = re.search(
            r"(?i)\bPA\s*[:\-]?\s*(0?[1-9]|1[0-2])\s*/\s*((?:19|20)\d{2})\b",
            texto,
        )
        if m:
            dados["referencia"] = f"{int(m.group(1)):02d}/{m.group(2)}"
            fortes.add("referencia")
        else:
            # No DARF/ITR o campo pode aparecer como uma data completa logo
            # abaixo de "CPF Nome", seguido do vencimento. Nesse caso a
            # primeira data é o Período de Apuração, não o vencimento.
            m_data_pa = re.search(
                r"(?is)CPF\s+Nome\s+"
                r"(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})\s+"
                r"\d{1,2}/\d{1,2}/(?:19|20)\d{2}",
                texto,
            )
            if m_data_pa:
                dados["referencia"] = f"{int(m_data_pa.group(2)):02d}/{m_data_pa.group(3)}"
                fortes.add("referencia")

        if "referencia" not in fortes:
            # Alguns DARFs exibem "agosto/2026" no Período de Apuração.
            meses_pt = {
                "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
                "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
                "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
            }
            texto_sem_acento = _normalizar_ocr(texto)
            mm = re.search(
                r"\b(janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*/\s*((?:19|20)\d{2})\b",
                texto_sem_acento,
            )
            if mm:
                dados["referencia"] = f"{meses_pt[mm.group(1)]:02d}/{mm.group(2)}"
                fortes.add("referencia")

        # Código de arrecadação com 48 dígitos (4 blocos de 12 contando DV).
        # Preserva espaços em grupos para ficar legível/copiável no cadastro.
        if not str(dados.get("codigo_pagamento") or "").strip():
            for linha in linhas:
                numeros = re.sub(r"\D", "", linha)
                if len(numeros) == 48 and numeros.startswith("8"):
                    dados["codigo_pagamento"] = " ".join(
                        numeros[i:i + 12] for i in range(0, 48, 12)
                    )
                    dados["tipo_pagamento"] = "Linha digitável"
                    fortes.add("codigo_pagamento")
                    break

        dados["tipo_documento"] = "conta"

    # PAGADOR / EMPREGADOR / CONTRIBUINTE ---------------------------------
    # Só executa o genérico se o tratamento específico ainda não confirmou.
    if "pagador" not in fortes:
        pagador = _extrair_valor_apos_rotulo(
            linhas,
            (
                "Nome/Razão Social do Empregador",
                "Nome / Razão Social do Empregador",
                "Nome/Razão Social do Contribuinte",
                "Nome / Razão Social do Contribuinte",
                "Nome/Razão Social do Pagador",
                "Nome do Pagador",
                "Razão Social do Empregador",
                "Razão Social do Contribuinte",
                "CNPJ Razão Social",
                "Razão Social",
            ),
            nome_valido,
        )
        if pagador:
            dados["pagador"] = pagador
            fortes.add("pagador")

    # REFERÊNCIA / COMPETÊNCIA --------------------------------------------
    if "referencia" not in fortes:
        referencia = ""
        padroes_ref = (
            r"(?i)\bPA\s*[:\-]?\s*(0?[1-9]|1[0-2])\s*/\s*((?:19|20)\d{2})\b",
            r"(?i)\bRECALC\.?\s*(0?[1-9]|1[0-2])[./-]((?:19|20)\d{2})\b",
            r"(?i)\bCOMPET[EÊ]NCIA\s*[:\-]?\s*(0?[1-9]|1[0-2])[./-]((?:19|20)\d{2})\b",
            r"(?i)\bPER[IÍ]ODO\s+DE\s+APURA[CÇ][AÃ]O\s*[:\-]?\s*(0?[1-9]|1[0-2])[./-]((?:19|20)\d{2})\b",
            r"(?i)\bM[EÊ]S\s*(?:DE\s*)?REFER[EÊ]NCIA\s*[:\-]?\s*(0?[1-9]|1[0-2])[./-]((?:19|20)\d{2})\b",
        )
        for padrao in padroes_ref:
            m = re.search(padrao, texto)
            if m:
                referencia = f"{int(m.group(1)):02d}/{m.group(2)}"
                break

        if not referencia:
            # Em tabelas, o rótulo e o valor podem cair em linhas separadas.
            for i, linha in enumerate(linhas):
                norm_linha = _normalizar_ocr(linha)
                if (
                    "competencia" in norm_linha
                    or "referencia" in norm_linha
                    or "periodo de apuracao" in norm_linha
                ):
                    bloco = " ".join(linhas[i:i + 8])
                    m = re.search(r"(?<!\d)(0?[1-9]|1[0-2])\s*/\s*((?:19|20)\d{2})(?!\d)", bloco)
                    if m:
                        referencia = f"{int(m.group(1)):02d}/{m.group(2)}"
                        break

        if referencia:
            dados["referencia"] = referencia
            fortes.add("referencia")

    # VENCIMENTO -----------------------------------------------------------
    m = re.search(
        r"(?is)(?:Pagar\s+este\s+documento\s+at[eé]|Data\s+de\s+Vencimento|Vencimento)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
        texto,
    )
    if m:
        try:
            dados["vencimento"] = datetime.strptime(m.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
            fortes.add("vencimento")
        except ValueError:
            pass

    # VALOR ---------------------------------------------------------------
    m = re.search(
        r"(?is)(?:Valor\s+a\s+recolher|Total\s+da\s+Guia|Valor\s+Total\s+do\s+Documento|Valor\s+do\s+documento)\s*[:\-]?\s*(?:R\$\s*)?([0-9.]+,[0-9]{2})",
        texto,
    )
    if m:
        valor = _valor_br_para_float(m.group(1))
        if valor is not None and valor > 0:
            dados["valor"] = valor
            fortes.add("valor")

    # PIX EMV completo ----------------------------------------------------
    # Captura o payload completo quando o PDF traz o PIX Copia e Cola em
    # uma linha contínua. O prefixo 000201 identifica o padrão BR Code/EMV.
    pix = ""
    for linha in linhas:
        candidato = re.sub(r"\s+", "", linha)
        if candidato.startswith("000201") and len(candidato) >= 80:
            pix = candidato
            break
    if pix:
        dados["codigo_pagamento"] = pix
        dados["tipo_pagamento"] = "PIX"
        fortes.add("codigo_pagamento")

    # Recebedor explicitamente presente no conteúdo textual/payload.
    # Não inventamos no fluxo genérico: só preenche se o próprio PDF contiver.
    if "recebedor" not in fortes and not str(dados.get("recebedor") or dados.get("fornecedor") or "").strip():
        if "caixa economica federal" in texto_norm:
            dados["recebedor"] = "CAIXA ECONOMICA FEDERAL"
            dados["fornecedor"] = "CAIXA ECONOMICA FEDERAL"
            fortes.add("recebedor")

    if "guia do fgts digital" in texto_norm:
        dados["tipo_documento"] = "conta"

    # Campos fortes deixam de ser considerados inseguros.
    inseguros = [str(x) for x in dados.get("campos_inseguros", [])]
    if fortes:
        inseguros = [x for x in inseguros if x not in fortes]
        dados["campos_inseguros"] = inseguros
        confianca = dados.get("confianca")
        if isinstance(confianca, dict):
            for campo in fortes:
                confianca[campo] = max(float(confianca.get(campo, 0) or 0), 0.98)
        motivos = dados.get("motivos")
        if isinstance(motivos, dict):
            for campo in fortes:
                motivos[campo] = "campo localizado por regra estrutural explícita na camada de texto do PDF"

    return dados, fortes



# ---------------------------------------------------------------------------
# APRENDIZADO LOCAL DE LAYOUTS
# ---------------------------------------------------------------------------
# O aprendizado não treina um modelo pesado. Ele guarda somente uma assinatura
# estrutural do documento e pequenas regiões (x/y relativos) dos campos que o
# usuário corrigiu. Em um layout conhecido, o sistema lê primeiro essas regiões;
# se não houver confiança suficiente, volta automaticamente para o leitor genérico.

_LAYOUT_V2 = LayoutLearningV2(paths.APP_DATA_DIR, tesseract_locator=_localizar_tesseract)

_ARQUIVO_APRENDIZADO = paths.APP_DATA_DIR / "aprendizado_layouts.json"
_ASSINATURA_CACHE: dict[tuple[str, int, int], dict] = {}
_APRENDIZADO_MEMORIA: dict | None = None
_CAMPOS_APRENDIVEIS = ("recebedor", "pagador", "referencia")

_STOP_LAYOUT = {
    "de", "da", "do", "das", "dos", "e", "em", "para", "por", "com", "sem",
    "a", "o", "as", "os", "um", "uma", "no", "na", "nos", "nas", "ao", "aos",
    "pagina", "pag", "valor", "data", "documento", "total", "numero", "codigo",
}


def _carregar_aprendizado() -> dict:
    global _APRENDIZADO_MEMORIA
    if _APRENDIZADO_MEMORIA is not None:
        return _APRENDIZADO_MEMORIA
    base = {"versao": 1, "perfis": []}
    try:
        if _ARQUIVO_APRENDIZADO.is_file():
            bruto = json.loads(_ARQUIVO_APRENDIZADO.read_text(encoding="utf-8"))
            if isinstance(bruto, dict) and isinstance(bruto.get("perfis"), list):
                base = bruto
    except Exception:
        pass
    _APRENDIZADO_MEMORIA = base
    return base


def _salvar_aprendizado() -> None:
    dados = _carregar_aprendizado()
    try:
        _ARQUIVO_APRENDIZADO.parent.mkdir(parents=True, exist_ok=True)
        temporario = _ARQUIVO_APRENDIZADO.with_suffix(".tmp")
        temporario.write_text(
            json.dumps(dados, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporario, _ARQUIVO_APRENDIZADO)
    except Exception:
        pass


def _texto_assinatura(caminho: str | Path) -> str:
    try:
        texto = _extrair_texto_original(caminho) or ""
    except Exception:
        texto = ""
    # PDF escaneado: o OCR já é necessário para a leitura. Reaproveitamos o
    # resultado em cache quando possível, sem criar uma segunda rotina pesada.
    if len(re.sub(r"\s+", "", texto)) < 80:
        try:
            texto_ocr = _ocr_documento(caminho).get("texto", "")
            if texto_ocr:
                texto = f"{texto}\n{texto_ocr}" if texto else texto_ocr
        except Exception:
            pass
    return texto


def _tokens_assinatura(texto: str) -> list[str]:
    texto = _normalizar_ocr(texto)
    # Remove conteúdo altamente variável antes de construir a assinatura.
    texto = re.sub(r"\b\d{1,2}\s*/\s*\d{4}\b", " periodo ", texto)
    texto = re.sub(r"\b\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b", " data ", texto)
    texto = re.sub(r"\b\d{2}\s*\d{3}\s*\d{3}\s*\d{4}\s*\d{2}\b", " cnpj ", texto)
    texto = re.sub(r"\b\d{3}\s*\d{3}\s*\d{3}\s*\d{2}\b", " cpf ", texto)
    texto = re.sub(r"\b\d+[.,]\d{2}\b", " valor ", texto)
    texto = re.sub(r"\b\d{5,}\b", " numero ", texto)

    tokens = []
    for token in texto.split():
        if len(token) < 3 or token in _STOP_LAYOUT:
            continue
        if token.isdigit():
            continue
        tokens.append(token)

    # Conjunto ordenado e limitado: pequeno no disco e suficiente para comparar
    # layouts. Mantemos palavras repetidas apenas uma vez.
    return sorted(set(tokens))[:240]


def _assinatura_layout(caminho: str | Path) -> dict:
    chave = _chave_cache(caminho)
    if chave in _ASSINATURA_CACHE:
        return dict(_ASSINATURA_CACHE[chave])

    texto = _texto_assinatura(caminho)
    paginas = 1
    try:
        import fitz
        doc = fitz.open(str(caminho))
        paginas = max(1, int(doc.page_count))
        doc.close()
    except Exception:
        pass

    assinatura = {
        "tokens": _tokens_assinatura(texto),
        "paginas": paginas,
    }
    if len(_ASSINATURA_CACHE) > 64:
        _ASSINATURA_CACHE.clear()
    _ASSINATURA_CACHE[chave] = dict(assinatura)
    return assinatura


def _similaridade_layout(a: dict, b: dict) -> float:
    ta = set(a.get("tokens") or [])
    tb = set(b.get("tokens") or [])
    if len(ta) < 6 or len(tb) < 6:
        return 0.0
    inter = len(ta & tb)
    uniao = len(ta | tb)
    menor = min(len(ta), len(tb))
    jaccard = inter / uniao if uniao else 0.0
    cobertura = inter / menor if menor else 0.0
    score = 0.55 * cobertura + 0.45 * jaccard
    if int(a.get("paginas") or 1) == int(b.get("paginas") or 1):
        score += 0.04
    else:
        score -= 0.06
    return max(0.0, min(1.0, score))


def _reconhecer_layout(caminho: str | Path):
    assinatura = _assinatura_layout(caminho)
    perfis = _carregar_aprendizado().get("perfis", [])
    pontuados = []
    for perfil in perfis:
        score = _similaridade_layout(assinatura, perfil.get("assinatura") or {})
        pontuados.append((score, perfil))
    if not pontuados:
        return None, 0.0
    pontuados.sort(key=lambda x: x[0], reverse=True)
    melhor_score, melhor = pontuados[0]
    segundo = pontuados[1][0] if len(pontuados) > 1 else 0.0
    # Limiar conservador e margem para evitar aplicar um modelo ambíguo.
    if melhor_score < 0.72:
        return None, melhor_score
    if len(pontuados) > 1 and segundo >= 0.68 and (melhor_score - segundo) < 0.055:
        return None, melhor_score
    return melhor, melhor_score


def _novo_perfil_layout(caminho: str | Path) -> dict:
    assinatura = _assinatura_layout(caminho)
    semente = "|".join(assinatura.get("tokens") or []) + f"|p{assinatura.get('paginas', 1)}"
    perfil = {
        "id": hashlib.sha1(semente.encode("utf-8", errors="ignore")).hexdigest()[:12],
        "assinatura": assinatura,
        "campos": {},
        "criado_em": datetime.now().isoformat(timespec="seconds"),
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
    }
    _carregar_aprendizado().setdefault("perfis", []).append(perfil)
    return perfil


def _linhas_posicionais_nativas(caminho: str | Path) -> list[dict]:
    linhas_saida: list[dict] = []
    try:
        import fitz
        doc = fitz.open(str(caminho))
        for pagina_idx, pagina in enumerate(doc):
            palavras = pagina.get_text("words", sort=True) or []
            grupos: dict[tuple[int, int], list] = {}
            for p in palavras:
                if len(p) < 8:
                    continue
                x0, y0, x1, y1, texto, bloco, linha, _ordem = p[:8]
                texto = str(texto or "").strip()
                if not texto:
                    continue
                grupos.setdefault((int(bloco), int(linha)), []).append(
                    (float(x0), float(y0), float(x1), float(y1), texto)
                )
            largura = max(float(pagina.rect.width), 1.0)
            altura = max(float(pagina.rect.height), 1.0)
            for grupo in grupos.values():
                grupo.sort(key=lambda item: item[0])
                texto = " ".join(item[4] for item in grupo).strip()
                if not texto:
                    continue
                x0 = min(item[0] for item in grupo) / largura
                y0 = min(item[1] for item in grupo) / altura
                x1 = max(item[2] for item in grupo) / largura
                y1 = max(item[3] for item in grupo) / altura
                linhas_saida.append({
                    "pagina": pagina_idx,
                    "texto": texto,
                    "rect": [x0, y0, x1, y1],
                    "fonte": "nativo",
                })
        doc.close()
    except Exception:
        return []
    return linhas_saida


def _linhas_posicionais_ocr_aprendizado(caminho: str | Path) -> list[dict]:
    # Executada apenas quando o usuário corrige um PDF escaneado cujo texto
    # nativo não tem coordenadas úteis. É aprendizado pontual, não leitura comum.
    saida: list[dict] = []
    try:
        import fitz
        import numpy as np
        import cv2
        import pytesseract
        from pytesseract import Output
    except Exception:
        return saida

    executavel = _localizar_tesseract()
    if not executavel:
        return saida
    pytesseract.pytesseract.tesseract_cmd = executavel
    idiomas = _idiomas_disponiveis(pytesseract)
    idioma = next((i for i in idiomas if i), None)

    doc = None
    try:
        doc = fitz.open(str(caminho))
        for pagina_idx, pagina in enumerate(doc):
            pix = pagina.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            kwargs = {"config": "--oem 3 --psm 11", "output_type": Output.DICT}
            if idioma:
                kwargs["lang"] = idioma
            data = pytesseract.image_to_data(img, **kwargs)
            total = len(data.get("text", []))
            grupos: dict[tuple[int, int, int], list] = {}
            for i in range(total):
                txt = str(data["text"][i] or "").strip()
                if not txt:
                    continue
                try:
                    conf = float(data["conf"][i])
                except Exception:
                    conf = -1
                if conf < 20:
                    continue
                chave = (
                    int(data.get("block_num", [0] * total)[i]),
                    int(data.get("par_num", [0] * total)[i]),
                    int(data.get("line_num", [0] * total)[i]),
                )
                grupos.setdefault(chave, []).append({
                    "texto": txt,
                    "x0": int(data["left"][i]),
                    "y0": int(data["top"][i]),
                    "x1": int(data["left"][i]) + int(data["width"][i]),
                    "y1": int(data["top"][i]) + int(data["height"][i]),
                })
            for grupo in grupos.values():
                grupo.sort(key=lambda item: item["x0"])
                texto = " ".join(item["texto"] for item in grupo).strip()
                if not texto:
                    continue
                saida.append({
                    "pagina": pagina_idx,
                    "texto": texto,
                    "rect": [
                        min(i["x0"] for i in grupo) / max(pix.width, 1),
                        min(i["y0"] for i in grupo) / max(pix.height, 1),
                        max(i["x1"] for i in grupo) / max(pix.width, 1),
                        max(i["y1"] for i in grupo) / max(pix.height, 1),
                    ],
                    "fonte": "ocr",
                })
    except Exception:
        return saida
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    return saida


def _linhas_posicionais_aprendizado(caminho: str | Path) -> list[dict]:
    linhas = _linhas_posicionais_nativas(caminho)
    # Se houver texto nativo suficiente, ele é mais rápido e exato.
    if sum(len(str(l.get("texto") or "")) for l in linhas) >= 80:
        return linhas
    return _linhas_posicionais_ocr_aprendizado(caminho)


def _formas_referencia(valor: str) -> set[str]:
    normal = _limpar_referencia_ocr(valor)
    if not normal:
        return set()
    formas = {_normalizar_ocr(normal)}
    m = re.fullmatch(r"(0[1-9]|1[0-2])/(\d{4})", normal)
    if m:
        meses = [
            "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
            "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
        ]
        mes = int(m.group(1))
        ano = m.group(2)
        formas.add(f"{meses[mes - 1]} {ano}")
        formas.add(f"{mes} {ano}")
        formas.add(f"{mes:02d} {ano}")
    return formas


def _pontuar_correspondencia(campo: str, valor: str, texto: str) -> float:
    alvo = _normalizar_ocr(valor)
    candidato = _normalizar_ocr(texto)
    if not alvo or not candidato:
        return 0.0

    if campo == "referencia":
        formas = _formas_referencia(valor)
        if any(f and f in candidato for f in formas):
            return 1.0
        ref_cand = _limpar_referencia_ocr(texto)
        if ref_cand and _limpar_referencia_ocr(valor) == ref_cand:
            return 1.0
        return SequenceMatcher(None, alvo, candidato).ratio() * 0.55

    ta = set(alvo.split())
    tc = set(candidato.split())
    if not ta or not tc:
        return 0.0
    cobertura = len(ta & tc) / len(ta)
    sequencia = SequenceMatcher(None, alvo, candidato).ratio()
    contem = 1.0 if (alvo in candidato or candidato in alvo) else 0.0
    score = max(cobertura * 0.86 + sequencia * 0.14, sequencia, contem * 0.92)
    # Linhas gigantes normalmente são parágrafos, não o valor de um campo.
    if len(candidato) > max(120, len(alvo) * 4):
        score *= 0.65
    return score


def _unir_rects(a: list[float], b: list[float]) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _localizar_regiao_corrigida(caminho: str | Path, campo: str, valor: str):
    linhas = _linhas_posicionais_aprendizado(caminho)
    if not linhas:
        return None
    candidatos: list[tuple[float, dict]] = []
    for i, linha in enumerate(linhas):
        texto = str(linha.get("texto") or "")
        score = _pontuar_correspondencia(campo, valor, texto)
        candidatos.append((score, linha))

        # Nome ou razão social pode quebrar em duas linhas próximas.
        if i + 1 < len(linhas):
            prox = linhas[i + 1]
            if int(prox.get("pagina", -1)) == int(linha.get("pagina", -2)):
                y_gap = float(prox["rect"][1]) - float(linha["rect"][3])
                if -0.005 <= y_gap <= 0.035:
                    combinado = f"{texto} {prox.get('texto', '')}"
                    score2 = _pontuar_correspondencia(campo, valor, combinado)
                    if score2 > score:
                        candidatos.append((score2, {
                            "pagina": linha["pagina"],
                            "texto": combinado,
                            "rect": _unir_rects(linha["rect"], prox["rect"]),
                            "fonte": linha.get("fonte", "nativo"),
                        }))

    candidatos.sort(key=lambda x: x[0], reverse=True)
    if not candidatos or candidatos[0][0] < (0.72 if campo != "referencia" else 0.78):
        return None
    melhor = dict(candidatos[0][1])
    r = [float(x) for x in melhor["rect"]]
    # Pequena margem para absorver diferenças mínimas de renderização.
    melhor["rect"] = [
        max(0.0, r[0] - 0.008),
        max(0.0, r[1] - 0.006),
        min(1.0, r[2] + 0.008),
        min(1.0, r[3] + 0.006),
    ]
    melhor["score"] = round(float(candidatos[0][0]), 4)
    return melhor


def _limpar_valor_aprendido(campo: str, texto: str) -> str:
    if campo in ("recebedor", "pagador"):
        return _limpar_nome_ocr(texto)
    if campo == "referencia":
        return _limpar_referencia_ocr(texto)
    return str(texto or "").strip()


def _ler_regiao_aprendida(caminho: str | Path, regra: dict, campo: str) -> str:
    try:
        import fitz
    except Exception:
        return ""
    try:
        pagina_idx = int(regra.get("pagina", 0))
        r = [float(x) for x in regra.get("rect", [])]
        if len(r) != 4:
            return ""
        doc = fitz.open(str(caminho))
        if pagina_idx < 0 or pagina_idx >= doc.page_count:
            doc.close()
            return ""
        pagina = doc[pagina_idx]
        rect = fitz.Rect(
            r[0] * pagina.rect.width,
            r[1] * pagina.rect.height,
            r[2] * pagina.rect.width,
            r[3] * pagina.rect.height,
        )
        texto = pagina.get_text("text", clip=rect).strip()
        candidato = _limpar_valor_aprendido(campo, texto)
        if candidato:
            doc.close()
            return candidato

        # Em PDF escaneado, OCR apenas do retângulo aprendido: muito mais rápido
        # do que reprocessar a página inteira.
        try:
            import numpy as np
            import cv2
            import pytesseract
            pix = pagina.get_pixmap(matrix=fitz.Matrix(3.0, 3.0), clip=rect, alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            executavel = _localizar_tesseract()
            if executavel:
                pytesseract.pytesseract.tesseract_cmd = executavel
            idiomas = _idiomas_disponiveis(pytesseract)
            idioma = next((i for i in idiomas if i), None)
            kwargs = {"config": "--oem 3 --psm 6"}
            if idioma:
                kwargs["lang"] = idioma
            texto_ocr = pytesseract.image_to_string(img, **kwargs)
            candidato = _limpar_valor_aprendido(campo, texto_ocr)
            doc.close()
            return candidato
        except Exception:
            doc.close()
            return ""
    except Exception:
        return ""


def _forca_regra(regra: dict) -> int:
    return (
        int(regra.get("correcoes", 0)) * 2
        + int(regra.get("confirmacoes", 0))
        - int(regra.get("erros", 0)) * 3
    )


def _aplicar_aprendizado_layout(caminho: str | Path, dados: dict):
    perfil, score = _reconhecer_layout(caminho)
    if not perfil:
        return dados, set()

    inseguros = set(str(x) for x in dados.get("campos_inseguros", []))
    aplicados: set[str] = set()
    for campo in _CAMPOS_APRENDIVEIS:
        regra = (perfil.get("campos") or {}).get(campo)
        if not isinstance(regra, dict):
            continue
        forca = _forca_regra(regra)
        if forca < 2:
            continue
        atual = dados.get("fornecedor") if campo == "recebedor" else dados.get(campo)
        deve_usar = bool(regra.get("override")) or not str(atual or "").strip() or campo in inseguros
        if not deve_usar:
            continue
        valor = _ler_regiao_aprendida(caminho, regra, campo)
        if not valor:
            continue
        if campo == "recebedor":
            dados["recebedor"] = valor
            dados["fornecedor"] = valor
        else:
            dados[campo] = valor
        inseguros.discard(campo)
        aplicados.add(campo)

    dados["campos_inseguros"] = list(inseguros)
    dados["_layout_aprendido"] = str(perfil.get("id") or "")
    dados["_layout_score"] = round(float(score), 3)
    dados["_campos_aprendidos"] = sorted(aplicados)
    if aplicados:
        confianca = dados.get("confianca")
        if isinstance(confianca, dict):
            for campo in aplicados:
                confianca[campo] = max(float(confianca.get(campo, 0) or 0), 0.96)
        motivos = dados.get("motivos")
        if isinstance(motivos, dict):
            for campo in aplicados:
                motivos[campo] = "campo lido por layout local aprendido e confirmado"
    return dados, aplicados


def _equivalente_salvo(campo: str, a: str, b: str) -> bool:
    if campo == "referencia":
        aa = _limpar_referencia_ocr(a)
        bb = _limpar_referencia_ocr(b)
        return bool(aa and bb and aa == bb)
    return _nomes_equivalentes(a, b)


def _aprender_com_dialogo_v1(dialogo) -> None:
    caminho = str(getattr(dialogo, "pdf_origem", "") or "")
    if not caminho or not Path(caminho).is_file():
        return

    previstos = dict(getattr(dialogo, "_patch_dados_lidos", {}) or {})
    aplicados = set(getattr(dialogo, "_patch_campos_aprendidos", []) or [])
    finais = {
        "recebedor": str(dialogo.fornecedor.text() or "").strip(),
        "pagador": str(dialogo.pagador.text() or "").strip(),
        "referencia": str(dialogo.referencia.text() or "").strip(),
    }

    perfil, _score = _reconhecer_layout(caminho)
    alterou = False

    # Só criamos um perfil novo quando existe uma correção explícita que pode
    # ser localizada no próprio documento. Assim o banco não cresce com layouts
    # que nunca precisaram ser ensinados.
    correcoes_localizadas: dict[str, dict] = {}
    for campo in _CAMPOS_APRENDIVEIS:
        final = finais.get(campo, "")
        previsto = str(previstos.get(campo, "") or "")
        if not final:
            continue
        if previsto and _equivalente_salvo(campo, final, previsto):
            continue
        regiao = _localizar_regiao_corrigida(caminho, campo, final)
        if regiao:
            correcoes_localizadas[campo] = regiao

    if perfil is None and correcoes_localizadas:
        perfil = _novo_perfil_layout(caminho)
        alterou = True

    if perfil is None:
        return

    campos = perfil.setdefault("campos", {})
    for campo in _CAMPOS_APRENDIVEIS:
        final = finais.get(campo, "")
        previsto = str(previstos.get(campo, "") or "")
        regra = campos.get(campo)

        if campo in correcoes_localizadas:
            regiao = correcoes_localizadas[campo]
            if not isinstance(regra, dict):
                regra = {}
                campos[campo] = regra
            regra.update({
                "pagina": int(regiao["pagina"]),
                "rect": [round(float(x), 6) for x in regiao["rect"]],
                "fonte": str(regiao.get("fonte") or ""),
                "override": True,
                "atualizado_em": datetime.now().isoformat(timespec="seconds"),
            })
            regra["correcoes"] = int(regra.get("correcoes", 0)) + 1
            # Uma correção nova supera um erro anterior, mas não apaga o histórico.
            regra["erros"] = max(0, int(regra.get("erros", 0)) - 1)
            alterou = True
            continue

        # Se um campo vindo de um layout aprendido foi salvo sem alteração,
        # isso é uma confirmação implícita e aumenta gradualmente a confiança.
        if isinstance(regra, dict) and campo in aplicados and final and previsto:
            if _equivalente_salvo(campo, final, previsto):
                regra["confirmacoes"] = int(regra.get("confirmacoes", 0)) + 1
                regra["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
                alterou = True
            else:
                regra["erros"] = int(regra.get("erros", 0)) + 1
                regra["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
                alterou = True

    if alterou:
        perfil["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
        _salvar_aprendizado()



def _aprender_com_dialogo(dialogo) -> None:
    """Aprendizado V2: TODO PDF salvo ensina o layout atual.

    O arquivo V1 fica congelado como compatibilidade de leitura. A V2 grava em
    aprendizado_layouts_v2.json e nunca altera um perfil diferente do layout
    reconhecido no PDF atual.
    """
    caminho = str(getattr(dialogo, "pdf_origem", "") or "")
    if not caminho or not Path(caminho).is_file():
        return

    previstos = dict(getattr(dialogo, "_patch_dados_lidos", {}) or {})

    try:
        vencimento = dialogo.vencimento.date().toString("yyyy-MM-dd")
    except Exception:
        vencimento = ""
    try:
        valor = float(dialogo.valor.valor() or 0)
    except Exception:
        valor = 0.0
    try:
        codigo = str(dialogo.codigo.toPlainText() or "").strip()
    except Exception:
        codigo = ""

    finais = {
        "recebedor": str(dialogo.fornecedor.text() or "").strip(),
        "pagador": str(dialogo.pagador.text() or "").strip(),
        "referencia": str(dialogo.referencia.text() or "").strip(),
        "vencimento": vencimento,
        "valor": valor,
        "codigo_pagamento": codigo,
    }

    try:
        resultado = _LAYOUT_V2.learn(
            caminho,
            final_values=finais,
            predicted_values=previstos,
        )
        dialogo._patch_layout_v2_ultimo_aprendizado = resultado
    except Exception:
        # Aprendizado nunca pode impedir o cadastro da conta.
        return

def _analisar_pdf_com_ocr_melhorado(caminho: str | Path):
    dados = _analisar_pdf_original(caminho)
    if not isinstance(dados, dict):
        return dados

    dados, campos_fortes_texto = _enriquecer_documento_textual(caminho, dados)

    # Campos determinísticos encontrados no próprio documento devem sempre ter
    # prioridade sobre perfis aprendidos. Guardamos os valores antes de aplicar
    # V1/V2 e restauramos depois; isso impede um perfil antigo contaminado de
    # sobrescrever Receita Federal, CPF/Nome, vencimento, valor etc.
    valores_fortes_texto = {}
    # Nem toda regra genérica marcada como "forte" é infalível (boletos, por
    # exemplo, podem colocar Sacador/Avalista colado ao rótulo). A restauração
    # acima do aprendizado fica restrita ao DARF, cuja identidade e campos são
    # explícitos e determinísticos no próprio documento.
    texto_nativo_norm = _normalizar_ocr(str(dados.get("texto_extraido") or ""))
    documento_nativo_darf = "documento de arrecadacao de receitas federais" in texto_nativo_norm
    documento_nativo_fgts = "guia do fgts digital" in texto_nativo_norm
    if documento_nativo_darf or documento_nativo_fgts:
        for campo in set(campos_fortes_texto):
            if campo == "recebedor":
                valor_forte = str(dados.get("recebedor") or dados.get("fornecedor") or "").strip()
            else:
                valor_forte = dados.get(campo)
            if valor_forte not in (None, "", 0, 0.0):
                valores_fortes_texto[campo] = valor_forte
    tipo_pagamento_forte = str(dados.get("tipo_pagamento") or "") if "codigo_pagamento" in valores_fortes_texto else ""

    # Compatibilidade: o V1 permanece somente como fallback de leitura.
    # Em seguida a V2, quando reconhecida, vence o V1 campo a campo — exceto
    # quando o próprio documento já forneceu uma regra determinística forte.
    dados, campos_aprendidos_v1 = _aplicar_aprendizado_layout(caminho, dados)
    try:
        dados, campos_aprendidos_v2 = _LAYOUT_V2.apply(caminho, dados)
    except Exception:
        campos_aprendidos_v2 = set()

    for campo, valor_forte in valores_fortes_texto.items():
        dados[campo] = valor_forte
        if campo == "recebedor":
            dados["fornecedor"] = valor_forte
        if campo == "codigo_pagamento" and tipo_pagamento_forte:
            dados["tipo_pagamento"] = tipo_pagamento_forte
        confianca_forte = dados.get("confianca")
        if isinstance(confianca_forte, dict):
            confianca_forte[campo] = max(float(confianca_forte.get(campo, 0) or 0), 0.99)
        motivos_fortes = dados.get("motivos")
        if isinstance(motivos_fortes, dict):
            motivos_fortes[campo] = "campo confirmado por regra estrutural explicita no proprio PDF"

    campos_aprendidos = set(campos_aprendidos_v1) | set(campos_aprendidos_v2)
    campos_fortes_texto = set(campos_fortes_texto) | campos_aprendidos
    inseguros = [str(x) for x in dados.get("campos_inseguros", []) if str(x) not in campos_fortes_texto]

    def marcar_inseguro(campo: str):
        if campo not in inseguros:
            inseguros.append(campo)

    # Nomes vindos do parser original so sao aceitos se realmente parecerem
    # pessoa/empresa. Isso impede casos como:
    #   "06/2026 694,48.. Voce podera ter o fornecimento de"
    # de serem tratados como Pagador confirmado.
    recebedor_atual = str(dados.get("recebedor") or dados.get("fornecedor") or "").strip()
    recebedor_limpo = _limpar_nome_ocr(recebedor_atual)
    if recebedor_limpo:
        dados["recebedor"] = recebedor_limpo
        dados["fornecedor"] = recebedor_limpo
    else:
        dados["recebedor"] = ""
        dados["fornecedor"] = ""
        marcar_inseguro("recebedor")

    pagador_atual = str(dados.get("pagador") or "").strip()
    pagador_limpo = _limpar_nome_ocr(pagador_atual)
    if pagador_limpo:
        dados["pagador"] = pagador_limpo
    else:
        dados["pagador"] = ""
        marcar_inseguro("pagador")

    referencia_atual = str(dados.get("referencia") or "").strip()
    referencia_limpa = _limpar_referencia_ocr(referencia_atual)
    if referencia_limpa:
        dados["referencia"] = referencia_limpa
    elif referencia_atual:
        dados["referencia"] = ""
        marcar_inseguro("referencia")

    falta_recebedor = not str(dados.get("recebedor") or dados.get("fornecedor") or "").strip() or "recebedor" in inseguros
    falta_pagador = not str(dados.get("pagador") or "").strip() or "pagador" in inseguros
    falta_referencia = not str(dados.get("referencia") or "").strip() or "referencia" in inseguros

    # PDF sem camada de texto precisa passar pela confirmacao estrutural do OCR
    # mesmo que um perfil aprendido tenha preenchido algum campo. Isso impede
    # um perfil antigo contaminado de vencer uma leitura oficial do documento.
    texto_nativo = str(dados.get("texto_extraido") or "")
    pdf_imagem = not _texto_suficiente(texto_nativo)

    if not (falta_recebedor or falta_pagador or falta_referencia or pdf_imagem):
        dados["campos_inseguros"] = inseguros
        return dados

    ocr = _ocr_documento(caminho)

    # Regra deterministica para DARF escaneado/imagem. Roda DEPOIS do V1/V2 e
    # portanto pode corrigir de forma segura um valor aprendido incorretamente.
    dados, campos_fortes_ocr = _enriquecer_darf_ocr(dados, ocr)
    if campos_fortes_ocr:
        campos_fortes_texto |= campos_fortes_ocr
        inseguros = [x for x in inseguros if x not in campos_fortes_ocr]

    falta_recebedor = not str(dados.get("recebedor") or dados.get("fornecedor") or "").strip() or "recebedor" in inseguros
    falta_pagador = not str(dados.get("pagador") or "").strip() or "pagador" in inseguros
    falta_referencia = not str(dados.get("referencia") or "").strip() or "referencia" in inseguros

    preenchidos: list[str] = []

    if falta_recebedor and ocr.get("recebedor"):
        candidato = _limpar_nome_ocr(ocr["recebedor"])
        if candidato:
            dados["recebedor"] = candidato
            dados["fornecedor"] = candidato
            preenchidos.append("recebedor")

    if falta_pagador and ocr.get("pagador"):
        candidato = _limpar_nome_ocr(ocr["pagador"])
        if candidato:
            dados["pagador"] = candidato
            preenchidos.append("pagador")

    if falta_referencia and ocr.get("referencia"):
        candidato = _limpar_referencia_ocr(ocr["referencia"])
        if candidato:
            dados["referencia"] = candidato
            preenchidos.append("referencia")

    # Se o OCR nao encontrou um nome confiavel, deixa vazio. E melhor pedir
    # revisao ao usuario do que preencher uma frase errada e dizer "confirmado".
    for campo in ("recebedor", "pagador", "referencia"):
        if campo in preenchidos:
            inseguros = [x for x in inseguros if x != campo]
        else:
            valor = dados.get("fornecedor") if campo == "recebedor" else dados.get(campo)
            if not str(valor or "").strip():
                marcar_inseguro(campo)

    dados["campos_inseguros"] = inseguros

    confianca = dados.get("confianca")
    if isinstance(confianca, dict):
        for campo in preenchidos:
            confianca[campo] = max(float(confianca.get(campo, 0) or 0), 0.90)
        for campo in inseguros:
            if campo in ("recebedor", "pagador", "referencia"):
                confianca[campo] = min(float(confianca.get(campo, 0) or 0), 0.49)

    motivos = dados.get("motivos")
    if isinstance(motivos, dict):
        for campo in preenchidos:
            motivos[campo] = "campo localizado por OCR posicional junto ao rotulo correspondente"
        for campo in inseguros:
            if campo in ("recebedor", "pagador", "referencia"):
                motivos[campo] = "texto encontrado nao parece um valor confiavel para este campo"

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
    """Bloqueia apenas duplicatas com evidência forte.

    Regras de semelhança (mesmo fornecedor, valor, vencimento etc.) não são
    suficientes para impedir o cadastro: contas reais podem ser muito parecidas.
    """
    encontradas: list[dict] = []
    ids: set[int] = set()

    novo_pdf = str(dados.get("pdf_conta") or "")
    novo_codigo_bruto = str(dados.get("codigo_pagamento") or "").strip()
    novo_codigo = _normalizar_codigo_duplicata(novo_codigo_bruto)
    novo_valor = round(float(dados.get("valor") or 0), 2)
    novo_vencimento = str(dados.get("vencimento") or "").strip()
    nova_referencia = str(dados.get("referencia") or "").strip()

    def _mesmos_dados_basicos(conta) -> bool:
        try:
            mesmo_valor = round(float(conta.get("valor") or 0), 2) == novo_valor
        except Exception:
            mesmo_valor = False
        mesmo_vencimento = str(conta.get("vencimento") or "").strip() == novo_vencimento
        return mesmo_valor and mesmo_vencimento

    def _codigo_e_linha_bancaria_forte(codigo: str) -> bool:
        # Códigos de barras / linhas digitáveis de cobrança e arrecadação.
        return codigo.isdigit() and len(codigo) in {44, 46, 47, 48}

    def _codigo_e_pix_emv(codigo: str) -> bool:
        # PIX Copia e Cola EMV. Um payload igual é um sinal forte, mas como
        # pode existir PIX estático, exigimos também valor + vencimento iguais.
        return codigo.startswith("000201") and len(codigo) >= 50

    for conta in self.listar_contas():
        try:
            conta_id = int(conta["id"])
        except Exception:
            continue
        if ignorar_id is not None and conta_id == int(ignorar_id):
            continue
        if conta_id in ids:
            continue

        # 1) Mesmos bytes = exatamente o mesmo PDF.
        if _mesmo_pdf_por_conteudo(novo_pdf, str(conta.get("pdf_conta") or "")):
            encontradas.append(conta)
            ids.add(conta_id)
            continue

        codigo_existente_bruto = str(conta.get("codigo_pagamento") or "").strip()
        codigo_existente = _normalizar_codigo_duplicata(codigo_existente_bruto)

        if novo_codigo and codigo_existente and novo_codigo == codigo_existente:
            # 2) Linha digitável / código de barras idêntico: bloqueio seguro.
            if _codigo_e_linha_bancaria_forte(novo_codigo):
                encontradas.append(conta)
                ids.add(conta_id)
                continue

            # 3) PIX Copia e Cola idêntico: só bloqueia junto com valor e
            # vencimento, evitando falso positivo em PIX estático reutilizado.
            if _codigo_e_pix_emv(novo_codigo) and _mesmos_dados_basicos(conta):
                encontradas.append(conta)
                ids.add(conta_id)
                continue

            # 4) Outros códigos/URLs/chaves: igualdade sozinha NÃO bloqueia.
            # Ex.: uma chave PIX fixa pode aparecer em várias contas legítimas.

        # 5) Referência, fornecedor, valor e vencimento semelhantes servem para
        # conferência humana, mas NÃO são prova de duplicidade e não bloqueiam.
        # Isso evita barrar cobranças distintas do mesmo fornecedor/período.

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

        # Guarda exatamente o que o leitor apresentou. Ao salvar, diferenças
        # feitas pelo usuário viram sinal de aprendizado para este layout.
        self._patch_dados_lidos = {
            "recebedor": recebedor,
            "pagador": pagador,
            "referencia": referencia,
            "vencimento": str(dados.get("vencimento") or ""),
            "valor": float(dados.get("valor") or 0),
            "codigo_pagamento": codigo_lido,
        }
        self._patch_campos_aprendidos = list(dados.get("_campos_aprendidos", []) or [])
        self._patch_layout_aprendido = str(dados.get("_layout_aprendido") or "")
        self._patch_campos_layout_v2 = list(dados.get("_campos_layout_v2", []) or [])
        self._patch_layout_v2 = str(dados.get("_layout_v2") or "")

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

        layout_v2_id = str(dados.get("_layout_v2") or "")
        if layout_v2_id:
            score = int(round(float(dados.get("_layout_v2_score") or 0) * 100))
            nomes_campos = {
                "recebedor": "Recebedor",
                "pagador": "Pagador",
                "referencia": "Referência",
                "vencimento": "Vencimento",
                "valor": "Valor",
                "codigo_pagamento": "Código de pagamento",
            }
            campos_modelo = [
                nomes_campos.get(c, c)
                for c in dados.get("_campos_layout_v2", [])
            ]
            detalhe = (
                f" Layout V2 conhecido ({score}% de similaridade)."
                + (f" Aprendizado aplicado em: {', '.join(campos_modelo)}." if campos_modelo else "")
            )
            self.aviso_leitura.setText(self.aviso_leitura.text() + detalhe)
        else:
            layout_id = str(dados.get("_layout_aprendido") or "")
            if layout_id:
                score = int(round(float(dados.get("_layout_score") or 0) * 100))
                campos_modelo = [
                    {"recebedor": "Recebedor", "pagador": "Pagador", "referencia": "Referência"}.get(c, c)
                    for c in dados.get("_campos_aprendidos", [])
                ]
                detalhe = (
                    f" Modelo V1 compatível ({score}% de similaridade)."
                    + (f" Aplicado em: {', '.join(campos_modelo)}." if campos_modelo else "")
                )
                self.aviso_leitura.setText(self.aviso_leitura.text() + detalhe)

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
_listar_contas_original = ui.Banco.listar_contas
_montar_ui_original = ui.JanelaPrincipal._montar_ui
_atualizar_tabela_original = ui.JanelaPrincipal.atualizar_tabela
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



# ---------------------------------------------------------------------------
# LISTA PRINCIPAL: COLUNA OBS + CABEÇALHO COMPACTO
# ---------------------------------------------------------------------------


def _normalizar_busca_global(valor) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.casefold()
    # Mantém números, mas transforma pontuação/separadores em espaço para que
    # 18/09/2026, 18-09-2026 e 18 09 2026 possam ser encontrados do mesmo jeito.
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _variantes_data_busca(valor) -> list[str]:
    bruto = str(valor or "").strip()
    if not bruto:
        return []
    saida = [bruto]
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", bruto)
    if m:
        ano, mes, dia = m.groups()
        meses = (
            "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
            "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
        )
        try:
            nome_mes = meses[int(mes) - 1]
            saida.extend([
                f"{dia}/{mes}/{ano}",
                f"{dia} {mes} {ano}",
                f"{mes}/{ano}",
                f"{nome_mes} {ano}",
                nome_mes,
            ])
        except Exception:
            pass
    return saida


def _variantes_valor_busca(valor) -> list[str]:
    try:
        numero = float(valor or 0)
    except Exception:
        return [str(valor or "")] if valor not in (None, "") else []
    br = f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return [str(valor), f"{numero:.2f}", br, f"R$ {br}"]


def _listar_contas_com_observacao(self, busca="", filtro="Todas"):
    # O método original continua responsável por ordenação, cálculo de status e
    # filtros. A busca é aplicada aqui sobre TODOS os dados da conta.
    itens = _listar_contas_original(self, "", filtro)
    termo_original = str(busca or "").strip()
    if not termo_original:
        return itens

    termo = _normalizar_busca_global(termo_original)
    tokens = [t for t in termo.split() if t]
    if not tokens:
        return itens

    # Dados completos do pagamento não vinham no SELECT da listagem original.
    # Buscamos todos de uma vez para evitar uma consulta por linha e manter a
    # pesquisa rápida mesmo com muitas contas.
    pagamentos: dict[int, dict] = {}
    try:
        with self.conexao() as conn:
            rows = conn.execute(
                """
                SELECT conta_id, beneficiario, documento_beneficiario,
                       detalhes_conferencia, criado_em
                FROM pagamentos
                """
            ).fetchall()
            pagamentos = {int(r["conta_id"]): dict(r) for r in rows}
    except Exception:
        pagamentos = {}

    resultado = []
    for item in itens:
        partes: list[str] = []

        # Absolutamente todos os campos vindos de contas + pagamento resumido.
        for chave, valor in item.items():
            if valor in (None, ""):
                continue
            partes.append(str(valor))

            if chave in {"vencimento", "data_pagamento", "criado_em", "atualizado_em", "enviado_pagamento_em"}:
                partes.extend(_variantes_data_busca(valor))
            elif chave in {"valor", "valor_pago"}:
                partes.extend(_variantes_valor_busca(valor))
            elif chave in {"pdf_conta", "pdf_comprovante"}:
                try:
                    p = Path(str(valor))
                    partes.extend([p.name, p.stem])
                except Exception:
                    pass

        # Campos adicionais da tabela pagamentos: beneficiário, documento do
        # beneficiário, detalhes da conferência e data de criação.
        try:
            extra = pagamentos.get(int(item.get("id") or 0), {})
        except Exception:
            extra = {}
        for chave, valor in extra.items():
            if valor in (None, ""):
                continue
            partes.append(str(valor))
            if chave == "criado_em":
                partes.extend(_variantes_data_busca(valor))

        # Termos amigáveis/derivados que o usuário vê na tela.
        partes.extend([
            str(item.get("status") or ""),
            "paga" if str(item.get("status") or "") == "Paga" else "",
            "pix" if str(item.get("tipo_pagamento") or "").casefold() == "pix" else "",
        ])

        texto = _normalizar_busca_global(" ".join(partes))

        # Permite buscas combinadas, ex.: "angelita setembro 534,93".
        # Todos os termos digitados precisam existir em algum campo da mesma conta.
        if all(token in texto for token in tokens):
            resultado.append(item)

    return resultado


def _compactar_area_superior(self) -> None:
    try:
        central = self.centralWidget()
        raiz = central.layout() if central else None
        if raiz:
            raiz.setContentsMargins(18, 10, 18, 10)
            raiz.setSpacing(8)

        header = self.findChild(ui.QFrame, "headerPanel")
        if header:
            header.setMinimumHeight(64)
            header.setMaximumHeight(72)
            if header.layout():
                header.layout().setContentsMargins(16, 8, 16, 8)
                header.layout().setSpacing(10)

        # Elementos decorativos que consomem altura e não são funcionais.
        marca = self.findChild(ui.QLabel, "eyebrow")
        if marca:
            marca.hide()
        subtitulo = self.findChild(ui.QLabel, "subtitle")
        if subtitulo:
            subtitulo.hide()
        legenda = self.findChild(ui.QLabel, "sectionCaption")
        if legenda:
            legenda.hide()

        if getattr(self, "area_drop", None):
            self.area_drop.setMinimumHeight(30)
            self.area_drop.setMaximumHeight(34)

        for card in getattr(self, "cards", {}).values():
            card.setMinimumHeight(70)
            card.setMaximumHeight(76)
            lay = card.layout()
            if lay:
                lay.setContentsMargins(12, 7, 12, 7)
                lay.setSpacing(1)
    except Exception:
        pass


def _configurar_coluna_observacao(self) -> None:
    try:
        tabela = self.tabela
        if tabela.columnCount() < 9:
            tabela.setColumnCount(9)
            cab = ui.QTableWidgetItem("Observações")
            cab.setTextAlignment(
                ui.Qt.AlignmentFlag.AlignLeft | ui.Qt.AlignmentFlag.AlignVCenter
            )
            tabela.setHorizontalHeaderItem(8, cab)

        header = tabela.horizontalHeader()
        # Recebedor e OBS dividem a área que antes ficava toda em Recebedor.
        header.setSectionResizeMode(0, ui.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(8, ui.QHeaderView.ResizeMode.Stretch)
        tabela.setColumnHidden(7, True)  # ID continua lógico/oculto

        # Mantemos os índices lógicos originais e movemos apenas visualmente a
        # coluna OBS para logo depois de Recebedor. Assim o bytecode original
        # pode continuar escrevendo Vencimento/Valor/etc. nos mesmos índices.
        visual_obs = header.visualIndex(8)
        if visual_obs >= 0 and visual_obs != 1:
            header.moveSection(visual_obs, 1)
    except Exception:
        pass


def _pagamentos_para_busca(banco) -> dict[int, dict]:
    """Carrega campos extras de pagamento uma única vez por atualização."""
    try:
        with banco.conexao() as conn:
            rows = conn.execute(
                """
                SELECT conta_id, beneficiario, documento_beneficiario,
                       detalhes_conferencia, criado_em
                FROM pagamentos
                """
            ).fetchall()
            return {int(r["conta_id"]): dict(r) for r in rows}
    except Exception:
        return {}


def _texto_pesquisavel_conta(item: dict, pagamento: dict | None = None) -> str:
    partes: list[str] = []

    for chave, valor in item.items():
        if valor in (None, ""):
            continue

        partes.append(str(valor))

        if chave in {
            "vencimento",
            "data_pagamento",
            "criado_em",
            "atualizado_em",
            "enviado_pagamento_em",
        }:
            partes.extend(_variantes_data_busca(valor))
        elif chave in {"valor", "valor_pago"}:
            partes.extend(_variantes_valor_busca(valor))
        elif chave in {"pdf_conta", "pdf_comprovante"}:
            try:
                p = Path(str(valor))
                partes.extend([p.name, p.stem])
            except Exception:
                pass

    for chave, valor in (pagamento or {}).items():
        if valor in (None, ""):
            continue
        partes.append(str(valor))
        if chave == "criado_em":
            partes.extend(_variantes_data_busca(valor))

    status = str(item.get("status") or "")
    partes.append(status)
    if status == "Paga":
        partes.extend(["paga", "pago", "quitada", "quitado"])

    tipo = str(item.get("tipo_pagamento") or "")
    if tipo:
        partes.append(tipo)

    # Permite encontrar também pelo nome limpo do documento.
    for chave in ("pdf_conta", "pdf_comprovante"):
        caminho = str(item.get(chave) or "").strip()
        if caminho:
            try:
                p = Path(caminho)
                partes.extend([p.name, p.stem])
            except Exception:
                pass

    return _normalizar_busca_global(" ".join(partes))



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


def _conta_bate_busca(
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

def _montar_ui_compacta_com_obs(self):
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

def _atualizar_tabela_com_obs(self, *args, **kwargs):
    """
    Atualização completa da tabela com busca global própria.

    Não usa a busca do método Banco.listar_contas original; carrega as contas
    sem termo de pesquisa e aplica o filtro aqui, de forma previsível.
    """
    aba_pagas = self.abas.currentIndex() == 1
    filtro = "Paga" if aba_pagas else self.filtro.currentText()

    # Pagamento e contabilizado pelo historico, sem depender de comprovante PDF.
    try:
        contas = list(_listar_contas_original(self.banco, "", "Todas") or [])
        with self.banco.conexao() as conn:
            paid = {int(r["conta_id"]) for r in conn.execute(
                "SELECT DISTINCT conta_id FROM pagamentos "
                "WHERE data_pagamento IS NOT NULL AND TRIM(data_pagamento) <> ''"
            ).fetchall()}
        for conta in contas:
            if int(conta.get("id") or 0) in paid:
                conta["status"] = "Paga"
        if aba_pagas:
            contas = [c for c in contas if c.get("status") == "Paga"]
        else:
            contas = [c for c in contas if c.get("status") != "Paga"
                      and (filtro == "Todas" or c.get("status") == filtro)]
    except Exception:
        contas = []

    consulta = ""
    try:
        consulta = self.busca.text().strip()
    except Exception:
        pass

    valor_exato = _interpretar_valor_busca_exata(consulta)
    pagamentos = (
        _pagamentos_para_busca(self.banco)
        if consulta and valor_exato is None
        else {}
    )

    if consulta:
        filtradas = []
        for conta in contas:
            try:
                conta_id = int(conta.get("id") or 0)
            except Exception:
                conta_id = 0

            if _conta_bate_busca(
                conta,
                consulta,
                pagamentos.get(conta_id, {}),
                valor_exato,
            ):
                filtradas.append(conta)
        contas = filtradas

    try:
        self.lbl_total_lista.setText(
            f"{len(contas)} registro{'s' if len(contas) != 1 else ''}"
        )
    except Exception:
        pass

    tabela = self.tabela
    if tabela.columnCount() < 9:
        _configurar_coluna_observacao(self)

    tabela.setRowCount(len(contas))

    cores = {
        "Vencida": "#ffe8e8",
        "Próxima": "#fff4cc",
        "Em dia": "#e8f2ff",
        "Paga": "#e4f7ea",
    }
    cores_texto = {
        "Vencida": "#9f2525",
        "Próxima": "#8a6500",
        "Em dia": "#245b93",
        "Paga": "#247447",
    }

    for linha, conta in enumerate(contas):
        envio = (
            ui.data_hora_br(conta.get("enviado_pagamento_em"))
            if conta.get("enviado_pagamento_em")
            else "Não enviada"
        )

        status = str(conta.get("status") or "")
        conferencia = conta.get("resultado_conferencia") or "—"

        pdf_conta = str(conta.get("pdf_conta") or "")
        try:
            pdf_status = (
                "Disponível"
                if pdf_conta and Path(pdf_conta).exists()
                else "Não anexado"
            )
        except Exception:
            pdf_status = "Não anexado"

        fornecedor = " ".join(
            str(conta.get("fornecedor") or "").split()
        )
        observacao = " ".join(
            str(conta.get("observacoes") or "").split()
        )

        valores = [
            fornecedor,                                      # 0 Recebedor
            ui.data_br(conta.get("vencimento")),             # 1 Vencimento
            ui.dinheiro(conta.get("valor")),                 # 2 Valor
            status,                                          # 3 Status
            envio,                                           # 4 Envio
            conferencia,                                     # 5 Conferência
            pdf_status,                                      # 6 PDF
            str(conta.get("id") or ""),                      # 7 ID (oculta)
            observacao,                                      # 8 OBS
        ]

        cor_fundo = cores.get(status, "#ffffff")
        cor_texto = cores_texto.get(status, "#202020")

        for coluna, valor in enumerate(valores):
            item = ui.QTableWidgetItem(str(valor))

            if coluna in (1, 2, 3, 4, 5, 6):
                item.setTextAlignment(
                    ui.Qt.AlignmentFlag.AlignCenter
                )
            else:
                item.setTextAlignment(
                    ui.Qt.AlignmentFlag.AlignLeft
                    | ui.Qt.AlignmentFlag.AlignVCenter
                )

            try:
                item.setBackground(ui.QColor(cor_fundo))
                item.setForeground(ui.QColor(cor_texto))
            except Exception:
                pass

            if coluna in (4, 5):
                try:
                    item.setFont(
                        ui.QFont(
                            "Arial",
                            10,
                            ui.QFont.Weight.DemiBold,
                        )
                    )
                except Exception:
                    pass

            if coluna == 8 and observacao:
                item.setToolTip(observacao)

            tabela.setItem(linha, coluna, item)

    # Garante que o ID continue oculto mesmo após reconfigurações de coluna.
    try:
        tabela.setColumnHidden(7, True)
    except Exception:
        pass



def _configurar_pasta_documentos(self):
    atual = _ler_pasta_base()
    nova = _selecionar_pasta_base(self)
    if nova is None:
        return

    try:
        self._patch_btn_pasta.setToolTip(f"Pasta-base atual:\n{nova}")
    except Exception:
        pass

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
        _aprender_com_dialogo(dialog)
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
        _aprender_com_dialogo(dialog)
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
        _aprender_com_dialogo(dialog)
        self.banco.adicionar_conta(dialog.dados())
        self.atualizar()

    dialog.accepted.connect(salvar)
    _manter_dialogo_vivo(self, dialog)


# ===========================================================================
# APRENDIZADO V2
# ===========================================================================
# A V2 está integrada no analisador e no salvamento acima. Não há mais override
# semântico global por tipo de boleto: um layout novo não modifica os anteriores.


# ---------------------------------------------------------------------------
# ICONE DA JANELA
# ---------------------------------------------------------------------------

_janela_init_com_pasta_base = _janela_init_com_pasta


def _janela_init_com_pasta(
    self,
    *args,
    **kwargs,
):
    _janela_init_com_pasta_base(
        self,
        *args,
        **kwargs,
    )

    try:
        candidatos = []

        meipass = str(
            getattr(
                sys,
                "_MEIPASS",
                "",
            )
            or ""
        )

        if meipass:
            candidatos.append(
                Path(meipass)
                / "assets"
                / "icone_controle_contas.ico"
            )

        candidatos.extend(
            [
                Path(__file__).resolve().parents[1]
                / "assets"
                / "icone_controle_contas.ico",

                Path(sys.executable).resolve().parent
                / "assets"
                / "icone_controle_contas.ico",
            ]
        )

        app = ui.QApplication.instance()

        for caminho_icone in candidatos:
            if not caminho_icone.is_file():
                continue

            icone = ui.QIcon(str(caminho_icone))

            if icone.isNull():
                continue

            self.setWindowIcon(icone)

            if app is not None:
                app.setWindowIcon(icone)

            break

    except Exception:
        pass


_aplicado = False


def aplicar_patches():
    global _aplicado
    if _aplicado:
        return

    pdf_reader._extrair_texto = _extrair_texto_com_ocr
    pdf_reader.analisar_pdf = _analisar_pdf_com_ocr_melhorado
    ui.analisar_pdf = _analisar_pdf_com_ocr_melhorado

    ui.Banco.localizar_duplicatas = _localizar_duplicatas_reforcado
    ui.Banco.listar_contas = _listar_contas_com_observacao
    ui.Banco.adicionar_conta = _banco_adicionar_conta_organizada
    ui.Banco.atualizar_conta = _banco_atualizar_conta_organizada
    ui.Banco.registrar_pagamento = _banco_registrar_pagamento_mesclado

    ui.ContaDialog.__init__ = _init_conta_preservando_edicoes
    ui.ContaDialog.carregar_pdf = _carregar_pdf_preservando_campos
    ui.ContaDialog.dados = _dados_conta_sem_copia
    ui.ContaDialog.validar = _validar_conta_com_pasta

    ui.PagamentoDialog.dados = _dados_pagamento_sem_copia
    ui.PagamentoDialog.validar = _validar_pagamento_com_pasta

    ui.JanelaPrincipal._montar_ui = _montar_ui_compacta_com_obs
    ui.JanelaPrincipal.atualizar_tabela = _atualizar_tabela_com_obs
    ui.JanelaPrincipal.__init__ = _janela_init_com_pasta
    ui.JanelaPrincipal._patch_configurar_pasta_documentos = _configurar_pasta_documentos
    ui.JanelaPrincipal.nova_conta = _nova_conta_nao_modal
    ui.JanelaPrincipal.editar_conta = _editar_conta_nao_modal
    ui.JanelaPrincipal.processar_pdf_solto = _processar_pdf_solto_nao_modal
    ui.JanelaPrincipal.excluir_conta = _excluir_conta_mensagem_atualizada

    _aplicado = True