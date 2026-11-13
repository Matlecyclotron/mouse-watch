# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Export des données d'activité.

  - ODS (LibreOffice) : un classeur à trois feuilles, Passages, Nuits et
    Notice, avec des cellules typées (dates, heures, nombres) et la ligne
    d'en-tête figée. Le fichier est écrit directement (archive ZIP + XML),
    sans bibliothèque supplémentaire.
  - CSV : fichiers texte UTF-8, virgule comme séparateur et point décimal,
    pour l'archive sur le NAS et les scripts.
"""
import csv
import datetime as dt
import io
import zipfile
from xml.sax.saxutils import escape

from stats import NIGHT_COLUMNS, PASSAGE_COLUMNS

SOURCE_NOTE = [
    "Données produites par rtsp-web (mouse-watch).",
    "Éphémérides (soleil, lune) : calculées localement à partir des coordonnées du site.",
    "Météo : Open-Meteo (https://open-meteo.com), données sous licence CC BY 4.0. "
    "Ce sont des données de modèles météorologiques, pas des mesures prises sur place.",
    "Un passage mesure une activité, pas un nombre d'animaux : le même animal peut passer "
    "plusieurs fois, et l'entrée ne se distingue pas de la sortie.",
    "Les passages supprimés restent comptés ; ceux marqués « fausse alerte » sont exclus.",
]


def tables(stats, **filters) -> list:
    """[(nom de feuille, colonnes, lignes)] pour les passages et les nuits."""
    return [("Passages", PASSAGE_COLUMNS, stats.passages(**filters)),
            ("Nuits", NIGHT_COLUMNS, stats.nights(**filters))]


# ----------------------------------------------------------------------- CSV
def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    return value


def csv_bytes(columns, rows) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([key for key, *_ in columns])
    for row in rows:
        writer.writerow([_csv_value(row.get(key)) for key, *_ in columns])
    return out.getvalue().encode("utf-8")


def notice_text() -> str:
    lines = list(SOURCE_NOTE) + [""]
    for title, columns in (("passages.csv — une ligne par passage", PASSAGE_COLUMNS),
                           ("nuits.csv — une ligne par nuit et par caméra", NIGHT_COLUMNS)):
        lines += [title, "-" * len(title)]
        lines += [f"{key} : {label}. {desc}" for key, label, _w, desc in columns]
        lines.append("")
    return "\n".join(lines)


def csv_files(stats, **filters) -> dict:
    (_, pcols, prows), (_, ncols, nrows) = tables(stats, **filters)
    return {"passages.csv": csv_bytes(pcols, prows), "nuits.csv": csv_bytes(ncols, nrows),
            "LISEZMOI.txt": notice_text().encode("utf-8")}


def csv_zip(stats, **filters) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in csv_files(stats, **filters).items():
            zf.writestr(name, data)
    return buf.getvalue()


# ----------------------------------------------------------------------- ODS
_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
       'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
       'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" '
       'xmlns:ooo="http://openoffice.org/2004/office" '
       'office:version="1.3"')
_MIME = "application/vnd.oasis.opendocument.spreadsheet"


def _cell(value) -> str:
    if value is None or value == "":
        return "<table:table-cell/>"
    if isinstance(value, bool):
        value = "oui" if value else "non"
    if isinstance(value, dt.datetime):
        value = value.replace(microsecond=0)
        return (f'<table:table-cell table:style-name="ceDate" office:value-type="date" '
                f'office:date-value="{value.isoformat()}"><text:p>{value.isoformat(" ")}</text:p>'
                f'</table:table-cell>')
    if isinstance(value, dt.date):
        return (f'<table:table-cell table:style-name="ceDate" office:value-type="date" '
                f'office:date-value="{value.isoformat()}"><text:p>{value.strftime("%d/%m/%Y")}</text:p>'
                f'</table:table-cell>')
    if isinstance(value, dt.time):
        iso = f"PT{value.hour:02d}H{value.minute:02d}M{value.second:02d}S"
        return (f'<table:table-cell table:style-name="ceTime" office:value-type="time" '
                f'office:time-value="{iso}"><text:p>{value.strftime("%H:%M:%S")}</text:p>'
                f'</table:table-cell>')
    if isinstance(value, (int, float)):
        return (f'<table:table-cell office:value-type="float" office:value="{value!r}">'
                f'<text:p>{value}</text:p></table:table-cell>')
    return (f'<table:table-cell office:value-type="string"><text:p>{escape(str(value))}</text:p>'
            f'</table:table-cell>')


def _sheet(index: int, name: str, columns, rows) -> str:
    parts = [f'<table:table table:name="{escape(name)}">']
    parts += [f'<table:table-column table:style-name="co{index}_{i}"/>' for i in range(len(columns))]
    parts.append("<table:table-row>" + "".join(
        f'<table:table-cell table:style-name="ceHead" office:value-type="string">'
        f'<text:p>{escape(label)}</text:p></table:table-cell>' for _k, label, *_ in columns)
        + "</table:table-row>")
    for row in rows:
        parts.append("<table:table-row>" + "".join(_cell(row.get(key)) for key, *_ in columns)
                     + "</table:table-row>")
    parts.append("</table:table>")
    return "".join(parts)


def _notice_rows():
    rows = [{"a": line} for line in SOURCE_NOTE] + [{}]
    for title, columns in (("Feuille « Passages » : une ligne par passage", PASSAGE_COLUMNS),
                           ("Feuille « Nuits » : une ligne par nuit et par caméra", NIGHT_COLUMNS)):
        rows.append({"a": title})
        rows += [{"a": label, "b": desc} for _k, label, _w, desc in columns]
        rows.append({})
    return rows


def _freeze(name: str) -> str:
    item = lambda key, kind, value: (f'<config:config-item config:name="{key}" '
                                     f'config:type="{kind}">{value}</config:config-item>')
    return (f'<config:config-item-map-entry config:name="{escape(name)}">'
            + item("HorizontalSplitMode", "short", 0) + item("VerticalSplitMode", "short", 2)
            + item("HorizontalSplitPosition", "int", 0) + item("VerticalSplitPosition", "int", 1)
            + item("ActiveSplitRange", "short", 2) + item("PositionLeft", "int", 0)
            + item("PositionRight", "int", 0) + item("PositionTop", "int", 0)
            + item("PositionBottom", "int", 1) + "</config:config-item-map-entry>")


def ods_bytes(stats, **filters) -> bytes:
    sheets = tables(stats, **filters)
    notice_cols = [("a", "Colonne", 7.0, ""), ("b", "Signification", 22.0, "")]
    sheets.append(("Notice", notice_cols, _notice_rows()))

    styles = []
    for si, (_name, columns, _rows) in enumerate(sheets):
        styles += [f'<style:style style:name="co{si}_{i}" style:family="table-column">'
                   f'<style:table-column-properties style:column-width="{width}cm"/></style:style>'
                   for i, (_k, _l, width, *_) in enumerate(columns)]
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {_NS}>'
        '<office:automatic-styles>' + "".join(styles) +
        '<number:date-style style:name="Ndate"><number:day number:style="long"/><number:text>/</number:text>'
        '<number:month number:style="long"/><number:text>/</number:text><number:year number:style="long"/>'
        '</number:date-style>'
        '<number:time-style style:name="Ntime"><number:hours number:style="long"/><number:text>:</number:text>'
        '<number:minutes number:style="long"/><number:text>:</number:text><number:seconds number:style="long"/>'
        '</number:time-style>'
        '<style:style style:name="ceDate" style:family="table-cell" style:data-style-name="Ndate"/>'
        '<style:style style:name="ceTime" style:family="table-cell" style:data-style-name="Ntime"/>'
        '<style:style style:name="ceHead" style:family="table-cell">'
        '<style:text-properties fo:font-weight="bold"/></style:style>'
        '</office:automatic-styles><office:body><office:spreadsheet>'
        + "".join(_sheet(i, name, cols, rows) for i, (name, cols, rows) in enumerate(sheets))
        + '</office:spreadsheet></office:body></office:document-content>')
    settings = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-settings {_NS}><office:settings>'
        '<config:config-item-set config:name="ooo:view-settings">'
        '<config:config-item-map-indexed config:name="Views"><config:config-item-map-entry>'
        '<config:config-item config:name="ViewId" config:type="string">view1</config:config-item>'
        '<config:config-item-map-named config:name="Tables">'
        + "".join(_freeze(name) for name, _c, _r in sheets[:2]) +
        '</config:config-item-map-named>'
        '<config:config-item config:name="ActiveTable" config:type="string">Passages</config:config-item>'
        '</config:config-item-map-entry></config:config-item-map-indexed>'
        '</config:config-item-set></office:settings></office:document-settings>')
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.3">'
        f'<manifest:file-entry manifest:full-path="/" manifest:version="1.3" manifest:media-type="{_MIME}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="settings.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Le type MIME doit être le premier fichier de l'archive, non compressé
        zf.writestr(zipfile.ZipInfo("mimetype"), _MIME, compress_type=zipfile.ZIP_STORED)
        for name, data in (("content.xml", content), ("settings.xml", settings),
                           ("META-INF/manifest.xml", manifest)):
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
