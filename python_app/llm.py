"""Optional OpenAI-compatible field mapper; works without extra packages."""
from __future__ import annotations

import json
from urllib import request

from api_client import chat_completions_url, readable_api_error
from browser_fill import flatten, local_mapping


def _message_content(body: dict) -> str:
    """Read both string and multimodal-style Chat Completions content."""
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def _json_from_text(text: str) -> dict:
    """Parse strict JSON while tolerating a model's accidental markdown fence."""
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回有效的 JSON 资料候选项")
        value = json.loads(cleaned[start:end + 1])
    return value if isinstance(value, dict) else {"items": value if isinstance(value, list) else []}


def _model_profile_context(profile: dict) -> dict:
    """Keep assistant prompts small enough for slower compatible endpoints."""
    def short(value, limit=600):
        text = str(value or "").strip()
        return text[:limit] + ("…" if len(text) > limit else "")

    sections = {}
    for name, rows in (profile.get("resumeSections", {}) or {}).items():
        if not isinstance(rows, list):
            continue
        compact = []
        for row in rows[:12]:
            if isinstance(row, dict):
                compact.append({key: short(value) for key, value in row.items() if key != "updatedAt"})
        if compact:
            sections[name] = compact
    return {
        "basics": {key: short(value, 240) for key, value in (profile.get("basics", {}) or {}).items()},
        "application": {key: short(value, 240) for key, value in (profile.get("application", {}) or {}).items()},
        # Never send government ID numbers to a model endpoint. They remain
        # local and are only considered by the explicit sensitive-fill gate.
        "custom": {key: short(value, 240) for key, value in (profile.get("custom", {}) or {}).items() if key not in {"idNumber", "id_card", "identityNumber"}},
        "resumeSections": sections,
        "knowledgeBase": {
            "selfIntroduction": short((profile.get("knowledgeBase", {}) or {}).get("selfIntroduction", ""), 600),
            "careerPreferences": short((profile.get("knowledgeBase", {}) or {}).get("careerPreferences", ""), 600),
        },
    }


def _page_field_category(field: dict) -> dict:
    """Safe local fallback for classifying exported recruiting-page fields.

    This is deliberately conservative around names: a mentor, relative or
    referee must never silently become the candidate's own name.
    """
    label = str(field.get("label") or field.get("name") or "").strip()
    context = str(field.get("context") or field.get("section") or "").strip()
    text = f"{context} {label}".lower()
    value = str(field.get("value") or "").strip()
    filled = bool(value) and value.lower() not in {"请选择", "select", "null", "undefined"}
    category, target, path, section, reason = "公司专属字段", "extra", "", "", "无法可靠归入固定资料，保留原始上下文"
    special = [
        (("导师", "指导教师", "导师姓名"), "导师信息", "extra", ""),
        (("亲属", "家庭成员", "父亲", "母亲", "配偶", "紧急联系人", "关系"), "家庭/紧急联系人", "extra", ""),
        (("证明人", "推荐人", "联系人", "referee", "reference"), "证明人/推荐人", "extra", ""),
        (("项目", "课题", "科研"), "项目经历", "section", "projects"),
        (("实习", "工作单位", "工作经历", "任职"), "工作/实习经历", "section", "workExperience"),
        (("学校", "院校", "学历", "学位", "专业", "毕业", "入学"), "教育经历", "section", "educationEntries"),
        (("奖项", "荣誉", "获奖"), "奖项荣誉", "section", "honors"),
        (("论文", "期刊", "发表", "著作"), "论文/学术成果", "section", "publications"),
        (("证书", "资格", "证书"), "资格证书", "section", "certificates"),
        (("自我介绍", "个人评价", "自我评价", "个人陈述"), "自我介绍/个人评价", "common", "resumeSections.personalSummary"),
    ]
    for words, cat, kind, destination in special:
        if any(word.lower() in text for word in words):
            category, target = cat, kind
            if kind == "section": section = destination
            elif kind == "common": path = destination
            reason = "根据字段名称和页面上下文判断"
            break
    else:
        # The local mapper already knows basic fields, but only use it when
        # no role/context makes the field a different person's information.
        from browser_fill import infer_profile_key
        key = infer_profile_key(field)
        key_paths = {
            "name": "basics.name", "email": "basics.email", "phone": "basics.phone", "city": "basics.city", "address": "basics.address",
            "school": "application.school", "degree": "application.degree", "major": "application.major", "graduationYear": "application.graduationYear",
            "title": "application.title", "gender": "custom.gender", "birthday": "custom.birthday", "idNumber": "custom.idNumber", "ethnicity": "custom.ethnicity", "politicalStatus": "custom.politicalStatus",
            "expectedSalary": "custom.expectedSalary", "noticePeriod": "custom.noticePeriod", "personalSummary": "resumeSections.personalSummary",
        }
        if key in key_paths and not any(word in text for word in ("导师", "亲属", "家庭", "紧急", "证明", "推荐", "联系人")):
            path = key_paths[key]; target = "common"; category = "基础/求职资料"; reason = "本地字段规则明确匹配"
    if not filled:
        target = "requirement"
        reason = "页面出现该字段，但当前尚未填写；保存为待补资料需求，不写入空值"
    if not context:
        context = category
        if value and target == "section":
            context = f"{category} / {value[:80]}"
    return {"category": category, "target": target, "path": path, "section": section,
            "field": label, "context": context, "filled": filled, "confidence": 0.72,
            "reason": reason}


def classify_page_fields(profile: dict, fields: list[dict]) -> list[dict]:
    """Classify page fields before export, with LLM enrichment and local fallback.

    Results remain reviewable. Blank controls are recorded as company requirements
    rather than being mistaken for facts about the user.
    """
    prepared = []
    for index, raw in enumerate(fields or []):
        if not isinstance(raw, dict):
            continue
        field = dict(raw)
        local = _page_field_category(field)
        prepared.append({"index": index, "label": str(field.get("label") or field.get("name") or ""),
                         "value": str(field.get("value") or "").strip(), "options": field.get("options") or [],
                         **local})
    settings = profile.get("llm", {})
    if not prepared or not settings.get("apiKey") or not settings.get("endpoint"):
        return prepared
    compact_profile = _model_profile_context(profile)
    result = {item["index"]: item for item in prepared}
    for offset in range(0, len(prepared), 40):
        batch = prepared[offset:offset + 40]
        prompt = f"""你是招聘资料库整理助手。请根据网页字段的标签、页面上下文和当前值，判断每一项归属。
只返回 JSON：{{"items":[{{"index":0,"category":"基础资料|求职信息|教育经历|工作/实习经历|项目经历|奖项荣誉|论文/成果|导师信息|家庭/紧急联系人|证明人/推荐人|公司专属字段|自我介绍/个人评价","target":"common|section|extra|requirement","path":"可选：basics.name 等","section":"可选：educationEntries/workExperience/projects/honors/publications/certificates","context":"保留并补全上下文","confidence":0.0,"reason":"简短原因"}}]}}
规则：
1. value 非空才可 target=common/section/extra；空值必须 target=requirement，表示公司需要但候选人未填写。
2. 严格区分候选人姓名与导师、亲属、紧急联系人、证明人/推荐人的姓名；没有明确上下文时不要覆盖基础资料。
3. 同名字段保留上下文；不推测敏感信息；不返回密码、验证码、上传文件或提交按钮。
4. path 只允许 basics.*、application.*、custom.*、resumeSections.personalSummary；section 只允许 educationEntries、workExperience、projects、honors、publications、certificates、skills、languages、training、portfolios。
当前资料仅供判断：{json.dumps(compact_profile, ensure_ascii=False)}
网页字段：{json.dumps(batch, ensure_ascii=False)}"""
        payload = json.dumps({"model": settings.get("model") or "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 1500}, ensure_ascii=False).encode("utf-8")
        req = request.Request(chat_completions_url(settings["endpoint"]), data=payload,
                              headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['apiKey']}"}, method="POST")
        try:
            with request.urlopen(req, timeout=38) as response:
                raw = _json_from_text(_message_content(json.loads(response.read().decode("utf-8"))))
            rows = raw.get("items", raw.get("fields", []))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try: index = int(row.get("index"))
            except (TypeError, ValueError): continue
            original = result.get(index)
            if not original: continue
            target = str(row.get("target") or original["target"])
            filled = bool(original.get("filled"))
            if not filled: target = "requirement"
            if target not in {"common", "section", "extra", "requirement"}: target = original["target"]
            try: confidence = float(row.get("confidence", original["confidence"]))
            except (TypeError, ValueError): confidence = original["confidence"]
            original.update({"category": str(row.get("category") or original["category"]), "target": target,
                             "path": str(row.get("path") or original["path"]), "section": str(row.get("section") or original["section"]),
                             "context": str(row.get("context") or original["context"]), "confidence": confidence,
                             "reason": str(row.get("reason") or original["reason"]), "aiClassified": True})
    return [result[index] for index in sorted(result)]

def chat_extract_profile(profile: dict, conversation: str) -> list[dict]:
    """Turn pasted natural language into reviewable, context-aware profile candidates."""
    settings = profile.get("llm", {})
    if not settings.get("apiKey") or not settings.get("endpoint"):
        raise ValueError("请先在个人资料中配置模型接口地址、模型和 API Key。")
    safe_profile = _model_profile_context(profile)
    schema = {
        "items": [{
            "target": "common|section|extra",
            "path": "basics.name/application.school/resumeSections.personalSummary（target=common 时填写）",
            "section": "educationEntries/workExperience/projects/honors/publications/skills 等（target=section 时填写）",
            "field": "字段键，例如 advisor、school、description、name",
            "label": "中文字段名",
            "value": "字段值；target=section 时也可用 entry 对象",
            "entry": "可选：重复经历的完整对象",
            "context": "明确上下文，例如 教育经历 / 硕士 / 导师信息、家庭成员 / 父亲",
            "confidence": 0.0,
            "reason": "一句话说明如何判断上下文",
        }]
    }
    prompt = f"""你是简历资料库助手。根据用户粘贴的内容，提取可以加入个人资料库的事实。
只返回 JSON，不要 Markdown、解释或额外文字，格式必须是 {json.dumps(schema, ensure_ascii=False)}。
规则：
1. 只提取用户明确说出的内容，不猜测身份证号、政治面貌、民族等敏感信息。
2. target=common 只能使用已有路径 basics.*、application.* 或 resumeSections.personalSummary。
3. 多段本科/硕士教育、工作、项目、奖项、论文等用 target=section，并把每段放进 entry 对象；不得覆盖其它段。
4. 同名字段必须写清 context，严格区分导师、亲属、证明人、紧急联系人。
5. 无法判断归属时用 target=extra，并保留原始上下文；confidence 低于 0.65 的项目不要返回。
6. 不要因为当前资料已有值就省略用户新提供的内容；系统会在用户确认后决定是否更新。

当前资料（仅作去重和上下文参考）：{json.dumps(safe_profile, ensure_ascii=False)}
用户补充内容：{conversation.strip()}
"""
    payload = json.dumps({
        "model": settings.get("model") or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1200,
    }, ensure_ascii=False).encode("utf-8")
    req = request.Request(chat_completions_url(settings["endpoint"]), data=payload,
                          headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['apiKey']}"}, method="POST")
    try:
        with request.urlopen(req, timeout=75) as response:
            body = json.loads(response.read().decode("utf-8"))
        raw = _json_from_text(_message_content(body))
    except Exception as exc:
        raise RuntimeError(readable_api_error(exc)) from exc
    items = raw.get("items", raw.get("fields", []))
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("value", "") if not isinstance(item.get("entry"), dict) else item.get("entry")).strip():
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if confidence < 0.65:
            continue
        result.append({
            "target": str(item.get("target") or "extra"),
            "path": str(item.get("path") or ""),
            "section": str(item.get("section") or ""),
            "field": str(item.get("field") or ""),
            "label": str(item.get("label") or item.get("field") or "资料"),
            "value": item.get("value", "") if not isinstance(item.get("value"), (dict, list)) else json.dumps(item.get("value"), ensure_ascii=False),
            "entry": item.get("entry") if isinstance(item.get("entry"), dict) else None,
            "context": str(item.get("context") or ""),
            "confidence": confidence,
            "reason": str(item.get("reason") or ""),
        })
    return result


def polish_self_introduction(profile: dict, draft: str = "", style: str = "专业简洁") -> str:
    """Rewrite a self-introduction using only facts present in the profile."""
    settings = profile.get("llm", {})
    if not settings.get("apiKey") or not settings.get("endpoint"):
        raise ValueError("请先在模型设置中配置接口地址、模型和 API Key。")
    context = _model_profile_context(profile)
    prompt = f"""你是求职材料编辑。请基于候选人的资料，润色一段适合招聘官网和面试开场使用的自我介绍。
风格：{style}
现有草稿（可能为空）：{str(draft or '').strip()[:2500]}
候选人资料：{json.dumps(context, ensure_ascii=False)}
要求：
1. 只能使用资料中明确存在的事实，不得编造公司、项目成果、数字、证书或技能。
2. 优先整合教育经历、工作/实习、项目、技能和求职方向；没有的内容不要补写。
3. 输出一段自然的中文正文，约 150-300 字，不要标题、引号、Markdown 或解释。
4. 如果现有草稿为空，就根据资料重新起草；如果资料不足，简洁说明已知内容，不要猜测。
"""
    payload = json.dumps({
        "model": settings.get("model") or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.35,
        "max_tokens": 700,
    }, ensure_ascii=False).encode("utf-8")
    req = request.Request(chat_completions_url(settings["endpoint"]), data=payload,
                          headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['apiKey']}"}, method="POST")
    try:
        with request.urlopen(req, timeout=75) as response:
            text = _message_content(json.loads(response.read().decode("utf-8"))).strip()
    except Exception as exc:
        raise RuntimeError(readable_api_error(exc)) from exc
    if not text:
        raise ValueError("模型没有返回可用的自我介绍。")
    return text.strip().strip('"“”')


def chat_assistant_reply(profile: dict, message: str, mode: str = "自由对话") -> str:
    """Return a plain-language assistant reply for the dedicated chat page."""
    settings = profile.get("llm", {})
    if not settings.get("apiKey") or not settings.get("endpoint"):
        raise ValueError("请先在模型设置中配置接口地址、模型和 API Key。")
    context = _model_profile_context(profile)
    task = {
        "总结问题与准备建议": "结合资料库指出待补充信息、简历风险和下一步准备建议。不要编造事实。",
        "自由对话": "直接回答用户问题；涉及个人经历时只能使用资料库中明确存在的内容。",
    }.get(mode, "帮助用户完善求职资料，给出可执行的建议。")
    prompt = f"""你是“投了么”中的求职资料助手。
任务类型：{mode}
任务要求：{task}
资料库（仅作参考，身份证号等敏感信息已剔除）：{json.dumps(context, ensure_ascii=False)}
用户消息：{str(message or '').strip()[:5000]}
请用中文直接回答，结构清晰、不要输出 Markdown 代码块，不要声称已经替用户提交申请。"""
    payload = json.dumps({
        "model": settings.get("model") or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.25,
        "max_tokens": 900,
    }, ensure_ascii=False).encode("utf-8")
    req = request.Request(chat_completions_url(settings["endpoint"]), data=payload,
                          headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['apiKey']}"}, method="POST")
    try:
        with request.urlopen(req, timeout=75) as response:
            text = _message_content(json.loads(response.read().decode("utf-8"))).strip()
    except Exception as exc:
        raise RuntimeError(readable_api_error(exc)) from exc
    return text or "模型没有返回内容，请换一种问法再试。"


def map_fields(profile: dict, descriptors: list[dict]) -> list[dict]:
    """Map only fields that local matching could not answer, in small batches.

    Recruitment pages often contain hundreds of controls from collapsed sections.
    Sending all of them in one request causes compatible endpoints to time out.
    Local answers are applied immediately by the UI; this function only enriches
    the remaining fields and tolerates a failed batch.
    """
    settings = profile.get("llm", {})
    if not settings.get("apiKey") or not settings.get("endpoint"):
        return []
    local_indexes = {x.get("index") for x in local_mapping(descriptors, profile) if isinstance(x, dict)}
    pending = []
    for index, field in enumerate(descriptors):
        if index in local_indexes:
            continue
        item = dict(field)
        item["_resume_index"] = index
        pending.append(item)
    if not pending:
        return []

    data = _model_profile_context(profile)
    data["extraFields"] = [
        {k: item.get(k, "") for k in ("label", "context", "value")}
        for item in profile.get("extraFields", []) if isinstance(item, dict) and item.get("value")
    ][:60]
    result = []
    # Keep each prompt bounded. Four batches are enough for a normal visible
    # section; additional collapsed controls are left for manual navigation.
    for offset in range(0, min(len(pending), 180), 45):
        batch = pending[offset:offset + 45]
        prompt = f"""你是招聘官网简历填写助手。根据申请人资料，为网页字段给出应填写的具体答案。
只返回 JSON：{{"mappings":[{{"index":0,"value":"具体答案","confidence":0.95,"reason":"简短说明"}}]}}
规则：index 必须使用字段列表中的 index（该字段附带 _resume_index）；选择控件的 value 必须来自 options；日期使用 YYYY-MM-DD、YYYY-MM 或年份；没有可靠答案不要返回；不要填写密码、验证码、文件上传、提交按钮；导师、亲属、证明人必须结合上下文区分。
申请人资料：{json.dumps(data, ensure_ascii=False)}
待映射网页字段：{json.dumps(batch, ensure_ascii=False)}"""
        payload = json.dumps({
            "model": settings.get("model") or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 1200,
        }, ensure_ascii=False).encode("utf-8")
        req = request.Request(chat_completions_url(settings["endpoint"]), data=payload,
                              headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['apiKey']}"}, method="POST")
        try:
            with request.urlopen(req, timeout=28) as response:
                body = json.loads(response.read().decode("utf-8"))
            raw = _json_from_text(_message_content(body))
            rows = raw if isinstance(raw, list) else raw.get("mappings", raw.get("fields", raw.get("items", []))) if isinstance(raw, dict) else []
            if not isinstance(rows, list):
                continue
        except Exception:
            # A slow batch must not undo local filling or prevent later batches.
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                raw_index = int(row.get("index"))
            except (TypeError, ValueError):
                continue
            target = next((field for field in batch if int(field.get("_resume_index", -1)) == raw_index), None)
            if target is None:
                # Some models ignore the helper index and use the batch offset.
                target = batch[raw_index] if 0 <= raw_index < len(batch) else None
            if target is None:
                continue
            index = int(target.get("_resume_index", raw_index))
            value = row.get("value", row.get("answer", row.get("text", row.get("content", ""))))
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            value = str(value or "").strip()
            if not value:
                continue
            try:
                confidence = float(row.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            if confidence >= 0.45:
                result.append({"index": index, "value": value, "confidence": confidence, "reason": str(row.get("reason") or "")})
    return result


def test_connection(settings: dict) -> str:
    """Runs a minimal explicit test and returns a safe status message."""
    if not settings.get("apiKey") or not settings.get("endpoint"):
        raise ValueError("请先填写接口地址、模型和 API Key。")
    payload = json.dumps({"model": settings.get("model") or "gpt-4o-mini", "messages": [{"role": "user", "content": "Reply with exactly: OK"}], "temperature": 0, "max_tokens": 8}).encode()
    req = request.Request(chat_completions_url(settings["endpoint"]), data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['apiKey']}"}, method="POST")
    try:
        with request.urlopen(req, timeout=25) as response:
            body = json.loads(response.read().decode())
        text = body["choices"][0]["message"].get("content", "").strip()
        return f"连接成功：{body.get('model', settings.get('model'))}（返回：{text[:80] or '已响应'}）"
    except Exception as exc:
        raise RuntimeError(readable_api_error(exc)) from exc
