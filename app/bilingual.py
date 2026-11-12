"""双语（中/英）公告核对：实时抓取双侧目录与详情，按公告编号配对比较。

业务规则
--------
1. 输入同站中文、英文目录 URL，分别抓取列表与全部详情页，按公告编号配对，
   比较项目编号、归一化后的公告类型与归一化后的截止时间。
2. 不一致字段返回双侧**页面原值**与来源链接。
3. 两份目录完整稳定（首次读取成功且结束前重读一致）时，报告单侧缺号
   （only_in_zh / only_in_en）。
4. 同一语言内同号指向不同链接：该编号列为待核实（ambiguous），不任选一条
   配对，也不参与缺号判定之外的任何比较。
5. 结束前重读两份目录；目录变动、详情读取或解析失败时，保留已确认的差异，
   整次标为 needs_review，绝不报全部一致。
6. 只使用本次抓取的页面，不读取数据库中的旧详情。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import fetcher, parser
from .fetcher import FetchError

# 核对状态
CONSISTENT = "consistent"
DIFFERENCES_FOUND = "differences_found"
NEEDS_REVIEW = "needs_review"


@dataclass
class _Side:
    """一侧（中文或英文）目录的抓取结果。"""
    language: str
    list_url: str
    # notice_no -> detail_url（同号仅一个链接的条目）
    unique: dict[str, str] = field(default_factory=dict)
    # notice_no -> [url, ...]（同号指向不同链接的歧义条目）
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    # 目录签名：用于结束前重读比对（含重复条目的多重集合）
    signature: list[tuple[str, str]] = field(default_factory=list)
    # notice_no -> 原始详情（仅抓取/解析成功的无歧义条目）
    details: dict[str, parser.RawNoticeDetail] = field(default_factory=dict)

    @property
    def all_numbers(self) -> set[str]:
        """目录中出现过的全部公告编号（含歧义条目）。"""
        return set(self.unique) | set(self.ambiguous)


def _index_entries(
    language: str, list_url: str, entries: list[parser.ListedNotice]
) -> _Side:
    """把列表条目整理为无歧义映射 + 歧义映射，并计算目录签名。"""
    by_no: dict[str, set[str]] = {}
    for entry in entries:
        by_no.setdefault(entry.notice_no, set()).add(entry.detail_url)

    side = _Side(language=language, list_url=list_url)
    side.signature = sorted((e.notice_no, e.detail_url) for e in entries)
    for no, urls in by_no.items():
        if len(urls) > 1:
            side.ambiguous[no] = sorted(urls)
        else:
            side.unique[no] = next(iter(urls))
    return side


def _diff(
    notice_no: str,
    field_name: str,
    zh: parser.RawNoticeDetail,
    en: parser.RawNoticeDetail,
) -> dict:
    return {
        "notice_no": notice_no,
        "field": field_name,
        "zh_value": getattr(zh, field_name),
        "en_value": getattr(en, field_name),
        "zh_url": zh.source_url,
        "en_url": en.source_url,
    }


def _compare_pair(
    zh: parser.RawNoticeDetail, en: parser.RawNoticeDetail
) -> list[dict]:
    """比较一对同号公告，返回不一致字段列表（值为双侧页面原值）。"""
    diffs: list[dict] = []
    no = zh.notice_no
    if zh.project_no != en.project_no:
        diffs.append(_diff(no, "project_no", zh, en))
    if parser.normalize_type(zh.notice_type) != parser.normalize_type(
        en.notice_type
    ):
        diffs.append(_diff(no, "notice_type", zh, en))
    if parser.normalize_datetime(zh.deadline) != parser.normalize_datetime(
        en.deadline
    ):
        diffs.append(_diff(no, "deadline", zh, en))
    return diffs


def check_bilingual(zh_list_url: str, en_list_url: str) -> dict:
    """执行一次双语核对并返回结果（不读写数据库）。"""
    differences: list[dict] = []
    ambiguous_out: list[dict] = []
    failures: list[dict] = []
    review_reasons: list[str] = []
    missing = {"only_in_zh": [], "only_in_en": []}
    checked_pairs = 0

    # 1. 首次读取两份目录
    sides: dict[str, _Side] = {}
    for language, list_url in (("zh", zh_list_url), ("en", en_list_url)):
        try:
            html = fetcher.fetch(list_url)
        except FetchError as exc:
            failures.append(
                {"notice_no": None, "language": language,
                 "url": list_url, "error": str(exc)}
            )
            review_reasons.append(f"listing_fetch_failed:{language}")
            continue
        sides[language] = _index_entries(
            language, list_url, parser.parse_list_entries(html, list_url)
        )

    # 目录读不全就无法配对，直接待核实
    if len(sides) < 2:
        return {
            "status": NEEDS_REVIEW,
            "checked_pairs": 0,
            "differences": [],
            "missing": missing,
            "ambiguous": [],
            "failures": failures,
            "review_reasons": review_reasons,
            "zh_list_url": zh_list_url,
            "en_list_url": en_list_url,
        }

    zh, en = sides["zh"], sides["en"]

    # 2. 同号不同链接 -> 待核实，不任选一条配对
    for side in (zh, en):
        for no, urls in sorted(side.ambiguous.items()):
            ambiguous_out.append(
                {"notice_no": no, "language": side.language, "urls": urls}
            )
    if ambiguous_out:
        review_reasons.append("duplicate_notice_links")

    # 3. 抓取并解析双侧详情（仅无歧义条目；只用本次抓到的页面）
    for side in (zh, en):
        for no, url in sorted(side.unique.items()):
            try:
                detail = parser.parse_detail_raw(fetcher.fetch(url), url)
                if detail.notice_no != no:
                    raise ValueError(
                        f"详情页公告编号 {detail.notice_no} 与目录条目 {no} 不符"
                    )
                side.details[no] = detail
            except (FetchError, ValueError) as exc:
                failures.append(
                    {"notice_no": no, "language": side.language,
                     "url": url, "error": str(exc)}
                )
    if failures:
        review_reasons.append("detail_fetch_or_parse_failed")

    # 4. 配对比较（两侧均成功解析的编号）
    for no in sorted(set(zh.details) & set(en.details)):
        checked_pairs += 1
        differences.extend(_compare_pair(zh.details[no], en.details[no]))

    # 5. 结束前重读两份目录，确认核对期间目录未变动
    listing_stable = True
    for side in (zh, en):
        try:
            html = fetcher.fetch(side.list_url)
        except FetchError as exc:
            listing_stable = False
            failures.append(
                {"notice_no": None, "language": side.language,
                 "url": side.list_url, "error": str(exc)}
            )
            review_reasons.append(f"listing_recheck_failed:{side.language}")
            continue
        entries = parser.parse_list_entries(html, side.list_url)
        if sorted((e.notice_no, e.detail_url) for e in entries) != side.signature:
            listing_stable = False
            review_reasons.append(f"listing_changed:{side.language}")

    # 6. 两份目录完整稳定时，报告单侧缺号
    if listing_stable:
        missing["only_in_zh"] = sorted(zh.all_numbers - en.all_numbers)
        missing["only_in_en"] = sorted(en.all_numbers - zh.all_numbers)

    if review_reasons:
        status = NEEDS_REVIEW
    elif differences or missing["only_in_zh"] or missing["only_in_en"]:
        status = DIFFERENCES_FOUND
    else:
        status = CONSISTENT

    return {
        "status": status,
        "checked_pairs": checked_pairs,
        "differences": differences,
        "missing": missing,
        "ambiguous": ambiguous_out,
        "failures": failures,
        "review_reasons": review_reasons,
        "zh_list_url": zh_list_url,
        "en_list_url": en_list_url,
    }
