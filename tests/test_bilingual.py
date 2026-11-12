"""双语核对 API：字段差异、单侧缺号、同号多链接、目录变动与详情失败。"""
from __future__ import annotations

from http.client import HTTPConnection

import pytest


def _toggle(base_url: str, path: str) -> None:
    conn = HTTPConnection("127.0.0.1", base_url.rsplit(":", 1)[1])
    conn.request("GET", path)
    resp = conn.getresponse()
    resp.read()
    conn.close()


@pytest.fixture(autouse=True)
def reset_site_toggles(site_base_url):
    """示例站开关为会话级共享状态，每个用例结束后复位。"""
    yield
    for path in (
        "/fail-toggle?enabled=0&notice=A-001",
        "/fail-toggle?enabled=0&notice=H-001",
        "/en/duplicate-toggle?enabled=0",
        "/en/mutate-toggle?enabled=0",
    ):
        _toggle(site_base_url, path)


def _check(client, site_base_url, zh_path="/announcements", en_path="/en/announcements"):
    return client.post(
        "/api/v1/bilingual-check",
        json={
            "zh_list_url": f"{site_base_url}{zh_path}",
            "en_list_url": f"{site_base_url}{en_path}",
        },
    )


def _diffs_by_key(body: dict) -> dict:
    return {(d["notice_no"], d["field"]): d for d in body["differences"]}


def test_reports_field_differences_with_raw_values_and_links(
    client, site_base_url
):
    c, _ = client
    resp = _check(c, site_base_url)
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "differences_found"
    assert body["review_reasons"] == []
    # 17 条中文 - H-001 = 16 对成功配对
    assert body["checked_pairs"] == 16

    diffs = _diffs_by_key(body)
    assert set(diffs) == {
        ("A-003", "deadline"),
        ("B-003", "notice_type"),
        ("F-001", "project_no"),
    }

    # 不一致字段返回双侧页面原值与来源链接
    d = diffs[("A-003", "deadline")]
    assert d["zh_value"] == "2026-11-25 15:00:00"
    assert d["en_value"] == "Nov 26, 2026 15:00"
    assert d["zh_url"].endswith("/notice/A-003")
    assert d["en_url"].endswith("/en/notice/A-003")

    d = diffs[("B-003", "notice_type")]
    assert d["zh_value"] == "撤销公告"
    assert d["en_value"] == "Correction Notice"

    d = diffs[("F-001", "project_no")]
    assert d["zh_value"] == "PRJ-F"
    assert d["en_value"] == "PRJ-F-EN"


def test_reports_missing_numbers_when_listings_stable(client, site_base_url):
    c, _ = client
    body = _check(c, site_base_url).json()
    assert body["missing"]["only_in_zh"] == ["H-001"]
    assert body["missing"]["only_in_en"] == ["EN-001"]


def test_consistent_when_two_catalogs_agree(client, site_base_url):
    c, _ = client
    # 英文目录与自身核对：全部一致、无缺号
    resp = _check(c, site_base_url, zh_path="/en/announcements")
    body = resp.json()
    assert body["status"] == "consistent"
    assert body["checked_pairs"] == 17
    assert body["differences"] == []
    assert body["missing"] == {"only_in_zh": [], "only_in_en": []}


def test_duplicate_links_flagged_not_paired(client, site_base_url):
    c, _ = client
    _toggle(site_base_url, "/en/duplicate-toggle?enabled=1")
    body = _check(c, site_base_url).json()

    assert body["status"] == "needs_review"
    assert "duplicate_notice_links" in body["review_reasons"]

    assert len(body["ambiguous"]) == 1
    amb = body["ambiguous"][0]
    assert amb["notice_no"] == "D-001"
    assert amb["language"] == "en"
    assert len(amb["urls"]) == 2
    assert any(u.endswith("/en/notice/D-001") for u in amb["urls"])
    assert any(u.endswith("/en/notice/D-001-alt") for u in amb["urls"])

    # 不任选一条配对：D-001 不参与比较
    assert body["checked_pairs"] == 15
    assert all(d["notice_no"] != "D-001" for d in body["differences"])
    # 已确认的差异仍然保留
    assert ("A-003", "deadline") in _diffs_by_key(body)


def test_listing_change_during_check_forces_review(client, site_base_url):
    c, _ = client
    _toggle(site_base_url, "/en/mutate-toggle?enabled=1")
    body = _check(c, site_base_url).json()

    assert body["status"] == "needs_review"
    assert "listing_changed:en" in body["review_reasons"]
    # 目录不稳定时不报告缺号，但已确认的差异保留
    assert body["missing"] == {"only_in_zh": [], "only_in_en": []}
    assert ("A-003", "deadline") in _diffs_by_key(body)
    assert ("B-003", "notice_type") in _diffs_by_key(body)


def test_detail_failure_keeps_confirmed_differences(client, site_base_url):
    c, _ = client
    _toggle(site_base_url, "/fail-toggle?enabled=1&notice=A-001")
    body = _check(c, site_base_url).json()

    assert body["status"] == "needs_review"
    assert "detail_fetch_or_parse_failed" in body["review_reasons"]

    failed = {(f["notice_no"], f["language"]) for f in body["failures"]}
    assert ("A-001", "zh") in failed
    assert ("A-001", "en") in failed

    # 失败编号不参与配对，其余配对与已确认差异保留
    assert body["checked_pairs"] == 15
    diffs = _diffs_by_key(body)
    assert ("A-003", "deadline") in diffs
    assert ("B-003", "notice_type") in diffs
    assert ("F-001", "project_no") in diffs
    # 目录本身稳定，缺号照常报告
    assert body["missing"]["only_in_zh"] == ["H-001"]
    assert body["missing"]["only_in_en"] == ["EN-001"]


def test_listing_fetch_failure_is_needs_review(client):
    c, _ = client
    resp = c.post(
        "/api/v1/bilingual-check",
        json={
            "zh_list_url": "http://127.0.0.1:1/announcements",
            "en_list_url": "http://127.0.0.1:1/en/announcements",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "needs_review"
    assert body["checked_pairs"] == 0
    assert body["differences"] == []
    assert len(body["failures"]) == 2
    assert "listing_fetch_failed:zh" in body["review_reasons"]
    assert "listing_fetch_failed:en" in body["review_reasons"]


def test_bilingual_check_rejects_bad_url(client):
    c, _ = client
    resp = c.post(
        "/api/v1/bilingual-check",
        json={"zh_list_url": "not-a-url", "en_list_url": "http://x/en"},
    )
    assert resp.status_code == 400
