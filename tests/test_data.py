import json

import pytest

from morphto.converters.data import read_xlsx, records_to_table, table_to_records, tree_to_xml
from morphto.core.detect import detect
from morphto.core.engine import convert, plan
from morphto.core.formats import get_format


@pytest.fixture
def people(tmp_path):
    path = tmp_path / "people.csv"
    path.write_text('name;age;city\nАнна;30;Москва\nBob;007;"New York, NY"\n', encoding="utf-8")
    return path


def test_csv_to_xlsx_and_back(tmp_path, people):
    xlsx = convert(people, "xlsx", out_dir=tmp_path / "x")
    assert detect(xlsx).id == "xlsx"
    assert read_xlsx(xlsx) == [
        ["name", "age", "city"],
        ["Анна", "30", "Москва"],
        ["Bob", "007", "New York, NY"],
    ]
    csv_text = convert(xlsx, "csv", out_dir=tmp_path / "c").read_text(encoding="utf-8-sig")
    assert csv_text.splitlines() == ["name,age,city", "Анна,30,Москва", 'Bob,007,"New York, NY"']


def test_xlsx_does_not_need_libreoffice():
    steps = plan(get_format("csv"), get_format("xlsx"))
    assert [s.converter.engine for s in steps] == [None]


def test_table_to_json_keeps_leading_zeros(tmp_path, people):
    data = json.loads(convert(people, "json", out_dir=tmp_path).read_text(encoding="utf-8"))
    assert data == [
        {"name": "Анна", "age": 30, "city": "Москва"},
        {"name": "Bob", "age": "007", "city": "New York, NY"},
    ]


@pytest.mark.parametrize("fmt", ["yaml", "xml", "xlsx", "csv"])
def test_round_trip_through_every_format(tmp_path, people, fmt):
    original = json.loads(convert(people, "json", out_dir=tmp_path / "a").read_text(encoding="utf-8"))
    middle = convert(tmp_path / "a" / "people.json", fmt, out_dir=tmp_path / "b")
    back = json.loads(convert(middle, "json", out_dir=tmp_path / "c").read_text(encoding="utf-8"))
    if fmt == "xml":
        back = back["root"]["item"]
    assert back == original


def test_xml_attributes_and_nesting(tmp_path):
    src = tmp_path / "feed.xml"
    src.write_text(
        '<?xml version="1.0"?><catalog><book id="1"><title>A</title></book><book id="2"><title>B</title></book></catalog>',
        encoding="utf-8",
    )
    data = json.loads(convert(src, "json", out_dir=tmp_path).read_text(encoding="utf-8"))
    assert data == {"catalog": {"book": [{"@id": 1, "title": "A"}, {"@id": 2, "title": "B"}]}}
    rows = convert(src, "csv", out_dir=tmp_path).read_text(encoding="utf-8-sig").splitlines()
    assert rows == ["id,title", "1,A", "2,B"]


def test_non_tabular_data_to_csv_fails_clearly():
    from morphto.core.registry import ConversionError

    with pytest.raises(ConversionError):
        records_to_table({"config": {"debug": True, "paths": {"a": 1}}})


def test_nested_values_become_json_text():
    assert records_to_table([{"a": 1, "b": {"x": [1, 2]}}]) == [["a", "b"], ["1", '{"x": [1, 2]}']]


def test_duplicate_and_empty_headers():
    assert list(table_to_records([["a", "", "a"], ["1", "2", "3"]])[0]) == ["a", "column_2", "a_"]


def test_xml_tag_names_are_made_valid():
    xml = tree_to_xml({"my items": [{"1st": "x"}]})
    assert "<my_items>" in xml and "<_1st>x</_1st>" in xml
