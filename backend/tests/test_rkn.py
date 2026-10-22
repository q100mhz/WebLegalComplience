import io

from app.rkn import _parse

XML = """<?xml version="1.0" encoding="utf-8"?>
<rkn:register xmlns:rkn="http://rsoc.ru">
  <rkn:record>
    <rkn:regn>77-14-000123</rkn:regn>
    <rkn:name_full>ПАО Сбербанк</rkn:name_full>
    <rkn:inn>7707083893</rkn:inn>
    <rkn:income_date>2014-03-01</rkn:income_date>
  </rkn:record>
  <rkn:record><rkn:inn>bad</rkn:inn></rkn:record>
  <rkn:record><rkn:pd_operator_num>1</rkn:pd_operator_num><rkn:inn>500100732259</rkn:inn></rkn:record>
</rkn:register>""".encode()


def test_parse_registry_xml():
    rows = [r for batch in _parse(io.BytesIO(XML)) for r in batch]
    assert [r["inn"] for r in rows] == ["7707083893", "500100732259"]
    assert rows[0]["reg_number"] == "77-14-000123"
    assert rows[0]["name"] == "ПАО Сбербанк"
    assert rows[0]["reg_date"] == "2014-03-01"
    assert rows[1]["reg_number"] == "1"


def test_catalog_finds_operators_dataset():
    from app.rkn import _as_base, _catalog_candidates
    csv_text = (
        "property;title;url\n"
        "7705846236-NewsRSS;Новости;https://rkn.gov.ru/opendata/7705846236-NewsRSS/\n"
        "7705846236-OperatorsPD;Реестр операторов, осуществляющих обработку персональных данных;"
        "https://rkn.gov.ru/opendata/7705846236-OperatorsPD/meta.csv\n"
    )
    urls = _catalog_candidates(csv_text)
    assert urls == ["https://rkn.gov.ru/opendata/7705846236-OperatorsPD/meta.csv"]
    assert _as_base(urls[0]) == "https://rkn.gov.ru/opendata/7705846236-OperatorsPD/"
