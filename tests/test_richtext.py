import base64
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from morphto.converters.richtext import find_edge, read_epub, read_fb2, read_markdown
from morphto.core.detect import detect
from morphto.core.engine import convert

MD = """# Guide

Some **bold**, *italic*, `code` and [a link](https://example.com).

## Lists

- one
- two
  1. nested

> A quote.

| Format | Read |
|---|---|
| PNG | yes |

![Picture](pic.png)

## Second chapter

Text.
"""


@pytest.fixture
def markdown(tmp_path):
    Image.new("RGB", (32, 20), "teal").save(tmp_path / "pic.png")
    path = tmp_path / "guide.md"
    path.write_text(MD, encoding="utf-8")
    return path


def _kinds(blocks):
    return [type(b).__name__ for b in blocks]


def test_markdown_model(markdown):
    doc = read_markdown(markdown)
    assert _kinds(doc.blocks) == [
        "Heading",
        "Para",
        "Heading",
        "ListBlock",
        "Quote",
        "Table",
        "Image",
        "Heading",
        "Para",
    ]
    spans = doc.blocks[1].inline
    assert any(s.bold and s.text == "bold" for s in spans)
    assert any(s.link == "https://example.com" for s in spans)


@pytest.mark.parametrize("fmt", ["epub", "fb2"])
def test_books_keep_structure_and_images(tmp_path, markdown, fmt):
    book = convert(markdown, fmt, out_dir=tmp_path / "out")
    assert detect(book).id == fmt
    doc = read_epub(book) if fmt == "epub" else read_fb2(book)
    assert "Second chapter" in [b.inline[0].text for b in doc.blocks if type(b).__name__ == "Heading"]
    images = [b for b in doc.blocks if type(b).__name__ == "Image"]
    assert len(images) == 1 and Image.open(io.BytesIO(images[0].data)).size == (32, 20)


def test_epub_is_well_formed(tmp_path, markdown):
    book = convert(markdown, "epub", out_dir=tmp_path)
    with zipfile.ZipFile(book) as zf:
        assert zf.namelist()[0] == "mimetype" and zf.read("mimetype") == b"application/epub+zip"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        for name in zf.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(zf.read(name))  # every XML part parses


def test_docx_is_well_formed(tmp_path, markdown):
    docx = convert(markdown, "docx", out_dir=tmp_path)
    assert detect(docx).id == "docx"
    with zipfile.ZipFile(docx) as zf:
        for name in zf.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))
        assert any(n.startswith("word/media/") for n in zf.namelist())
        body = zf.read("word/document.xml").decode()
        assert "Heading1" in body and "w:numPr" in body and "w:tbl" in body


def test_markdown_output_is_self_contained(tmp_path, markdown):
    html = convert(markdown, "html", out_dir=tmp_path / "h")
    md = convert(html, "md", out_dir=tmp_path / "m").read_text(encoding="utf-8")
    assert "## Lists" in md and "](data:image/png;base64," in md
    assert not list((tmp_path / "m").glob("*_files"))


def test_fb2_reader(tmp_path):
    png = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(png, "PNG")
    fb2 = tmp_path / "book.fb2"
    fb2.write_text(
        '<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><author><first-name>Лев</first-name>'
        "<last-name>Толстой</last-name></author><book-title>Война и мир</book-title><lang>ru</lang></title-info>"
        "</description><body><section><title><p>Том первый</p></title><p>Текст <emphasis>курсив</emphasis>.</p>"
        f'<image l:href="#i1"/></section></body><binary id="i1" content-type="image/png">{base64.b64encode(png.getvalue()).decode()}</binary></FictionBook>',
        encoding="utf-8",
    )
    doc = read_fb2(fb2)
    assert (doc.title, doc.author, doc.lang) == ("Война и мир", "Лев Толстой", "ru")
    assert _kinds(doc.blocks) == ["Heading", "Para", "Image"]
    assert any(s.italic for s in doc.blocks[1].inline)


@pytest.mark.skipif(find_edge() is None, reason="Microsoft Edge not installed")
def test_markdown_to_pdf_with_edge(tmp_path, markdown):
    pdf = convert(markdown, "pdf", out_dir=tmp_path)
    assert detect(pdf).id == "pdf"
    text = convert(pdf, "txt", out_dir=tmp_path / "t").read_text(encoding="utf-8")
    assert "Second chapter" in text
