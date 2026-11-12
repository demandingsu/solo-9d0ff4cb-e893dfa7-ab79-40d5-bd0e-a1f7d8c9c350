"""解析器单元测试。"""
from __future__ import annotations

from app.parser import (
    normalize_datetime,
    normalize_type,
    parse_detail,
    parse_detail_raw,
    parse_list,
    parse_list_entries,
)


def test_normalize_type():
    assert normalize_type("更正公告") == "correction"
    assert normalize_type("首次公告") == "first"
    assert normalize_type("撤销公告") == "revocation"
    assert normalize_type("未知类型") is None
    assert normalize_type(None) is None


def test_normalize_type_english():
    assert normalize_type("First Notice") == "first"
    assert normalize_type("Correction Notice") == "correction"
    assert normalize_type("Revocation Notice") == "revocation"
    assert normalize_type("Amendment No. 2") == "correction"
    assert normalize_type("Cancellation of Tender") == "revocation"


def test_normalize_datetime_formats():
    assert normalize_datetime("2026-11-20 09:30:00") == "2026-11-20 09:30:00"
    assert normalize_datetime("2026/11/20 9:30") == "2026-11-20 09:30:00"
    assert normalize_datetime("2026年11月20日") == "2026-11-20 00:00:00"
    assert normalize_datetime("  ") is None
    # 无法识别的格式原样保留
    assert normalize_datetime("待定") == "待定"


def test_normalize_datetime_english_formats():
    assert normalize_datetime("Nov 20, 2026 09:30") == "2026-11-20 09:30:00"
    assert normalize_datetime("November 20, 2026") == "2026-11-20 00:00:00"
    assert normalize_datetime("11/20/2026 09:30") == "2026-11-20 09:30:00"


def test_parse_list_extracts_notices_and_absolute_urls():
    html = """
    <table class="announcement-list">
      <tr><td><a href="/notice/A-001">A-001 公告</a></td></tr>
      <tr><td><a href="/notice/B-002">B-002 公告</a></td></tr>
      <tr><td><a href="/notice/A-001">A-001 重复入口</a></td></tr>
    </table>
    """
    items = parse_list(html, "http://example.com/announcements")
    assert [i.notice_no for i in items] == ["A-001", "B-002"]
    assert items[0].detail_url == "http://example.com/notice/A-001"


def test_parse_detail_maps_all_fields():
    html = """
    <table class="detail-table">
      <tr><th>公告编号</th><td>A-001</td></tr>
      <tr><th>项目编号</th><td>PRJ-A</td></tr>
      <tr><th>公告类型</th><td>首次公告</td></tr>
      <tr><th>发布时间</th><td>2026-10-20 09:00:00</td></tr>
      <tr><th>投标截止时间</th><td>2026/11/20 09:30</td></tr>
      <tr><th>引用编号</th><td></td></tr>
    </table>
    """
    detail = parse_detail(html, "http://example.com/notice/A-001")
    assert detail.notice_no == "A-001"
    assert detail.project_no == "PRJ-A"
    assert detail.notice_type == "first"
    assert detail.publish_time == "2026-10-20 09:00:00"
    assert detail.deadline == "2026-11-20 09:30:00"
    assert detail.ref_notice_no is None
    assert detail.source_url == "http://example.com/notice/A-001"


def test_parse_list_entries_keeps_duplicate_links():
    html = """
    <table class="announcement-list">
      <tr><td><a href="/notice/D-001">D-001</a></td></tr>
      <tr><td><a href="/notice/D-001-alt">D-001</a></td></tr>
    </table>
    """
    entries = parse_list_entries(html, "http://example.com/en/announcements")
    assert [e.notice_no for e in entries] == ["D-001", "D-001"]
    assert entries[0].detail_url != entries[1].detail_url
    # parse_list 仍按同号取第一条去重
    assert len(parse_list(html, "http://example.com/en/announcements")) == 1


def test_parse_detail_raw_keeps_original_text():
    html = """
    <table class="detail-table">
      <tr><th>Notice No</th><td>A-001</td></tr>
      <tr><th>Project No</th><td>PRJ-A</td></tr>
      <tr><th>Notice Type</th><td>Correction Notice</td></tr>
      <tr><th>Publish Time</th><td>Oct 20, 2026 09:00</td></tr>
      <tr><th>Bid Deadline</th><td>Nov 20, 2026 09:30</td></tr>
      <tr><th>Reference No</th><td></td></tr>
    </table>
    """
    detail = parse_detail_raw(html, "http://example.com/en/notice/A-001")
    assert detail.notice_no == "A-001"
    assert detail.project_no == "PRJ-A"
    # 类型与截止时间保留页面原文，不归一化
    assert detail.notice_type == "Correction Notice"
    assert detail.deadline == "Nov 20, 2026 09:30"
    assert detail.source_url == "http://example.com/en/notice/A-001"
