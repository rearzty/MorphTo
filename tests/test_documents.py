import zipfile

import pytest
from PIL import Image

from morphto import engines
from morphto.core.detect import detect, sniff
from morphto.core.engine import convert, plan
from morphto.core.formats import get_format
from morphto.core.registry import ConversionError
from morphto.core.targets import available_targets


def _zip(path, names, mimetype=None):
    with zipfile.ZipFile(path, "w") as zf:
        if mimetype:
            zf.writestr("mimetype", mimetype)
        for name in names:
            zf.writestr(name, "<x/>")
    return path


@pytest.mark.parametrize(
    ("names", "mimetype", "expected"),
    [
        (["[Content_Types].xml", "word/document.xml"], None, "docx"),
        (["[Content_Types].xml", "xl/workbook.xml"], None, "xlsx"),
        (["[Content_Types].xml", "ppt/presentation.xml"], None, "pptx"),
        (["content.xml"], "application/vnd.oasis.opendocument.text", "odt"),
        (["content.xml"], "application/vnd.oasis.opendocument.spreadsheet", "ods"),
        (["content.xml"], "application/vnd.oasis.opendocument.presentation", "odp"),
    ],
)
def test_detect_office_zip_by_content(tmp_path, names, mimetype, expected):
    # Wrong extension on purpose: the content decides.
    assert detect(_zip(tmp_path / "file.zip", names, mimetype)).id == expected


def test_detect_plain_zip_is_not_a_document(tmp_path):
    assert detect(_zip(tmp_path / "file.zip", ["readme.md"])) is None


def test_sniff_rtf_and_ole():
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8
    assert sniff(b"{\\rtf1\\ansi", "") == "rtf"
    assert sniff(ole, "doc") == "doc"
    assert sniff(ole, "xls") == "xls"
    assert sniff(ole, "msi") is None  # other OLE files (installers, Outlook messages) are not ours


def test_office_targets_go_through_pdf():
    steps = plan(get_format("pptx"), get_format("png"))
    assert [(s.src, s.dst) for s in steps] == [("pptx", "pdf"), ("pdf", "png")]
    ids = {t.id for t in available_targets([get_format("docx")])}
    assert {"pdf", "png", "jpg", "odt", "txt"} <= ids
    assert "mp4" not in ids and "xlsx" not in ids


@pytest.mark.parametrize(("src", "dst"), [("png", "txt"), ("pdf", "docx"), ("jpg", "pptx")])
def test_lossy_or_pointless_chains_are_refused(src, dst):
    # PNG -> PDF -> TXT would be an empty file (no OCR); PDF -> TXT -> DOCX loses everything but words.
    with pytest.raises(ConversionError):
        plan(get_format(src), get_format(dst))


@pytest.fixture
def pdf(tmp_path):
    def make(pages: int):
        path = tmp_path / f"doc{pages}.pdf"
        images = [Image.new("RGB", (200, 280), (40 * i, 120, 200)) for i in range(pages)]
        images[0].save(path, save_all=True, append_images=images[1:], resolution=72)
        return path

    return make


def test_pdf_one_page_to_png(tmp_path, pdf):
    out = convert(pdf(1), "png", out_dir=tmp_path / "out")
    assert out.is_file() and out.suffix == ".png"
    with Image.open(out) as im:
        assert abs(im.width - 417) <= 1 and abs(im.height - 583) <= 1  # 200x280 pt at 150 dpi


def test_pdf_pages_to_folder(tmp_path, pdf):
    out = convert(pdf(3), "jpg", out_dir=tmp_path / "out")
    assert out.is_dir() and out.name == "doc3"
    assert sorted(p.name for p in out.iterdir()) == ["page-01.jpg", "page-02.jpg", "page-03.jpg"]
    # A second run never overwrites the first folder.
    assert convert(pdf(3), "jpg", out_dir=tmp_path / "out").name == "doc3 (1)"


def test_pdf_pages_to_multipage_tiff(tmp_path, pdf):
    out = convert(pdf(3), "tiff", out_dir=tmp_path / "out")
    with Image.open(out) as im:
        assert im.n_frames == 3


def test_pdf_max_side(tmp_path, pdf):
    from morphto.core.options import Options

    out = convert(pdf(1), "png", out_dir=tmp_path, options=Options(max_side=100))
    with Image.open(out) as im:
        assert max(im.size) <= 100


# ---- LibreOffice (skipped when it is not installed) ----

_LO = engines.get("libreoffice").tool()


@pytest.fixture(scope="session")
def lo_engines(tmp_path_factory):
    if _LO is None:
        pytest.skip("LibreOffice not available")
    return tmp_path_factory.mktemp("engines")  # one profile for all tests: creating it takes seconds


@pytest.fixture
def libreoffice(lo_engines, monkeypatch):
    monkeypatch.setenv("MORPHTO_ENGINES", str(lo_engines))
    monkeypatch.setenv("MORPHTO_LIBREOFFICE", str(_LO))


def test_libreoffice_text_document_round_trip(tmp_path, libreoffice):
    src = tmp_path / "note.txt"
    src.write_text("Hello MorphTo\nПривет\n", encoding="utf-8")
    docx = convert(src, "docx", out_dir=tmp_path / "out")
    assert detect(docx).id == "docx"
    pdf = convert(docx, "pdf", out_dir=tmp_path / "out")
    assert detect(pdf).id == "pdf"
    text = convert(pdf, "txt", out_dir=tmp_path / "back").read_text(encoding="utf-8")
    assert "Hello MorphTo" in text and "Привет" in text
    png = convert(docx, "png", out_dir=tmp_path / "out")
    assert png.is_file() and detect(png).id == "png"


def test_libreoffice_spreadsheet(tmp_path, libreoffice):
    src = tmp_path / "table.csv"
    src.write_text("name,value\nα,1\nβ,2\n", encoding="utf-8")
    xlsx = convert(src, "xlsx", out_dir=tmp_path / "out")
    assert detect(xlsx).id == "xlsx"
    back = convert(xlsx, "csv", out_dir=tmp_path / "back")
    assert back.read_text(encoding="utf-8").splitlines()[1:] == ["α,1", "β,2"]
