"""Exporta a lista do Controle de Contas para Excel .xlsx sem dependências extras.

Não modifica lançamentos e mantém textos de fornecedores/observações como texto,
inclusive quando começam com "=" (proteção contra fórmulas injetadas).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import math
import re
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
COLUMNS = ("Fornecedor", "Observações", "Vencimento", "Valor (R$)",
           "Status", "Sugerido em", "Enviado em")
MAX_ROWS = 6000
# XLSX uses Excel's historical 1900 date system (including the leap-year bug).
_EXCEL_EPOCH = datetime(1899, 12, 30)
_INVALID_XML = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


def _text(value):
    if value is None:
        return ""
    return _INVALID_XML.sub("", str(value))[:32767]


def _inline_cell(ref, value, style):
    content = escape(_text(value))
    return (f'<c r="{ref}" s="{style}" t="inlineStr">'
            f'<is><t xml:space="preserve">{content}</t></is></c>')


def _number_cell(ref, value, style):
    number = float(value or 0)
    if not math.isfinite(number):
        number = 0.0
    return f'<c r="{ref}" s="{style}"><v>{number:.2f}</v></c>'


def _date_number(value, has_time=False):
    if not value:
        return None
    try:
        original = str(value).strip()
        if has_time:
            dt = datetime.fromisoformat(original.replace("Z", "+00:00"))
            # São Paulo has used UTC-03 since 2019. Treat naive times as
            # already local; UTC/offset timestamps should match the UI.
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone(timedelta(hours=-3))).replace(tzinfo=None)
        else:
            dt = datetime.combine(date.fromisoformat(original[:10]), datetime.min.time())
        delta = dt - _EXCEL_EPOCH
        if delta.days < 1 or delta.days > 2958465:
            return None
        return round(delta.total_seconds() / 86400, 9)
    except (ValueError, TypeError, OverflowError):
        return None


def _date_cell(ref, value, style, has_time=False):
    if not value:
        return f'<c r="{ref}" s="{style}"/>'
    number = _date_number(value, has_time)
    if number is None:
        return _inline_cell(ref, value, 4)
    return f'<c r="{ref}" s="{style}"><v>{number}</v></c>'


def _styles_xml():
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    head = (f'<styleSheet {ns}>'
            '<numFmts count="3">'
            '<numFmt numFmtId="164" formatCode="dd/mm/yyyy"/>'
            '<numFmt numFmtId="165" formatCode="&quot;R$&quot; #,##0.00"/>'
            '<numFmt numFmtId="166" formatCode="dd/mm/yyyy hh:mm"/>'
            '</numFmts>'
            '<fonts count="3">'
            '<font><sz val="10"/><color rgb="FF29435D"/><name val="Aptos"/></font>'
            '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font>'
            '<font><b/><sz val="17"/><color rgb="FFFFFFFF"/><name val="Aptos Display"/></font>'
            '</fonts>'
            '<fills count="7">'
            '<fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FF173658"/><bgColor indexed="64"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFF5F8FC"/><bgColor indexed="64"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFFDE8EB"/><bgColor indexed="64"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF1D6"/><bgColor indexed="64"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFE6F5ED"/><bgColor indexed="64"/></patternFill></fill>'
            '</fills>'
            '<borders count="2">'
            '<border><left/><right/><top/><bottom/><diagonal/></border>'
            '<border><left/><right/><top/><bottom style="hair"><color rgb="FFE4EBF3"/></bottom><diagonal/></border>'
            '</borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>')
    # style indexes: title 1, subtitle 2, heading 3; text 4/5;
    # dates 6/7; money 8/9; datetimes 10/11; status 12/13/14.
    configurations = [
        (0, 0, 0, 0), (2, 2, 0, 0), (0, 3, 0, 0),
        (1, 2, 0, 1), (0, 0, 0, 1), (0, 3, 0, 1),
        (0, 0, 164, 1), (0, 3, 164, 1),
        (0, 0, 165, 1), (0, 3, 165, 1),
        (0, 0, 166, 1), (0, 3, 166, 1),
        (0, 4, 0, 1), (0, 5, 0, 1), (0, 6, 0, 1)
    ]
    xfs = []
    for idx, (font, fill, number_format, border) in enumerate(configurations):
        align = ' horizontal="center"' if idx == 3 else ''
        xfs.append(
            f'<xf numFmtId="{number_format}" fontId="{font}" fillId="{fill}" '
            f'borderId="{border}" xfId="0" applyAlignment="1" '
            f'applyNumberFormat="{int(bool(number_format))}">'
            f'<alignment vertical="center"{align}/></xf>'
        )
    return (head + f'<cellXfs count="{len(xfs)}">' + "".join(xfs)
            + '</cellXfs><cellStyles count="1">'
            '<cellStyle name="Normal" xfId="0" builtinId="0"/>'
            '</cellStyles></styleSheet>').encode("utf-8")


def _sheet_xml(accounts, label, generated):
    end = 4 + len(accounts)
    header = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              f'<dimension ref="A1:G{end}"/>'
              '<sheetViews><sheetView workbookViewId="0">'
              '<pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/>'
              '<selection pane="bottomLeft" activeCell="A5" sqref="A5"/>'
              '</sheetView></sheetViews>'
              '<sheetFormatPr defaultRowHeight="20"/>'
              '<cols>'
              '<col min="1" max="1" width="34" customWidth="1"/>'
              '<col min="2" max="2" width="51" customWidth="1"/>'
              '<col min="3" max="3" width="18" customWidth="1"/>'
              '<col min="4" max="4" width="21" customWidth="1"/>'
              '<col min="5" max="5" width="18" customWidth="1"/>'
              '<col min="6" max="7" width="23" customWidth="1"/>'
              '</cols><sheetData>')
    rows = [header]
    rows.append('<row r="1" ht="37" customHeight="1">'
                + _inline_cell("A1", "CONTROLE DE CONTAS  /  EXPORTAÇÃO", 1)
                + "".join(f'<c r="{c}1" s="1"/>' for c in "BCDEFG")
                + '</row>')
    summary = f"{label}  •  {len(accounts)} lançamento(s)  •  {generated.strftime('%d/%m/%Y %H:%M')}"
    rows.append('<row r="2" ht="27" customHeight="1">'
                + _inline_cell("A2", summary, 2)
                + "".join(f'<c r="{c}2" s="2"/>' for c in "BCDEFG")
                + '</row>')
    rows.append('<row r="3" ht="9" customHeight="1"/>')
    rows.append('<row r="4" ht="32" customHeight="1">'
                + "".join(_inline_cell(f"{col}4", value, 3)
                          for col, value in zip("ABCDEFG", COLUMNS))
                + '</row>')
    for i, entry in enumerate(accounts, start=5):
        regular = 4 if i % 2 else 5
        is_alternate = regular == 5
        status = str(entry.get("status") or "")
        status_style = {"Vencida": 12, "Próxima": 13,
                        "Paga": 14, "Em dia": 14}.get(status, regular)
        cells = [
            _inline_cell(f"A{i}", entry.get("fornecedor"), regular),
            _inline_cell(f"B{i}", entry.get("observacoes"), regular),
            _date_cell(f"C{i}", entry.get("vencimento"), 7 if is_alternate else 6),
            _number_cell(f"D{i}", entry.get("valor"), 9 if is_alternate else 8),
            _inline_cell(f"E{i}", status, status_style),
            _date_cell(f"F{i}", entry.get("suggested_at"), 11 if is_alternate else 10, True),
            _date_cell(f"G{i}", entry.get("enviado_pagamento_em"), 11 if is_alternate else 10, True)
        ]
        rows.append(f'<row r="{i}" ht="24" customHeight="1">'
                    + "".join(cells) + '</row>')
    rows.append('</sheetData><mergeCells count="2">'
                '<mergeCell ref="A1:G1"/><mergeCell ref="A2:G2"/>'
                '</mergeCells>'
                f'<autoFilter ref="A4:G{end}"/>'
                '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" '
                'header="0.2" footer="0.2"/>'
                '</worksheet>')
    return "".join(rows).encode("utf-8")


def build_xlsx(accounts, label="Lista filtrada", generated=None):
    """Returns binary XLSX for an already selected and ordered list of accounts."""
    if not isinstance(accounts, list) or len(accounts) > MAX_ROWS:
        raise ValueError(f"A exportação permite até {MAX_ROWS} lançamentos.")
    generated = generated or datetime.now()
    label = _text(label)[:120]
    sheet = _sheet_xml(accounts, label, generated)
    ns_package = "http://schemas.openxmlformats.org/package/2006/relationships"
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    rels = (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{ns_package}">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>')
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Lançamentos" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    book_rels = (f'<?xml version="1.0" encoding="UTF-8"?>'
                 f'<Relationships xmlns="{ns_package}">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                 '</Relationships>')
    result = BytesIO()
    with ZipFile(result, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for path, contents in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", rels),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", book_rels),
            ("xl/styles.xml", _styles_xml()),
            ("xl/worksheets/sheet1.xml", sheet),
        ):
            archive.writestr(path, contents)
    return result.getvalue()
