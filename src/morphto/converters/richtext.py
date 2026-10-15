"""Text with markup: Markdown, HTML, FictionBook, EPUB and plain text, plus DOCX and PDF output.

Every reader turns its file into one simple document model (headings, paragraphs, lists, quotes, code,
tables, images) and every writer renders that model, so each new format works with all the others.
PDF is printed by Microsoft Edge, which ships with Windows 10 and 11, in headless mode.
"""

from __future__ import annotations

import base64
import contextlib
import html
import io
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from morphto.core.formats import Format
from morphto.core.options import Options
from morphto.core.registry import Context, ConversionError, Converter

READ = ("md", "html", "fb2", "epub", "txt")
WRITE = ("md", "html", "docx", "epub", "fb2", "txt", "pdf")

# ---- document model ----


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False
    link: str | None = None


Inline = list[Span]


@dataclass
class Heading:
    level: int
    inline: Inline


@dataclass
class Para:
    inline: Inline


@dataclass
class ListBlock:
    ordered: bool
    items: list[list]  # each item is a list of blocks
    start: int = 1


@dataclass
class Code:
    text: str


@dataclass
class Quote:
    blocks: list


@dataclass
class Rule:
    pass


@dataclass
class Table:
    rows: list[list[Inline]]
    header: bool = True


@dataclass
class Image:
    data: bytes
    ext: str  # png, jpg, gif ...
    alt: str = ""


@dataclass
class Document:
    title: str = ""
    author: str = ""
    lang: str = ""
    blocks: list = field(default_factory=list)
    cover: Image | None = None


def plain(inline: Inline) -> str:
    return "".join(s.text for s in inline)


def _clean_inline(inline: Inline) -> Inline:
    """Merge neighbours with the same marks, collapse whitespace, trim the ends."""
    out: Inline = []
    for s in inline:
        text = (
            re.sub(r"[ \t\r\f\v]*\n[ \t\r\f\v]*", "\n", re.sub(r"[ \t\r\f\v]+", " ", s.text))
            if not s.code
            else s.text
        )
        if not text:
            continue
        if out and (out[-1].bold, out[-1].italic, out[-1].code, out[-1].strike, out[-1].link) == (
            s.bold, s.italic, s.code, s.strike, s.link
        ):  # fmt: skip
            out[-1] = Span(out[-1].text + text, s.bold, s.italic, s.code, s.strike, s.link)
        else:
            out.append(Span(text, s.bold, s.italic, s.code, s.strike, s.link))
    if out:
        out[0].text = out[0].text.lstrip()  # also non-breaking spaces left by word processors
        out[-1].text = out[-1].text.rstrip()
    return [s for s in out if s.text]


def _image_ext(data: bytes, hint: str = "") -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if b"<svg" in data[:512]:
        return "svg"
    return hint.lower().lstrip(".").replace("jpeg", "jpg") or "png"


def _first_heading(blocks: list) -> str:
    return next((plain(b.inline) for b in blocks if isinstance(b, Heading)), "")


# ---- readers ----


def read_txt(path: Path) -> Document:
    from morphto.converters.data import _read_text

    text = _read_text(path).replace("\r\n", "\n")
    chunks = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    return Document(blocks=[Para([Span(c.strip())]) for c in chunks if c.strip()])


def read_markdown(path: Path) -> Document:
    from markdown_it import MarkdownIt

    from morphto.converters.data import _read_text

    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    tokens = md.parse(_read_text(path))
    blocks, _ = _md_blocks(tokens, 0, None, path.parent)
    return Document(blocks=blocks)


def _md_blocks(tokens, i: int, until: str | None, base: Path) -> tuple[list, int]:
    blocks: list = []
    while i < len(tokens):
        t = tokens[i]
        if until and t.type == until:
            return blocks, i + 1
        if t.type == "heading_open":
            inline, images = _md_inline(tokens[i + 1].children or [], base)
            blocks.append(Heading(int(t.tag[1]), inline))
            blocks.extend(images)
            i += 3
        elif t.type == "paragraph_open":
            inline, images = _md_inline(tokens[i + 1].children or [], base)
            if inline:
                blocks.append(Para(inline))
            blocks.extend(images)
            i += 3
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            ordered = t.type == "ordered_list_open"
            start = int(t.attrGet("start") or 1)
            items, i = [], i + 1
            close = "ordered_list_close" if ordered else "bullet_list_close"
            while tokens[i].type != close:
                item, i = _md_blocks(tokens, i + 1, "list_item_close", base)
                items.append(item)
            blocks.append(ListBlock(ordered, items, start))
            i += 1
        elif t.type == "blockquote_open":
            inner, i = _md_blocks(tokens, i + 1, "blockquote_close", base)
            blocks.append(Quote(inner))
        elif t.type in ("fence", "code_block"):
            blocks.append(Code(t.content.rstrip("\n")))
            i += 1
        elif t.type == "hr":
            blocks.append(Rule())
            i += 1
        elif t.type == "table_open":
            rows, header, i = [], False, i + 1
            while tokens[i].type != "table_close":
                if tokens[i].type == "thead_open":
                    header = True
                if tokens[i].type == "tr_open":
                    row, i = [], i + 1
                    while tokens[i].type != "tr_close":
                        if tokens[i].type == "inline":
                            row.append(_md_inline(tokens[i].children or [], base)[0])
                        i += 1
                    rows.append(row)
                i += 1
            blocks.append(Table(rows, header))
            i += 1
        elif t.type == "html_block":
            blocks.extend(html_to_blocks(t.content, _FileResolver(base)))
            i += 1
        else:
            i += 1
    return blocks, i


def _md_inline(children, base: Path) -> tuple[Inline, list]:
    spans: Inline = []
    images: list = []
    bold = italic = strike = 0
    link: list[str] = []
    for c in children:
        if c.type == "text":
            spans.append(Span(c.content, bold > 0, italic > 0, False, strike > 0, link[-1] if link else None))
        elif c.type == "code_inline":
            spans.append(Span(c.content, bold > 0, italic > 0, True, strike > 0, link[-1] if link else None))
        elif c.type in ("softbreak",):
            spans.append(Span(" "))
        elif c.type == "hardbreak":
            spans.append(Span("\n"))
        elif c.type == "strong_open":
            bold += 1
        elif c.type == "strong_close":
            bold -= 1
        elif c.type == "em_open":
            italic += 1
        elif c.type == "em_close":
            italic -= 1
        elif c.type == "s_open":
            strike += 1
        elif c.type == "s_close":
            strike -= 1
        elif c.type == "link_open":
            link.append(c.attrGet("href") or "")
        elif c.type == "link_close" and link:
            link.pop()
        elif c.type == "image":
            image = _FileResolver(base).image(c.attrGet("src") or "", c.content)
            if image:
                images.append(image)
    return _clean_inline(spans), images


class _FileResolver:
    """Finds images referenced by a document: local files, data: URIs; never the network."""

    def __init__(self, base: Path | None = None, zf: zipfile.ZipFile | None = None, doc: str = "") -> None:
        self.base, self.zf, self.doc = base, zf, doc

    def read(self, src: str) -> bytes | None:
        if src.startswith("data:"):
            header, _, payload = src.partition(",")
            try:
                return base64.b64decode(payload) if ";base64" in header else unquote(payload).encode()
            except ValueError:
                return None
        parsed = urlparse(src)
        if parsed.scheme in ("http", "https"):
            return None  # offline by design
        name = unquote(parsed.path)
        if self.zf is not None:
            full = posixpath.normpath(posixpath.join(posixpath.dirname(self.doc), name))
            try:
                return self.zf.read(full)
            except KeyError:
                return None
        if self.base is None:
            return None
        path = Path(name) if Path(name).is_absolute() else self.base / name
        try:
            return path.read_bytes() if path.is_file() and path.stat().st_size < 50_000_000 else None
        except OSError:
            return None

    def image(self, src: str, alt: str = "") -> Image | None:
        data = self.read(src)
        if not data:
            return None
        return Image(data, _image_ext(data, posixpath.splitext(urlparse(src).path)[1]), alt)


# HTML -> a small DOM -> blocks.

_VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source"}
_SKIP = {"script", "style", "head", "title", "noscript", "template", "svg", "math"}
_BLOCK = {
    "p", "div", "section", "article", "main", "header", "footer", "body", "html", "nav", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "pre", "blockquote", "hr", "table",
    "figure", "figcaption", "dl", "dt", "dd", "center", "address",
}  # fmt: skip


@dataclass
class _Node:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)


class _DomBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.title = ""
        self.css = ""
        self._in_title = False
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag == "style":
            self._in_style = True
        if tag in ("p", "li", "dt", "dd") and any(n.tag == tag for n in self.stack[-1:]):
            self.stack.pop()  # <p>a<p>b, <li>a<li>b: the previous one ends here
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "style":
            self._in_style = False
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_style:
            self.css += data
        self.stack[-1].children.append(data)

    def finish(self) -> _Node:
        self.close()
        _apply_styles(self.root, _parse_css(self.css))
        return self.root


def _parse_css(css: str) -> dict[str, str]:
    """class name -> its declarations, from simple rules such as `.T1 { font-weight: bold }`."""
    rules: dict[str, str] = {}
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for selector in selectors.split(","):
            match = re.fullmatch(r"\s*[\w-]*\.([\w-]+)\s*", selector)
            if match:
                rules[match[1]] = rules.get(match[1], "") + ";" + body
    return rules


def _style_flags(declarations: str) -> set[str]:
    d = declarations.lower().replace(" ", "")
    flags = set()
    if re.search(r"font-weight:(bold|bolder|[6-9]00)", d):
        flags.add("b")
    if re.search(r"font-style:(italic|oblique)", d):
        flags.add("i")
    if "line-through" in d:
        flags.add("s")
    if re.search(r"font-family:[^;]*(consolas|courier|mono|menlo|lucidaconsole)", d):
        flags.add("code")
    if "float:left" in d and "display:block" in d:
        flags.add("label")  # LibreOffice writes list numbers/bullets as floating spans
    return flags


def _apply_styles(node: _Node, rules: dict[str, str]) -> None:
    """Mark nodes whose CSS (class or inline) makes them bold, italic, monospace..."""
    for child in node.children:
        if isinstance(child, _Node):
            declarations = child.attrs.get("style") or ""
            for name in (child.attrs.get("class") or "").split():
                declarations += ";" + rules.get(name, "")
            flags = _style_flags(declarations) if declarations else set()
            if "quote" in (child.attrs.get("class") or "").lower():
                flags.add("quote")
            child.attrs["_flags"] = flags
            _apply_styles(child, rules)


def html_to_blocks(text: str, resolver: _FileResolver) -> list:
    builder = _DomBuilder()
    builder.feed(text)
    return _node_blocks(builder.finish(), resolver, preformatted=False)


def _node_blocks(node: _Node, res: _FileResolver, preformatted: bool) -> list:
    blocks: list = []
    pending: Inline = []

    def flush() -> None:
        inline = _clean_inline(pending)
        if inline:
            blocks.append(Para(inline))
        pending.clear()

    for child in node.children:
        if isinstance(child, str):
            pending.append(Span(child))
            continue
        tag = child.tag
        if tag in _SKIP:
            continue
        if tag not in _BLOCK and tag != "img":
            inline, images = _inline_of(child, res, Span(""))
            pending.extend(inline)
            if images:
                flush()
                blocks.extend(images)
            continue
        flush()
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            inline = _clean_inline(_inline_of(child, res, Span(""))[0])
            if inline:
                blocks.append(Heading(int(tag[1]), _unbold(inline)))
        elif tag in ("ul", "ol"):
            items = [
                _node_blocks(li, res, False)
                for li in child.children
                if isinstance(li, _Node) and li.tag == "li"
            ]
            start = int(child.attrs.get("start") or 1) if str(child.attrs.get("start", "1")).isdigit() else 1
            blocks.append(_tidy_list(ListBlock(tag == "ol", [i for i in items if i] or [[]], start)))
        elif tag == "pre":
            blocks.append(Code(_text_of_node(child).strip("\n")))
        elif tag == "blockquote":
            blocks.append(Quote(_node_blocks(child, res, False)))
        elif tag == "hr":
            blocks.append(Rule())
        elif tag == "img":
            image = res.image(child.attrs.get("src", ""), child.attrs.get("alt", ""))
            if image:
                blocks.append(image)
        elif tag == "table":
            rows = []
            header = False
            for tr in _find_all(child, "tr"):
                cells = [c for c in tr.children if isinstance(c, _Node) and c.tag in ("td", "th")]
                header = header or (not rows and any(c.tag == "th" for c in cells))
                rows.append([_clean_inline(_inline_of(c, res, Span(""))[0]) for c in cells])
            if (
                rows
                and not header
                and rows[0]
                and all(cell and all(s.bold for s in cell) for cell in rows[0])
            ):
                header = True  # word processors mark the header row by making it bold
            if header and rows:
                rows[0] = [_unbold(cell) for cell in rows[0]]  # header cells are bold anyway
            if rows:
                blocks.append(Table(rows, header))
        elif "code" in (flags := child.attrs.get("_flags") or set()) and tag in ("p", "div"):
            blocks.append(Code(_text_of_node(child).strip("\n")))  # a paragraph styled monospace
        elif "quote" in flags and tag in ("p", "div"):
            inline = _clean_inline(_inline_of(child, res, Span(""))[0])
            if inline:
                blocks.append(Quote([Para(inline)]))
        elif tag == "p":
            inline, images = _inline_of(child, res, Span(""))
            inline = _clean_inline(inline)
            if inline:
                blocks.append(Para(inline))
            blocks.extend(images)
        else:
            blocks.extend(_node_blocks(child, res, preformatted))
    flush()
    return _merge_neighbours(blocks)


def _unbold(inline: Inline) -> Inline:
    """Headings and table headers are bold by themselves; explicit bold there is noise."""
    return _clean_inline([Span(s.text, False, s.italic, s.code, s.strike, s.link) for s in inline])


def _tidy_list(lst: ListBlock) -> ListBlock:
    """Word processors nest lists as <li><ol>...</ol></li> with no text: attach such a sublist to the
    previous item, or unwrap it when it is all the list holds."""
    items: list[list] = []
    for item in lst.items:
        if len(item) == 1 and isinstance(item[0], ListBlock) and items:
            items[-1].append(item[0])
        else:
            items.append(item)
    if len(items) == 1 and len(items[0]) == 1 and isinstance(items[0][0], ListBlock):
        return items[0][0]
    return ListBlock(lst.ordered, items, lst.start)


def _merge_neighbours(blocks: list) -> list:
    """Consecutive code lines and quotes (one paragraph each in word processor HTML) become one block."""
    out: list = []
    for b in blocks:
        if isinstance(b, Code) and out and isinstance(out[-1], Code):
            out[-1] = Code(out[-1].text + "\n" + b.text)
        elif isinstance(b, Quote) and out and isinstance(out[-1], Quote):
            out[-1] = Quote(out[-1].blocks + b.blocks)
        else:
            out.append(b)
    return out


def _find_all(node: _Node, tag: str) -> list[_Node]:
    found = []
    for c in node.children:
        if isinstance(c, _Node):
            if c.tag == tag:
                found.append(c)
            elif c.tag != "table":
                found.extend(_find_all(c, tag))
    return found


def _text_of_node(node: _Node) -> str:
    return "".join(
        c if isinstance(c, str) else ("\n" if c.tag == "br" else _text_of_node(c)) for c in node.children
    )


def _inline_of(node: _Node, res: _FileResolver, style: Span) -> tuple[Inline, list]:
    tag = node.tag
    flags = node.attrs.get("_flags") or set()
    if "label" in flags:
        return [], []  # a list bullet/number drawn as text: our lists number themselves
    style = Span(
        "",
        style.bold or tag in ("b", "strong") or "b" in flags,
        style.italic or tag in ("i", "em", "cite", "dfn") or "i" in flags,
        style.code or tag in ("code", "kbd", "samp", "tt") or "code" in flags,
        style.strike or tag in ("s", "del", "strike") or "s" in flags,
        node.attrs.get("href") if tag == "a" and node.attrs.get("href") else style.link,
    )
    if tag == "br":
        return [Span("\n")], []
    if tag == "img":
        image = res.image(node.attrs.get("src", ""), node.attrs.get("alt", ""))
        return [], [image] if image else []
    spans: Inline = []
    images: list = []
    for c in node.children:
        if isinstance(c, str):
            spans.append(Span(c, style.bold, style.italic, style.code, style.strike, style.link))
        elif c.tag not in _SKIP:
            inner, imgs = _inline_of(c, res, style)
            spans.extend(inner)
            images.extend(imgs)
            if c.tag in _BLOCK:
                spans.append(Span(" "))
    return spans, images


def read_html(path: Path) -> Document:
    from morphto.converters.data import _read_text

    text = _read_text(path)
    builder = _DomBuilder()
    builder.feed(text)
    blocks = _node_blocks(builder.finish(), _FileResolver(path.parent), False)
    title = builder.title.strip()
    if title.startswith("- no title"):
        title = ""  # LibreOffice's placeholder
    return Document(title=title, blocks=blocks)


def read_epub(path: Path) -> Document:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ConversionError("Not a valid EPUB file") from exc
    with zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        opf_path = next(e.get("full-path") for e in container.iter() if e.tag.endswith("rootfile"))
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        doc = Document()
        for e in opf.iter():
            name = e.tag.rsplit("}", 1)[-1]
            if name == "title" and not doc.title:
                doc.title = (e.text or "").strip()
            elif name == "creator" and not doc.author:
                doc.author = (e.text or "").strip()
            elif name == "language" and not doc.lang:
                doc.lang = (e.text or "").strip()
        manifest = {e.get("id"): e for e in opf.iter() if e.tag.endswith("}item") or e.tag == "item"}
        spine = [e.get("idref") for e in opf.iter() if e.tag.endswith("itemref") or e.tag == "itemref"]
        cover_id = next(
            (e.get("content") for e in opf.iter() if e.tag.endswith("meta") and e.get("name") == "cover"),
            None,
        )
        cover_item = manifest.get(cover_id) or next(
            (m for m in manifest.values() if "cover-image" in (m.get("properties") or "")), None
        )
        if cover_item is not None:
            href = posixpath.normpath(posixpath.join(base, unquote(cover_item.get("href", ""))))
            try:
                data = zf.read(href)
                doc.cover = Image(data, _image_ext(data, posixpath.splitext(href)[1]))
            except KeyError:
                pass
        for idref in spine:
            item = manifest.get(idref)
            if item is None:
                continue
            href = posixpath.normpath(posixpath.join(base, unquote(item.get("href", ""))))
            try:
                text = zf.read(href).decode("utf-8", "replace")
            except KeyError:
                continue
            doc.blocks.extend(html_to_blocks(text, _FileResolver(zf=zf, doc=href)))
    if doc.cover and doc.blocks and isinstance(doc.blocks[0], Image) and doc.blocks[0].data == doc.cover.data:
        doc.blocks.pop(0)  # the cover page repeats the cover image
    return doc


FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
XLINK = "http://www.w3.org/1999/xlink"


def read_fb2(path: Path) -> Document:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ConversionError(f"Cannot read the FB2 file: {exc}") from exc

    def local(el: ET.Element) -> str:
        return el.tag.rsplit("}", 1)[-1]

    def href(el: ET.Element) -> str:
        return next((v for k, v in el.attrib.items() if k.endswith("href")), "")

    binaries = {}
    for el in root:
        if local(el) == "binary":
            with contextlib.suppress(ValueError):
                binaries[el.get("id")] = base64.b64decode(el.text or "")

    def image_of(el: ET.Element) -> Image | None:
        data = binaries.get(href(el).lstrip("#"))
        return Image(data, _image_ext(data)) if data else None

    doc = Document()
    info = next((e for e in root.iter() if local(e) == "title-info"), None)
    if info is not None:
        for e in info:
            name = local(e)
            if name == "book-title":
                doc.title = (e.text or "").strip()
            elif name == "author" and not doc.author:
                parts = [
                    (x.text or "").strip()
                    for x in e
                    if local(x) in ("first-name", "middle-name", "last-name")
                ]
                doc.author = " ".join(p for p in parts if p) or next(
                    (x.text or "" for x in e if local(x) == "nickname"), ""
                )
            elif name == "lang":
                doc.lang = (e.text or "").strip()
            elif name == "coverpage":
                img = next((x for x in e.iter() if local(x) == "image"), None)
                doc.cover = image_of(img) if img is not None else None

    def inline(el: ET.Element, style: Span) -> Inline:
        tag = local(el)
        style = Span(
            "",
            style.bold or tag == "strong",
            style.italic or tag == "emphasis",
            style.code or tag == "code",
            style.strike or tag == "strikethrough",
            href(el) if tag == "a" else style.link,
        )
        spans = [Span(el.text or "", style.bold, style.italic, style.code, style.strike, style.link)]
        for c in el:
            spans.extend(inline(c, style))
            spans.append(Span(c.tail or "", style.bold, style.italic, style.code, style.strike, style.link))
        return spans

    def blocks_of(el: ET.Element, depth: int) -> list:
        out: list = []
        for c in el:
            tag = local(c)
            if tag == "title":
                text = [_clean_inline(inline(p, Span(""))) for p in c if local(p) == "p"]
                joined: Inline = []
                for part in text:
                    if joined:
                        joined.append(Span(" "))
                    joined.extend(part)
                if joined:
                    out.append(Heading(min(depth, 6), joined))
            elif tag == "section":
                out.extend(blocks_of(c, depth + 1))
            elif tag in ("p", "v", "text-author"):
                para = _clean_inline(inline(c, Span("")))
                if para:
                    out.append(Para(para))
            elif tag == "subtitle":
                out.append(Heading(min(depth + 1, 6), _clean_inline(inline(c, Span("")))))
            elif tag in ("cite", "epigraph"):
                out.append(Quote(blocks_of(c, depth)))
            elif tag in ("poem", "stanza"):
                out.extend(blocks_of(c, depth))
            elif tag == "empty-line":
                pass
            elif tag == "image":
                image = image_of(c)
                if image:
                    out.append(image)
            elif tag == "table":
                rows = [
                    [_clean_inline(inline(td, Span(""))) for td in tr if local(td) in ("td", "th")]
                    for tr in c
                    if local(tr) == "tr"
                ]
                out.append(Table(rows, header=any(local(td) == "th" for td in next(iter(c), []))))
        return out

    for body in root:
        if local(body) == "body" and body.get("name") not in ("notes", "comments"):
            doc.blocks.extend(blocks_of(body, 1))
    return doc


def read_pdf_text(path: Path) -> Document:
    """A text PDF as paragraphs, for e-books. Lines are joined back into paragraphs."""
    import pypdfium2 as pdfium

    from morphto.converters.pdf import _lock

    with _lock:
        pdf = pdfium.PdfDocument(path)
        try:
            pages = []
            for i in range(len(pdf)):
                page = pdf[i]
                textpage = page.get_textpage()
                pages.append(textpage.get_text_range().replace("\r\n", "\n"))
                textpage.close()
                page.close()
            title = (pdf.get_metadata_dict().get("Title") or "").strip()
            author = (pdf.get_metadata_dict().get("Author") or "").strip()
        finally:
            pdf.close()
    paragraphs: list[str] = []
    current = ""
    for line in "\n".join(pages).split("\n"):
        line = line.strip()
        if not line:
            if current:
                paragraphs.append(current)
            current = ""
            continue
        if current.endswith("-") and line[:1].islower():
            current = current[:-1] + line  # hyphenated word split across lines
        elif current and (current[-1] in '.!?:;»"”' and line[:1].isupper()):
            paragraphs.append(current)
            current = line
        else:
            current = f"{current} {line}" if current else line
    if current:
        paragraphs.append(current)
    if not paragraphs:
        raise ConversionError("The PDF has no text (it may be scanned pages): nothing to put into a book")
    return Document(title=title, author=author, blocks=[Para([Span(p)]) for p in paragraphs])


# ---- writers ----

_CSS = """
body { font-family: "Segoe UI", system-ui, sans-serif; line-height: 1.55; max-width: 46em; margin: 2em auto;
       padding: 0 1em; color: #1a1a1a; }
h1, h2, h3, h4 { line-height: 1.25; margin: 1.4em 0 .5em; }
pre { background: #f4f4f6; padding: .8em 1em; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: .92em; }
blockquote { margin: 1em 0; padding: .1em 1em; border-left: 4px solid #ccc; color: #444; }
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #bbb; padding: .35em .7em; text-align: left; vertical-align: top; }
th { background: #f0f0f2; }
img { max-width: 100%; height: auto; }
li > p { margin: .15em 0; }
@page { size: A4; margin: 18mm 16mm; }
@media print { body { margin: 0; max-width: none; padding: 0; } pre, blockquote, table, img { break-inside: avoid; } }
"""

_MIME = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}


def _inline_html(inline: Inline) -> str:
    out = []
    for s in inline:
        text = html.escape(s.text).replace("\n", "<br>\n")
        if s.code:
            text = f"<code>{text}</code>"
        if s.bold:
            text = f"<strong>{text}</strong>"
        if s.italic:
            text = f"<em>{text}</em>"
        if s.strike:
            text = f"<del>{text}</del>"
        if s.link:
            text = f"<a href={quoteattr(s.link)}>{text}</a>"
        out.append(text)
    return "".join(out)


def _blocks_html(blocks: list, image_src) -> str:
    out = []
    for b in blocks:
        if isinstance(b, Heading):
            out.append(f"<h{b.level}>{_inline_html(b.inline)}</h{b.level}>")
        elif isinstance(b, Para):
            out.append(f"<p>{_inline_html(b.inline)}</p>")
        elif isinstance(b, ListBlock):
            tag = "ol" if b.ordered else "ul"
            start = f' start="{b.start}"' if b.ordered and b.start != 1 else ""
            items = "".join(f"<li>{_blocks_html(item, image_src)}</li>" for item in b.items)
            out.append(f"<{tag}{start}>{items}</{tag}>")
        elif isinstance(b, Code):
            out.append(f"<pre><code>{html.escape(b.text)}</code></pre>")
        elif isinstance(b, Quote):
            out.append(f"<blockquote>{_blocks_html(b.blocks, image_src)}</blockquote>")
        elif isinstance(b, Rule):
            out.append("<hr/>")
        elif isinstance(b, Table):
            rows = []
            for r, row in enumerate(b.rows):
                cell = "th" if b.header and r == 0 else "td"
                rows.append("<tr>" + "".join(f"<{cell}>{_inline_html(c)}</{cell}>" for c in row) + "</tr>")
            out.append("<table>" + "".join(rows) + "</table>")
        elif isinstance(b, Image):
            out.append(f"<p><img src={quoteattr(image_src(b))} alt={quoteattr(b.alt)}/></p>")
    return "\n".join(out)


def _data_uri(image: Image) -> str:
    return f"data:{_MIME.get(image.ext, 'image/' + image.ext)};base64,{base64.b64encode(image.data).decode()}"


def write_html(doc: Document, dst: Path, title: str) -> None:
    body = _blocks_html(doc.blocks, _data_uri)
    lang = f' lang="{html.escape(doc.lang)}"' if doc.lang else ""
    dst.write_text(
        f'<!DOCTYPE html>\n<html{lang}>\n<head>\n<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(doc.title or title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n",
        encoding="utf-8",
    )


def _inline_md(inline: Inline) -> str:
    """Markers open and close only where the style changes: *quote with **bold** inside*."""
    out: list[str] = []
    open_marks: list[str] = []  # outermost first
    for s in inline:
        text = s.text if s.code else re.sub(r"([\\`*_\[\]<>])", r"\\\1", s.text)
        text = text.replace("\n", "  \n")
        if s.code:
            fence = "``" if "`" in text else "`"
            text = f"{fence}{text}{fence}"
        if s.link:
            text = f"[{text}]({s.link})"
        wanted = [m for m, on in (("~~", s.strike), ("**", s.bold), ("*", s.italic)) if on]
        keep = 0
        while keep < len(open_marks) and open_marks[keep] in wanted:
            keep += 1
        out.extend(reversed(open_marks[keep:]))
        opened = [m for m in wanted if m not in open_marks[:keep]]
        out.extend(opened)
        open_marks = open_marks[:keep] + opened
        out.append(text)
    out.extend(reversed(open_marks))
    return "".join(out)


def _blocks_md(blocks: list, images: dict[int, str], indent: str = "") -> str:
    out = []
    for b in blocks:
        if isinstance(b, Heading):
            out.append(f"{'#' * b.level} {_inline_md(b.inline)}")
        elif isinstance(b, Para):
            out.append(_inline_md(b.inline))
        elif isinstance(b, ListBlock):
            lines = []
            for n, item in enumerate(b.items, b.start):
                marker = f"{n}. " if b.ordered else "- "
                body = _blocks_md(item, images, " " * len(marker)).strip("\n")
                lines.append(
                    marker
                    + body.replace("\n", "\n" + " " * len(marker)).replace(
                        "\n" + " " * len(marker) + "\n", "\n\n"
                    )
                )
            out.append("\n".join(lines))
        elif isinstance(b, Code):
            out.append(f"```\n{b.text}\n```")
        elif isinstance(b, Quote):
            out.append(
                "\n".join(
                    f"> {line}" if line else ">"
                    for line in _blocks_md(b.blocks, images).rstrip("\n").split("\n")
                )
            )
        elif isinstance(b, Rule):
            out.append("---")
        elif isinstance(b, Table) and b.rows:
            width = max(len(r) for r in b.rows)
            rows = [
                [_inline_md(c).replace("|", "\\|").replace("\n", " ") for c in r] + [""] * (width - len(r))
                for r in b.rows
            ]
            lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
            lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            out.append("\n".join(lines))
        elif isinstance(b, Image):
            out.append(f"![{b.alt}]({images.get(id(b), '')})")
    return "\n\n".join(out) + "\n"


def write_markdown(doc: Document, dst: Path, title: str) -> None:
    """Images are embedded as data: URIs, so the result stays one self-contained file."""
    images = {id(image): _data_uri(image) for image in _walk_images(doc.blocks)}
    text = _blocks_md(doc.blocks, images)
    if doc.title and _first_heading(doc.blocks) != doc.title:
        text = f"# {doc.title}\n\n{text}"
    dst.write_text(text, encoding="utf-8")


def _walk_images(blocks: list):
    for b in blocks:
        if isinstance(b, Image):
            yield b
        elif isinstance(b, Quote):
            yield from _walk_images(b.blocks)
        elif isinstance(b, ListBlock):
            for item in b.items:
                yield from _walk_images(item)


def _blocks_text(blocks: list, indent: str = "") -> list[str]:
    out = []
    for b in blocks:
        if isinstance(b, (Heading, Para)):
            out.append(indent + plain(b.inline).replace("\n", "\n" + indent))
        elif isinstance(b, ListBlock):
            for n, item in enumerate(b.items, b.start):
                marker = f"{n}. " if b.ordered else "• "
                lines = _blocks_text(item, indent + " " * len(marker))
                if lines:
                    lines[0] = indent + marker + lines[0].lstrip()
                out.append("\n".join(lines))
        elif isinstance(b, Code):
            out.append("\n".join(indent + "    " + line for line in b.text.split("\n")))
        elif isinstance(b, Quote):
            out.extend(_blocks_text(b.blocks, indent + "    "))
        elif isinstance(b, Rule):
            out.append(indent + "* * *")
        elif isinstance(b, Table):
            out.append("\n".join(indent + "\t".join(plain(c) for c in row) for row in b.rows))
    return out


def write_text(doc: Document, dst: Path, title: str) -> None:
    lines = _blocks_text(doc.blocks)
    if doc.title and _first_heading(doc.blocks) != doc.title:
        lines.insert(0, doc.title)
    dst.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


# EPUB 3 (with an EPUB 2 table of contents for older readers).


def _split_chapters(blocks: list) -> list[list]:
    level = min((b.level for b in blocks if isinstance(b, Heading)), default=0)
    chapters: list[list] = [[]]
    for b in blocks:
        if isinstance(b, Heading) and b.level == level and chapters[-1]:
            chapters.append([])
        chapters[-1].append(b)
    return [c for c in chapters if c]


def write_epub(doc: Document, dst: Path, title: str) -> None:
    title = doc.title or _first_heading(doc.blocks) or title
    lang = doc.lang or "en"
    book_id = f"urn:uuid:{uuid.uuid4()}"
    images: dict[int, str] = {}
    manifest, files = [], {}
    for n, image in enumerate(_walk_images(doc.blocks), 1):
        name = f"images/image{n:03d}.{image.ext}"
        images[id(image)] = name
        files[f"OEBPS/{name}"] = image.data
        manifest.append(f'<item id="img{n}" href="{name}" media-type="{_MIME.get(image.ext, "image/png")}"/>')
    if doc.cover is not None:
        name = f"images/cover.{doc.cover.ext}"
        files[f"OEBPS/{name}"] = doc.cover.data
        manifest.append(
            f'<item id="cover-image" href="{name}" media-type="{_MIME.get(doc.cover.ext, "image/png")}" properties="cover-image"/>'
        )
    chapters = _split_chapters(doc.blocks) or [[Para([Span(title)])]]
    nav, ncx, spine = [], [], []
    for n, chapter in enumerate(chapters, 1):
        name = f"chapter{n:03d}.xhtml"
        heading = next((plain(b.inline) for b in chapter if isinstance(b, Heading)), f"{n}")
        body = _blocks_html(chapter, lambda im: images.get(id(im), ""))
        body = re.sub(r"<(br|hr)>", r"<\1/>", body)
        files[f"OEBPS/{name}"] = (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{html.escape(lang)}">\n<head><title>{html.escape(heading)}</title>'
            f'<link rel="stylesheet" type="text/css" href="style.css"/></head>\n<body>\n{body}\n</body>\n</html>\n'
        ).encode()
        manifest.append(f'<item id="ch{n}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{n}"/>')
        nav.append(f'<li><a href="{name}">{html.escape(heading)}</a></li>')
        ncx.append(
            f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>{html.escape(heading)}</text></navLabel><content src="{name}"/></navPoint>'
        )
    files["OEBPS/style.css"] = _CSS.replace("@page { size: A4; margin: 18mm 16mm; }", "").encode()
    files["OEBPS/nav.xhtml"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{html.escape(title)}</title></head><body>'
        f'<nav epub:type="toc" id="toc"><ol>{"".join(nav)}</ol></nav></body></html>\n'
    ).encode()
    files["OEBPS/toc.ncx"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{book_id}"/></head><docTitle><text>{html.escape(title)}</text></docTitle>'
        f"<navMap>{''.join(ncx)}</navMap></ncx>\n"
    ).encode()
    modified = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    author = f"<dc:creator>{html.escape(doc.author)}</dc:creator>" if doc.author else ""
    cover_meta = '<meta name="cover" content="cover-image"/>' if doc.cover is not None else ""
    files["OEBPS/content.opf"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="bookid">{book_id}</dc:identifier><dc:title>{html.escape(title)}</dc:title>'
        f'<dc:language>{html.escape(lang)}</dc:language>{author}<meta property="dcterms:modified">{modified}</meta>{cover_meta}</metadata>'
        '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="css" href="style.css" media-type="text/css"/>'
        f'{"".join(manifest)}</manifest><spine toc="ncx">{"".join(spine)}</spine></package>\n'
    ).encode()
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>\n'
    )
    with zipfile.ZipFile(dst, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)


# FictionBook 2.


def _inline_fb2(inline: Inline) -> str:
    out = []
    for s in inline:
        text = escape(s.text).replace("\n", " ")
        if s.code:
            text = f"<code>{text}</code>"
        if s.bold:
            text = f"<strong>{text}</strong>"
        if s.italic:
            text = f"<emphasis>{text}</emphasis>"
        if s.strike:
            text = f"<strikethrough>{text}</strikethrough>"
        if s.link:
            text = f"<a l:href={quoteattr(s.link)}>{text}</a>"
        out.append(text)
    return "".join(out)


def _blocks_fb2(blocks: list, images: dict[int, str]) -> str:
    out = []
    for b in blocks:
        if isinstance(b, Heading):
            out.append(f"<subtitle>{_inline_fb2(b.inline)}</subtitle>")
        elif isinstance(b, Para):
            out.append(f"<p>{_inline_fb2(b.inline)}</p>")
        elif isinstance(b, ListBlock):
            for n, item in enumerate(b.items, b.start):
                marker = f"{n}. " if b.ordered else "• "
                inner = _blocks_fb2(item, images)
                out.append(
                    inner.replace("<p>", f"<p>{escape(marker)}", 1) if inner.startswith("<p>") else inner
                )
        elif isinstance(b, Code):
            out.extend(f"<p><code>{escape(line)}</code></p>" for line in b.text.split("\n"))
        elif isinstance(b, Quote):
            out.append(f"<cite>{_blocks_fb2(b.blocks, images) or '<p/>'}</cite>")
        elif isinstance(b, Rule):
            out.append("<empty-line/>")
        elif isinstance(b, Table):
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline_fb2(c)}</td>" for c in row) + "</tr>" for row in b.rows
            )
            out.append(f"<table>{rows}</table>")
        elif isinstance(b, Image):
            out.append(f'<image l:href="#{images[id(b)]}"/>')
    return "".join(out)


def write_fb2(doc: Document, dst: Path, title: str) -> None:
    title = doc.title or _first_heading(doc.blocks) or title
    images: dict[int, str] = {}
    binaries = []
    for n, image in enumerate(_walk_images(doc.blocks), 1):
        image = _fb2_image(image)
        name = f"image{n:03d}.{image.ext}"
        images[id(image)] = name
        binaries.append((name, image))
    # _fb2_image may return a converted copy: map the originals too
    for (name, _image), original in zip(binaries, list(_walk_images(doc.blocks)), strict=True):
        images[id(original)] = name
    cover = ""
    if doc.cover is not None:
        cover_image = _fb2_image(doc.cover)
        binaries.append(("cover." + cover_image.ext, cover_image))
        cover = f'<coverpage><image l:href="#cover.{cover_image.ext}"/></coverpage>'
    first, _, last = doc.author.rpartition(" ")
    author = (
        f"<author><first-name>{escape(first)}</first-name><last-name>{escape(last)}</last-name></author>"
        if first
        else f"<author><nickname>{escape(doc.author or 'Unknown')}</nickname></author>"
    )
    sections = []
    for chapter in _split_chapters(doc.blocks) or [[]]:
        head = chapter[0] if chapter and isinstance(chapter[0], Heading) else None
        body = chapter[1:] if head else chapter
        heading = f"<title><p>{_inline_fb2(head.inline)}</p></title>" if head else ""
        sections.append(f"<section>{heading}{_blocks_fb2(body, images) or '<empty-line/>'}</section>")
    binary_xml = "".join(
        f'<binary id="{name}" content-type="{_MIME.get(im.ext, "image/png")}">{base64.b64encode(im.data).decode()}</binary>'
        for name, im in binaries
    )
    lang = escape(doc.lang or "en")
    # The book title opens the body, unless the text already starts with it as a heading.
    body_title = "" if _first_heading(doc.blocks) == title else f"<title><p>{escape(title)}</p></title>"
    dst.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<FictionBook xmlns="{FB2_NS}" xmlns:l="{XLINK}">'
        f"<description><title-info><genre>prose</genre>{author}<book-title>{escape(title)}</book-title>"
        f"{cover}<lang>{lang}</lang></title-info>"
        f"<document-info>{author}<date>{datetime.now().date().isoformat()}</date><id>{uuid.uuid4()}</id><version>1.0</version></document-info>"
        f"</description><body>{body_title}{''.join(sections)}</body>{binary_xml}</FictionBook>\n",
        encoding="utf-8",
    )


def _fb2_image(image: Image) -> Image:
    """FB2 readers only take PNG and JPEG."""
    if image.ext in ("png", "jpg"):
        return image
    converted = _to_png(image)
    return converted or image


def _to_png(image: Image) -> Image | None:
    try:
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(image.data)) as im:
            out = io.BytesIO()
            im.convert("RGBA").save(out, "PNG")
        return Image(out.getvalue(), "png", image.alt)
    except Exception:
        return None


# DOCX: the parts a word processor needs, nothing more.

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_EMU_PER_PX = 9525
_MAX_WIDTH_EMU = 6 * 914400  # 6 inches: the text width of an A4/Letter page with normal margins


class _DocxWriter:
    def __init__(self) -> None:
        self.rels: list[tuple[str, str, str, str]] = []  # id, type, target, mode
        self.media: dict[str, bytes] = {}
        self.nums: list[tuple[int, int]] = []  # (abstract id, start)
        self.drawing_id = 0

    def rel(self, kind: str, target: str, external: bool = False) -> str:
        rid = f"rId{len(self.rels) + 10}"
        self.rels.append((rid, kind, target, "External" if external else ""))
        return rid

    def runs(self, inline: Inline) -> str:
        out = []
        for s in inline:
            props = []
            if s.bold:
                props.append("<w:b/>")
            if s.italic:
                props.append("<w:i/>")
            if s.strike:
                props.append("<w:strike/>")
            if s.code:
                props.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>')
            if s.link:
                props.insert(0, '<w:rStyle w:val="Hyperlink"/>')
            rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
            parts = s.text.split("\n")
            body = "<w:br/>".join(f'<w:t xml:space="preserve">{escape(_xml_safe(p))}</w:t>' for p in parts)
            run = f"<w:r>{rpr}{body}</w:r>"
            if s.link and re.match(r"(https?|mailto):", s.link):
                rid = self.rel(f"{_R}/hyperlink", s.link, external=True)
                run = f'<w:hyperlink r:id="{rid}">{run}</w:hyperlink>'
            out.append(run)
        return "".join(out)

    def para(self, inline: Inline, style: str = "", extra: str = "") -> str:
        ppr = f'<w:pStyle w:val="{style}"/>' if style else ""
        ppr = f"<w:pPr>{ppr}{extra}</w:pPr>" if ppr or extra else ""
        return f"<w:p>{ppr}{self.runs(inline)}</w:p>"

    def blocks(self, blocks: list, level: int = 0, quote: bool = False) -> str:
        out = []
        for b in blocks:
            if isinstance(b, Heading):
                out.append(self.para(b.inline, f"Heading{min(b.level, 6)}"))
            elif isinstance(b, Para):
                out.append(self.para(b.inline, "Quote" if quote else ""))
            elif isinstance(b, ListBlock):
                self.nums.append((1 if b.ordered else 0, b.start))
                num = len(self.nums)
                for item in b.items:
                    first = True
                    for inner in item or [Para([])]:
                        if isinstance(inner, Para) and first:
                            numpr = f'<w:numPr><w:ilvl w:val="{min(level, 8)}"/><w:numId w:val="{num}"/></w:numPr>'
                            out.append(self.para(inner.inline, "ListParagraph", numpr))
                        elif isinstance(inner, ListBlock):
                            out.append(self.blocks([inner], level + 1))
                        else:
                            indent = f'<w:ind w:left="{720 * (level + 1)}"/>'
                            out.append(
                                self.para(inner.inline, "", indent)
                                if isinstance(inner, Para)
                                else self.blocks([inner], level + 1)
                            )
                        first = False
            elif isinstance(b, Code):
                out.extend(self.para([Span(line, code=True)], "Code") for line in b.text.split("\n"))
            elif isinstance(b, Quote):
                out.append(self.blocks(b.blocks, level, quote=True))
            elif isinstance(b, Rule):
                out.append(
                    '<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="AAAAAA"/></w:pBdr></w:pPr></w:p>'
                )
            elif isinstance(b, Table) and b.rows:
                width = max(len(r) for r in b.rows)
                rows = []
                for r, row in enumerate(b.rows):
                    cells = []
                    for c in range(width):
                        inline = row[c] if c < len(row) else []
                        if b.header and r == 0:
                            inline = [Span(s.text, True, s.italic, s.code, s.strike, s.link) for s in inline]
                        cells.append(
                            f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>{self.para(inline)}</w:tc>'
                        )
                    header = "<w:trPr><w:tblHeader/></w:trPr>" if b.header and r == 0 else ""
                    rows.append(f"<w:tr>{header}{''.join(cells)}</w:tr>")
                grid = "".join("<w:gridCol/>" for _ in range(width))
                out.append(
                    '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
                    f"<w:tblGrid>{grid}</w:tblGrid>{''.join(rows)}</w:tbl><w:p/>"
                )
            elif isinstance(b, Image):
                out.append(self.image(b))
        return "".join(out)

    def image(self, image: Image) -> str:
        if image.ext not in ("png", "jpg", "gif", "bmp"):
            converted = _to_png(image)
            if converted is None:
                return ""
            image = converted
        try:
            from PIL import Image as PILImage

            with PILImage.open(io.BytesIO(image.data)) as im:
                w, h = im.size
        except Exception:
            return ""
        cx, cy = w * _EMU_PER_PX, h * _EMU_PER_PX
        if cx > _MAX_WIDTH_EMU:
            cx, cy = _MAX_WIDTH_EMU, int(cy * _MAX_WIDTH_EMU / cx)
        self.drawing_id += 1
        n = self.drawing_id
        name = f"image{n}.{image.ext}"
        self.media[name] = image.data
        rid = self.rel(f"{_R}/image", f"media/{name}")
        return (
            '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:docPr id="{n}" name="Picture {n}" descr={quoteattr(image.alt)}/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{n}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
            "</wp:inline></w:drawing></w:r></w:p>"
        )

    def numbering(self) -> str:
        def levels(ordered: bool) -> str:
            out = []
            for lvl in range(9):
                fmt, text = ("decimal", f"%{lvl + 1}.") if ordered else ("bullet", "•◦▪"[lvl % 3])
                out.append(
                    f'<w:lvl w:ilvl="{lvl}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/>'
                    f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * (lvl + 1)}" w:hanging="360"/></w:pPr></w:lvl>'
                )
            return "".join(out)

        nums = "".join(
            f'<w:num w:numId="{i}"><w:abstractNumId w:val="{abstract}"/>'
            f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="{start}"/></w:lvlOverride></w:num>'
            for i, (abstract, start) in enumerate(self.nums, 1)
        )
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="{_W}">'
            f'<w:abstractNum w:abstractNumId="0">{levels(False)}</w:abstractNum>'
            f'<w:abstractNum w:abstractNumId="1">{levels(True)}</w:abstractNum>{nums}</w:numbering>'
        )


def _xml_safe(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


_DOCX_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:sz w:val="52"/></w:rPr></w:style>
{"".join(f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="{360 - n * 40}" w:after="120"/><w:outlineLvl w:val="{n - 1}"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3864"/><w:sz w:val="{max(22, 40 - n * 4)}"/></w:rPr></w:style>' for n in range(1, 7))}
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="567"/></w:pPr><w:rPr><w:i/><w:color w:val="404040"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="60"/><w:contextualSpacing/></w:pPr></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:space="0" w:color="A0A0A0"/><w:left w:val="single" w:sz="4" w:space="0" w:color="A0A0A0"/>
<w:bottom w:val="single" w:sz="4" w:space="0" w:color="A0A0A0"/><w:right w:val="single" w:sz="4" w:space="0" w:color="A0A0A0"/>
<w:insideH w:val="single" w:sz="4" w:space="0" w:color="A0A0A0"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="A0A0A0"/>
</w:tblBorders><w:tblCellMar><w:left w:w="100" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""


def write_docx(doc: Document, dst: Path, title: str) -> None:
    writer = _DocxWriter()
    body = ""
    if doc.title and _first_heading(doc.blocks) != doc.title:
        body += writer.para([Span(doc.title)], "Title")
    body += writer.blocks(doc.blocks)
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="{_W}" xmlns:r="{_R}" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        f'<w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="709" w:footer="709" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    rels = [
        ("rId1", f"{_R}/styles", "styles.xml", ""),
        ("rId2", f"{_R}/numbering", "numbering.xml", ""),
        *writer.rels,
    ]
    rels_xml = "".join(
        f'<Relationship Id="{rid}" Type="{kind}" Target={quoteattr(target)}{f" TargetMode={quoteattr(mode)}" if mode else ""}/>'
        for rid, kind, target, mode in rels
    )
    defaults = "".join(
        f'<Default Extension="{ext}" ContentType="{_MIME.get(ext, "image/" + ext)}"/>'
        for ext in sorted({name.rsplit(".", 1)[1] for name in writer.media})
    )
    core_title = escape(doc.title)
    core_author = f"<dc:creator>{escape(doc.author)}</dc:creator>" if doc.author else ""
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{defaults}'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_R}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>"
        ),
        "docProps/core.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>'
            f"{core_title}</dc:title>{core_author}</cp:coreProperties>"
        ),
        "word/document.xml": document,
        "word/styles.xml": _DOCX_STYLES,
        "word/numbering.xml": writer.numbering(),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels_xml}</Relationships>'
        ),
    }
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
        for name, data in writer.media.items():
            zf.writestr(f"word/media/{name}", data)


# PDF through Microsoft Edge (headless). It ships with Windows 10 and 11.

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_browser() -> Path | None:
    """A Chromium-based browser that can print to PDF headless: Edge (in every Windows), Chrome, Chromium,
    Brave. On macOS and Linux whichever is installed."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Chromium", "Brave Browser"):
            for root in (Path("/Applications"), Path.home() / "Applications"):
                candidates.append(root / f"{app}.app" / "Contents" / "MacOS" / app)
    names = (
        "msedge",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "brave-browser",
    )
    candidates += [Path(p) for p in map(shutil.which, names) if p]
    return next((c for c in candidates if c.is_file()), None)


find_edge = find_browser  # older name


def html_to_pdf(html_file: Path, dst: Path, ctx: Context) -> None:
    edge = find_browser()
    if edge is None:
        raise ConversionError("Saving to PDF needs Microsoft Edge, Google Chrome or Chromium")
    with tempfile.TemporaryDirectory(prefix="morphto-edge-") as profile:
        cmd = [
            str(edge), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--no-pdf-header-footer", f"--user-data-dir={profile}",
            f"--print-to-pdf={dst}", html_file.resolve().as_uri(),
        ]  # fmt: skip
        from morphto.converters.office import _run

        _run(cmd, ctx)
    if not dst.is_file() or dst.stat().st_size == 0:
        raise ConversionError("The browser could not create the PDF")


def write_pdf(doc: Document, dst: Path, title: str, ctx: Context) -> None:
    with tempfile.TemporaryDirectory(prefix="morphto-html-") as tmp:
        page = Path(tmp) / "document.html"
        write_html(doc, page, title)
        html_to_pdf(page, dst, ctx)


# ---- the converter ----

_READERS = {"md": read_markdown, "html": read_html, "fb2": read_fb2, "epub": read_epub, "txt": read_txt}
_WRITERS = {
    "md": write_markdown, "html": write_html, "docx": write_docx, "epub": write_epub, "fb2": write_fb2,
    "txt": write_text,
}  # fmt: skip


class RichTextConverter(Converter):
    name = "MorphTo documents"
    cost = 8  # below LibreOffice (txt -> docx/pdf) and Office

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [(s, d) for s in READ for d in WRITE if s != d]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        if src_fmt.id == "html" and dst_fmt.id == "pdf":
            html_to_pdf(src, dst, ctx)  # print the page as it is, with its own styles
            return
        doc = _READERS[src_fmt.id](src)
        ctx.check_cancelled()
        ctx.progress(0.4)
        title = src.stem if not src.stem.startswith("step") else ""
        if dst_fmt.id == "pdf":
            write_pdf(doc, dst, title, ctx)
        else:
            _WRITERS[dst_fmt.id](doc, dst, title)


class PdfBookConverter(RichTextConverter):
    """E-books from text PDFs. Only words and paragraphs survive, so it costs more than any other route
    (DOCX -> EPUB goes through HTML, not through PDF)."""

    name = "MorphTo PDF to e-book"
    cost = 30

    def pairs(self) -> Iterable[tuple[str, str]]:
        return [("pdf", "epub"), ("pdf", "fb2")]

    def convert(
        self, src: Path, dst: Path, src_fmt: Format, dst_fmt: Format, options: Options, ctx: Context
    ) -> None:
        doc = read_pdf_text(src)
        ctx.progress(0.5)
        _WRITERS[dst_fmt.id](doc, dst, src.stem if not src.stem.startswith("step") else "")


__all__ = ["PdfBookConverter", "RichTextConverter", "html_to_pdf"]
