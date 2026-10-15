"""Data files: CSV and XLSX tables, JSON / YAML / XML trees. Our own code, no external engine.

Tables become JSON/YAML/XML as a list of records (the first row is the header); a tree becomes a table
when it holds a list of records somewhere inside (``[{...}, ...]`` or ``{"items": [{...}, ...]}``).
Only the first sheet of a workbook is read; dates come out as ISO text (2026-10-15).
"""

from __future__ import annotations

import csv
import io
import json
import locale
import re
import zipfile
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import Context, ConversionError, Converter

TABLES = ("csv", "xlsx")
TREES = ("json", "yaml", "xml")

Table = list[list[str]]


class DataConverter(Converter):
    name = "MorphTo data"
    cost = 5  # cheaper than LibreOffice for CSV <-> XLSX

    def pairs(self) -> Iterable[tuple[str, str]]:
        every = TABLES + TREES
        return [(s, d) for s in every for d in every if s != d]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        s, d = src_fmt.id, dst_fmt.id
        if s in TABLES:
            table = read_csv(src) if s == "csv" else read_xlsx(src)
            ctx.progress(0.5)
            if d in TABLES:
                write_table(table, dst, d)
            else:
                write_tree(table_to_records(table), dst, d)
        else:
            tree = read_tree(src, s)
            ctx.progress(0.5)
            if d in TABLES:
                write_table(records_to_table(tree), dst, d)
            else:
                write_tree(tree, dst, d)


# ---- reading ----


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")  # e.g. cp1251 from old Excel


def read_csv(path: Path) -> Table:
    text = _read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect) if row]


def read_tree(path: Path, fmt: str):
    text = _read_text(path)
    try:
        if fmt == "json":
            return json.loads(text)
        if fmt == "yaml":
            import yaml

            docs = list(yaml.safe_load_all(text))
            return docs[0] if len(docs) == 1 else docs
        root = ET.fromstring(text.encode("utf-8"))
        return {_local(root.tag): _xml_to_tree(root)}
    except (ValueError, ET.ParseError) as exc:
        raise ConversionError(f"Cannot read the {fmt.upper()} file: {exc}") from exc
    except Exception as exc:  # yaml.YAMLError without importing yaml at module level
        raise ConversionError(f"Cannot read the {fmt.upper()} file: {exc}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_to_tree(el: ET.Element):
    """xmltodict-style: attributes as "@name", text as "#text", repeated children as lists."""
    node: dict = {f"@{_local(k)}": _typed(v) for k, v in el.attrib.items()}
    for child in el:
        key, value = _local(child.tag), _xml_to_tree(child)
        if key in node:
            if not isinstance(node[key], list):
                node[key] = [node[key]]
            node[key].append(value)
        else:
            node[key] = value
    text = (el.text or "").strip()
    if not node:
        return _typed(text) if text else None
    if text:
        node["#text"] = text
    return node


# XLSX: a zip of XML parts. We need the first sheet, shared strings and which cells are dates.
_BUILTIN_KINDS = {14: "date", 15: "date", 16: "date", 17: "date", 22: "datetime",
                  18: "time", 19: "time", 20: "time", 21: "time", 45: "time", 46: "time", 47: "time"}  # fmt: skip


def read_xlsx(path: Path) -> Table:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ConversionError("Not a valid XLSX file") from exc
    with zf:
        names = set(zf.namelist())

        def xml(name: str) -> ET.Element | None:
            return ET.fromstring(zf.read(name)) if name in names else None

        sheet = _first_sheet(xml("xl/workbook.xml"), xml("xl/_rels/workbook.xml.rels"))
        root = xml(sheet)
        if root is None:
            raise ConversionError("The workbook has no sheets")
        shared = [_text_of(si) for si in _children(xml("xl/sharedStrings.xml"), "si")]
        kinds = _cell_kinds(xml("xl/styles.xml"))
        table: Table = []
        for row in _iter(root, "row"):
            values: dict[int, str] = {}
            for i, c in enumerate(_children(row, "c")):
                col = _column(c.get("r")) if c.get("r") else i
                values[col] = _cell_value(c, shared, kinds)
            if values:
                index = int(row.get("r", len(table) + 1)) - 1
                while len(table) < index:
                    table.append([])
                width = max(values) + 1
                table.append([values.get(i, "") for i in range(width)])
    while table and not any(table[-1]):
        table.pop()
    return table or [[]]


def _iter(el: ET.Element | None, name: str):
    if el is None:
        return
    for node in el.iter():
        if _local(node.tag) == name:
            yield node


def _children(el: ET.Element | None, name: str) -> list[ET.Element]:
    return [] if el is None else [c for c in el if _local(c.tag) == name]


def _text_of(el: ET.Element) -> str:
    """Text of a shared/inline string, rich runs included, phonetic hints (rPh) skipped."""
    parts = []
    for node in el:
        tag = _local(node.tag)
        if tag == "t":
            parts.append(node.text or "")
        elif tag == "r":
            parts.extend(t.text or "" for t in node if _local(t.tag) == "t")
    return "".join(parts)


def _first_sheet(workbook: ET.Element | None, rels: ET.Element | None) -> str:
    sheets = list(_iter(workbook, "sheet"))
    if sheets and rels is not None:
        rid = next((v for k, v in sheets[0].attrib.items() if _local(k) == "id"), None)
        for rel in _iter(rels, "Relationship"):
            if rel.get("Id") == rid:
                target = rel.get("Target", "")
                return target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    return "xl/worksheets/sheet1.xml"


def _cell_kinds(styles: ET.Element | None) -> list[str]:
    """For each cell style index: "date", "time", "datetime" or "" (plain)."""
    custom = {int(f.get("numFmtId", 0)): f.get("formatCode", "") for f in _iter(styles, "numFmt")}
    xfs = next(_iter(styles, "cellXfs"), None)
    kinds = []
    for xf in _children(xfs, "xf"):
        fid = int(xf.get("numFmtId", 0))
        kinds.append(_format_kind(custom[fid]) if fid in custom else _BUILTIN_KINDS.get(fid, ""))
    return kinds


def _format_kind(code: str) -> str:
    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code).lower()  # quoted text, [Red], escapes
    has_date = "y" in code or "d" in code
    has_time = "h" in code or "s" in code
    if has_date and has_time:
        return "datetime"
    return "date" if has_date else "time" if has_time else ""


_EPOCH = datetime(1899, 12, 30)


def _cell_value(c: ET.Element, shared: list[str], kinds: list[str]) -> str:
    kind = c.get("t", "n")
    if kind == "inlineStr":
        node = next((n for n in c if _local(n.tag) == "is"), None)
        return _text_of(node) if node is not None else ""
    v = next((n.text or "" for n in c if _local(n.tag) == "v"), "")
    if kind == "s":
        return shared[int(v)] if v.isdigit() and int(v) < len(shared) else ""
    if kind == "b":
        return "TRUE" if v == "1" else "FALSE"
    if kind != "n" or not v:
        return v
    style = int(c.get("s", 0))
    fmt = kinds[style] if style < len(kinds) else ""
    try:
        number = float(v)
    except ValueError:
        return v
    if fmt:
        moment = _EPOCH + timedelta(days=number)
        if fmt == "date" and number == int(number):
            return moment.date().isoformat()
        if fmt == "time" and number < 1:
            return moment.time().isoformat(timespec="seconds")
        return moment.isoformat(sep=" ", timespec="seconds")
    return _number_text(number)


def _number_text(number: float) -> str:
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return format(number, ".15g")  # 0.30000000000000004 -> 0.3, as Excel shows it


def _column(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


# ---- table <-> tree ----

_INT = re.compile(r"-?(0|[1-9]\d{0,17})")
_FLOAT = re.compile(r"-?(0|[1-9]\d*)\.\d+([eE][-+]?\d+)?")


def _typed(value: str):
    """Numbers become numbers in JSON/YAML; "007", "+7 999" or "1,5" stay text."""
    if _INT.fullmatch(value):
        return int(value)
    if _FLOAT.fullmatch(value):
        return float(value)
    return value


def table_to_records(table: Table) -> list[dict]:
    if not table or not table[0]:
        return []
    header, seen = [], set()
    for i, name in enumerate(table[0]):
        key = name.strip() or f"column_{i + 1}"
        while key in seen:
            key += "_"
        seen.add(key)
        header.append(key)
    records = []
    for row in table[1:]:
        if any(cell.strip() for cell in row):
            records.append({k: _typed(row[i]) if i < len(row) else "" for i, k in enumerate(header)})
    return records


def find_records(tree) -> list | None:
    """The first list of records (dicts) or rows (lists) in a tree, breadth first."""
    queue = [tree]
    while queue:
        node = queue.pop(0)
        if (
            isinstance(node, list)
            and node
            and (all(isinstance(x, dict) for x in node) or all(isinstance(x, list) for x in node))
        ):
            return node
        if isinstance(node, dict):
            queue.extend(node.values())
    return None


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _number_text(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def records_to_table(tree) -> Table:
    records = find_records(tree)
    if records is None:
        while isinstance(tree, dict) and len(tree) == 1 and isinstance(next(iter(tree.values())), dict):
            tree = next(iter(tree.values()))  # {"root": {"row": {...}}} -> the single record
        if isinstance(tree, dict) and tree and all(not isinstance(v, (dict, list)) for v in tree.values()):
            records = [tree]  # one flat object: one row
        else:
            raise ConversionError("The file has no table-like data (a list of records) to put into rows")
    if all(isinstance(r, list) for r in records):
        return [[_cell(v) for v in row] for row in records]
    header: list[str] = []
    for record in records:
        for key in record:
            if key not in header:
                header.append(key)
    # XML attributes ("@id") and text ("#text") read better without the markers.
    names = [k[1:] if k[:1] in "@#" and k[1:] not in header else k for k in header]
    return [names] + [[_cell(r.get(k)) for k in header] for r in records]


# ---- writing ----


def write_table(table: Table, dst: Path, fmt: str) -> None:
    if fmt == "csv":
        # BOM: Excel otherwise opens UTF-8 CSV as ANSI and garbles non-English text.
        with dst.open("w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(table)
    else:
        write_xlsx(table, dst)


def write_tree(tree, dst: Path, fmt: str) -> None:
    if fmt == "json":
        text = json.dumps(tree, ensure_ascii=False, indent=2) + "\n"
    elif fmt == "yaml":
        import yaml

        text = yaml.safe_dump(tree, allow_unicode=True, sort_keys=False, default_flow_style=False)
    else:
        text = tree_to_xml(tree)
    dst.write_text(text, encoding="utf-8")


_BAD_TAG = re.compile(r"[^\w.-]", re.UNICODE)


def _tag(name: str) -> str:
    name = _BAD_TAG.sub("_", str(name).strip()) or "item"
    return name if name[0].isalpha() or name[0] == "_" else f"_{name}"


def tree_to_xml(tree) -> str:
    if isinstance(tree, dict) and len(tree) == 1 and not str(next(iter(tree))).startswith(("@", "#")):
        name, value = next(iter(tree.items()))
        if isinstance(value, list):
            body = _xml_element("root", {name: value}, 0)
        else:
            body = _xml_element(name, value, 0)
    elif isinstance(tree, list):  # a JSON array: <root><item>...</item>...</root>
        body = "<root>\n" + "".join(_xml_element("item", item, 1) for item in tree) + "</root>\n"
    else:
        body = _xml_element("root", tree, 0)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


def _xml_element(name: str, value, depth: int) -> str:
    pad = "  " * depth
    tag = _tag(name)
    if isinstance(value, list):
        return "".join(_xml_element(name, item, depth) for item in value)
    if isinstance(value, dict):
        attrs = "".join(
            f" {_tag(k[1:])}={quoteattr(_cell(v))}" for k, v in value.items() if str(k).startswith("@")
        )
        text = value.get("#text")
        children = [(k, v) for k, v in value.items() if not str(k).startswith(("@", "#"))]
        if not children:
            return (
                f"{pad}<{tag}{attrs}>{escape(_cell(text))}</{tag}>\n"
                if text is not None
                else f"{pad}<{tag}{attrs}/>\n"
            )
        inner = "".join(
            _xml_element(k, v, depth + 1) if not isinstance(v, list) else
            "".join(_xml_element(k, item, depth + 1) for item in v)
            for k, v in children
        )  # fmt: skip
        lead = f"{pad}  {escape(_cell(text))}\n" if text is not None else ""
        return f"{pad}<{tag}{attrs}>\n{lead}{inner}{pad}</{tag}>\n"
    if value is None:
        return f"{pad}<{tag}/>\n"
    return f"{pad}<{tag}>{escape(_cell(value))}</{tag}>\n"


_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col_name(i: int) -> str:
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


def write_xlsx(table: Table, dst: Path) -> None:
    """A minimal valid workbook: one sheet, numbers as numbers, the first row in bold."""
    rows = []
    for r, row in enumerate(table, 1):
        cells = []
        for c, value in enumerate(row):
            if value == "":
                continue
            ref = f"{_col_name(c)}{r}"
            style = ' s="1"' if r == 1 else ""
            if isinstance(_typed(value), (int, float)) and len(value) < 16:
                cells.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
            else:
                text = escape(_ILLEGAL_XML.sub("", value))
                cells.append(
                    f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
                )
        rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    widths = [
        max((len(row[c]) for row in table[:200] if c < len(row)), default=8)
        for c in range(max(map(len, table), default=0))
    ]
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{min(60, max(8, w + 2))}" customWidth="1"/>'
        for i, w in enumerate(widths)
    )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    sheet = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet {ns} {rel_ns}>'
        + (f"<cols>{cols}</cols>" if cols else "")
        + f"<sheetData>{''.join(rows)}</sheetData></worksheet>"
    )
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook {ns} {rel_ns}>'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet {ns}>'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            "</styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
