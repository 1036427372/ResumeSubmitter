"""Extracts reusable profile data from a candidate's own resume."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib import request

from api_client import chat_completions_url, readable_api_error

SHAPE = {
    "basics": ("name", "email", "phone", "city", "address", "linkedin", "website"),
    "application": ("education", "school", "degree", "major", "graduationYear", "company", "title", "yearsExperience", "workAuthorization", "sponsorship"),
    "custom": ("gender", "birthday", "idNumber", "ethnicity", "politicalStatus", "researchDirection", "courses", "hobbies", "expectedSalary", "noticePeriod"),
}
SECTION_SHAPE = {
    "workExperience": ("company", "title", "start", "end", "description", "achievements"),
    "projects": ("name", "role", "start", "end", "description", "technologies", "link"),
    "educationEntries": ("school", "degree", "major", "start", "end", "gpa"),
    "skills": ("name", "level", "category"), "languages": ("language", "level", "certificate"),
    "certificates": ("name", "issuer", "date", "credentialId", "link"),
    "honors": ("name", "date", "level", "description"),
    "training": ("course", "provider", "start", "end", "description"),
    "publications": ("title", "publisher", "date", "link"), "portfolios": ("name", "type", "link", "description"),
    "personalSummary": ("description",),
}


def extract_text(filename: str) -> str:
    path = Path(filename)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        except ImportError:
            # A Windows release normally includes pypdf. This fallback also keeps
            # PDF import usable in a developer environment with Poppler installed.
            executable = shutil.which("pdftotext")
            if not executable:
                raise ValueError("缺少 PDF 解析组件：请安装 pypdf，或安装 Poppler 的 pdftotext。")
            completed = subprocess.run([executable, "-layout", str(path), "-"], capture_output=True, text=True, encoding="utf-8", errors="ignore", check=True)
            return completed.stdout
    if suffix == ".docx":
        from docx import Document
        return "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError("目前支持 PDF、DOCX 或 TXT 简历。")


def local_parse(text: str) -> dict:
    """Extract the common one-page Chinese-resume layout without needing an LLM."""
    result = {group: {key: "" for key in keys} for group, keys in SHAPE.items()}
    result["resumeSections"] = {section: [] for section in SECTION_SHAPE}
    text = text.replace("\u3000", " ")
    compact = lambda value: re.sub(r"[ \t]+", " ", value or "").strip(" -：:，,。")
    section = lambda start, *ends: _between(text, start, *ends)
    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
    phone = re.search(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[- ]?\d{4}[- ]?\d{4}(?!\d)|(?<!\d)\+?[\d][\d ()-]{7,}\d(?!\d)", text)
    id_number = re.search(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)", text)
    linkedin = re.search(r"https?://(?:[\w-]+\.)?linkedin\.com/in/[^\s)>]+", text, re.I)
    website = re.search(r"https?://[^\s)>]+", text, re.I)
    if email: result["basics"]["email"] = email.group(0)
    if phone: result["basics"]["phone"] = re.sub(r"\s+", "", phone.group(0))
    if id_number: result["custom"]["idNumber"] = id_number.group(0).upper()
    if linkedin: result["basics"]["linkedin"] = linkedin.group(0)
    if website: result["basics"]["website"] = website.group(0)
    name = re.search(r"姓\s*名\s*[：:]\s*([^\s：:]{2,12})", text)
    birthday = re.search(r"出生(?:年月|日期)?\s*[：:]\s*((?:19|20)\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?)", text)
    ethnicity = re.search(r"民\s*族\s*[：:]\s*([^\s：:]{1,12})", text)
    political = re.search(r"政治面貌\s*[：:]\s*([^\n\r]{1,24})", text)
    if name: result["basics"]["name"] = compact(name.group(1))
    if birthday: result["custom"]["birthday"] = birthday.group(1).replace(".", "-").replace("/", "-")
    if ethnicity: result["custom"]["ethnicity"] = compact(ethnicity.group(1))
    if political: result["custom"]["politicalStatus"] = compact(political.group(1))
    year = re.search(r"(?:毕业|graduat\w*)[^\d]{0,12}((?:19|20)\d{2})|((?:19|20)\d{2})[^\n]{0,12}(?:毕业|graduat\w*)", text, re.I)
    if year: result["application"]["graduationYear"] = next(value for value in year.groups() if value)
    education = re.search(r"博士|硕士|研究生|本科|大专|ph\.?d|master'?s?|bachelor'?s?", text, re.I)
    if education: result["application"]["education"] = education.group(0)
    education_text = section("教育经历", "学术研究", "科研项目", "项目经历")
    for degree, school, major, ranking in re.findall(r"(?:^|\n)\s*(?:\d+\s+)?(本科|硕士|博士|专科)\s+([^\s\n]{2,40})\s+([^\s\n]{2,40})(?:\s+专业排名[：:]?\s*([^\n]+))?", education_text):
        result["resumeSections"]["educationEntries"].append({"school": compact(school), "degree": degree, "major": compact(major), "start": "", "end": "", "gpa": compact(ranking)})
    highest = next((entry for entry in result["resumeSections"]["educationEntries"] if entry["degree"] == "硕士"), None) or (result["resumeSections"]["educationEntries"][-1] if result["resumeSections"]["educationEntries"] else None)
    if highest:
        result["application"].update({"education": highest["degree"], "school": highest["school"], "degree": highest["degree"], "major": highest["major"]})
    research = re.search(r"研究方向[：:]\s*([^\n]+)", education_text)
    courses = re.search(r"专业课程[：:]\s*([^\n]+)", education_text)
    if research: result["custom"]["researchDirection"] = compact(research.group(1))
    if courses: result["custom"]["courses"] = compact(courses.group(1))

    result["resumeSections"]["projects"].extend(_parse_projects(section("科研项目", "项目经历", "技能证书", "专业技能", "个人经历")))
    publications, patents = _parse_academic(section("学术研究", "科研项目", "项目经历"))
    result["resumeSections"]["publications"].extend(publications)
    result["resumeSections"]["certificates"].extend(patents)
    skill_text = section("技能证书", "专业技能", "个人经历", "自我评价")
    skills, languages, certificates = _parse_skills(skill_text)
    result["resumeSections"]["skills"].extend(skills)
    result["resumeSections"]["languages"].extend(languages)
    result["resumeSections"]["certificates"].extend(certificates)
    honors, hobbies = _parse_personal_experience(section("个人经历", "自我评价", "求职意向"))
    if not hobbies:
        hobby = re.search(r"爱好[：:]?\s*([^✸★\n]+)", skill_text)
        hobbies = _clean_inline(hobby.group(1)) if hobby else ""
    result["resumeSections"]["honors"].extend(honors)
    result["custom"]["hobbies"] = hobbies
    summary = _clean_inline(section("自我评价", "求职意向", "教育经历", "工作经历", "项目经历"))
    if summary:
        result["resumeSections"]["personalSummary"].append({"description": summary})
    return result


def _between(text: str, start: str, *ends: str) -> str:
    match = re.search(re.escape(start), text)
    if not match:
        return ""
    tail = text[match.end():]
    positions = [found.start() for end in ends if (found := re.search(re.escape(end), tail))]
    return tail[:min(positions)] if positions else tail


def _parse_projects(text: str) -> list[dict]:
    entries: list[dict] = []
    pattern = re.compile(r"(?P<period>20\d{2}\.\d{1,2}\s*-\s*(?:至今|20\d{2}\.\d{1,2}))\s+(?P<company>[^《\n]{2,70})《(?P<name>[^》\n]{2,100})》\s*(?P<role>主要负责人|负责人|核心成员|项目成员)?")
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        details = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        details = re.sub(r"^\s*主要工作[：:]?", "", details)
        start, end = (part.strip() for part in match.group("period").split("-", 1))
        entries.append({"name": f"{_clean_inline(match.group('company'))}《{_clean_inline(match.group('name'))}》", "role": match.group("role") or "", "start": start, "end": end, "description": _clean_inline(details), "technologies": _technologies(details), "link": ""})
    return entries


def _parse_academic(text: str) -> tuple[list[dict], list[dict]]:
    publications, patents = [], []
    for match in re.finditer(r"《(?P<title>[^》\n]{3,180})》(?P<tail>[\s\S]{0,120}?)(?=《|$)", text):
        title, tail = _clean_inline(match.group("title")), _clean_inline(match.group("tail"))
        if not title:
            continue
        if "专利" in tail:
            patents.append({"name": title, "issuer": "发明专利", "date": "", "credentialId": "", "link": ""})
        else:
            publications.append({"title": title, "publisher": tail, "date": "", "link": ""})
    return publications, patents


def _parse_skills(text: str) -> tuple[list[dict], list[dict], list[dict]]:
    skills, languages, certificates = [], [], []
    english = re.search(r"英语[：:]?\s*([^✸★\n]+)", text)
    if english:
        for item in re.findall(r"(?:CET[- ]?[46]|雅思[^\s，、]*|托福[^\s，、]*)", english.group(1), re.I):
            languages.append({"language": "英语", "level": item.upper().replace(" ", "-"), "certificate": item.upper().replace(" ", "-")})
    computer = re.search(r"计算机[：:]?\s*([^✸★\n]+)", text)
    if computer:
        items = re.findall(r"计算机[一二三四]级(?:（[^）]+）)?", computer.group(1))
        for item in items or [_clean_inline(computer.group(1))]:
            if item: certificates.append({"name": _clean_inline(item), "issuer": "", "date": "", "credentialId": "", "link": ""})
    software = re.search(r"软件技能[：:]?\s*([\s\S]*?)(?=\s*[✸★]\s*(?:驾驶证|爱好|业务技能)|$)", text)
    if software:
        skills.append({"name": _clean_inline(software.group(1)), "level": "熟练", "category": "软件与工程工具"})
    business = re.search(r"业务技能[：:]?\s*([^\n]+)", text)
    if business:
        skills.append({"name": _clean_inline(business.group(1)), "level": "", "category": "业务能力"})
    driving = re.search(r"驾驶证[：:]?\s*([^✸★\n]+)", text)
    if driving:
        certificates.append({"name": _clean_inline(driving.group(1)), "issuer": "", "date": "", "credentialId": "", "link": ""})
    return skills, languages, certificates


def _parse_personal_experience(text: str) -> tuple[list[dict], str]:
    honors = []
    patterns = (
        r"校学业[一二三]等奖学金", r"(?<!学业)[一二三]等奖学金", r"“[^”\n]+”[^，。\n]{0,45}全国[一二三]等奖",
        r"校级优秀毕业生", r"校级奖励\s*\d+\s*项", r"校级党支部风采大赛校级[一二三]等奖",
    )
    for pattern in patterns:
        for item in re.findall(pattern, text):
            item = _clean_inline(item)
            if item and not any(row["name"] == item for row in honors): honors.append({"name": item, "date": "", "level": "", "description": ""})
    hobby = re.search(r"爱好[：:]?\s*([^✸★\n]+)", text)
    return honors, _clean_inline(hobby.group(1)) if hobby else ""


def _clean_inline(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" ：:，,。")


def _technologies(text: str) -> str:
    terms = re.findall(r"(?:NSET|BiLSTM|LSTM|SOH|SOC|风资源评价|功率曲线|数据清洗|故障预警|声雷达)", text, re.I)
    return "、".join(dict.fromkeys(term.upper() if term.lower() in {"nset", "bilstm", "lstm", "soh", "soc"} else term for term in terms))


def _clean_shape(payload: dict) -> dict:
    result = {group: {} for group in SHAPE}
    for group, keys in SHAPE.items():
        values = payload.get(group, {}) if isinstance(payload, dict) else {}
        for key in keys:
            value = values.get(key, "") if isinstance(values, dict) else ""
            result[group][key] = str(value).strip() if value is not None else ""
    result["resumeSections"] = {}
    sections = payload.get("resumeSections", {}) if isinstance(payload, dict) else {}
    for section, fields in SECTION_SHAPE.items():
        raw_entries = sections.get(section, []) if isinstance(sections, dict) else []
        entries = raw_entries if isinstance(raw_entries, list) else []
        result["resumeSections"][section] = [{field: str(entry.get(field, "")).strip() for field in fields} for entry in entries if isinstance(entry, dict) and any(entry.get(field) for field in fields)]
    return result


def llm_parse(text: str, settings: dict) -> dict:
    if not settings.get("apiKey") or not settings.get("endpoint"):
        return {}
    schema = {group: list(keys) for group, keys in SHAPE.items()}
    schema["resumeSections"] = {section: ["one object with: " + ", ".join(fields)] for section, fields in SECTION_SHAPE.items()}
    prompt = ("Extract only explicit personal details from this resume. Do not infer or fabricate. "
              f"Return only a JSON object shaped exactly like this: {json.dumps(schema)}. Use empty strings for unknown values.\n\nRESUME:\n{text[:30000]}")
    payload = json.dumps({"model": settings.get("model") or "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0, "response_format": {"type": "json_object"}}).encode()
    req = request.Request(chat_completions_url(settings["endpoint"]), data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['apiKey']}"}, method="POST")
    with request.urlopen(req, timeout=35) as response:
        body = json.loads(response.read().decode())
    return _clean_shape(json.loads(body["choices"][0]["message"]["content"]))


def parse_resume(filename: str, settings: dict) -> tuple[dict, str]:
    text = extract_text(filename)
    local = local_parse(text)
    if not settings.get("apiKey"):
        return local, "本地提取"
    try:
        remote = llm_parse(text, settings)
        for group, values in remote.items():
            if group == "resumeSections":
                for section, entries in values.items():
                    if entries:
                        local[group][section] = entries
                continue
            for key, value in values.items():
                if value:
                    local[group][key] = value
        return local, "模型增强解析"
    except Exception as exc:
        return local, f"本地提取（模型不可用：{readable_api_error(exc)}）"
