"""Pydantic 请求/响应模型。"""
from typing import Optional

from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    list_url: str = Field(..., description="公共采购网站公告列表页 URL")


class AnnouncementItem(BaseModel):
    notice_no: str
    project_no: Optional[str] = None
    notice_type: Optional[str] = None
    publish_time: Optional[str] = None
    deadline: Optional[str] = None
    ref_notice_no: Optional[str] = None
    source_url: Optional[str] = None
    conflict_reasons: list[str] = []
    effective_deadline: Optional[str] = None


class Chain(BaseModel):
    chain_id: str = Field(..., description="链标识，即首次公告编号")
    project_no: Optional[str] = None
    status: str = Field(..., description="ok / conflict / cancelled")
    effective_deadline: Optional[str] = Field(
        None, description="有效截止时间；冲突或已撤销时为 null"
    )
    announcements: list[AnnouncementItem] = []


class OrphanAnnouncement(BaseModel):
    notice_no: str
    project_no: Optional[str] = None
    notice_type: Optional[str] = None
    status: str
    conflict_reasons: list[str] = []
    effective_deadline: Optional[str] = None
    source_url: Optional[str] = None
    publish_time: Optional[str] = None
    deadline: Optional[str] = None
    ref_notice_no: Optional[str] = None


class QueryResponse(BaseModel):
    chains: list[Chain] = []
    orphans: list[OrphanAnnouncement] = []


class SyncResponse(BaseModel):
    run_id: str
    status: str
    total_listed: int
    fetched_ok: int
    failed_count: int
    kept_last: int
    message: Optional[str] = None


class BilingualCheckRequest(BaseModel):
    zh_list_url: str = Field(..., description="中文公告目录（列表页）URL")
    en_list_url: str = Field(..., description="同站英文公告目录（列表页）URL")


class FieldDifference(BaseModel):
    notice_no: str
    field: str = Field(..., description="project_no / notice_type / deadline")
    zh_value: Optional[str] = Field(None, description="中文页面原始值")
    en_value: Optional[str] = Field(None, description="英文页面原始值")
    zh_url: Optional[str] = Field(None, description="中文详情页链接")
    en_url: Optional[str] = Field(None, description="英文详情页链接")


class MissingNotices(BaseModel):
    only_in_zh: list[str] = Field(
        default_factory=list, description="仅中文目录存在的公告编号"
    )
    only_in_en: list[str] = Field(
        default_factory=list, description="仅英文目录存在的公告编号"
    )


class AmbiguousNotice(BaseModel):
    notice_no: str
    language: str = Field(..., description="出现歧义的一侧：zh / en")
    urls: list[str] = Field(..., description="同一编号指向的不同链接")


class SideFailure(BaseModel):
    language: str
    url: str
    error: str
    notice_no: Optional[str] = None


class BilingualCheckResponse(BaseModel):
    status: str = Field(
        ..., description="consistent / differences_found / needs_review"
    )
    checked_pairs: int = Field(..., description="成功配对并比较的公告数")
    differences: list[FieldDifference] = Field(
        default_factory=list, description="已确认的字段差异（含双侧原值与链接）"
    )
    missing: MissingNotices = Field(
        default_factory=MissingNotices,
        description="单侧缺号（仅目录完整稳定时报告）",
    )
    ambiguous: list[AmbiguousNotice] = Field(
        default_factory=list, description="同号多链接的待核实公告"
    )
    failures: list[SideFailure] = Field(
        default_factory=list, description="目录/详情抓取或解析失败明细"
    )
    review_reasons: list[str] = Field(
        default_factory=list, description="整次标为待核实的原因"
    )
    zh_list_url: str
    en_list_url: str
