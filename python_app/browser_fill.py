"""Website form inspection and conservative, user-triggered filling."""
from __future__ import annotations

import json

ALIASES = {
    "name": ["full name", "姓名", "name"], "firstName": ["first name", "given name", "名"],
    "lastName": ["last name", "family name", "surname", "姓"], "email": ["email", "e-mail", "邮箱", "电子邮件"],
    "phone": ["phone", "mobile", "tel", "电话", "手机"], "city": ["city", "location", "城市", "所在地"],
    "address": ["address", "地址"], "linkedin": ["linkedin"], "website": ["website", "portfolio", "个人网站", "作品集"],
    "school": ["school", "university", "college", "学校", "院校"],
    "nationality": ["nationality", "国籍"], "height": ["height", "身高"], "weight": ["weight", "体重"],
    "health": ["health", "健康状况"], "maritalStatus": ["marital status", "婚姻状况"], "degree": ["degree", "学历", "学位"],
    "major": ["major", "专业"], "graduationYear": ["graduation", "毕业年份", "毕业年"],
    "company": ["company", "employer", "公司"], "title": ["job title", "position", "职位", "职务"],
    "yearsExperience": ["years of experience", "experience years", "工作年限"],
    "workAuthorization": ["work authorization", "work permit", "工作许可"], "sponsorship": ["sponsorship", "签证担保"],
    "gender": ["gender", "性别"], "birthday": ["birthday", "date of birth", "出生日期"], "idNumber": ["id number", "身份证号", "身份证号码", "证件号码"], "ethnicity": ["ethnicity", "民族"], "politicalStatus": ["political status", "政治面貌"],
    "personalSummary": ["self evaluation", "self-evaluation", "personal statement", "personal summary", "self introduction", "个人评价", "自我评价", "个人陈述", "个人优势", "自我介绍"],
    "expectedSalary": ["salary expectation", "expected salary", "期望薪资"], "noticePeriod": ["notice period", "到岗时间"],
}

FIELD_COLLECTOR = r"""
  const fields = [];
  const seen = new Set();
  const walk = root => {
    let nodes = [];
    try { nodes = Array.from(root.querySelectorAll('input, textarea, select, [contenteditable="true"], [role="combobox"], [role="radio"], [role="checkbox"], input[selecttype], select.selectpicker')); } catch (_) {}
    for (const element of nodes) {
      if (seen.has(element) || element.disabled || !element.getClientRects().length || element.type === 'hidden' || ['submit','button','reset','file','password'].includes(element.type)) continue;
      seen.add(element); fields.push(element);
    }
    let all = [];
    try { all = Array.from(root.querySelectorAll('*')); } catch (_) {}
    for (const element of all) {
      if (element.shadowRoot) walk(element.shadowRoot);
      if (element.tagName === 'IFRAME') { try { if (element.contentDocument) walk(element.contentDocument); } catch (_) {} }
    }
  };
  walk(document);
"""

# The old collector used the full text of a parent container as a last-resort
# label. On complex recruitment portals this mixes adjacent rows, producing
# pairs such as "2026-09-01 / 姓名". Only use explicit or local label elements.
LABEL_RESOLVER = r"""
  const label = e => {
    const doc=e.ownerDocument;
    const clean=value=>(value||'').replace(/\s+/g,' ').replace(/[：:]+$/,'').trim();
    let text=e.labels?.[0]?.innerText || e.closest?.('label')?.innerText || '';
    if (!text && e.id) { try { text=doc.querySelector('label[for="'+CSS.escape(e.id)+'"]')?.innerText || ''; } catch (_) {} }
    if (!text) text=e.getAttribute('aria-label') || e.getAttribute('data-label') || e.getAttribute('data-title') || e.getAttribute('title') || e.placeholder || '';
    for (let parent=e.parentElement, depth=0; !text && parent && depth++<4; parent=parent.parentElement) {
      const children=Array.from(parent.children || []);
      const named=children.filter(node => node !== e && (node.tagName === 'LABEL' || /(?:^|[-_ ])(?:label|title|caption|field-name)(?:[-_ ]|$)/i.test(String(node.className || ''))));
      for (const node of named) {
        if (node.querySelector?.('input,textarea,select,[contenteditable="true"],[role="combobox"]')) continue;
        const candidate=clean(node.innerText);
        if (candidate && candidate.length <= 80) { text=candidate; break; }
      }
      if (!text) {
        const previous=parent.previousElementSibling;
        if (previous && !previous.querySelector?.('input,textarea,select,[contenteditable="true"],[role="combobox"]')) {
          const candidate=clean(previous.innerText);
          if (candidate && candidate.length <= 60) text=candidate;
        }
      }
    }
    if (!text) text=e.name || '';
    return clean(text);
  };
"""

FRAME_BRIDGE_SCRIPT = r"""(() => {
  const collect = () => {
    const fields = [];
""" + LABEL_RESOLVER + r"""
    for (const e of Array.from(document.querySelectorAll('input,textarea,select,[contenteditable="true"],[role="combobox"],[role="radio"],[role="checkbox"],input[selecttype],select.selectpicker'))) {
      if (e.disabled || !e.getClientRects().length || e.type === 'hidden' || ['submit','button','reset','file','password'].includes(e.type)) continue;
      const base={label:label(e),name:e.name||'',id:e.id||'',type:e.type||e.tagName.toLowerCase()};
      if(e.type==='radio') fields.push({...base,value:e.checked?(e.labels?.[0]?.innerText||e.value||'已选择').trim():'',selected:e.checked});
      else if(e.type==='checkbox') fields.push({...base,value:e.checked?'true':'false',selected:e.checked});
      else if(e.tagName==='SELECT') fields.push({...base,value:e.selectedOptions?.[0]?.text?.trim()||''});
      else if(e.getAttribute('contenteditable')==='true') fields.push({...base,value:e.innerText||''});
      else if(e.getAttribute('role')==='combobox') fields.push({...base,value:e.getAttribute('aria-valuetext')||e.querySelector('input')?.value||e.innerText||''});
      else if(['radio','checkbox'].includes(e.getAttribute('role'))) fields.push({...base,value:e.getAttribute('aria-checked')==='true'?(e.getAttribute('aria-label')||e.innerText||'已选择'):'',selected:e.getAttribute('aria-checked')==='true'});
      else fields.push({...base,value:e.value||''});
    }
    return fields.filter(f=>f.label||f.name);
  };
  const collectPreview = () => {
    const fields=[], known=new Set(), clean=value=>(value||'').replace(/\s+/g,' ').replace(/[：:]+$/,'').trim();
    const push=(label,value,context='')=>{label=clean(label);value=clean(value);context=clean(context);const key=(context+'|'+label+'|'+value).toLowerCase();if(label&&value&&label.length<=80&&value.length<=500&&!known.has(key)){known.add(key);fields.push({label,context,name:'',id:'',type:'preview',value});}};
    let section='', entry='';
    for(const row of Array.from(document.querySelectorAll('tr'))){const cells=Array.from(row.querySelectorAll('th,td')).map(cell=>clean(cell.innerText)).filter(Boolean);if(cells.length===1&&cells[0].length<=100){section=cells[0];entry='';continue;}for(let index=0;index+1<cells.length;index+=2){if(/\u9879\u76ee\u540d\u79f0|\u8bfe\u9898|\u5b66\u6821|\u516c\u53f8|\u5355\u4f4d|\u5173\u7cfb/.test(cells[index]))entry=cells[index+1].slice(0,100);push(cells[index],cells[index+1],[section,entry].filter(Boolean).join(' / '));}}
    if(!fields.length){const lines=(document.body?.innerText||'').split(/\n+/).map(clean).filter(Boolean);for(let index=0;index+1<lines.length;index++)if(lines[index].length<=60&&lines[index+1].length<=500)push(lines[index],lines[index+1]);}
    return fields;
  };
  if (window === window.top) {
    window.__resumeSubmitterFrameResults = [];
    window.addEventListener('message', event => { if (event.data?.type === 'RS_FIELDS_RESULT' || event.data?.type === 'RS_PREVIEW_RESULT') window.__resumeSubmitterFrameResults.push(event.data); });
  }
  window.addEventListener('message', event => {
    if (event.data?.type === 'RS_SCAN_FIELDS') { const frameId=Math.random().toString(36).slice(2); window.top.postMessage({type:'RS_FIELDS_RESULT', frameId, fields: collect()}, '*'); }
    if (event.data?.type === 'RS_SCAN_PREVIEW') window.top.postMessage({type:'RS_PREVIEW_RESULT', frameId: Math.random().toString(36).slice(2), fields: collectPreview()}, '*');
  });
})();"""

FRAME_SCAN_SCRIPT = r"""(() => {
  const root = window.top || window;
  if (!Array.isArray(root.__resumeSubmitterFrameResults)) root.__resumeSubmitterFrameResults = [];
  const message = {type: 'RS_SCAN_FIELDS'};
  window.postMessage(message, '*');
  const send = frame => { try { frame.postMessage(message, '*'); } catch (_) {} };
  for (const frame of Array.from(window.frames || [])) send(frame);
  return true;
})()"""

FRAME_RESULTS_SCRIPT = r"""(() => {
  const root = window.top || window;
  const rows = Array.isArray(root.__resumeSubmitterFrameResults) ? root.__resumeSubmitterFrameResults : [];
  const fields = [];
  rows.forEach(result => (result.fields || []).forEach((field, frameIndex) => fields.push({...field, _frameId: result.frameId || '', _frameIndex: frameIndex})));
  return JSON.stringify({title: document.title, fields});
})()"""

INSPECT_SCRIPT = r"""(() => {
  """ + FIELD_COLLECTOR + r"""
  """ + LABEL_RESOLVER + r"""
  return fields.map((e,index) => ({index, tag:e.tagName, type:e.type, name:e.name||'', id:e.id||'', placeholder:e.placeholder||'', label:label(e), options:e.tagName==='SELECT'?Array.from(e.options).map(o=>o.text.trim()).slice(0,20):[]}));
})()"""

SNAPSHOT_SCRIPT = r"""(() => {
  """ + FIELD_COLLECTOR + r"""
  """ + LABEL_RESOLVER + r"""
  return JSON.stringify({title:document.title, fields:fields.map(e => { const base={label:label(e),name:e.name||'',id:e.id||'',type:e.type||e.tagName.toLowerCase(),readOnly:!!e.readOnly,className:String(e.className||'').slice(0,160),selectType:e.getAttribute('selecttype')||''}; if(e.type==='radio') return {...base,value:e.checked?(e.labels?.[0]?.innerText||e.value||'已选中').trim():'',selected:e.checked,options:[e.labels?.[0]?.innerText||e.value||'']}; if(e.type==='checkbox') return {...base,value:e.checked?'true':'false',selected:e.checked,options:['是','否']}; if(e.tagName==='SELECT') return {...base,value:e.selectedOptions?.[0]?.text?.trim()||'',selectedValue:e.value||'',options:Array.from(e.options).map(o=>({text:(o.textContent||'').trim(),value:o.value})).slice(0,40)}; if(e.getAttribute('contenteditable')==='true') return {...base,value:e.innerText||''}; if(e.getAttribute('role')==='combobox') return {...base,value:e.getAttribute('aria-valuetext')||e.querySelector('input')?.value||e.innerText||'',options:Array.from(e.querySelectorAll('[role=option],option')).map(o=>(o.innerText||o.textContent||'').trim()).filter(Boolean).slice(0,40)}; if(['radio','checkbox'].includes(e.getAttribute('role'))) return {...base,value:e.getAttribute('aria-checked')==='true'?(e.getAttribute('aria-label')||e.innerText||'已选中'):'',selected:e.getAttribute('aria-checked')==='true'}; return {...base,value:e.value||''}; }).filter(f=>f.label||f.name)});
})()"""

PREVIEW_DATA_SCRIPT = r"""(() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').replace(/[：:]+$/, '').trim();
  const fields = []; const known = new Set();
  const push = (label, value, context='') => { label=clean(label); value=clean(value); context=clean(context); const key=(context+'|'+label+'|'+value).toLowerCase(); if(label && value && label.length<=80 && value.length<=500 && !known.has(key)) { known.add(key); fields.push({label,context,name:'',id:'',type:'preview',value}); } };
  const visit = doc => {
    let section='', entry='';
    for (const row of Array.from(doc.querySelectorAll('tr'))) {
      const cells=Array.from(row.querySelectorAll('th, td')).map(cell=>clean(cell.innerText)).filter(Boolean);
      if(cells.length===1 && cells[0].length<=100) { section=cells[0]; entry=''; continue; }
      for(let index=0; index+1<cells.length; index+=2) { if(/\u9879\u76ee\u540d\u79f0|\u8bfe\u9898|\u5b66\u6821|\u516c\u53f8|\u5355\u4f4d|\u5173\u7cfb/.test(cells[index])) entry=cells[index+1].slice(0,100); push(cells[index], cells[index+1], [section,entry].filter(Boolean).join(' / ')); }
    }
    for (const list of Array.from(doc.querySelectorAll('dl'))) {
      const labels=Array.from(list.querySelectorAll('dt')), values=Array.from(list.querySelectorAll('dd'));
      labels.forEach((label,index)=>push(label.innerText, values[index]?.innerText));
    }
    if(!fields.length) { const lines=(doc.body?.innerText||'').split(/\n+/).map(clean).filter(Boolean);
      for(let index=0; index+1<lines.length; index++) if(lines[index].length<=60 && lines[index+1].length<=500) push(lines[index], lines[index+1]); }
    for (const frame of Array.from(doc.querySelectorAll('iframe, frame'))) { try { if(frame.contentDocument) visit(frame.contentDocument); } catch (_) {} }
  };
  visit(document);
  return JSON.stringify({title:document.title, fields});
})()"""


def flatten(profile: dict) -> dict:
    basics, application, custom = profile.get("basics", {}), profile.get("application", {}), profile.get("custom", {})
    name = basics.get("name", "")
    parts = name.split()
    summaries = profile.get("resumeSections", {}).get("personalSummary", [])
    summary = next((str(item.get("description", "")).strip() for item in summaries if isinstance(item, dict) and item.get("description")), "")
    return {**basics, **application, **custom, "personalSummary": summary, "firstName": parts[0] if parts else "", "lastName": " ".join(parts[1:])}


def normalized(text: str) -> str:
    return "".join(str(text or "").lower().split())


def alias_candidates(field: dict, data: dict | None = None) -> list[tuple[int, str]]:
    text = " ".join(str(field.get(key, "")) for key in ("label", "name", "placeholder")).lower()
    matches = []
    for key, aliases in ALIASES.items():
        if data is not None and not data.get(key):
            continue
        # “email address” is an email field, not a postal-address field.
        if key == "address" and ("email" in text or "e-mail" in text):
            continue
        matches.extend((len(alias), key) for alias in aliases if alias.lower() in text)
    return matches


def infer_profile_key(field: dict) -> str | None:
    """Returns the common profile key represented by a web field, if known."""
    candidates = alias_candidates(field)
    return max(candidates)[1] if candidates else None


def field_identity(field: dict) -> str:
    """A stable local identity for a company-specific answer."""
    # A label alone is unsafe: "姓名" can belong to the candidate, a mentor,
    # an emergency contact, or a family member.  Keep its semantic location
    # in the identity so answers from different resume sections never merge.
    context = field.get("context") or field.get("section") or field.get("group") or ""
    return normalized(f"{context}::{field.get('name') or field.get('label') or field.get('id')}")


def _clean_profile_value(key: str, value: object) -> str:
    text = str(value or "").strip()
    if key == "name":
        text = __import__("re").sub(r"^\s*姓名\s*[:：]?\s*", "", text)
    if key == "graduationYear":
        m = __import__("re").search(r"(20\d{2})\s*[年./-]\s*(\d{1,2})?", text)
        if m and m.group(2):
            text = f"{m.group(1)}-{int(m.group(2)):02d}"
    return text


def _option_value(field: dict, value: str) -> str:
    """Return the site's option text for a local answer, or empty if unsafe."""
    options = field.get("options") or []
    if not options:
        return value
    texts = []
    for option in options:
        text = option.get("text", "") if isinstance(option, dict) else option
        text = str(text or "").strip()
        if text and not __import__("re").search(r"请选择|请选择|选择$", text):
            texts.append(text)
    wanted = value.strip().lower()
    exact = next((text for text in texts if text.lower() == wanted), None)
    if exact:
        return exact
    contains = next((text for text in texts if wanted in text.lower() or text.lower() in wanted), None)
    return contains or ""


def local_mapping(descriptors: list[dict], profile: dict) -> list[dict]:
    data, result = flatten(profile), []
    for index, field in enumerate(descriptors):
        text = " ".join(str(field.get(key, "")) for key in ("label", "name", "placeholder")).lower()
        # “院校地点/所在地” is not the candidate's school name.
        if "school" in text or "学校" in text or "院校" in text:
            if any(token in text for token in ("地点", "所在地", "地址")):
                field = {**field, "label": ""}
        candidates = alias_candidates(field, data)
        if candidates:
            _, key = max(candidates)
            if key == "idNumber" and not profile.get("privacy", {}).get("allowSensitiveAutofill", False):
                continue
            value = _clean_profile_value(key, data.get(key, ""))
            if value:
                option = _option_value(field, value)
                if field.get("options") and not option:
                    continue
                result.append({"index": index, "value": option or value, "confidence": 0.80})
                continue
        identity = field_identity(field)
        extra = next((item for item in profile.get("extraFields", []) if item.get("identity") == identity and item.get("value")), None)
        if extra:
            value = _option_value(field, str(extra["value"]))
            if value:
                result.append({"index": index, "value": value, "confidence": 0.95})
    return result


def fill_script(mappings: list[dict], only_empty: bool = False) -> str:
    values = json.dumps(mappings, ensure_ascii=False)
    empty_flag = "true" if only_empty else "false"
    return f"""((values, onlyEmpty) => {{
      const fields=[]; const seen=new Set();
      const walk=root=>{{let nodes=[];try{{nodes=Array.from(root.querySelectorAll('input,textarea,select,[contenteditable=\\"true\\"],[role=\\"combobox\\"],[role=\\"radio\\"],[role=\\"checkbox\\"],input[selecttype],select.selectpicker'));}}catch(_){{}}
        for(const e of nodes){{if(seen.has(e)||e.disabled||e.type==='hidden'||['submit','button','reset','file','password'].includes(e.type))continue;seen.add(e);fields.push(e);}}
        let all=[];try{{all=Array.from(root.querySelectorAll('*'));}}catch(_){{}}
        for(const e of all){{if(e.shadowRoot)walk(e.shadowRoot);if(e.tagName==='IFRAME'){{try{{if(e.contentDocument)walk(e.contentDocument);}}catch(_){{}}}}}}
      }}; walk(document); let filled=0,skipped=0,errors=[];
      const isEmpty = el => {{
        if(!el) return true;
        if(el.type==='checkbox'||el.type==='radio'||['checkbox','radio'].includes(el.getAttribute('role'))) return !(el.checked || el.getAttribute('aria-checked')==='true');
        if(el.tagName==='SELECT') {{ const o=el.selectedOptions?.[0]; return !o || !String(o.value||o.textContent||'').trim() || /请选择|选择|select/i.test(String(o.textContent||'')); }}
        if(el.getAttribute('contenteditable')==='true') return !String(el.innerText||'').trim();
        if(el.getAttribute('role')==='combobox') return !String(el.getAttribute('aria-valuetext')||el.querySelector('input')?.value||el.innerText||'').trim();
        return !String(el.value||'').trim();
      }};
      const textOf = el => String(el?.innerText||el?.textContent||'').replace(/\\s+/g,' ').trim().toLowerCase();
      const chooseVisible = wanted => Array.from(document.querySelectorAll('[role=option],[role=treeitem],.el-select-dropdown__item,.ant-select-item-option,.tree-select li,.ztree li,li')).find(x=>{{const t=textOf(x);return t && (t===wanted || t.includes(wanted) || wanted.includes(t));}});
      for(const item of values) {{
        const el=fields[item.index]; if(!el){{skipped++;continue;}}
        const isTree=el.matches?.('input[selecttype=tree]') || /tree|cascader|cascade/.test(String(el.className||'')+' '+String(el.getAttribute('data-component')||''));
        // Some recruitment portals mark date/text inputs readonly because a picker\n        // controls them. Programmatic value setting is still valid; tree widgets\n        // remain protected by the dedicated branch below.
        if(onlyEmpty && !isEmpty(el)){{skipped++;continue;}}
        const wanted=String(item.value||'').trim().toLowerCase(); if(!wanted){{skipped++;continue;}}
        try {{
          if(isTree) {{
            const trigger=(el.nextElementSibling?.classList?.contains('tree-select')?el.nextElementSibling:el);
            trigger.click();
            const pick=()=>{{ const option=chooseVisible(wanted); if(option){{option.click();return true;}} return false; }};
            if(pick()) filled++; else {{ setTimeout(()=>pick(),450); filled++; }}
            continue;
          }}
          if(el.getAttribute('role')==='combobox') {{
            const input=el.matches('input')?el:el.querySelector('input'); el.click();
            const option=chooseVisible(wanted);
            if(option) {{ option.click(); filled++; continue; }}
            if(input) {{ const proto=Object.getPrototypeOf(input), descriptor=Object.getOwnPropertyDescriptor(proto,'value'); if(descriptor?.set) descriptor.set.call(input,item.value); else input.value=item.value; input.dispatchEvent(new Event('input',{{bubbles:true}})); input.dispatchEvent(new Event('change',{{bubbles:true}})); filled++; }} else skipped++;
            continue;
          }}
          if(el.getAttribute('role')==='radio'||el.getAttribute('role')==='checkbox') {{ const caption=textOf(el.getAttribute('aria-label')||el); if(caption===wanted||caption.includes(wanted)||wanted.includes(caption)){{el.click();filled++;}}else skipped++; continue; }}
          if(el.tagName==='SELECT') {{ const opt=Array.from(el.options).find(o=>{{const t=String(o.text||'').trim().toLowerCase();return t===wanted||t.includes(wanted)||wanted.includes(t);}}); if(!opt){{skipped++;continue;}} el.value=opt.value; el.dispatchEvent(new Event('input',{{bubbles:true}})); el.dispatchEvent(new Event('change',{{bubbles:true}})); el.dispatchEvent(new Event('blur',{{bubbles:true}})); filled++; continue; }}
          if(el.type==='radio'||el.type==='checkbox') {{ const caption=textOf(el.labels?.[0]||el.value); if(caption.includes(wanted)||wanted.includes(caption)){{el.click();filled++;}}else skipped++; continue; }}
          if(el.getAttribute('contenteditable')==='true') el.innerText=item.value;
          else {{ const proto=Object.getPrototypeOf(el), descriptor=Object.getOwnPropertyDescriptor(proto,'value'); if(descriptor?.set) descriptor.set.call(el,item.value); else el.value=item.value; }}
          el.dispatchEvent(new Event('input',{{bubbles:true}})); el.dispatchEvent(new Event('change',{{bubbles:true}})); el.dispatchEvent(new Event('blur',{{bubbles:true}})); filled++;
        }} catch(error) {{ skipped++; errors.push(String(error?.message||error)); }}
      }}
      return JSON.stringify({{filled,skipped,total:fields.length,onlyEmpty,errors}});
    }})({values}, {empty_flag})"""


