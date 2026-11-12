"""示例公共采购公告站（仅依赖 Python 标准库）。

用于在本地/容器内演示采集 API，覆盖以下场景：
  链 A：首次 + 更正（含截止时间继承）
  链 B：首次 + 更正 + 撤销（撤销使链失效）
  链 C：并行更正（同一公告有两个后继 -> 冲突）
  链 D：引用编号不存在（missing_reference -> 冲突 + 孤立）
  链 E：引用成环（reference_cycle -> 孤立）
  链 F：更正无引用编号（missing_reference -> 孤立）
  链 G：同一项目两个独立首次公告（保持两条独立链）
  公告 H：可通过 /fail-toggle 控制详情页返回 500，验证失败保留上次结果

英文目录（/en/announcements）用于演示双语核对，与中文同批但故意制造差异：
  A-003：英文截标时间晚一天（deadline 差异）
  B-003：中文为撤销公告，英文误译为 Correction Notice（公告类型差异）
  F-001：英文项目编号误写为 PRJ-F-EN（项目编号差异）
  H-001：英文目录缺失（only_in_zh）；EN-001：英文目录独有（only_in_en）
  D-001：开启 /en/duplicate-toggle 后出现同号双链接（待核实，不配对）
  目录变动：开启 /en/mutate-toggle 后英文目录每次请求交替增删 EN-900

控制接口：
  GET /fail-toggle?enabled=1|0&notice=H-001   开关指定公告详情页的 500 故障
  GET /fail-status                             查询 H-001 故障开关
  GET /en/duplicate-toggle?enabled=1|0         开关英文目录 D-001 同号双链接
  GET /en/mutate-toggle?enabled=1|0            开关英文目录逐次变动
"""
from __future__ import annotations

import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 每条公告：编号、项目编号、类型、发布时间、截止时间、引用编号
NOTICES: list[dict] = [
    # 链 A：正常链，COR-A-02 不带截止时间（应继承首次的截止时间）
    {"no": "A-001", "project": "PRJ-A", "type": "首次公告",
     "publish": "2026-10-20 09:00:00", "deadline": "2026-11-20 09:30:00",
     "ref": ""},
    {"no": "A-002", "project": "PRJ-A", "type": "更正公告",
     "publish": "2026-10-25 14:00:00", "deadline": "",
     "ref": "A-001"},
    {"no": "A-003", "project": "PRJ-A", "type": "更正公告",
     "publish": "2026-11-01 10:00:00", "deadline": "2026-11-25 15:00:00",
     "ref": "A-002"},

    # 链 B：撤销使整条链失效
    {"no": "B-001", "project": "PRJ-B", "type": "首次公告",
     "publish": "2026-10-21 09:00:00", "deadline": "2026-11-21 09:30:00",
     "ref": ""},
    {"no": "B-002", "project": "PRJ-B", "type": "更正公告",
     "publish": "2026-10-26 14:00:00", "deadline": "2026-11-22 09:30:00",
     "ref": "B-001"},
    {"no": "B-003", "project": "PRJ-B", "type": "撤销公告",
     "publish": "2026-10-28 16:00:00", "deadline": "",
     "ref": "B-002"},

    # 链 C：并行更正（C-002 与 C-003 同时引用 C-001）
    {"no": "C-001", "project": "PRJ-C", "type": "首次公告",
     "publish": "2026-10-22 09:00:00", "deadline": "2026-11-22 09:30:00",
     "ref": ""},
    {"no": "C-002", "project": "PRJ-C", "type": "更正公告",
     "publish": "2026-10-27 10:00:00", "deadline": "2026-11-23 09:30:00",
     "ref": "C-001"},
    {"no": "C-003", "project": "PRJ-C", "type": "更正公告",
     "publish": "2026-10-27 15:00:00", "deadline": "2026-11-24 09:30:00",
     "ref": "C-001"},

    # 链 D：引用了不存在的公告 X-999
    {"no": "D-001", "project": "PRJ-D", "type": "更正公告",
     "publish": "2026-10-23 09:00:00", "deadline": "2026-11-23 09:30:00",
     "ref": "X-999"},

    # 链 E：成环 E-001 -> E-002 -> E-001
    {"no": "E-001", "project": "PRJ-E", "type": "更正公告",
     "publish": "2026-10-24 09:00:00", "deadline": "2026-11-24 09:30:00",
     "ref": "E-002"},
    {"no": "E-002", "project": "PRJ-E", "type": "更正公告",
     "publish": "2026-10-24 10:00:00", "deadline": "2026-11-25 09:30:00",
     "ref": "E-001"},

    # 链 F：更正公告没有引用编号
    {"no": "F-001", "project": "PRJ-F", "type": "更正公告",
     "publish": "2026-10-25 09:00:00", "deadline": "2026-11-26 09:30:00",
     "ref": ""},

    # 链 G：同一项目两个独立首次公告
    {"no": "G-001", "project": "PRJ-G", "type": "首次公告",
     "publish": "2026-10-26 09:00:00", "deadline": "2026-11-26 09:30:00",
     "ref": ""},
    {"no": "G-101", "project": "PRJ-G", "type": "首次公告",
     "publish": "2026-10-27 09:00:00", "deadline": "2026-11-27 09:30:00",
     "ref": ""},
    {"no": "G-102", "project": "PRJ-G", "type": "更正公告",
     "publish": "2026-10-29 09:00:00", "deadline": "2026-11-28 09:30:00",
     "ref": "G-101"},

    # H：详情页故障开关目标（默认正常）
    {"no": "H-001", "project": "PRJ-H", "type": "首次公告",
     "publish": "2026-10-30 09:00:00", "deadline": "2026-11-30 09:30:00",
     "ref": ""},
]

NOTICE_BY_NO = {n["no"]: n for n in NOTICES}

# 英文公告：与中文同批，但故意制造若干译文差异，用于演示双语核对
EN_NOTICES: list[dict] = [
    {"no": "A-001", "project": "PRJ-A", "type": "First Notice",
     "publish": "Oct 20, 2026 09:00", "deadline": "Nov 20, 2026 09:30",
     "ref": ""},
    {"no": "A-002", "project": "PRJ-A", "type": "Correction Notice",
     "publish": "Oct 25, 2026 14:00", "deadline": "",
     "ref": "A-001"},
    # 差异①：英文截标时间晚一天（中文为 2026-11-25 15:00:00）
    {"no": "A-003", "project": "PRJ-A", "type": "Correction Notice",
     "publish": "Nov 1, 2026 10:00", "deadline": "Nov 26, 2026 15:00",
     "ref": "A-002"},

    {"no": "B-001", "project": "PRJ-B", "type": "First Notice",
     "publish": "Oct 21, 2026 09:00", "deadline": "Nov 21, 2026 09:30",
     "ref": ""},
    {"no": "B-002", "project": "PRJ-B", "type": "Correction Notice",
     "publish": "Oct 26, 2026 14:00", "deadline": "Nov 22, 2026 09:30",
     "ref": "B-001"},
    # 差异②：中文为撤销公告，英文误译为更正
    {"no": "B-003", "project": "PRJ-B", "type": "Correction Notice",
     "publish": "Oct 28, 2026 16:00", "deadline": "",
     "ref": "B-002"},

    {"no": "C-001", "project": "PRJ-C", "type": "First Notice",
     "publish": "Oct 22, 2026 09:00", "deadline": "Nov 22, 2026 09:30",
     "ref": ""},
    {"no": "C-002", "project": "PRJ-C", "type": "Correction Notice",
     "publish": "Oct 27, 2026 10:00", "deadline": "Nov 23, 2026 09:30",
     "ref": "C-001"},
    {"no": "C-003", "project": "PRJ-C", "type": "Correction Notice",
     "publish": "Oct 27, 2026 15:00", "deadline": "Nov 24, 2026 09:30",
     "ref": "C-001"},

    {"no": "D-001", "project": "PRJ-D", "type": "Correction Notice",
     "publish": "Oct 23, 2026 09:00", "deadline": "Nov 23, 2026 09:30",
     "ref": "X-999"},

    {"no": "E-001", "project": "PRJ-E", "type": "Correction Notice",
     "publish": "Oct 24, 2026 09:00", "deadline": "Nov 24, 2026 09:30",
     "ref": "E-002"},
    {"no": "E-002", "project": "PRJ-E", "type": "Correction Notice",
     "publish": "Oct 24, 2026 10:00", "deadline": "Nov 25, 2026 09:30",
     "ref": "E-001"},

    # 差异③：英文项目编号误写（中文为 PRJ-F）
    {"no": "F-001", "project": "PRJ-F-EN", "type": "Correction Notice",
     "publish": "Oct 25, 2026 09:00", "deadline": "Nov 26, 2026 09:30",
     "ref": ""},

    {"no": "G-001", "project": "PRJ-G", "type": "First Notice",
     "publish": "Oct 26, 2026 09:00", "deadline": "Nov 26, 2026 09:30",
     "ref": ""},
    {"no": "G-101", "project": "PRJ-G", "type": "First Notice",
     "publish": "Oct 27, 2026 09:00", "deadline": "Nov 27, 2026 09:30",
     "ref": ""},
    {"no": "G-102", "project": "PRJ-G", "type": "Correction Notice",
     "publish": "Oct 29, 2026 09:00", "deadline": "Nov 28, 2026 09:30",
     "ref": "G-101"},

    # 英文目录缺 H-001（only_in_zh），另多一条 EN-001（only_in_en）
    {"no": "EN-001", "project": "PRJ-EN", "type": "First Notice",
     "publish": "Nov 2, 2026 09:00", "deadline": "Dec 2, 2026 09:30",
     "ref": ""},
]

EN_NOTICE_BY_NO = {n["no"]: n for n in EN_NOTICES}

# 目录变动演示时交替增删的公告
EN_EXTRA_NOTICE = {"no": "EN-900", "project": "PRJ-X", "type": "First Notice",
                   "publish": "Nov 5, 2026 09:00", "deadline": "Dec 5, 2026 09:30",
                   "ref": ""}

# 英文详情页可渲染的公告（含目录变动演示用的 EN-900）
EN_DETAIL_PAGES = {**EN_NOTICE_BY_NO, EN_EXTRA_NOTICE["no"]: EN_EXTRA_NOTICE}

# 运行期开关（内存态，重启容器即恢复）
FAIL_ENABLED = {"H-001": False}          # 公告编号 -> 详情页是否返回 500
DUPLICATE_LINKS = {"enabled": False}     # 英文目录 D-001 同号双链接
MUTATE_LISTING = {"enabled": False, "tick": 0}  # 英文目录逐次变动

PAGE_CSS = """
body { font-family: sans-serif; margin: 2em; color: #222; }
h1 { font-size: 1.4em; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #999; padding: 6px 10px; text-align: left; }
th { background: #f0f3f7; }
a { color: #1a56c4; }
.meta { color: #666; margin-bottom: 1em; }
"""


def render_list() -> str:
    rows = "\n".join(
        '<tr><td><a href="/notice/{no}">{no}</a></td>'
        '<td>{project}</td><td>{type}</td><td>{publish}</td></tr>'.format(
            no=escape(n["no"]), project=escape(n["project"]),
            type=escape(n["type"]), publish=escape(n["publish"]),
        )
        for n in NOTICES
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>公共采购公告列表</title><style>{PAGE_CSS}</style></head>
<body>
<h1>公共采购公告列表（示例站）</h1>
<p class="meta">共 {len(NOTICES)} 条公告 · 仅用于演示采集 API</p>
<table class="announcement-list">
<thead><tr><th>公告编号</th><th>项目编号</th><th>类型</th><th>发布时间</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""


def _row(label: str, value: str) -> str:
    return (
        f"<tr><th>{escape(label)}</th>"
        f"<td>{escape(value) if value else '—'}</td></tr>"
    )


def render_detail(no: str) -> str:
    n = NOTICE_BY_NO[no]
    rows = "".join([
        _row("公告编号", n["no"]),
        _row("项目编号", n["project"]),
        _row("公告类型", n["type"]),
        _row("发布时间", n["publish"]),
        _row("投标截止时间", n["deadline"]),
        _row("引用编号", n["ref"]),
    ])
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>公告 {escape(n['no'])}</title><style>{PAGE_CSS}</style></head>
<body>
<h1>公告详情 {escape(n['no'])}</h1>
<p><a href="/announcements">← 返回列表</a></p>
<table class="detail-table"><tbody>{rows}</tbody></table>
</body></html>"""


def _en_row(n: dict, href_no: str | None = None) -> str:
    href = href_no or n["no"]
    return (
        f'<tr><td><a href="/en/notice/{escape(href)}">{escape(n["no"])}</a></td>'
        f'<td>{escape(n["project"])}</td><td>{escape(n["type"])}</td>'
        f'<td>{escape(n["publish"])}</td></tr>'
    )


def render_en_list() -> str:
    rows = [_en_row(n) for n in EN_NOTICES]
    if DUPLICATE_LINKS["enabled"]:
        # 同号不同链接：D-001 出现第二个入口，指向另一个详情地址
        rows.insert(10, _en_row(EN_NOTICE_BY_NO["D-001"], href_no="D-001-alt"))
    if MUTATE_LISTING["enabled"]:
        # 每次请求交替增删一条，模拟核对期间目录发生变动
        MUTATE_LISTING["tick"] += 1
        if MUTATE_LISTING["tick"] % 2 == 1:
            rows.append(_en_row(EN_EXTRA_NOTICE))
    body = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Procurement Notices</title><style>{PAGE_CSS}</style></head>
<body>
<h1>Procurement Notices (Sample Site, EN)</h1>
<p class="meta">{len(rows)} notices · for bilingual check demo</p>
<table class="announcement-list">
<thead><tr><th>Notice No</th><th>Project No</th><th>Type</th><th>Publish Time</th></tr></thead>
<tbody>{body}</tbody>
</table>
</body></html>"""


def render_en_detail(no: str) -> str:
    n = EN_DETAIL_PAGES[no]
    rows = "".join([
        _row("Notice No", n["no"]),
        _row("Project No", n["project"]),
        _row("Notice Type", n["type"]),
        _row("Publish Time", n["publish"]),
        _row("Bid Deadline", n["deadline"]),
        _row("Reference No", n["ref"]),
    ])
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Notice {escape(n['no'])}</title><style>{PAGE_CSS}</style></head>
<body>
<h1>Notice Detail {escape(n['no'])}</h1>
<p><a href="/en/announcements">← Back to list</a></p>
<table class="detail-table"><tbody>{rows}</tbody></table>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, status: int = 200,
              content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path in ("/", "/announcements"):
            self._send(render_list())
            return

        if path == "/en/announcements":
            self._send(render_en_list())
            return

        if path == "/fail-toggle":
            enabled = (query.get("enabled", ["0"])[0] in {"1", "true", "yes"})
            notice = query.get("notice", ["H-001"])[0]
            FAIL_ENABLED[notice] = enabled
            self._send(
                f'{{"notice": "{escape(notice)}", '
                f'"fail_enabled": {str(enabled).lower()}}}',
                content_type="application/json; charset=utf-8",
            )
            return

        if path == "/fail-status":
            enabled = FAIL_ENABLED.get("H-001", False)
            self._send(
                f'{{"H-001_fail_enabled": {str(enabled).lower()}}}',
                content_type="application/json; charset=utf-8",
            )
            return

        if path == "/en/duplicate-toggle":
            enabled = (query.get("enabled", ["0"])[0] in {"1", "true", "yes"})
            DUPLICATE_LINKS["enabled"] = enabled
            self._send(
                f'{{"en_duplicate_links_enabled": {str(enabled).lower()}}}',
                content_type="application/json; charset=utf-8",
            )
            return

        if path == "/en/mutate-toggle":
            enabled = (query.get("enabled", ["0"])[0] in {"1", "true", "yes"})
            MUTATE_LISTING["enabled"] = enabled
            MUTATE_LISTING["tick"] = 0
            self._send(
                f'{{"en_listing_mutate_enabled": {str(enabled).lower()}}}',
                content_type="application/json; charset=utf-8",
            )
            return

        if path.startswith("/notice/"):
            no = path.rsplit("/", 1)[-1]
            notice = NOTICE_BY_NO.get(no)
            if notice is None:
                self._send("<h1>404 公告不存在</h1>", status=404)
                return
            if FAIL_ENABLED.get(no):
                self._send("<h1>500 服务器内部错误（模拟故障）</h1>", status=500)
                return
            self._send(render_detail(no))
            return

        if path.startswith("/en/notice/"):
            no = path.rsplit("/", 1)[-1]
            notice = EN_DETAIL_PAGES.get(no)
            if notice is None and no.endswith("-alt"):
                notice = EN_DETAIL_PAGES.get(no[: -len("-alt")])
            if notice is None:
                self._send("<h1>404 Notice Not Found</h1>", status=404)
                return
            if FAIL_ENABLED.get(notice["no"]):
                self._send("<h1>500 Internal Server Error (simulated)</h1>",
                           status=500)
                return
            self._send(render_en_detail(notice["no"]))
            return

        self._send("<h1>404</h1>", status=404)

    def log_message(self, fmt: str, *args) -> None:  # 精简日志
        print(f"[sample-site] {self.address_string()} {fmt % args}")


def main() -> None:
    port = int(os.getenv("SAMPLE_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[sample-site] 示例公告站监听 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
