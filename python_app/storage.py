"""Local, draft-friendly storage for ResumeSubmitter."""
from __future__ import annotations

import copy
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from profile_schema import SCHEMA_VERSION

DEFAULT_PROFILE = {
    "schemaVersion": SCHEMA_VERSION,
    "profileId": "",
    "updatedAt": "",
    "sync": {"workspaceId": "", "syncVersion": 0, "lastSyncedAt": "", "deletedAt": None},
    "basics": {"name": "", "email": "", "phone": "", "city": "", "address": "", "linkedin": "", "website": ""},
    "application": {"education": "", "school": "", "degree": "", "major": "", "graduationYear": "", "company": "", "title": "", "yearsExperience": "", "workAuthorization": "", "sponsorship": ""},
    "custom": {"gender": "", "birthday": "", "ethnicity": "", "politicalStatus": "", "researchDirection": "", "courses": "", "hobbies": "", "expectedSalary": "", "noticePeriod": "", "idNumber": ""},
    "privacy": {"allowSensitiveAutofill": False},
    "llm": {"endpoint": "https://api.openai.com/v1/chat/completions", "model": "gpt-4o-mini", "apiKey": ""},
    "attachments": [],
    "extraFields": [],
    "knowledgeBase": {
        "selfIntroduction": "",
        "careerPreferences": "",
        "problemSummaries": [],
        "interviewAnswers": [],
        "notes": "",
    },
    "resumeSections": {
        "workExperience": [], "projects": [], "educationEntries": [], "skills": [], "languages": [],
        "certificates": [], "honors": [], "training": [], "publications": [], "portfolios": [], "personalSummary": [],
    },
}


def merge(default: dict, value: dict) -> dict:
    result = copy.deepcopy(default)
    for key, item in (value or {}).items():
        if isinstance(item, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], item)
        else:
            result[key] = item
    return result


class LocalLibrary:
    """Keeps unfinished profile data locally and writes every change atomically."""

    def __init__(self) -> None:
        # Keep user-provided resumes outside the installed app directory, so
        # upgrades/uninstalls cannot accidentally remove them. The location can
        # be overridden for backups or a portable drive when needed.
        root = os.environ.get("RESUME_SUBMITTER_LIBRARY")
        self.root = Path(root) if root else (Path.home() / "Documents" / "简历投递器资料库")
        self.attachments_dir = self.root / "attachments"
        self.exports_dir = self.root / "company-records"
        self.root.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.profile_file = self.root / "profile.json"
        self.applications_file = self.root / "applications.json"

    def load_profile(self) -> dict:
        try:
            profile = merge(DEFAULT_PROFILE, json.loads(self.profile_file.read_text(encoding="utf-8")))
            changed = self._migrate_single_legacy_resume(profile)
            if self._ensure_profile_metadata(profile):
                changed = True
            if self._repair_attachment_references(profile):
                changed = True
            if changed:
                self.save_profile(profile)
            return profile
        except (OSError, json.JSONDecodeError):
            # Even a brand-new library must receive a stable profileId.  This
            # identifier is used by future cloud/shared-workspace sync and is
            # also included in page exports, so returning the raw default here
            # caused the first export to have an empty profileId.
            profile = copy.deepcopy(DEFAULT_PROFILE)
            self._ensure_profile_metadata(profile)
            self.save_profile(profile)
            return profile

    def save_profile(self, profile: dict) -> None:
        self._ensure_profile_metadata(profile)
        profile["updatedAt"] = datetime.now(timezone.utc).isoformat()
        self._write_json(self.profile_file, profile)

    @staticmethod
    def _ensure_profile_metadata(profile: dict) -> bool:
        """Add stable IDs/timestamps so a future cloud sync can merge records safely."""
        changed = False
        if not profile.get("profileId"):
            profile["profileId"] = str(uuid.uuid4()); changed = True
        if int(profile.get("schemaVersion", 0) or 0) < SCHEMA_VERSION:
            profile["schemaVersion"] = SCHEMA_VERSION
            changed = True
        profile.setdefault("sync", {"workspaceId": "", "syncVersion": 0, "lastSyncedAt": "", "deletedAt": None})
        profile.setdefault("knowledgeBase", copy.deepcopy(DEFAULT_PROFILE["knowledgeBase"]))
        for rows in (profile.get("resumeSections", {}) or {}).values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    if not row.get("id"):
                        row["id"] = str(uuid.uuid4()); changed = True
                    if not row.get("updatedAt"):
                        row["updatedAt"] = datetime.now(timezone.utc).isoformat(); changed = True
        for rows in (profile.get("extraFields", []), profile.get("knowledgeBase", {}).get("problemSummaries", []), profile.get("knowledgeBase", {}).get("interviewAnswers", [])):
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    if not row.get("id"):
                        row["id"] = str(uuid.uuid4()); changed = True
                    if not row.get("updatedAt"):
                        row["updatedAt"] = datetime.now(timezone.utc).isoformat(); changed = True
        return changed

    def load_applications(self) -> list[dict]:
        try:
            data = json.loads(self.applications_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def save_applications(self, applications: list[dict]) -> None:
        self._write_json(self.applications_file, applications)

    @staticmethod
    def _migrate_single_legacy_resume(profile: dict) -> bool:
        """Mark a legacy one-file library entry as the primary resume."""
        rows = profile.get("attachments", [])
        if len(rows) != 1 or not isinstance(rows[0], dict):
            return False
        row = rows[0]
        if row.get("kind") or row.get("isResume") is not None:
            return False
        name = str(row.get("originalName", "")).lower()
        if not name.endswith((".pdf", ".doc", ".docx", ".txt")):
            return False
        row["kind"] = "resume"
        row["isResume"] = True
        profile["primaryResume"] = {"originalName": row.get("originalName", ""), "storedName": row.get("storedName", "")}
        return True

    def replace_resume(self, filename: str, profile: dict) -> dict:
        """Store exactly one primary resume and replace any older resume copy."""
        source = Path(filename)
        if not source.is_file():
            raise OSError(f"简历文件不存在：{source.name}")
        attachments = profile.setdefault("attachments", [])
        def looks_like_resume(entry):
            if entry.get("kind") == "resume" or entry.get("isResume") is True:
                return True
            name = str(entry.get("originalName", "")).lower()
            return "简历" in name or "resume" in name
        old_rows = [entry for entry in attachments if looks_like_resume(entry)]
        # Reuse the existing stored copy when the same source was selected again.
        same = next((entry for entry in old_rows if entry.get("originalName") == source.name and
                     (self.attachments_dir / str(entry.get("storedName", ""))).is_file()), None)
        if same:
            same["kind"] = "resume"
            same["isResume"] = True
            self.save_profile(profile)
            return profile
        stored_name = f"resume-{int(datetime.now().timestamp() * 1000)}-{source.name}"
        destination = self.attachments_dir / stored_name
        shutil.copy2(source, destination)
        if not destination.is_file() or destination.stat().st_size != source.stat().st_size:
            raise OSError(f"简历复制未完成：{source.name}")
        kept = []
        for entry in old_rows:
            old_path = self.attachments_dir / str(entry.get("storedName", ""))
            # Old imported resumes are safe to remove because this operation
            # explicitly replaces the single primary resume.
            if old_path != destination and old_path.is_file():
                try:
                    old_path.unlink()
                except OSError:
                    pass
        for entry in attachments:
            if entry not in old_rows:
                kept.append(entry)
        kept.append({"originalName": source.name, "storedName": stored_name,
                     "kind": "resume", "isResume": True,
                     "addedAt": datetime.now(timezone.utc).isoformat()})
        profile["attachments"] = kept
        profile["primaryResume"] = {"originalName": source.name, "storedName": stored_name}
        self.save_profile(profile)
        return profile

    def import_files(self, selected: list[str], profile: dict) -> dict:
        known = {entry.get("originalName") for entry in profile.get("attachments", [])}
        for filename in selected:
            source = Path(filename)
            if not source.is_file():
                continue
            # Re-parsing the same resume should refresh its structured data, not
            # create another physical copy of an unchanged attachment.
            existing = next((entry for entry in profile.get("attachments", []) if entry.get("originalName") == source.name and (self.attachments_dir / str(entry.get("storedName", ""))).is_file()), None)
            if existing:
                continue
            stored_name = f"{int(datetime.now().timestamp() * 1000)}-{source.name}"
            destination = self.attachments_dir / stored_name
            shutil.copy2(source, destination)
            # Never point profile.json to a file unless the copy is actually
            # present. This also catches interrupted/locked file operations.
            if not destination.is_file() or destination.stat().st_size != source.stat().st_size:
                raise OSError(f"附件复制未完成：{source.name}")
            if source.name in known:
                profile["attachments"] = [entry for entry in profile["attachments"] if entry.get("originalName") != source.name]
            profile.setdefault("attachments", []).append({"originalName": source.name, "storedName": stored_name, "kind": "attachment", "isResume": False, "addedAt": datetime.now(timezone.utc).isoformat()})
            known.add(source.name)
        self.save_profile(profile)
        return profile

    def export_record(self, filename: str, record: dict) -> None:
        self._write_json(Path(filename), record)

    def _repair_attachment_references(self, profile: dict) -> bool:
        """Recovers a prior copy if an interrupted import left only a bad name."""
        changed = False
        for entry in profile.get("attachments", []):
            stored = self.attachments_dir / str(entry.get("storedName", ""))
            original = str(entry.get("originalName", ""))
            if stored.is_file() or not original:
                continue
            candidates = sorted(self.attachments_dir.glob(f"*-{original}"), key=lambda item: item.stat().st_mtime, reverse=True)
            if candidates:
                entry["storedName"] = candidates[0].name
                changed = True
        return changed

    @staticmethod
    def _write_json(destination: Path, content: dict) -> None:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
