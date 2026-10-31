from app.scanner import snapshot
from app.scanner.report import build_report
from tests.test_checks import good_site

RKN = {"rkn": {"checked": True, "matches": {"7707083893": []}}}


def test_snapshot_roundtrip_gives_same_report(tmp_path):
    site = good_site()
    site.profile = dict(RKN)
    snapshot.save(site, tmp_path)  # как в воркере: до построения отчёта
    before = build_report(site)

    restored = snapshot.load(tmp_path / snapshot.SNAPSHOT_NAME)
    assert restored.pages[0].forms[0].checkboxes[0].label == site.pages[0].forms[0].checkboxes[0].label
    after = build_report(restored)
    assert [(f["id"], f["status"]) for f in before["findings"]] == [(f["id"], f["status"]) for f in after["findings"]]
