"""Central schema and registries for ResumeSubmitter.

Keep product vocabulary here instead of scattering it through the Qt window.
Adding a new profile field, resume section, application status, or export format
should normally require changing this file (and optionally one small handler),
not rewriting the UI.
"""
from __future__ import annotations

SCHEMA_VERSION = 3

PROFILE_FIELDS = [
    ("基础信息", [("姓名", "basics.name"), ("邮箱", "basics.email"), ("手机号", "basics.phone"), ("城市", "basics.city"), ("地址", "basics.address")]),
    ("求职信息", [("最高学历", "application.education"), ("学校", "application.school"), ("学位", "application.degree"), ("专业", "application.major"), ("毕业年份", "application.graduationYear"), ("期望职位", "application.title")]),
    ("其他信息", [("性别", "custom.gender"), ("生日", "custom.birthday"), ("身份证号", "custom.idNumber"), ("民族", "custom.ethnicity"), ("政治面貌", "custom.politicalStatus"), ("研究方向", "custom.researchDirection"), ("个人评价", "resumeSections.personalSummary")]),
]

RESUME_SECTIONS = {
    "educationEntries": ("教育经历", [("学校", "school"), ("学历 / 学位", "degree"), ("专业", "major"), ("开始时间", "start"), ("结束时间", "end")]),
    "workExperience": ("工作 / 实习经历", [("公司", "company"), ("职位", "title"), ("开始时间", "start"), ("结束时间", "end"), ("工作内容", "description")]),
    "projects": ("项目经历", [("项目名称", "name"), ("担任角色", "role"), ("项目描述", "description"), ("技术 / 关键词", "technologies")]),
    "personalSummary": ("个人评价 / 自我评价", [("个人评价", "description")]),
}

SECTION_FORMS = {
    **RESUME_SECTIONS,
    "skills": ("专业技能", [("技能名称", "name"), ("熟练程度", "level"), ("说明", "description")]),
    "languages": ("语言能力", [("语言", "name"), ("水平", "level"), ("成绩 / 说明", "description")]),
    "certificates": ("资格证书", [("证书名称", "name"), ("获得时间", "date"), ("发证机构", "issuer"), ("说明", "description")]),
    "honors": ("奖项荣誉", [("奖项名称", "name"), ("获得时间", "date"), ("级别", "level"), ("说明", "description")]),
    "training": ("培训经历", [("培训名称", "name"), ("培训机构", "provider"), ("时间", "date"), ("说明", "description")]),
    "publications": ("论文 / 学术成果", [("名称", "name"), ("发表时间", "date"), ("作者 / 期刊", "publisher"), ("说明", "description")]),
    "portfolios": ("作品 / 链接", [("名称", "name"), ("链接", "url"), ("说明", "description")]),
}

PAGE_FIELD_CATEGORIES = [
    "基础资料", "求职信息", "教育经历", "工作/实习经历", "项目经历", "奖项荣誉",
    "论文/成果", "导师信息", "家庭/紧急联系人", "证明人/推荐人", "自我介绍/个人评价", "公司专属字段",
]

APPLICATION_STATUSES = ["准备投递", "已投递", "笔试", "面试", "Offer", "已入职", "拒绝", "已撤回", "已关闭"]
FAILURE_REASONS = ["未填写", "不符合岗位要求", "学历/专业不匹配", "工作经验不足", "薪资/地点不合适", "招聘暂停/岗位关闭", "重复投递", "技术原因", "其他"]

COMMON_PATHS = {key.rsplit(".", 1)[-1]: key for _, fields in PROFILE_FIELDS for _, key in fields if key != "resumeSections.personalSummary"}
ALLOWED_COMMON_PATHS = {key for _, fields in PROFILE_FIELDS for _, key in fields}
ALLOWED_COMMON_PATHS.update({"basics.linkedin", "basics.website", "custom.expectedSalary", "custom.noticePeriod"})
ALLOWED_SECTION_NAMES = set(SECTION_FORMS)

EXPORT_FORMATS = [("JSON（含 AI 判断）", "json"), ("CSV（便于表格查看）", "csv")]
EXPORT_SCOPES = [("全部审核字段", "all"), ("仅已填写字段", "filled"), ("仅未填写要求", "blank")]


def section_title(section: str) -> str:
    return SECTION_FORMS.get(section, (section, []))[0]
