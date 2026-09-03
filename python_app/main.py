from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QDate, QThreadPool, Qt, QTimer, QUrl, QRunnable, QObject, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QProgressBar, QScrollArea, QStackedWidget, QTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

from browser_fill import FRAME_BRIDGE_SCRIPT, FRAME_RESULTS_SCRIPT, FRAME_SCAN_SCRIPT, PREVIEW_DATA_SCRIPT, SNAPSHOT_SCRIPT, field_identity, fill_script, infer_profile_key, local_mapping
from llm import chat_assistant_reply, chat_extract_profile, classify_page_fields, map_fields, polish_self_introduction, test_connection
from profile_schema import APPLICATION_STATUSES, COMMON_PATHS, FAILURE_REASONS, PAGE_FIELD_CATEGORIES, PROFILE_FIELDS, SECTION_FORMS
from resume_parser import parse_resume
from storage import LocalLibrary
from ui_theme import APP_STYLESHEET

APP_NAME = "投了么"

def asset_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name

def get_value(data, dotted):
    for part in dotted.split("."): data = data.get(part, {}) if isinstance(data, dict) else {}
    return str(data or "")

def set_value(data, dotted, value):
    parts = dotted.split(".")
    for part in parts[:-1]: data = data.setdefault(part, {})
    data[parts[-1]] = value

def chinese_button_box(box):
    """Qt's stock captions may be English on machines without a Chinese Qt locale."""
    for standard,text in ((QDialogButtonBox.StandardButton.Save,"保存"),(QDialogButtonBox.StandardButton.Cancel,"取消"),(QDialogButtonBox.StandardButton.Ok,"确定")):
        button=box.button(standard)
        if button: button.setText(text)
    return box

def context_label(field):
    label = str(field.get("label") or field.get("name") or "").strip(); context = str(field.get("context") or field.get("section") or "").strip()
    return f"{context} / {label}" if context else label

def contextualize(fields):
    out=[]; active=""
    for f in fields or []:
        f=dict(f); label=str(f.get("label") or f.get("name") or ""); value=str(f.get("value") or ""); ctx=str(f.get("context") or "")
        if not ctx:
            if any(x in label for x in ("项目名称","课题名称")): ctx=f"项目经历 / {value[:80]}"
            elif any(x in label for x in ("学校","院校")): ctx=f"教育经历 / {value[:80]}"
            elif any(x in label for x in ("公司名称","工作单位","实习单位")): ctx=f"工作/实习经历 / {value[:80]}"
            elif any(x in label for x in ("亲属关系","与本人关系","家庭关系")): ctx=f"家庭成员 / {value[:40]}"
            elif any(x in label for x in ("导师","指导教师")): ctx="教育经历 / 导师信息"
            elif "证明人" in label: ctx=active if any(x in active for x in ("项目","工作","实习")) else "经历补充 / 证明人"
            elif any(x in label for x in ("姓名","性别","出生日期","身份证","民族","手机","邮箱")) and not active: ctx="基本信息"
            else: ctx=""
        # A context may carry across consecutive fields in the same resume
        # block, but never across unrelated controls on a large portal page.
        if ctx:
            f["context"]=ctx
            if any(word in ctx for word in ("教育", "项目", "工作", "实习", "家庭", "导师")): active=ctx
        out.append(f)
    return out

def clean_page_fields(fields):
    """Keep only stable, human-readable controls before model classification."""
    out=[]; seen=set()
    placeholders={"", "请选择", "请选择...", "select", "--请选择--", "null", "undefined"}
    caption_values={"姓名", "电子邮箱", "申请职位", "最后修改时间", "性别", "出生日期", "手机号码"}
    for raw in contextualize(fields):
        field=dict(raw); label=" ".join(str(field.get("label") or field.get("name") or "").split()).strip("：:")
        value=" ".join(str(field.get("value") or "").split()).strip()
        name=str(field.get("name") or "").strip(); field_type=str(field.get("type") or "").lower()
        # A technical DOM id/name with no visible label cannot be reviewed or
        # safely written to the candidate profile.
        if not label or (not name and len(label) > 80) or label.lower().startswith(("ng-", "input_", "form_")):
            continue
        # Portal templates can expose decorative controls carrying the next
        # field caption as a value. Do not present those as candidate facts.
        if value in caption_values or field_type in {"button", "submit", "reset", "file", "password", "hidden"}:
            continue
        field["label"]=label; field["value"]="" if value.lower() in placeholders else value
        identity="|".join((str(field.get("context") or ""), label, name, field_type, field["value"])).lower()
        if identity in seen: continue
        seen.add(identity); out.append(field)
    return out

def parse_js_json(value, fallback=None):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else (fallback if fallback is not None else {})
        except (TypeError, ValueError):
            return fallback if fallback is not None else {}
    return fallback if fallback is not None else {}

class HtmlTables(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.rows=[]; self.row=None; self.cell=None; self.skip=0
    def handle_starttag(self, tag, attrs):
        tag=tag.lower()
        if tag in {"script","style","noscript"}: self.skip+=1
        elif not self.skip and tag=="tr": self.row=[]
        elif not self.skip and tag in {"td","th"} and self.row is not None: self.cell=[]
    def handle_endtag(self, tag):
        tag=tag.lower()
        if tag in {"script","style","noscript"} and self.skip: self.skip-=1
        elif not self.skip and tag in {"td","th"} and self.row is not None and self.cell is not None: self.row.append(" ".join(self.cell).strip()); self.cell=None
        elif not self.skip and tag=="tr" and self.row is not None: self.rows.append(self.row); self.row=None
    def handle_data(self, data):
        if not self.skip and self.cell is not None: self.cell.append(data)

def html_preview(markup):
    p=HtmlTables(); p.feed(markup or ""); fields=[]
    for row in p.rows:
        cells=[" ".join(str(x).split()).rstrip(":：") for x in row if str(x).strip()]
        for i in range(0,len(cells)-1,2): fields.append({"label":cells[i],"value":cells[i+1],"type":"preview-html","context":""})
    return {"title":"","fields":contextualize(fields),"sourceLength":len(markup or "")}

class ParseWorker(QRunnable):
    def __init__(self, filename, settings): super().__init__(); self.filename=filename; self.settings=settings; self.done=Signal(str, object, str)
    def run(self): pass


class ChatSignals(QObject):
    finished = Signal(object)
    error = Signal(str)


class ChatWorker(QRunnable):
    """Keep model calls off the GUI thread so the profile page stays responsive."""
    def __init__(self, profile, text):
        super().__init__(); self.profile = profile; self.text = text; self.signals = ChatSignals()

    def run(self):
        try:
            self.signals.finished.emit(chat_extract_profile(self.profile, self.text))
        except Exception as exc:
            self.signals.error.emit(str(exc))


class PolishWorker(QRunnable):
    """Run self-introduction polishing away from the Qt GUI thread."""
    def __init__(self, profile, draft, style):
        super().__init__(); self.profile=profile; self.draft=draft; self.style=style; self.signals=ChatSignals()

    def run(self):
        try: self.signals.finished.emit(polish_self_introduction(self.profile, self.draft, self.style))
        except Exception as exc: self.signals.error.emit(str(exc))


class AssistantWorker(QRunnable):
    def __init__(self, profile, message, mode):
        super().__init__(); self.profile=profile; self.message=message; self.mode=mode; self.signals=ChatSignals()
    def run(self):
        try: self.signals.finished.emit(chat_assistant_reply(self.profile, self.message, self.mode))
        except Exception as exc: self.signals.error.emit(str(exc))


class MappingWorker(QRunnable):
    """Optional model mapping for the current application form."""
    def __init__(self, profile, descriptors):
        super().__init__(); self.profile=profile; self.descriptors=descriptors; self.signals=ChatSignals()
    def run(self):
        try: self.signals.finished.emit(map_fields(self.profile,self.descriptors))
        except Exception as exc: self.signals.error.emit(str(exc))


class PageClassificationWorker(QRunnable):
    """Classify captured page fields without freezing the embedded browser."""
    def __init__(self, profile, fields):
        super().__init__(); self.profile=profile; self.fields=fields; self.signals=ChatSignals()
    def run(self):
        try: self.signals.finished.emit(classify_page_fields(self.profile,self.fields))
        except Exception as exc: self.signals.error.emit(str(exc))

class ChooseDialog(QDialog):
    def __init__(self, items, parent):
        super().__init__(parent); self.items=items; self.boxes=[]; self.setWindowTitle("保存资料（含上下文）"); self.resize(760,520); layout=QVBoxLayout(self); layout.addWidget(QLabel("请确认保存项。字段前的模块/条目用于区分导师姓名、亲属姓名、证明人等同名字段。")); body=QWidget(); box=QVBoxLayout(body)
        for item in items:
            c=QCheckBox(f"{item.get('context','')} / {item.get('label','')}：{item.get('value','')}"); c.setChecked(item.get("selected",True)); box.addWidget(c); self.boxes.append(c)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(body); layout.addWidget(scroll); buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def chosen(self): return [x for x,b in zip(self.items,self.boxes) if b.isChecked()]


class PageFieldReviewDialog(QDialog):
    """Review page fields, export selection, profile consent and destination."""
    CATEGORY_OPTIONS=PAGE_FIELD_CATEGORIES
    def __init__(self, items, parent, export_only=False):
        super().__init__(parent); self.items=[dict(item) for item in items]; self.export_only=export_only
        self.setWindowTitle("审核网页字段"); self.resize(1440, 760); self.setMinimumSize(1080, 620)
        layout=QVBoxLayout(self); layout.setContentsMargins(24,22,24,22); layout.setSpacing(12)
        filled=sum(1 for item in self.items if item.get("filled")); blank=len(self.items)-filled
        hint=QLabel(f"已识别 {len(self.items)} 个字段：网页已填写 {filled} 个，未填写 {blank} 个。左侧决定是否导出，第二列单独确认是否加入个人资料库，‘合并到’用于指定写入位置。")
        hint.setObjectName("muted"); hint.setWordWrap(True); layout.addWidget(hint)
        if export_only:
            export_bar=QHBoxLayout(); export_bar.addWidget(QLabel("导出范围")); self.export_scope=QComboBox(); self.export_scope.addItem("全部审核字段", "all"); self.export_scope.addItem("仅已填写字段", "filled"); self.export_scope.addItem("仅未填写要求", "blank"); export_bar.addWidget(self.export_scope)
            export_bar.addSpacing(18); export_bar.addWidget(QLabel("文件格式")); self.export_format=QComboBox(); self.export_format.addItem("JSON（含 AI 判断）", "json"); self.export_format.addItem("CSV（便于表格查看）", "csv"); export_bar.addWidget(self.export_format); export_bar.addStretch(); layout.addLayout(export_bar)
        quick=QHBoxLayout(); quick.addWidget(QLabel("快速选择导出"))
        for text, predicate in (("全选", lambda item: True), ("清空", lambda item: False), ("资料库已有", lambda item: item.get("profileHasValue", False)), ("资料库缺少", lambda item: not item.get("profileHasValue", False)), ("网页已填写", lambda item: bool(item.get("filled"))), ("网页未填写", lambda item: not item.get("filled"))):
            button=QPushButton(text); button.setProperty("secondary", True); button.clicked.connect(lambda _checked=False, p=predicate: self.set_checked(p)); quick.addWidget(button)
        if export_only:
            quick.addSpacing(12); quick.addWidget(QLabel("快速加入资料库"))
            for text, predicate in (("选中资料库缺少", lambda item: not item.get("profileHasValue", False)), ("清空加入项", lambda item: False)):
                button=QPushButton(text); button.setProperty("secondary", True); button.clicked.connect(lambda _checked=False, p=predicate: self.set_profile_checked(p)); quick.addWidget(button)
        quick.addStretch(); layout.addLayout(quick)
        headers=["导出","加入资料库","网页状态","资料库状态","归类","上下文","字段","当前值","合并到","处理方式","判断原因"]
        self.table=QTableWidget(len(self.items), len(headers)); self.table.setHorizontalHeaderLabels(headers); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.table.setTextElideMode(Qt.TextElideMode.ElideRight); self.table.setAlternatingRowColors(True); self.table.verticalHeader().setVisible(False); self.table.verticalHeader().setDefaultSectionSize(48)
        header=self.table.horizontalHeader()
        for column in (0,1,2,3,4,8,9): header.setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        for column in (5,6,7,10): header.setSectionResizeMode(column,QHeaderView.ResizeMode.Stretch)
        for row,item in enumerate(self.items):
            checked=bool(item.get("filled")) and float(item.get("confidence",0) or 0)>=0.55
            if item.get("target")=="requirement": checked=True
            select=QTableWidgetItem(); select.setFlags(Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsEnabled); select.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked); self.table.setItem(row,0,select)
            profile_select=QTableWidgetItem(); profile_select.setFlags(Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsEnabled); profile_default=(not export_only and item.get("target")!="requirement"); profile_select.setCheckState(Qt.CheckState.Checked if profile_default else Qt.CheckState.Unchecked); self.table.setItem(row,1,profile_select)
            self.table.setItem(row,2,QTableWidgetItem("已填写" if item.get("filled") else "未填写")); self.table.setItem(row,3,QTableWidgetItem("资料库已有" if item.get("profileHasValue") else "资料库缺少")); self.table.setItem(row,4,QTableWidgetItem(str(item.get("category") or "公司专属字段"))); self.table.setItem(row,5,QTableWidgetItem(str(item.get("context") or ""))); self.table.setItem(row,6,QTableWidgetItem(str(item.get("label") or ""))); self.table.setItem(row,7,QTableWidgetItem(str(item.get("value") or ""))); self.table.setCellWidget(row,8,self.destination_box(item)); self.table.setItem(row,9,QTableWidgetItem("记录为公司要求" if item.get("target")=="requirement" else "写入个人资料库")); self.table.setItem(row,10,QTableWidgetItem(str(item.get("reason") or "")))
        self.table.cellDoubleClicked.connect(self.edit_row); layout.addWidget(self.table,1)
        buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel));
        if export_only: buttons.button(QDialogButtonBox.StandardButton.Save).setText("合并导出并确认")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def destination_options(self, item):
        options=[("不加入资料库", "none|")]
        options += [(f"基础资料 / {label}", f"common|{path}") for group, fields in PROFILE_FIELDS for label, path in fields if path != "resumeSections.personalSummary"]
        options.append(("基础资料 / 个人评价", "common|resumeSections.personalSummary"))
        options += [(f"经历 / {title}", f"section|{section}") for section,(title,_fields) in SECTION_FORMS.items()]
        for section,(title,_fields) in SECTION_FORMS.items():
            rows=(self.parent().profile.get("resumeSections",{}) or {}).get(section,[]) if self.parent() is not None else []
            for index,row in enumerate(rows):
                if not isinstance(row,dict): continue
                name=str(row.get("school") or row.get("company") or row.get("name") or row.get("description") or f"条目 {index+1}").strip()
                options.append((f"经历 / {title} / {name[:36]}", f"section_existing|{section}|{index}"))
        options += [("自定义字段", "extra|"), ("公司要求 / 待补资料", "requirement|")]
        target=str(item.get("target") or "")
        if target=="common" and item.get("path"): preferred=f"common|{item.get('path')}"
        elif target=="section" and item.get("section"): preferred=f"section|{item.get('section')}"
        elif target=="requirement": preferred="requirement|"
        else: preferred="extra|"
        return options, preferred
    def destination_box(self, item):
        box=QComboBox(); options,preferred=self.destination_options(item)
        for text,data in options: box.addItem(text,data)
        index=box.findData(preferred); box.setCurrentIndex(index if index>=0 else 0); return box
    def set_checked(self, predicate):
        for row,item in enumerate(self.items):
            check=self.table.item(row,0)
            if check: check.setCheckState(Qt.CheckState.Checked if predicate(item) else Qt.CheckState.Unchecked)
    def set_profile_checked(self, predicate):
        for row,item in enumerate(self.items):
            check=self.table.item(row,1)
            if check: check.setCheckState(Qt.CheckState.Checked if predicate(item) else Qt.CheckState.Unchecked)
    def chosen(self):
        rows=[]
        for row,base in enumerate(self.items):
            check=self.table.item(row,0)
            if not check or check.checkState()!=Qt.CheckState.Checked: continue
            item=dict(base); item["category"]=self.table.item(row,4).text().strip(); item["context"]=self.table.item(row,5).text().strip(); item["label"]=self.table.item(row,6).text().strip(); item["value"]=self.table.item(row,7).text().strip(); item["profileSelected"]=bool(self.table.item(row,1) and self.table.item(row,1).checkState()==Qt.CheckState.Checked)
            destination=self.table.cellWidget(row,8); destination_value=str(destination.currentData() if destination else "extra|"); parts=destination_value.split("|"); kind=parts[0] if parts else "extra"; path=parts[1] if len(parts)>1 else ""; index=int(parts[2]) if kind=="section_existing" and len(parts)>2 and parts[2].isdigit() else None; item.update({"destinationKind":kind,"destinationPath":path if kind=="common" else "","destinationSection":path if kind in {"section","section_existing"} else "","destinationIndex":index,"destination":destination.currentText() if destination else ""})
            action_text=self.table.item(row,9).text().strip() if self.table.item(row,9) else "写入个人资料库"; action={"写入个人资料库":"profile", "记录为公司要求":"requirement", "不保存":"ignore"}.get(action_text,"profile"); item["reviewAction"]=action
            if kind=="requirement": item["target"]="requirement"
            elif kind=="none": item["profileSelected"]=False
            if action=="ignore": continue
            if action=="requirement": item["target"]="requirement"
            rows.append(item)
        if self.export_only and hasattr(self,"export_scope"):
            scope=self.export_scope.currentData()
            if scope=="filled": rows=[item for item in rows if item.get("filled")]
            elif scope=="blank": rows=[item for item in rows if not item.get("filled")]
        return rows
    def edit_row(self,row,_column):
        if row<0 or row>=len(self.items): return
        dialog=QDialog(self); dialog.setWindowTitle("查看并编辑网页字段"); dialog.resize(800,650); dialog.setMinimumSize(700,580); form=QFormLayout(dialog); form.setContentsMargins(28,26,28,26); form.setVerticalSpacing(13)
        category=QComboBox(); category.setEditable(True); category.addItems(self.CATEGORY_OPTIONS); category.setCurrentText(self.table.item(row,4).text()); context=QLineEdit(self.table.item(row,5).text()); label=QLineEdit(self.table.item(row,6).text()); value=QTextEdit(self.table.item(row,7).text()); value.setMinimumHeight(120); destination=self.destination_box(self.items[row]); current=self.table.cellWidget(row,8); destination.setCurrentIndex(current.currentIndex() if current else destination.currentIndex()); action=QComboBox(); action.addItems(["写入个人资料库","记录为公司要求","不保存"]); action.setCurrentText(self.table.item(row,9).text()); reason=QTextEdit(self.table.item(row,10).text()); reason.setReadOnly(True); reason.setMaximumHeight(80)
        form.addRow("归类",category); form.addRow("上下文",context); form.addRow("字段",label); form.addRow("当前值",value); form.addRow("合并到",destination); form.addRow("处理方式",action); form.addRow("判断原因",reason); buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        for column,text in ((4,category.currentText().strip()),(5,context.text().strip()),(6,label.text().strip()),(7,value.toPlainText().strip()),(9,action.currentText().strip())): self.table.setItem(row,column,QTableWidgetItem(text))
        self.table.setCellWidget(row,8,destination); changed=self.items[row]; changed.update({"category":category.currentText().strip(),"context":context.text().strip(),"label":label.text().strip(),"value":value.toPlainText().strip(),"filled":bool(value.toPlainText().strip())}); self.table.setItem(row,2,QTableWidgetItem("已填写" if changed["filled"] else "未填写"))
    def export_format_name(self): return self.export_format.currentData() if hasattr(self,"export_format") else "json"

class WebPage(QWebEnginePage):
    def __init__(self, library, parent=None):
        profile=QWebEngineProfile("ResumeSubmitter", parent); profile.setPersistentStoragePath(str(library.root/"browser-profile")); profile.setCachePath(str(library.root/"browser-cache")); profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies); script=QWebEngineScript(); script.setSourceCode(FRAME_BRIDGE_SCRIPT); script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation); script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld); script.setRunsOnSubFrames(True); profile.scripts().insert(script); super().__init__(profile,parent)
    def createWindow(self, _): return self

class App(QMainWindow):
    def __init__(self):
        super().__init__(); self.library=LocalLibrary(); self.profile=self.library.load_profile(); self.applications=self.library.load_applications(); self.pool=QThreadPool.globalInstance(); self.inputs={}; self.setWindowTitle(APP_NAME); self.setWindowIcon(QIcon(str(asset_path("touleme-logo.png")))); self.resize(1400,900); self.setMinimumSize(1080,720); self.setStyleSheet(APP_STYLESHEET); self.build()
    def build(self):
        root=QWidget(); layout=QHBoxLayout(root); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        sidebar=QFrame(); sidebar.setObjectName("sidebar"); sidebar.setMinimumWidth(228); sidebar.setMaximumWidth(260); side=QVBoxLayout(sidebar); side.setContentsMargins(18,24,18,20); side.setSpacing(7)
        brand_row=QHBoxLayout(); brand_row.setSpacing(10)
        brand_icon=QLabel(); brand_icon.setPixmap(QPixmap(str(asset_path("touleme-logo.png"))).scaled(40,40,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)); brand_icon.setFixedSize(40,40); brand_icon.setStyleSheet("background: transparent;"); brand_row.addWidget(brand_icon)
        brand=QLabel(APP_NAME); brand.setObjectName("sideBrand"); brand_row.addWidget(brand); brand_row.addStretch(); side.addLayout(brand_row)
        tagline=QLabel("简历投递与资料助手"); tagline.setObjectName("sideHint"); side.addWidget(tagline); side.addSpacing(24)
        pages=[("工作台",self.dashboard()),("投递助手",self.browser_tab()),("AI 对话",self.ai_chat_tab()),("个人资料",self.profile_tab()),("经历与内容",self.sections_tab()),("附件资料",self.attachments_tab()),("自定义字段",self.custom_fields_tab()),("模型设置",self.model_tab()),("投递记录",self.records_tab())]
        self.tabs=QStackedWidget(); self.nav_buttons=[]
        for index,(name,page) in enumerate(pages):
            self.tabs.addWidget(page); button=QPushButton(name); button.setProperty("nav",True); button.setCheckable(True); button.clicked.connect(lambda _checked=False,i=index: self.navigate(i)); side.addWidget(button); self.nav_buttons.append(button)
        side.addStretch(); library=QLabel(f"资料库\n{self.library.root}"); library.setObjectName("sideLibrary"); library.setWordWrap(True); library.setToolTip(str(self.library.root)); side.addWidget(library); layout.addWidget(sidebar)
        content=QWidget(); content_layout=QVBoxLayout(content); content_layout.setContentsMargins(30,22,30,24); content_layout.setSpacing(12); header=QHBoxLayout(); self.page_title=QLabel("工作台"); self.page_title.setObjectName("pageTitle"); header.addWidget(self.page_title); header.addStretch(); status=QLabel("所有资料仅保存在本机"); status.setObjectName("muted"); header.addWidget(status); content_layout.addLayout(header); content_layout.addWidget(self.tabs,1); layout.addWidget(content,1); self.setCentralWidget(root); self.navigate(0)
    def navigate(self,index):
        self.tabs.setCurrentIndex(index)
        names=["工作台","投递助手","AI 对话","个人资料","经历与内容","附件资料","自定义字段","模型设置","投递记录"]
        if hasattr(self,"page_title"): self.page_title.setText(names[index])
        for button_index,button in enumerate(getattr(self,"nav_buttons",[])): button.setChecked(button_index==index)
    def card(self, title="", subtitle=""):
        frame=QFrame(); frame.setObjectName("card"); layout=QVBoxLayout(frame); layout.setContentsMargins(22,20,22,20); layout.setSpacing(12)
        if title:
            heading=QLabel(title); heading.setObjectName("cardTitle"); layout.addWidget(heading)
        if subtitle:
            hint=QLabel(subtitle); hint.setObjectName("muted"); hint.setWordWrap(True); layout.addWidget(hint)
        return frame,layout
    @staticmethod
    def secondary(button):
        button.setProperty("secondary", True); return button
    def dashboard(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(18)
        hero=QFrame(); hero.setObjectName("hero"); h=QVBoxLayout(hero); h.setContentsMargins(28,25,28,25); h.setSpacing(7)
        title=QLabel("让每一次投递，都有完整的准备"); title.setObjectName("heroTitle"); h.addWidget(title)
        text=QLabel("导入简历建立资料库，在官网按需填写；系统会保留每一家公司的专属字段和投递轨迹。"); text.setObjectName("heroText"); h.addWidget(text)
        action=QPushButton("导入并解析我的简历"); action.setMinimumWidth(190); action.clicked.connect(self.import_resume); h.addWidget(action,0,Qt.AlignmentFlag.AlignLeft); l.addWidget(hero)
        grid=QGridLayout(); grid.setHorizontalSpacing(16); grid.setVerticalSpacing(16)
        complete=sum(1 for path in ("basics.name","basics.email","basics.phone","application.school","application.major") if get_value(self.profile,path).strip())
        stats=[("资料完整度", f"{complete}/5", "基础资料越完整，自动填写越准确"),("已保存附件", str(len(self.profile.get("attachments",[]))), "简历、证书与作品集都在本地资料库"),("投递记录", str(len(self.applications)), "持续记录状态、结果与失败原因")]
        for index,(title,value,hint) in enumerate(stats):
            card,box=self.card(title); value_label=QLabel(value); value_label.setStyleSheet("font-size:28px; font-weight:700; color:#2459bc;"); box.addWidget(value_label); note=QLabel(hint); note.setObjectName("muted"); note.setWordWrap(True); box.addWidget(note); grid.addWidget(card,0,index)
        l.addLayout(grid)
        next_card,box=self.card("建议从这里开始","只需三步，就可以开始更高效地投递。")
        steps=QHBoxLayout()
        for number,title,hint in [("01","导入简历","自动解析已有信息"),("02","补全资料","在个人资料或模型助手中补充"),("03","打开官网","登录后使用智能填写")]:
            item=QVBoxLayout(); badge=QLabel(number); badge.setStyleSheet("color:#2f6fed; font-size:12px; font-weight:700;"); item.addWidget(badge); name=QLabel(title); name.setStyleSheet("font-size:15px; font-weight:700;"); item.addWidget(name); sub=QLabel(hint); sub.setObjectName("muted"); sub.setWordWrap(True); item.addWidget(sub); steps.addLayout(item,1)
        box.addLayout(steps); l.addWidget(next_card); l.addStretch(); return w
    def browser_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(14)
        control,box=self.card("投递助手","打开官网后可自行登录、返回上一步或回到投递器。系统不会替你提交申请、上传附件或绕过验证码。")
        nav=QHBoxLayout(); self.browser_back=self.secondary(QPushButton("← 后退")); self.browser_back.clicked.connect(lambda: self.browser.back()); self.browser_forward=self.secondary(QPushButton("前进 →")); self.browser_forward.clicked.connect(lambda: self.browser.forward()); self.browser_reload=self.secondary(QPushButton("刷新")); self.browser_reload.clicked.connect(lambda: self.browser.reload()); self.browser_stop=self.secondary(QPushButton("停止")); self.browser_stop.clicked.connect(lambda: self.browser.stop()); self.url=QLineEdit(); self.url.setPlaceholderText("粘贴招聘官网链接，例如 https://jobs.example.com"); self.url.setClearButtonEnabled(True); self.url.returnPressed.connect(self.open_site); openb=QPushButton("打开官网"); openb.clicked.connect(self.open_site); back_to_app=self.secondary(QPushButton("返回概览")); back_to_app.clicked.connect(lambda: self.tabs.setCurrentIndex(0)); nav.addWidget(self.browser_back); nav.addWidget(self.browser_forward); nav.addWidget(self.browser_reload); nav.addWidget(self.browser_stop); nav.addWidget(self.url,1); nav.addWidget(openb); nav.addWidget(back_to_app); box.addLayout(nav)
        row=QHBoxLayout();
        for text,fn,primary in [("智能填写当前页面",self.fill_page,True),("AI 审核并导入资料",self.collect_page,False),("只填写网页空白项",self.fill_page_only_empty,False),("记录本次投递",self.record_current_page,False),("审核后导出页面字段",self.export_current_page,False)]:
            b=QPushButton(text); b.clicked.connect(fn)
            if not primary: self.secondary(b)
            row.addWidget(b)
        row.addStretch(); box.addLayout(row); l.addWidget(control)
        self.browser=QWebEngineView(); self.browser.setMinimumHeight(420); self.browser.setPage(WebPage(self.library,self.browser)); self.browser.urlChanged.connect(lambda u:(self.url.setText(u.toString()),self.update_browser_navigation())); self.browser.loadFinished.connect(lambda _ok:self.update_browser_navigation()); l.addWidget(self.browser,1); self.update_browser_navigation(); return w

    def ai_chat_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(14)
        card,box=self.card("AI 对话","把简历完善、公司字段、面试问题和自我介绍润色放在一个连续对话里。发送前可选择任务类型，结果不会自动覆盖资料。")
        self.ai_chat_history=QTextEdit(); self.ai_chat_history.setReadOnly(True); self.ai_chat_history.setMinimumHeight(420); self.ai_chat_history.setPlaceholderText("对话记录会显示在这里"); box.addWidget(self.ai_chat_history,1)
        controls=QHBoxLayout(); controls.addWidget(QLabel("任务")); self.ai_chat_mode=QComboBox(); self.ai_chat_mode.addItems(["自由对话", "润色自我介绍", "总结问题与准备建议"]); controls.addWidget(self.ai_chat_mode); clear=self.secondary(QPushButton("清空对话")); clear.clicked.connect(self.ai_chat_history.clear); controls.addWidget(clear); controls.addStretch(); box.addLayout(controls)
        self.ai_chat_input=QTextEdit(); self.ai_chat_input.setMinimumHeight(100); self.ai_chat_input.setPlaceholderText("例如：请根据我的简历写一段适合国企网申的自我介绍，突出硕士和项目经历。发送后可以继续追问。"); box.addWidget(self.ai_chat_input)
        actions=QHBoxLayout(); self.ai_chat_send=QPushButton("发送"); self.ai_chat_send.clicked.connect(self.send_ai_chat); extract=self.secondary(QPushButton("提取资料并保存")); extract.setToolTip("让模型从当前消息中提取教育、工作、项目等资料，审核后保存"); extract.clicked.connect(self.extract_ai_chat_profile); save=self.secondary(QPushButton("保存最后回复到问题总结")); save.clicked.connect(self.save_last_ai_reply); actions.addWidget(self.ai_chat_send); actions.addWidget(extract); actions.addWidget(save); actions.addStretch(); box.addLayout(actions); l.addWidget(card,1); return w

    def send_ai_chat(self):
        text=self.ai_chat_input.toPlainText().strip()
        if not text: self.message("请先输入消息。", True); return
        self._last_ai_question=text; self.ai_chat_history.append(f"<div style='margin:8px 0'><b>我</b><br>{text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace(chr(10),'<br>')}</div>")
        self.ai_chat_input.clear(); self.ai_chat_send.setEnabled(False); self.ai_chat_send.setText("处理中…")
        mode=self.ai_chat_mode.currentText()
        worker=PolishWorker(self.profile,text,"专业简洁") if mode == "润色自我介绍" else AssistantWorker(self.profile,text,mode)
        worker.signals.finished.connect(self.ai_chat_result); worker.signals.error.connect(self.ai_chat_error); self._assistant_worker=worker; self.pool.start(worker)

    def ai_chat_result(self, reply):
        self._last_ai_reply=str(reply); self.ai_chat_history.append(f"<div style='margin:8px 0 14px'><b>AI</b><br>{self._last_ai_reply.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace(chr(10),'<br>')}</div>"); self.ai_chat_send.setEnabled(True); self.ai_chat_send.setText("发送")

    def ai_chat_error(self, error):
        self.ai_chat_history.append(f"<span style='color:#b54708'>模型暂时无法处理：{str(error)}</span>"); self.ai_chat_send.setEnabled(True); self.ai_chat_send.setText("发送"); self.message(str(error), True)

    def extract_ai_chat_profile(self):
        text=self.ai_chat_input.toPlainText().strip() or str(getattr(self, "_last_ai_question", "")).strip()
        if not text: self.message("请先输入要提取的资料。", True); return
        self.ai_chat_history.append(f"<div style='margin:8px 0'><b>提取任务</b><br>{text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace(chr(10),'<br>')}</div>")
        worker=ChatWorker(self.profile,text); worker.signals.finished.connect(self.ai_profile_extract_result); worker.signals.error.connect(self.ai_chat_error); self._assistant_extract_worker=worker; self.ai_chat_send.setEnabled(False); self.pool.start(worker)

    def ai_profile_extract_result(self, items):
        self.ai_chat_send.setEnabled(True); self.ai_chat_send.setText("发送")
        if not items: self.message("没有提取到可确认的资料。请补充学校、岗位、时间或上下文。", True); return
        candidates=[]
        for item in items:
            target=item.get("target")
            if target == "common":
                path=item.get("path", ""); value=str(item.get("value") or "").strip()
                if path and value: candidates.append({**item,"label":item.get("label") or path,"value":value,"_newValue":value,"kind":"common"})
            elif target == "section":
                entry=item.get("entry") or ({item.get("field"):item.get("value")} if item.get("field") else {"description":item.get("value")})
                candidates.append({**item,"label":item.get("label") or item.get("field") or "经历条目","value":json.dumps(entry,ensure_ascii=False),"_entry":entry,"kind":"section"})
            else:
                value=str(item.get("value") or "").strip()
                if value: candidates.append({**item,"label":item.get("label") or "自定义资料","value":value,"kind":"extra"})
        if not candidates: self.message("模型返回的资料缺少可保存内容。", True); return
        dialog=ChooseDialog(candidates,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        chosen=dialog.chosen(); self.apply_profile_items(chosen); self.ai_chat_history.append(f"<div style='margin:8px 0 14px'><b>系统</b><br>已审核保存 {len(chosen)} 项资料。</div>")

    def save_last_ai_reply(self):
        answer=str(getattr(self, "_last_ai_reply", "")).strip()
        if not answer: self.message("还没有可保存的 AI 回复。", True); return
        title=str(getattr(self, "_last_ai_question", "AI 建议")).strip()[:120]
        now=datetime.now(timezone.utc).isoformat(); rows=self.profile.setdefault("knowledgeBase",{}).setdefault("problemSummaries",[])
        rows.append({"id":str(uuid.uuid4()),"title":title,"question":title,"answer":answer,"status":"待确认","context":"AI 对话","source":"AI 对话","createdAt":now,"updatedAt":now})
        self.library.save_profile(self.profile); self.message("AI 回复已保存到个人资料的问题总结。")
    def open_site(self):
        raw=self.url.text().strip()
        if not raw: self.message("请先粘贴招聘官网链接。",True); return
        self.browser.setUrl(QUrl.fromUserInput(raw))
    def update_browser_navigation(self):
        if not hasattr(self,"browser"): return
        history=self.browser.history()
        if hasattr(self,"browser_back"): self.browser_back.setEnabled(history.canGoBack())
        if hasattr(self,"browser_forward"): self.browser_forward.setEnabled(history.canGoForward())
    def attachments_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(14)
        card,box=self.card("简历与附件","导入后会复制一份到本地资料库，原始文件不会被修改。双击附件可直接打开本地文件。")
        row=QHBoxLayout(); add=QPushButton("导入简历或附件"); add.clicked.connect(self.import_attachments); folder=self.secondary(QPushButton("打开资料文件夹")); folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.attachments_dir)))); row.addWidget(add); row.addWidget(folder); row.addStretch(); box.addLayout(row); l.addWidget(card)
        self.attachment_table=QTableWidget(0,3); self.attachment_table.setHorizontalHeaderLabels(["文件名","导入时间","本地文件"]); self.attachment_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch); self.attachment_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents); self.attachment_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents); self.attachment_table.verticalHeader().setVisible(False); self.attachment_table.verticalHeader().setDefaultSectionSize(44); self.attachment_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.attachment_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.attachment_table.cellDoubleClicked.connect(self.open_attachment); l.addWidget(self.attachment_table,1)
        export_card,export_box=self.card("网页字段导出","在投递助手中审核后的字段会保存到这里；双击一条记录可打开导出文件。")
        export_row=QHBoxLayout(); export_folder=self.secondary(QPushButton("打开导出文件夹")); export_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.exports_dir)))); export_row.addWidget(export_folder); export_row.addStretch(); export_box.addLayout(export_row)
        self.export_table=QTableWidget(0,4); self.export_table.setHorizontalHeaderLabels(["导出文件","页面 / 公司","字段数量","导出时间"]); self.export_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch); self.export_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); self.export_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents); self.export_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.ResizeToContents); self.export_table.verticalHeader().setVisible(False); self.export_table.verticalHeader().setDefaultSectionSize(40); self.export_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.export_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.export_table.cellDoubleClicked.connect(self.open_export); export_box.addWidget(self.export_table,1); l.addWidget(export_card,1); self.refresh_attachments(); self.refresh_exports(); return w
    def refresh_attachments(self):
        if not hasattr(self,"attachment_table"): return
        rows=self.profile.get("attachments",[]); self.attachment_table.setRowCount(len(rows))
        for index,item in enumerate(rows):
            stored=self.library.attachments_dir / str(item.get("storedName", "")); added=str(item.get("addedAt", "")).replace("T"," ")[:16]
            self.attachment_table.setItem(index,0,QTableWidgetItem(str(item.get("originalName", ""))))
            self.attachment_table.setItem(index,1,QTableWidgetItem(added))
            self.attachment_table.setItem(index,2,QTableWidgetItem(("主简历 · 已保存" if item.get("kind")=="resume" else "已保存") if stored.is_file() else "文件缺失"))
    def open_attachment(self, row, _column):
        rows=self.profile.get("attachments",[])
        if row < 0 or row >= len(rows): return
        stored=self.library.attachments_dir / str(rows[row].get("storedName", ""))
        if stored.is_file(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(stored)))
        else: self.message("这个附件在资料库中不存在。", True)

    def refresh_exports(self):
        if not hasattr(self, "export_table"): return
        files=sorted(self.library.exports_dir.glob("*.json"), key=lambda p:p.stat().st_mtime, reverse=True)
        files += sorted(self.library.exports_dir.glob("*.csv"), key=lambda p:p.stat().st_mtime, reverse=True)
        self._export_files=files; self.export_table.setRowCount(len(files))
        for row,path in enumerate(files):
            page=""; count=""
            try:
                if path.suffix.lower()==".json":
                    payload=json.loads(path.read_text(encoding="utf-8")); page=payload.get("title") or payload.get("url") or "网页字段"; count=str(len(payload.get("fields",[]) or []))
                else:
                    with path.open("r", newline="", encoding="utf-8-sig") as handle:
                        rows=list(csv.DictReader(handle)); count=str(len(rows)); page=(rows[0].get("title") or rows[0].get("url") or "网页字段（CSV）") if rows else "网页字段（CSV）"
            except (OSError, ValueError, json.JSONDecodeError):
                page="无法读取文件摘要"; count="-"
            values=(path.name, page, count, datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"))
            for column,value in enumerate(values): self.export_table.setItem(row,column,QTableWidgetItem(str(value)))

    def open_export(self, row, _column):
        files=getattr(self, "_export_files", [])
        if row < 0 or row >= len(files): return
        path=files[row]
        if path.is_file(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else: self.message("导出文件不存在。", True)
    def import_attachments(self):
        files,_=QFileDialog.getOpenFileNames(self,"导入简历或附件","","文件 (*.pdf *.docx *.doc *.txt *.jpg *.jpeg *.png *.zip);;所有文件 (*)")
        if not files: return
        try:
            self.library.import_files(files,self.profile); self.refresh_attachments(); self.message(f"已导入 {len(files)} 个文件。")
        except Exception as exc: self.message(str(exc),True)
    def sections_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(14); card,box=self.card("经历、技能与附加内容","所有教育、工作、项目、奖项、技能、论文和链接都在下方同一列表中展示；本科和硕士等多段经历会各自保留。")
        row=QHBoxLayout(); add=QPushButton("新增条目"); add.clicked.connect(self.new_section_entry); edit=self.secondary(QPushButton("编辑选中")); edit.clicked.connect(lambda: self.edit_section_entry()); delete=self.secondary(QPushButton("删除选中")); delete.setProperty("danger",True); delete.clicked.connect(self.delete_section_entry); row.addWidget(add); row.addWidget(edit); row.addWidget(delete); row.addStretch(); box.addLayout(row); l.addWidget(card)
        self.sections_table=QTableWidget(0,4); self.sections_table.setHorizontalHeaderLabels(["类型","条目","时间 / 角色","说明"]); header=self.sections_table.horizontalHeader(); header.setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed); header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); header.setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed); header.setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch); self.sections_table.setColumnWidth(0,150); self.sections_table.setColumnWidth(2,300); self.sections_table.verticalHeader().setVisible(False); self.sections_table.verticalHeader().setDefaultSectionSize(48); self.sections_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.sections_table.setTextElideMode(Qt.TextElideMode.ElideRight); self.sections_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.sections_table.cellDoubleClicked.connect(lambda _row,_column: self.edit_section_entry()); l.addWidget(self.sections_table,1); self.refresh_sections(); return w
    def refresh_sections(self, *_):
        if not hasattr(self,"sections_table"): return
        flattened=[]
        for section,(section_name,_) in SECTION_FORMS.items():
            for index,item in enumerate(self.profile.get("resumeSections",{}).get(section,[])):
                flattened.append((section,section_name,index,item if isinstance(item,dict) else {"description":str(item)}))
        self.sections_table.setRowCount(len(flattened))
        for table_row,(section,section_name,index,item) in enumerate(flattened):
            title=item.get("name") or item.get("school") or item.get("company") or item.get("description","")[:40]
            middle=" · ".join(str(item.get(key,"")) for key in ("degree","title","role","date","start","end") if item.get(key))
            description=item.get("description") or item.get("context") or item.get("url","")
            type_item=QTableWidgetItem(section_name); type_item.setData(Qt.ItemDataRole.UserRole,(section,index)); self.sections_table.setItem(table_row,0,type_item)
            for col,value in enumerate((title,middle,description),1): self.sections_table.setItem(table_row,col,QTableWidgetItem(str(value)))
    def new_section_entry(self):
        dialog=QDialog(self); dialog.setWindowTitle("选择新增内容类型"); dialog.setMinimumWidth(460); form=QFormLayout(dialog); form.setContentsMargins(26,24,26,24); form.setSpacing(16); selector=QComboBox()
        for key,(name,_) in SECTION_FORMS.items(): selector.addItem(name,key)
        form.addRow("要新增的内容",selector); buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted: self.edit_section_entry(-1,selector.currentData())
    def edit_section_entry(self,row=None,section=None):
        if section is None:
            table_row=self.sections_table.currentRow() if row is None else row
            selected=self.sections_table.item(table_row,0) if table_row is not None and table_row>=0 else None
            payload=selected.data(Qt.ItemDataRole.UserRole) if selected else None
            if not payload: self.message("请先选中一条内容。",True); return
            section,row=payload
        rows=self.profile.setdefault("resumeSections",{}).setdefault(section,[]); old=rows[row] if row is not None and row>=0 and row<len(rows) and isinstance(rows[row],dict) else {}
        dialog=QDialog(self); dialog.setWindowTitle(f"编辑{SECTION_FORMS.get(section,(section,[]))[0]}"); dialog.resize(700,620); dialog.setMinimumSize(640,560); form=QFormLayout(dialog); form.setContentsMargins(28,26,28,26); form.setHorizontalSpacing(20); form.setVerticalSpacing(14); inputs={}
        date_keys={"start","end","date","publishedDate"}
        for label,key in SECTION_FORMS.get(section,(section,[("内容","description")]))[1]:
            raw=str(old.get(key,"")); editor=None
            parsed=QDate.fromString(raw,"yyyy-MM-dd") if key in date_keys else QDate()
            if key in date_keys and (not raw or parsed.isValid()):
                editor=QDateEdit(); editor.setCalendarPopup(True); editor.setDisplayFormat("yyyy-MM-dd"); editor.setMinimumDate(QDate(1900,1,1)); editor.setDate(parsed if parsed.isValid() else QDate.currentDate())
            elif key=="description":
                editor=QTextEdit(raw); editor.setMaximumHeight(110)
            else:
                editor=QLineEdit(raw); editor.setPlaceholderText("例如 2025-09 或 至今" if key in date_keys else f"填写{label}")
            inputs[key]=editor; form.addRow(label,editor)
        context=QLineEdit(str(old.get("context", ""))); context.setPlaceholderText("例如：教育经历 / 硕士（可选）"); form.addRow("上下文",context); buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        item={}
        for key,editor in inputs.items():
            if isinstance(editor,QTextEdit): item[key]=editor.toPlainText().strip()
            elif isinstance(editor,QDateEdit): item[key]=editor.date().toString("yyyy-MM-dd")
            else: item[key]=editor.text().strip()
        item["context"]=context.text().strip(); item["updatedAt"]=datetime.now(timezone.utc).isoformat()
        if row is not None and row>=0 and row<len(rows): rows[row]=item
        else: rows.append(item)
        self.library.save_profile(self.profile); self.refresh_sections()
    def delete_section_entry(self):
        table_row=self.sections_table.currentRow() if hasattr(self,"sections_table") else -1; selected=self.sections_table.item(table_row,0) if table_row>=0 else None; payload=selected.data(Qt.ItemDataRole.UserRole) if selected else None
        if not payload: self.message("请先选中一条内容。",True); return
        section,row=payload; rows=self.profile.setdefault("resumeSections",{}).setdefault(section,[])
        if QMessageBox.question(self,"删除条目","确定删除选中的条目吗？")!=QMessageBox.StandardButton.Yes: return
        rows.pop(row); self.library.save_profile(self.profile); self.refresh_sections()
    def custom_fields_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(14); card,box=self.card("公司专属字段","这里保存招聘官网中出现的特殊问题与答案。字段的页面上下文会一并保存，避免混淆导师、亲属和证明人等同名信息。")
        row=QHBoxLayout(); add=QPushButton("手动新增字段"); add.clicked.connect(self.new_custom_field); edit=self.secondary(QPushButton("编辑选中")); edit.clicked.connect(lambda: self.edit_custom_field()); delete=self.secondary(QPushButton("删除选中")); delete.setProperty("danger",True); delete.clicked.connect(self.delete_custom_field); row.addWidget(add); row.addWidget(edit); row.addWidget(delete); row.addStretch(); box.addLayout(row); l.addWidget(card)
        self.custom_table=QTableWidget(0,4); self.custom_table.setHorizontalHeaderLabels(["上下文","字段","答案","更新时间"]); self.custom_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.custom_table.verticalHeader().setVisible(False); self.custom_table.verticalHeader().setDefaultSectionSize(44); self.custom_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.custom_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.custom_table.cellDoubleClicked.connect(lambda _row,_column: self.edit_custom_field()); l.addWidget(self.custom_table,1); self.refresh_custom_fields(); return w
    def refresh_custom_fields(self):
        if not hasattr(self,"custom_table"): return
        rows=self.profile.get("extraFields",[]); self.custom_table.setRowCount(len(rows))
        for index,item in enumerate(rows):
            values=(item.get("context",""),item.get("label",""),item.get("value",""),str(item.get("updatedAt","")).replace("T"," ")[:16])
            for col,value in enumerate(values): self.custom_table.setItem(index,col,QTableWidgetItem(str(value)))
    def new_custom_field(self): self.edit_custom_field(-1)
    def edit_custom_field(self,row=None):
        rows=self.profile.setdefault("extraFields",[]); row=self.custom_table.currentRow() if row is None else row; old=rows[row] if row is not None and row>=0 and row<len(rows) else {}
        dialog=QDialog(self); dialog.setWindowTitle("编辑公司专属字段"); dialog.resize(680,440); dialog.setMinimumSize(600,400); form=QFormLayout(dialog); form.setContentsMargins(28,26,28,26); form.setVerticalSpacing(14); context=QLineEdit(str(old.get("context",""))); label=QLineEdit(str(old.get("label",""))); value=QTextEdit(str(old.get("value",""))); value.setMinimumHeight(120); form.addRow("上下文",context); form.addRow("字段名称",label); form.addRow("答案",value); buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        label_text=label.text().strip(); value_text=value.toPlainText().strip(); context_text=context.text().strip()
        if not label_text or not value_text: self.message("字段名称和答案不能为空。",True); return
        item={"identity":"".join(f"{context_text}::{label_text}".lower().split()),"context":context_text,"label":label_text,"value":value_text,"updatedAt":datetime.now(timezone.utc).isoformat(),"source":"手动添加"}
        if row is not None and row>=0 and row<len(rows): rows[row]=item
        else: rows.append(item)
        self.library.save_profile(self.profile); self.refresh_custom_fields()
    def delete_custom_field(self):
        row=self.custom_table.currentRow() if hasattr(self,"custom_table") else -1; rows=self.profile.setdefault("extraFields",[])
        if row<0 or row>=len(rows): self.message("请先选中一个字段。",True); return
        if QMessageBox.question(self,"删除字段","确定删除选中的公司专属字段吗？")!=QMessageBox.StandardButton.Yes: return
        rows.pop(row); self.library.save_profile(self.profile); self.refresh_custom_fields()
    def model_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(14); card,box=self.card("模型设置","支持 OpenAI 兼容的 Chat Completions API。API Key 只保存在本机资料库中，用于简历解析、字段匹配和资料助手。")
        form=QFormLayout(); self.llm_inputs={}
        for label,key,placeholder in [("接口地址","endpoint","https://api.openai.com/v1/chat/completions"),("模型名称","model","gpt-4o-mini"),("API Key","apiKey","sk-...")]:
            editor=QLineEdit(str(self.profile.get("llm",{}).get(key,""))); editor.setPlaceholderText(placeholder); editor.setEchoMode(QLineEdit.EchoMode.Password if key=="apiKey" else QLineEdit.EchoMode.Normal); self.llm_inputs[key]=editor; form.addRow(label,editor)
        box.addLayout(form); row=QHBoxLayout(); save=QPushButton("保存模型设置"); save.clicked.connect(self.save_model_settings); test=self.secondary(QPushButton("测试模型连接")); test.clicked.connect(self.test_model_settings); row.addWidget(save); row.addWidget(test); row.addStretch(); box.addLayout(row); l.addWidget(card); hint, hint_layout=self.card("数据说明","未配置模型时，程序仍会使用本地规则填写。模型调用只会发送资料和字段描述，不会读取网站密码、验证码或自动提交。 "); l.addWidget(hint); l.addStretch(); return w
    def save_model_settings(self):
        self.profile.setdefault("llm",{}).update({key:editor.text().strip() for key,editor in self.llm_inputs.items()}); self.library.save_profile(self.profile); self.message("模型设置已保存。")
    def test_model_settings(self):
        self.save_model_settings()
        try: self.message(test_connection(self.profile["llm"]))
        except Exception as exc: self.message(str(exc),True)
    def profile_tab(self):
        outer=QWidget(); outer_layout=QVBoxLayout(outer); outer_layout.setContentsMargins(0,18,0,0); outer_layout.setSpacing(0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); content=QWidget(); l=QVBoxLayout(content); l.setContentsMargins(0,0,10,12); l.setSpacing(16)
        profile_card,form_box=self.card("个人资料","所有信息保存在同一个资料库中，可分多次填写；官网新增字段会保留原本的页面上下文。")
        grid=QGridLayout(); grid.setHorizontalSpacing(18); grid.setVerticalSpacing(13)
        index=0
        for _,fields in PROFILE_FIELDS:
            for label,key in fields:
                initial=get_value(self.profile,key)
                if key == "resumeSections.personalSummary":
                    summaries=self.profile.get("resumeSections",{}).get("personalSummary",[])
                    initial=next((str(x.get("description", "")) for x in summaries if isinstance(x,dict) and x.get("description")), "")
                cell=QWidget(); cell_box=QVBoxLayout(cell); cell_box.setContentsMargins(0,0,0,0); cell_box.setSpacing(5); field_label=QLabel(label); field_label.setObjectName("fieldLabel"); cell_box.addWidget(field_label); e=QLineEdit(initial); e.setPlaceholderText(f"填写{label}"); e.textChanged.connect(lambda v,k=key:self.save_key(k,v)); self.inputs[key]=e; cell_box.addWidget(e); grid.addWidget(cell,index//2,index%2); index+=1
        form_box.addLayout(grid)
        privacy=self.profile.setdefault("privacy", {})
        self.sensitive_autofill=QCheckBox("允许自动填写身份证号等敏感信息（默认关闭）")
        self.sensitive_autofill.setChecked(bool(privacy.get("allowSensitiveAutofill", False)))
        self.sensitive_autofill.stateChanged.connect(lambda state: self.save_sensitive_setting(bool(state)))
        form_box.addWidget(self.sensitive_autofill)
        actions=QHBoxLayout(); b=QPushButton("保存个人资料"); b.clicked.connect(lambda:self.library.save_profile(self.profile)); actions.addWidget(b)
        add_custom=self.secondary(QPushButton("新增自定义个人字段")); add_custom.setToolTip("添加官网特殊问题、偏好或其他资料"); add_custom.clicked.connect(lambda:(self.navigate(6), self.new_custom_field())); actions.addWidget(add_custom); actions.addStretch(); form_box.addLayout(actions); l.addWidget(profile_card)

        # Model assistant: paste a sentence or a whole paragraph and review the
        # extracted candidates before anything is written to profile.json.
        assistant_card,assistant_box=self.card("模型助手 · 补充资料","粘贴一段话，让已配置的模型提取候选资料。确认后才会写入资料库，导师、亲属和证明人等会按上下文分开保存。")
        self.chat_history=QTextEdit(); self.chat_history.setReadOnly(True); self.chat_history.setPlaceholderText("这里显示本次对话和提取结果"); self.chat_history.setMaximumHeight(170)
        assistant_box.addWidget(self.chat_history)
        self.chat_input=QTextEdit(); self.chat_input.setPlaceholderText("例如：我在 XX 大学攻读硕士，导师是张三；项目证明人是李四，电话……"); self.chat_input.setMaximumHeight(105); assistant_box.addWidget(self.chat_input)
        row=QHBoxLayout(); self.chat_send=QPushButton("发送给模型提取"); self.chat_send.clicked.connect(self.send_profile_chat); open_chat=self.secondary(QPushButton("打开 AI 对话")); open_chat.clicked.connect(lambda:self.navigate(2)); save_publication=self.secondary(QPushButton("直接保存为论文 / 成果")); save_publication.clicked.connect(self.save_chat_as_publication); clear=self.secondary(QPushButton("清空输入")); clear.clicked.connect(self.chat_input.clear); row.addWidget(self.chat_send); row.addWidget(open_chat); row.addWidget(save_publication); row.addWidget(clear); row.addStretch(); assistant_box.addLayout(row); l.addWidget(assistant_card)

        kb_card,kb_box=self.card("自我介绍与问题总结","自我介绍、求职偏好、待补公司要求和面试问答都保存在同一份个人资料库中；后续可安全同步到云端或团队空间。")
        kb_form=QFormLayout(); kb=self.profile.setdefault("knowledgeBase",{})
        self.kb_intro=QTextEdit(str(kb.get("selfIntroduction", ""))); self.kb_intro.setPlaceholderText("例如：我是一名控制科学与工程硕士，擅长……"); self.kb_intro.setMaximumHeight(110); kb_form.addRow("自我介绍",self.kb_intro)
        polish_intro=self.secondary(QPushButton("AI 基于简历润色")); polish_intro.setToolTip("读取结构化简历生成润色草稿，确认后才保存"); polish_intro.clicked.connect(self.polish_intro_dialog); kb_form.addRow("自我介绍助手",polish_intro)
        self.kb_preferences=QTextEdit(str(kb.get("careerPreferences", ""))); self.kb_preferences.setPlaceholderText("期望行业、岗位、城市、薪资、到岗时间等"); self.kb_preferences.setMaximumHeight(90); kb_form.addRow("求职偏好",self.kb_preferences)
        self.kb_notes=QTextEdit(str(kb.get("notes", ""))); self.kb_notes.setPlaceholderText("长期备注、需要持续完善的内容"); self.kb_notes.setMaximumHeight(90); kb_form.addRow("长期备注",self.kb_notes)
        kb_box.addLayout(kb_form); kb_save=QPushButton("保存这些内容"); kb_save.clicked.connect(self.save_knowledge); kb_box.addWidget(kb_save,0,Qt.AlignmentFlag.AlignLeft)
        self.knowledge_table=QTableWidget(0,5); self.knowledge_table.setHorizontalHeaderLabels(["类型","问题 / 标题","答案","状态","来源"]); self.knowledge_table.setMinimumHeight(190); self.knowledge_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.knowledge_table.verticalHeader().setVisible(False); self.knowledge_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.knowledge_table.cellDoubleClicked.connect(lambda _r,_c:self.edit_knowledge_item()); kb_box.addWidget(self.knowledge_table)
        kb_actions=QHBoxLayout(); add_problem=self.secondary(QPushButton("新增问题总结")); add_problem.clicked.connect(lambda:self.edit_knowledge_item(-1,"problemSummaries")); add_answer=self.secondary(QPushButton("新增面试问答")); add_answer.clicked.connect(lambda:self.edit_knowledge_item(-1,"interviewAnswers")); edit_kb=self.secondary(QPushButton("编辑选中")); edit_kb.clicked.connect(self.edit_knowledge_item); kb_actions.addWidget(add_problem); kb_actions.addWidget(add_answer); kb_actions.addWidget(edit_kb); kb_actions.addStretch(); kb_box.addLayout(kb_actions); l.addWidget(kb_card); self.refresh_knowledge()
        l.addStretch(); scroll.setWidget(content); outer_layout.addWidget(scroll); return outer

    def save_knowledge(self):
        kb=self.profile.setdefault("knowledgeBase",{}); kb.update({"selfIntroduction":self.kb_intro.toPlainText().strip(),"careerPreferences":self.kb_preferences.toPlainText().strip(),"notes":self.kb_notes.toPlainText().strip()}); self.library.save_profile(self.profile); self.message("自我介绍、求职偏好和长期备注已保存。")

    def save_sensitive_setting(self, enabled):
        self.profile.setdefault("privacy", {})["allowSensitiveAutofill"] = bool(enabled)
        self.library.save_profile(self.profile)

    def polish_intro_dialog(self):
        dialog=QDialog(self); dialog.setWindowTitle("AI 润色自我介绍"); dialog.resize(780,650); dialog.setMinimumSize(680,560)
        form=QFormLayout(dialog); form.setContentsMargins(28,26,28,26); form.setVerticalSpacing(13)
        style=QComboBox(); style.addItems(["专业简洁", "技术岗位", "应届生校招", "管理 / 沟通", "真诚自然"]); form.addRow("写作风格",style)
        draft=QTextEdit(self.kb_intro.toPlainText()); draft.setPlaceholderText("可填写已有草稿；留空则根据简历重新起草"); draft.setMinimumHeight(120); form.addRow("现有草稿",draft)
        output=QTextEdit(); output.setReadOnly(False); output.setPlaceholderText("点击“生成润色稿”后显示结果"); output.setMinimumHeight(180); form.addRow("润色结果",output)
        buttons=QHBoxLayout(); generate=QPushButton("生成润色稿"); save_button=QPushButton("保存到个人资料"); save_button.setEnabled(False); close=self.secondary(QPushButton("关闭")); buttons.addWidget(generate); buttons.addWidget(save_button); buttons.addWidget(close); buttons.addStretch(); form.addRow(buttons)
        close.clicked.connect(dialog.reject)
        def done(text):
            output.setPlainText(str(text)); save_button.setEnabled(bool(str(text).strip())); generate.setEnabled(True); generate.setText("重新生成")
        def failed(error):
            generate.setEnabled(True); generate.setText("生成润色稿"); QMessageBox.warning(dialog, "模型提示", str(error))
        def run():
            generate.setEnabled(False); generate.setText("模型处理中…")
            worker=PolishWorker(self.profile, draft.toPlainText().strip(), style.currentText()); worker.signals.finished.connect(done); worker.signals.error.connect(failed); self._polish_worker=worker; self.pool.start(worker)
        def save():
            text=output.toPlainText().strip()
            if not text: return
            self.kb_intro.setPlainText(text); self.save_knowledge(); dialog.accept()
        generate.clicked.connect(run); save_button.clicked.connect(save)
        dialog.exec()

    def refresh_knowledge(self):
        if not hasattr(self,"knowledge_table"): return
        rows=[]; kb=self.profile.get("knowledgeBase",{}) or {}
        for key,label in (("problemSummaries","问题总结"),("interviewAnswers","面试问答")):
            for index,item in enumerate(kb.get(key,[]) or []):
                if isinstance(item,dict): rows.append((key,index,item,label))
        self._knowledge_rows=rows; self.knowledge_table.setRowCount(len(rows))
        for r,(_key,_index,item,label) in enumerate(rows):
            values=(label,item.get("title") or item.get("question") or "",item.get("answer") or item.get("summary") or "",item.get("status") or "",item.get("source") or "")
            for c,value in enumerate(values): self.knowledge_table.setItem(r,c,QTableWidgetItem(str(value)))

    def edit_knowledge_item(self,row=None,kind=None):
        if row is None:
            row=self.knowledge_table.currentRow() if hasattr(self,"knowledge_table") else -1
            if row>=0 and row<len(getattr(self,"_knowledge_rows",[])): kind,index,old,_=self._knowledge_rows[row]
            else: self.message("请先选中一条问题总结或面试问答。",True); return
        else:
            index=-1; old={}; kind=kind or "problemSummaries"
        dialog=QDialog(self); dialog.setWindowTitle("编辑问题总结 / 面试问答"); dialog.resize(700,520); form=QFormLayout(dialog); form.setContentsMargins(28,26,28,26); form.setVerticalSpacing(14)
        type_box=QComboBox(); type_box.addItem("问题总结","problemSummaries"); type_box.addItem("面试问答","interviewAnswers"); type_box.setCurrentIndex(1 if kind=="interviewAnswers" else 0); form.addRow("内容类型",type_box)
        title=QLineEdit(str(old.get("title") or old.get("question") or "")); form.addRow("问题 / 标题",title)
        answer=QTextEdit(str(old.get("answer") or old.get("summary") or "")); answer.setMinimumHeight(150); form.addRow("答案 / 总结",answer)
        status=QComboBox(); status.setEditable(True); status.addItems(["待补充","待确认","已解决","已验证"]); status.setCurrentText(str(old.get("status") or "待补充")); form.addRow("状态",status)
        context=QLineEdit(str(old.get("context") or "")); form.addRow("上下文",context)
        source=QLineEdit(str(old.get("source") or "手动添加")); form.addRow("来源",source)
        bb=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)); bb.accepted.connect(dialog.accept); bb.rejected.connect(dialog.reject); form.addRow(bb)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        selected_kind=type_box.currentData(); now=datetime.now(timezone.utc).isoformat(); payload={"id":old.get("id",str(uuid.uuid4())),"title":title.text().strip(),"question":title.text().strip(),"answer":answer.toPlainText().strip(),"status":status.currentText().strip(),"context":context.text().strip(),"source":source.text().strip() or "手动添加","createdAt":old.get("createdAt",now),"updatedAt":now}
        if not payload["title"] or not payload["answer"]: self.message("标题和答案不能为空。",True); return
        rows=self.profile.setdefault("knowledgeBase",{}).setdefault(selected_kind,[])
        if kind!=selected_kind and index>=0:
            self.profile["knowledgeBase"].setdefault(kind,[]).pop(index); index=-1
        if index>=0 and index<len(rows): rows[index]=payload
        else: rows.append(payload)
        self.library.save_profile(self.profile); self.refresh_knowledge(); self.message("内容已保存到个人资料库。")

    def send_profile_chat(self):
        text=self.chat_input.toPlainText().strip()
        if not text:
            self.message("请先输入或粘贴要补充的资料。", True); return
        self.last_chat_text=text; self.chat_history.append(f"<b>我：</b>{text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace(chr(10),'<br>')}")
        self.chat_send.setEnabled(False); self.chat_send.setText("模型处理中…")
        worker=ChatWorker(self.profile, text); worker.signals.finished.connect(self.profile_chat_result); worker.signals.error.connect(self.profile_chat_error); self.pool.start(worker)

    def profile_chat_error(self, error):
        self.chat_send.setEnabled(True); self.chat_send.setText("发送给模型提取")
        if not self.chat_input.toPlainText().strip() and getattr(self,"last_chat_text",""):
            self.chat_input.setPlainText(self.last_chat_text)
        detail=str(error or "")
        is_timeout="timed out" in detail.lower() or "timeout" in detail.lower()
        friendly="模型请求超时，原文已保留在输入框：可直接重试，也可使用“直接保存为论文 / 成果”。" if is_timeout else f"模型暂时无法处理，原文已保留在输入框。{detail}"
        self.chat_history.append(f"<span style='color:#b54708'><b>提示：</b>{friendly}</span>")
        self.message(friendly, True)

    def save_chat_as_publication(self):
        text=self.chat_input.toPlainText().strip() or getattr(self,"last_chat_text","").strip()
        if not text:
            self.message("请先粘贴论文或学术成果内容。", True); return
        lines=[line.strip() for line in text.splitlines() if line.strip()]
        content=[line for line in lines if line.rstrip("：:").strip() not in {"论文","学术成果","成果"}]
        if not content:
            self.message("没有识别到可保存的论文内容。", True); return
        title=content[0][:300]
        rest="\n".join(content[1:])[:1600]
        entry={"name":title,"publisher":rest[:500],"description":text[:4000],"context":"论文 / 学术成果","updatedAt":datetime.now(timezone.utc).isoformat()}
        rows=self.profile.setdefault("resumeSections",{}).setdefault("publications",[])
        duplicate=next((item for item in rows if isinstance(item,dict) and item.get("name")==title),None)
        if duplicate: duplicate.update(entry)
        else: rows.append(entry)
        self.library.save_profile(self.profile); self.refresh_sections()
        self.message("已保存到“经历与内容”的论文 / 学术成果。")

    def profile_chat_result(self, items):
        self.chat_send.setEnabled(True); self.chat_send.setText("发送给模型")
        if not items:
            self.chat_history.append("<b>模型：</b>没有找到置信度足够高、且能明确归类的资料。可以补充更多上下文，例如‘硕士期间’或‘项目证明人’。")
            self.message("模型没有提取到可保存的资料。", True); return
        candidates=[]
        for item in items:
            target=item.get("target")
            if target == "common":
                path=item.get("path", ""); old=get_value(self.profile,path) if path else ""
                value=str(item.get("value") or "").strip()
                if not path or not value: continue
                shown=f"{value}" + (f"（当前：{old}）" if old and old != value else "")
                candidates.append({**item,"label":item.get("label") or path,"value":shown,"_newValue":value,"kind":"common"})
            elif target == "section":
                entry=item.get("entry") or ({item.get("field"):item.get("value")} if item.get("field") else {"description":item.get("value")})
                candidates.append({**item,"label":item.get("label") or item.get("field") or item.get("section") or "经历条目","value":json.dumps(entry,ensure_ascii=False),"_entry":entry,"kind":"section"})
            else:
                candidates.append({**item,"label":item.get("label") or "自定义资料","value":str(item.get("value") or ""),"kind":"extra"})
        if not candidates:
            self.message("模型返回的资料缺少可保存路径。", True); return
        d=ChooseDialog(candidates,self)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        chosen=d.chosen(); self.apply_profile_items(chosen)
        self.chat_history.append(f"<b>模型：</b>已提取 {len(items)} 项，已保存 {len(chosen)} 项。")

    def apply_profile_items(self, items):
        """Apply confirmed candidates while keeping repeated entries separate."""
        now=datetime.now(timezone.utc).isoformat()
        for item in items:
            kind=item.get("destinationKind") or item.get("kind") or item.get("target") or "extra"
            if kind == "common":
                path=item.get("destinationPath") or item.get("path", ""); value=item.get("_newValue", item.get("value", ""))
                allowed_paths={key for _, fields in PROFILE_FIELDS for _, key in fields}
                allowed_paths.update({"basics.linkedin","basics.website","custom.expectedSalary","custom.noticePeriod"})
                if path not in allowed_paths:
                    # A model may suggest an arbitrary path; keep it as a
                    # contextual field instead of mutating the profile shape.
                    kind="extra"
                else:
                    if path == "resumeSections.personalSummary":
                        rows=self.profile.setdefault("resumeSections",{}).setdefault("personalSummary",[])
                        if rows and isinstance(rows[0],dict): rows[0].update({"description":str(value),"context":item.get("context") or "个人评价","updatedAt":now})
                        elif value: rows.append({"description":str(value),"context":item.get("context") or "个人评价","updatedAt":now})
                    elif path and value:
                        set_value(self.profile,path,str(value))
                    if path in self.inputs: self.inputs[path].setText(str(value))
            if kind in {"section", "section_existing"}:
                section=item.get("destinationSection") or item.get("section") or "customEntries"; entry=item.get("_entry") or item.get("entry") or {}
                if section not in SECTION_FORMS:
                    kind="extra"
                else:
                    if not entry:
                        raw_label=str(item.get("field") or item.get("label") or "").strip()
                        raw_value=str(item.get("value") or "").strip()
                        aliases={
                            "school": ("\u5b66\u6821", "\u9662\u6821", "\u5927\u5b66"), "degree": ("\u5b66\u5386", "\u5b66\u4f4d"), "major": ("\u4e13\u4e1a", "\u65b9\u5411"),
                            "company": ("\u516c\u53f8", "\u5355\u4f4d", "\u96c7\u4e3b"), "title": ("\u804c\u4f4d", "\u804c\u52a1", "\u5c97\u4f4d"),
                            "name": ("\u540d\u79f0", "\u9879\u76ee", "\u8bfe\u9898", "\u5956\u9879", "\u8bc1\u4e66", "\u8bba\u6587"), "description": ("\u63cf\u8ff0", "\u5185\u5bb9", "\u804c\u8d23", "\u8bf4\u660e"),
                            "start": ("\u5f00\u59cb", "\u8d77\u59cb", "\u5165\u5b66"), "end": ("\u7ed3\u675f", "\u622a\u6b62", "\u6bd5\u4e1a"),
                        }
                        destination="description"
                        for key, words in aliases.items():
                            if any(word in raw_label for word in words):
                                destination=key; break
                        entry={destination: raw_value} if raw_value else {}
                    if not isinstance(entry,dict): entry={"description":str(entry)}
                    entry=dict(entry); entry.setdefault("context",item.get("context", "")); entry["updatedAt"]=now
                    rows=self.profile.setdefault("resumeSections",{}).setdefault(section,[])
                    # Only update an identical contextual entry; otherwise append a
                    # new item so bachelor's/master's and multiple jobs coexist.
                    identity=(item.get("context", "") + "|" + str(entry.get("name") or entry.get("school") or entry.get("company") or entry.get("description") or "")).strip().lower()
                    existing_index=item.get("destinationIndex")
                    old=rows[existing_index] if isinstance(existing_index,int) and 0 <= existing_index < len(rows) and isinstance(rows[existing_index],dict) else next((row for row in rows if isinstance(row,dict) and identity and (str(row.get("context", "")) + "|" + str(row.get("name") or row.get("school") or row.get("company") or row.get("description") or "")).strip().lower()==identity),None)
                    if old: old.update(entry)
                    else: rows.append(entry)
            if kind == "extra":
                value=str(item.get("value") or "").strip(); label=str(item.get("label") or "").strip(); context=str(item.get("context") or "").strip()
                if not value or not label: continue
                identity="".join(f"{context}::{label}".lower().split())
                rows=self.profile.setdefault("extraFields",[]); old=next((row for row in rows if row.get("identity")==identity),None)
                payload={"identity":identity,"label":label,"context":context,"value":value,"category":item.get("category","company-specific"),"source":item.get("source",""),"pageUrl":item.get("pageUrl",""),"updatedAt":now}
                if old: old.update(payload)
                else: rows.append(payload)
        self.library.save_profile(self.profile); self.refresh_sections(); self.refresh_custom_fields()
    def records_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,18,0,0); l.setSpacing(14); card,box=self.card("投递记录","记录投递时间、状态、结果和失败原因；支持手动补充并导出为 CSV。")
        row=QHBoxLayout();
        for text,fn,primary in [("新增记录",self.new_record,True),("编辑选中",lambda: self.edit_record(),False),("导出 CSV",self.export_records,False)]:
            b=QPushButton(text); b.clicked.connect(fn)
            if not primary: self.secondary(b)
            row.addWidget(b)
        row.addStretch(); box.addLayout(row); l.addWidget(card)
        self.records=QTableWidget(0,7); self.records.setAlternatingRowColors(True); self.records.setHorizontalHeaderLabels(["公司","职位","渠道","状态","投递日期","失败原因","备注"]); self.records.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.records.verticalHeader().setVisible(False); self.records.verticalHeader().setDefaultSectionSize(44); self.records.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.records.cellDoubleClicked.connect(lambda _row,_column: self.edit_record()); l.addWidget(self.records,1); self.refresh_records(); return w
    def save_key(self,key,value):
        if key == "resumeSections.personalSummary":
            rows=self.profile.setdefault("resumeSections",{}).setdefault("personalSummary",[])
            if rows and isinstance(rows[0],dict): rows[0]["description"]=value
            elif value.strip(): rows.append({"description":value,"context":"个人评价"})
            if not value.strip(): rows[:]=[]
        else:
            set_value(self.profile,key,value)
        self.library.save_profile(self.profile)
    def import_resume(self):
        f,_=QFileDialog.getOpenFileName(self,"选择简历","","Resume (*.pdf *.docx *.txt)");
        if not f:return
        try: parsed,mode=parse_resume(f,self.profile.get("llm",{})); self.library.replace_resume(f,self.profile); items=[]
        except Exception as e: self.message(str(e),True); return
        for group,vals in parsed.items():
            if group=="resumeSections": continue
            for key,val in vals.items():
                path=f"{group}.{key}"; 
                if val: items.append({"label":path,"value":str(val),"context":"简历解析","selected":True,"path":path,"kind":"common"})
        for section,entries in parsed.get("resumeSections",{}).items():
            for entry in entries or []:
                if not isinstance(entry,dict) or not any(str(value).strip() for value in entry.values()): continue
                title=entry.get("name") or entry.get("school") or entry.get("company") or SECTION_FORMS.get(section,(section,[]))[0]
                items.append({"label":str(title),"value":json.dumps(entry,ensure_ascii=False),"context":f"简历解析 / {SECTION_FORMS.get(section,(section,[]))[0]}","selected":True,"section":section,"_entry":entry,"kind":"section"})
        dialog=ChooseDialog(items,self) if items else None
        if dialog and dialog.exec()==QDialog.DialogCode.Accepted:
            self.apply_profile_items(dialog.chosen()); self.refresh_sections(); self.refresh_attachments()
        self.message(f"简历已解析（{mode}）")
    def collect_page(self): self.scan_preview(lambda x:self.classify_collected(x,False)) if "preview" in self.url.text().lower() else self.scan_form(lambda x:self.classify_collected(x,False))
    def auto_import(self): self.scan_preview(self.auto_save) if "preview" in self.url.text().lower() else self.scan_form(self.auto_save)
    def record_current_page(self):
        url=self.browser.url().toString(); host=self.browser.url().host() or "未命名公司"; preset={"company":host,"position":"","channel":url,"status":"准备投递","appliedDate":datetime.now().strftime("%Y-%m-%d"),"failureReason":"","notes":f"由投递助手自动创建；页面标题：{self.browser.title()}"}; self.edit_record(-1,preset)
    def export_current_page(self):
        self.scan_preview(lambda x:self.classify_collected(x,True)) if "preview" in self.url.text().lower() else self.scan_form(lambda x:self.classify_collected(x,True))

    def _page_item_has_profile_value(self, item):
        """Return whether a classified page field already has a saved answer."""
        if not isinstance(item, dict) or item.get("target") == "requirement":
            return False
        target = str(item.get("target") or "")
        if target == "common":
            path = str(item.get("path") or "").strip()
            if not path:
                return False
            if path == "resumeSections.personalSummary":
                rows = (self.profile.get("resumeSections", {}) or {}).get("personalSummary", [])
                return any(isinstance(row, dict) and str(row.get("description") or "").strip() for row in rows)
            return bool(get_value(self.profile, path).strip())
        if target == "extra":
            context = str(item.get("context") or "").strip()
            label = str(item.get("label") or item.get("field") or item.get("name") or "").strip()
            identity = field_identity({"context": context, "label": label, "name": item.get("name")})
            normalized_identity = "".join(f"{context}::{label}".lower().split())
            for row in self.profile.get("extraFields", []) or []:
                if not isinstance(row, dict) or not str(row.get("value") or "").strip():
                    continue
                if row.get("identity") in {identity, normalized_identity}:
                    return True
                same_context = "".join(str(row.get("context") or "").lower().split()) == "".join(context.lower().split())
                same_label = "".join(str(row.get("label") or "").lower().split()) == "".join(label.lower().split())
                if same_context and same_label:
                    return True
            return False
        if target == "section":
            section = str(item.get("section") or "").strip()
            rows = (self.profile.get("resumeSections", {}) or {}).get(section, [])
            if not isinstance(rows, list) or not rows:
                return False
            value = "".join(str(item.get("value") or "").lower().split())
            context = "".join(str(item.get("context") or "").lower().split())
            label = "".join(str(item.get("label") or item.get("field") or "").lower().split())
            for row in rows:
                if not isinstance(row, dict) or not any(str(v or "").strip() for v in row.values()):
                    continue
                row_context = "".join(str(row.get("context") or "").lower().split())
                row_values = ["".join(str(v or "").lower().split()) for v in row.values()]
                if value and any(value == candidate or (len(value) >= 4 and value in candidate) for candidate in row_values):
                    return True
                if context and row_context and (context == row_context or context in row_context or row_context in context):
                    if not label or any(label in key.lower() for key in row):
                        return True
            return False
        return False

    def _decorate_page_items(self, items):
        decorated=[]
        for item in items or []:
            row=dict(item)
            has_value=self._page_item_has_profile_value(row)
            row["profileHasValue"]=has_value
            row["profileState"]="资料库已有" if has_value else "资料库缺少"
            decorated.append(row)
        return decorated
    def scan_form(self,cb):
        # The recruitment form may live inside a cross-origin iframe. A direct
        # runJavaScript call cannot see that frame, while the injected bridge can.
        self.browser.page().runJavaScript(SNAPSHOT_SCRIPT, lambda direct: self._scan_frames(cb, parse_js_json(direct)))
    def _scan_frames(self, cb, direct):
        self.browser.page().runJavaScript(FRAME_SCAN_SCRIPT)
        # Some portals create the resume iframe after Angular finishes its
        # first render. Send a second scan request without clearing earlier
        # results, then merge all frame responses.
        QTimer.singleShot(650, lambda: self.browser.page().runJavaScript(FRAME_SCAN_SCRIPT))
        QTimer.singleShot(1800, lambda: self.browser.page().runJavaScript(FRAME_RESULTS_SCRIPT, lambda bridged: self._merge_scan(cb, direct, parse_js_json(bridged))))
    def _merge_scan(self, cb, direct, bridged):
        direct_fields=direct.get("fields",[]) if isinstance(direct,dict) else []
        frame_fields=bridged.get("fields",[]) if isinstance(bridged,dict) else []
        # The bridge can report the top document twice (two delayed scans) and
        # portals frequently keep hidden template controls in DOM. Normalize
        # before classification so the user sees one trustworthy row per field.
        fields=clean_page_fields(frame_fields or direct_fields)
        cb({"title": (direct or {}).get("title") or (bridged or {}).get("title", ""), "fields": fields})
    def scan_preview(self,cb):
        self.browser.page().runJavaScript(PREVIEW_DATA_SCRIPT,lambda x: self._preview_done(cb,parse_js_json(x)))
    def _preview_done(self,cb,data):
        if data.get("fields"): cb(data); return
        self.browser.page().toHtml(lambda h: cb(html_preview(h)))
    def fill_page(self): self.scan_form(lambda snapshot: self.fill_snapshot(snapshot, only_empty=False))
    def fill_page_only_empty(self): self.scan_form(lambda snapshot: self.fill_snapshot(snapshot, only_empty=True))
    def fill_snapshot(self,snapshot, only_empty=False):
        fields=snapshot.get("fields",[]) if isinstance(snapshot,dict) else []
        if not fields:
            self.message("当前页面没有扫描到可填写控件。请确认已进入简历编辑页，并等待页面加载完成后再试。",True); return
        local=local_mapping(fields,self.profile); settings=self.profile.get("llm",{})
        # Always apply deterministic answers first. A slow model must never
        # block known values such as name, phone, email, birthday and gender.
        self.apply_fill_mappings(local,[],only_empty,fields=fields)
        if not settings.get("apiKey") or not settings.get("endpoint"):
            return
        known={item.get("index") for item in local}
        pending=[dict(field, _resume_index=index) for index,field in enumerate(fields) if index not in known]
        if not pending:
            return
        self.statusBar().showMessage(f"已先填写 {len(local)} 项，正在分批识别其余 {len(pending)} 项…")
        worker=MappingWorker(self.profile,pending); worker.setAutoDelete(False); self.mapping_worker=worker
        worker.signals.finished.connect(lambda remote:self.apply_fill_mappings(local,remote,only_empty,fields=fields))
        worker.signals.error.connect(lambda error:self.message(f"模型补充识别失败，但本地已填写 {len(local)} 项：{error}",True))
        self.pool.start(worker)
    def apply_fill_mappings(self,local,remote,only_empty=False,fields=None,error=""):
        fields=fields or []
        merged={item.get("index"):item for item in local if isinstance(item,dict) and isinstance(item.get("index"),int)}
        for item in remote or []:
            if isinstance(item,dict) and isinstance(item.get("index"),int): merged[item["index"]]=item
        mappings=list(merged.values())
        if error:
            self.message(f"模型识别失败；已扫描 {len(fields)} 个字段，本地可确定 {len(local)} 项。原因：{error}",True)
        elif not mappings:
            self.message(f"已扫描 {len(fields)} 个字段，但没有得到可用答案。请检查个人资料是否有对应内容，或先在模型设置中测试接口。",True)
        def done(result):
            result=parse_js_json(result)
            filled=result.get("filled",0); skipped=result.get("skipped",0)
            self.message(f"扫描 {len(fields)} 项；发送 {len(mappings)} 项；实际填写 {filled} 项，跳过 {skipped} 项。请检查官网页面后再提交。")
        self.browser.page().runJavaScript(fill_script(mappings, only_empty), done)
    def classify_collected(self,snapshot,export_only=False):
        fields=clean_page_fields(snapshot.get("fields",[]) if isinstance(snapshot,dict) else [])
        fields=[field for field in fields if str(field.get("label") or field.get("name") or "").strip()]
        if not fields:
            self.message("没有读取到页面字段。请确认已进入简历编辑页或信息预览页。",True); return
        self.statusBar().showMessage(f"已读取 {len(fields)} 个字段，正在用本地规则和模型判断归类、上下文与填写状态…")
        worker=PageClassificationWorker(self.profile,fields); worker.setAutoDelete(False); self.page_classification_worker=worker
        worker.signals.finished.connect(lambda items:self.review_classified_fields(items,snapshot,export_only))
        worker.signals.error.connect(lambda error:self.classification_error(fields,snapshot,export_only,error))
        self.pool.start(worker)
    def classification_error(self,fields,snapshot,export_only,error):
        # classify_page_fields has a local fallback, but retain this path for
        # unexpected parser errors so page data is never discarded.
        fallback=[{"label":str(f.get("label") or f.get("name") or ""),"value":str(f.get("value") or "").strip(),"context":str(f.get("context") or ""),"filled":bool(str(f.get("value") or "").strip()),"category":"公司专属字段","target":"extra" if str(f.get("value") or "").strip() else "requirement","reason":"模型分类未完成，已使用保守本地归档"} for f in fields]
        self.statusBar().showMessage(f"模型分类未完成，已切换为本地审核：{error}",8000); self.review_classified_fields(fallback,snapshot,export_only)
    def review_classified_fields(self,items,snapshot,export_only=False):
        if not items:
            self.message("没有可审核的页面字段。",True); return
        items=self._decorate_page_items(items)
        dialog=PageFieldReviewDialog(items,self,export_only)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        chosen=dialog.chosen()
        if export_only and not chosen:
            self.message("没有选择要导出的字段。", True); return
        if export_only:
            page_url=self.browser.url().toString(); payload={"url":page_url,"title":snapshot.get("title",self.browser.title()),"exportedAt":datetime.now(timezone.utc).isoformat(),"profileId":self.profile.get("profileId",""),"fields":chosen}
            export_stem=self._export_stem(page_url)
            if dialog.export_format_name() == "csv":
                destination=self.library.exports_dir / f"{export_stem}.csv"; self._merge_csv_export(destination,payload)
            else:
                destination=self.library.exports_dir / f"{export_stem}.json"; self._merge_json_export(destination,payload)
            profile_chosen=[item for item in chosen if item.get("profileSelected")]
            if profile_chosen: self.apply_page_field_import(profile_chosen)
            self.refresh_exports()
            sync_note=f"，已确认写入资料库 {len(profile_chosen)} 项" if profile_chosen else "，未写入个人资料库"
            self.message(f"已将本网址的字段合并到 {destination.name}（本次 {len(chosen)} 项{sync_note}）。")
            return
        self.apply_page_field_import(chosen)

    @staticmethod
    def _export_stem(url):
        parts=urlsplit(str(url or "")); host="".join(char if char.isalnum() else "_" for char in (parts.netloc or "company"))[:48] or "company"
        canonical=urlunsplit((parts.scheme.lower(),parts.netloc.lower(),parts.path or "/",parts.query,""))
        digest=hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
        return f"{host}-{digest}"

    @staticmethod
    def _export_field_key(field):
        return "|".join("".join(str(field.get(key) or "").lower().split()) for key in ("context","label","name","path","section"))

    def _merge_json_export(self, destination, payload):
        existing={}
        if destination.is_file():
            try: existing=json.loads(destination.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError): existing={}
        old_fields=existing.get("fields",[]) if isinstance(existing,dict) else []
        merged={self._export_field_key(row):dict(row) for row in old_fields if isinstance(row,dict) and self._export_field_key(row)}
        for row in payload.get("fields",[]):
            if isinstance(row,dict): merged[self._export_field_key(row) or hashlib.sha1(json.dumps(row,ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()]=dict(row)
        record=dict(existing) if isinstance(existing,dict) else {}; record.update({key:payload[key] for key in ("url","title","profileId")}); record.setdefault("firstExportedAt",payload["exportedAt"]); record["updatedAt"]=payload["exportedAt"]; record["exportCount"]=int(record.get("exportCount",0) or 0)+1; record["fields"]=list(merged.values()); self.library.export_record(str(destination),record)

    def _merge_csv_export(self, destination, payload):
        fieldnames=["url","title","category","context","label","value","filled","profileHasValue","profileState","target","destination","profileSelected","reviewAction","reason","pageUrl","exportedAt"]
        existing=[]
        if destination.is_file():
            try:
                with destination.open("r",newline="",encoding="utf-8-sig") as handle: existing=list(csv.DictReader(handle))
            except (OSError, csv.Error): existing=[]
        merged={self._export_field_key(row):dict(row) for row in existing if self._export_field_key(row)}
        for row in payload.get("fields",[]):
            if not isinstance(row,dict): continue
            record={key:row.get(key,"") for key in fieldnames}; record.update({"url":payload.get("url",""),"title":payload.get("title",""),"exportedAt":payload.get("exportedAt","")}); merged[self._export_field_key(record) or hashlib.sha1(json.dumps(record,ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()]=record
        with destination.open("w",newline="",encoding="utf-8-sig") as handle:
            writer=csv.DictWriter(handle,fieldnames=fieldnames); writer.writeheader(); writer.writerows(merged.values())
    def apply_page_field_import(self,items):
        if not items:
            self.message("没有选择要保存的字段。",True); return
        now=datetime.now(timezone.utc).isoformat(); source=self.browser.url().host() or "网页采集"; page_url=self.browser.url().toString()
        profile_items=[]; requirements=[]
        for item in items:
            item=dict(item); item.update({"source":source,"pageUrl":page_url,"updatedAt":now})
            if item.get("target")=="requirement" or not str(item.get("value") or "").strip(): requirements.append(item)
            else: profile_items.append(item)
        if profile_items: self.apply_profile_items(profile_items)
        knowledge=self.profile.setdefault("knowledgeBase",{}); records=knowledge.setdefault("problemSummaries",[])
        for item in requirements:
            label=str(item.get("label") or "公司要求").strip(); context=str(item.get("context") or "").strip(); identity="".join(f"{source}::{context}::{label}".lower().split())
            record={"id":str(uuid.uuid4()),"kind":"companyRequirement","identity":identity,"title":f"待补资料：{label}","question":label,"answer":"","status":"待补充","category":str(item.get("category") or "公司专属字段"),"context":context,"source":source,"pageUrl":page_url,"reason":str(item.get("reason") or ""),"createdAt":now,"updatedAt":now}
            old=next((row for row in records if isinstance(row,dict) and row.get("identity")==identity),None)
            if old: old.update(record); record["id"]=old.get("id",record["id"])
            else: records.append(record)
        self.library.save_profile(self.profile); self.refresh_custom_fields(); self.refresh_sections(); self.refresh_knowledge()
        self.message(f"已写入资料库 {len(profile_items)} 项；已记录待补公司要求 {len(requirements)} 项。")
    def auto_save(self,snapshot):
        """Import only unknown answers; this path must never overwrite a saved answer."""
        existing={item.get("identity") for item in self.profile.get("extraFields",[]) if isinstance(item,dict)}; added=[]
        for field in contextualize(snapshot.get("fields",[])):
            label=str(field.get("label") or "").strip(); value=str(field.get("value") or "").strip(); identity=field_identity(field)
            if label and value and identity and identity not in existing:
                added.append({"identity":identity,"label":label,"context":field.get("context",""),"value":value,"source":self.browser.url().host(),"updatedAt":datetime.now(timezone.utc).isoformat()}); existing.add(identity)
        if not added: self.message("没有发现尚未保存的新字段。",True); return
        self.profile.setdefault("extraFields",[]).extend(added); self.library.save_profile(self.profile); self.refresh_custom_fields(); self.message(f"已自动导入 {len(added)} 个此前为空的新字段。")
    def new_record(self): self.edit_record(-1)
    def edit_record(self,row=None,preset=None):
        row=self.records.currentRow() if row is None else row
        old=preset if preset is not None else (self.applications[row] if row is not None and row>=0 and row<len(self.applications) else {})
        d=QDialog(self); d.setWindowTitle("编辑投递记录"); d.resize(700,620); d.setMinimumSize(640,560)
        form=QFormLayout(d); form.setContentsMargins(28,26,28,26); form.setVerticalSpacing(14); fields={}
        fields["company"]=QLineEdit(str(old.get("company",""))); form.addRow("公司",fields["company"])
        fields["position"]=QLineEdit(str(old.get("position",""))); form.addRow("职位",fields["position"])
        fields["channel"]=QLineEdit(str(old.get("channel",""))); form.addRow("申请入口",fields["channel"])
        status=QComboBox(); status.addItems(APPLICATION_STATUSES); old_status=str(old.get("status","准备投递"))
        if old_status and old_status not in APPLICATION_STATUSES: status.insertItem(0,old_status)
        status.setCurrentText(old_status or "准备投递"); fields["status"]=status; form.addRow("投递状态",status)
        date=QDateEdit(); date.setCalendarPopup(True); date.setDisplayFormat("yyyy-MM-dd"); date.setMinimumDate(QDate(1900,1,1)); date.setSpecialValueText("未填写")
        parsed=QDate.fromString(str(old.get("appliedDate","")),"yyyy-MM-dd"); date.setDate(parsed if parsed.isValid() else QDate.currentDate()); fields["appliedDate"]=date; form.addRow("投递日期",date)
        reason=QComboBox(); reason.setEditable(True); reason.addItems(FAILURE_REASONS); old_reason=str(old.get("failureReason","")); reason.setCurrentText(old_reason or "未填写"); fields["failureReason"]=reason; form.addRow("失败原因",reason)
        notes=QTextEdit(str(old.get("notes",""))); notes.setMinimumHeight(110); fields["notes"]=notes; form.addRow("备注",notes)
        bb=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)); bb.accepted.connect(d.accept); bb.rejected.connect(d.reject); form.addRow(bb)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        item={"company":fields["company"].text().strip(),"position":fields["position"].text().strip(),"channel":fields["channel"].text().strip(),"status":fields["status"].currentText().strip(),"appliedDate":fields["appliedDate"].date().toString("yyyy-MM-dd"),"failureReason":fields["failureReason"].currentText().strip(),"notes":fields["notes"].toPlainText().strip(),"id":old.get("id",str(uuid.uuid4()))}
        if item["failureReason"]=="未填写": item["failureReason"]=""
        if row is not None and row>=0 and row<len(self.applications): self.applications[row]=item
        else: self.applications.append(item)
        self.library.save_applications(self.applications); self.refresh_records()
    def refresh_records(self):
        if not hasattr(self,"records"):return
        self.records.setRowCount(len(self.applications))
        for r,x in enumerate(self.applications):
            for c,k in enumerate(("company","position","channel","status","appliedDate","failureReason","notes")): self.records.setItem(r,c,QTableWidgetItem(str(x.get(k,""))))
    def export_records(self):
        f,_=QFileDialog.getSaveFileName(self,"导出投递记录",str(self.library.exports_dir/"投递记录.csv"),"CSV (*.csv)");
        if not f:return
        with open(f,"w",newline="",encoding="utf-8-sig") as h:
            w=csv.DictWriter(h,fieldnames=["company","position","channel","status","appliedDate","failureReason","notes"]); w.writeheader(); w.writerows(self.applications)
        self.message("投递记录已导出。")
    def message(self,text,error=False): self.statusBar().showMessage(text,8000); QMessageBox.warning(self,APP_NAME,text) if error else None

if __name__=="__main__":
    app=QApplication(sys.argv); win=App(); win.show(); sys.exit(app.exec())


