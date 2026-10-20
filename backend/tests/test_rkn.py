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
