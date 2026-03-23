"""
Database storage service - SQLite backend with same API as FileStorageService.
"""
import json
import os
import uuid
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from sqlalchemy.orm import Session

# Import from parent (backend) so we can run from backend dir
import sys
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from backend.db.db import Base
from backend.db.db import get_engine
from backend.models.roles import Role as RoleModel
from backend.models.job_descriptions import JobDescription as JDModel
from backend.models.candidates import Candidate as CandidateModel
from backend.models.hr_briefings import HRBriefing as HRBriefingModel
from backend.models.role_hr_briefings import RoleHRBriefing
from backend.models.interviews import Interview as InterviewModel
from backend.models.evaluation_chats import EvaluationChat as EvaluationChatModel
from backend.models.consent_templates import ConsentTemplate as ConsentTemplateModel
from backend.services.audio_transcription import resolve_hr_briefing_audio_extension
from sqlalchemy.orm import sessionmaker


def _resolve_data_dir() -> Path:
    backend_dir = Path(__file__).resolve().parent.parent
    data_dir = backend_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _json_loads(s: Optional[str], default=None):
    if s is None or s == "":
        return default if default is not None else {}
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else {}


def _json_dumps(obj) -> str:
    if obj is None:
        return "null"
    return json.dumps(obj, ensure_ascii=False)


def _to_str(val: Any, max_len: int = 500) -> str:
    """Coerce value to string for DB text columns. Lists (e.g. multiple job titles) are joined with ', '."""
    if val is None:
        return ""
    if isinstance(val, list):
        s = ", ".join(str(x).strip() for x in val if x is not None and str(x).strip())
        return (s[:max_len] + "…") if len(s) > max_len else s
    s = str(val).strip()
    return (s[:max_len] + "…") if len(s) > max_len else s


class DatabaseStorageService:
    def __init__(self):
        self.base_dir = _resolve_data_dir()
        self.engine = get_engine()
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.roles_dir = self.base_dir / "roles"
        self.roles_dir.mkdir(parents=True, exist_ok=True)

    def init_db(self):
        """Create all tables and add any missing columns (e.g. created_by_user_id)."""
        Base.metadata.create_all(bind=self.engine)
        from sqlalchemy import text
        try:
            with self.engine.connect() as conn:
                conn.execute(text("ALTER TABLE roles ADD COLUMN created_by_user_id VARCHAR(255)"))
                conn.commit()
        except Exception:
            pass  # column likely already exists

    def _get_session(self) -> Session:
        return self.SessionLocal()

    def _get_role_dir(self, role_id: str) -> Path:
        role_dir = self.roles_dir / role_id
        role_dir.mkdir(parents=True, exist_ok=True)
        return role_dir

    def _get_candidate_dir(self, role_id: str, candidate_id: str) -> Path:
        candidate_dir = self._get_role_dir(role_id) / "candidates" / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        return candidate_dir

    # ---------- Roles ----------
    def create_role(self, role_data: Dict[str, Any]) -> str:
        role_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        req_fields = role_data.get("candidate_requirement_fields") or [
            "expected_salary", "earliest_start_date", "work_authorization",
            "location_preferences", "notice_period"
        ]
        eval_criteria = role_data.get("evaluation_criteria") or [
            "Must-haves", "Nice-to-haves", "Competencies", "Technical criteria", "Behavioral criteria"
        ]
        with self._get_session() as session:
            r = RoleModel(
                id=role_id,
                title=role_data.get("title", ""),
                status=role_data.get("status", "New"),
                created_at=now,
                updated_at=now,
                candidate_requirement_fields=_json_dumps(req_fields),
                evaluation_criteria=_json_dumps(eval_criteria),
                created_by_user_id=role_data.get("created_by_user_id"),
            )
            session.add(r)
            session.commit()
        return role_id

    def get_all_roles(self) -> List[Dict[str, Any]]:
        with self._get_session() as session:
            roles = session.query(RoleModel).all()
            result = []
            for r in roles:
                candidates = session.query(CandidateModel).filter(CandidateModel.role_id == r.id).all()
                role_dict = {
                    "id": r.id,
                    "title": r.title,
                    "status": r.status,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                    "created_by_user_id": getattr(r, "created_by_user_id", None),
                    "candidates_count": len(candidates),
                    "successful_candidates_count": sum(1 for c in candidates if c.sent_to_client),
                    "hiring_budget": r.hiring_budget,
                    "vacancies": r.vacancies,
                    "urgency": r.urgency,
                    "timeline": r.timeline,
                    "candidate_requirement_fields": _json_loads(r.candidate_requirement_fields, []),
                    "evaluation_criteria": _json_loads(r.evaluation_criteria, []),
                }
                role_dict["outreach_count"] = sum(1 for c in candidates if (c.column or "outreach") == "outreach")
                role_dict["follow_up_count"] = sum(1 for c in candidates if c.column == "follow-up")
                role_dict["evaluation_count"] = sum(1 for c in candidates if c.column == "evaluation")
                role_dict["sent_to_client_count"] = sum(1 for c in candidates if c.sent_to_client)
                role_dict["not_pushing_forward_count"] = sum(1 for c in candidates if c.not_pushing_forward)
                role_dict["has_jd"] = session.query(JDModel).filter(JDModel.role_id == r.id).first() is not None
                role_dict["has_hr_briefing"] = session.query(RoleHRBriefing).filter(RoleHRBriefing.role_id == r.id).first() is not None
                result.append(role_dict)
            return result

    def get_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        with self._get_session() as session:
            r = session.query(RoleModel).filter(RoleModel.id == role_id).first()
            if not r:
                return None
            return {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "created_by_user_id": getattr(r, "created_by_user_id", None),
                "hiring_budget": r.hiring_budget,
                "vacancies": r.vacancies,
                "urgency": r.urgency,
                "timeline": r.timeline,
                "candidate_requirement_fields": _json_loads(r.candidate_requirement_fields, []),
                "evaluation_criteria": _json_loads(r.evaluation_criteria, []),
            }

    def update_role(self, role_id: str, updates: Dict[str, Any]) -> bool:
        with self._get_session() as session:
            r = session.query(RoleModel).filter(RoleModel.id == role_id).first()
            if not r:
                return False
            for k, v in updates.items():
                if k in ("candidate_requirement_fields", "evaluation_criteria") and isinstance(v, list):
                    setattr(r, k, _json_dumps(v))
                elif hasattr(r, k):
                    setattr(r, k, v)
            r.updated_at = datetime.now().isoformat()
            session.commit()
        return True

    def delete_role(self, role_id: str) -> bool:
        with self._get_session() as session:
            r = session.query(RoleModel).filter(RoleModel.id == role_id).first()
            if not r:
                return False
            session.delete(r)
            session.commit()
        role_dir = self.roles_dir / role_id
        if role_dir.exists():
            shutil.rmtree(role_dir)
        return True

    # ---------- Job Description ----------
    def save_jd(self, role_id: str, file) -> Path:
        role_dir = self._get_role_dir(role_id)
        jd_path = role_dir / "jd.pdf"
        with open(jd_path, "wb") as f:
            f.write(file.file.read())
        with self._get_session() as session:
            jd = session.query(JDModel).filter(JDModel.role_id == role_id).first()
            if jd:
                jd.jd_file_path = str(jd_path)
            else:
                session.add(JDModel(role_id=role_id, jd_file_path=str(jd_path)))
            session.commit()
        return jd_path

    def save_parsed_jd(self, role_id: str, parsed_jd: Dict[str, Any]):
        with self._get_session() as session:
            jd = session.query(JDModel).filter(JDModel.role_id == role_id).first()
            if not jd:
                jd = JDModel(role_id=role_id)
                session.add(jd)
            # Normalize string fields: LLM may return lists (e.g. multiple job titles)
            jd.job_title = _to_str(parsed_jd.get("job_title", ""), max_len=500)
            jd.job_summary = _to_str(parsed_jd.get("job_summary", ""), max_len=10000)
            jd.responsibilities = _json_dumps(parsed_jd.get("responsibilities", []))
            jd.requirements = _json_dumps(parsed_jd.get("requirements", []))
            jd.skills = _json_dumps(parsed_jd.get("skills", []))
            session.commit()

    def get_parsed_jd(self, role_id: str) -> Optional[Dict[str, Any]]:
        with self._get_session() as session:
            jd = session.query(JDModel).filter(JDModel.role_id == role_id).first()
            if not jd:
                return None
            return {
                "job_title": jd.job_title,
                "job_summary": jd.job_summary,
                "responsibilities": _json_loads(jd.responsibilities, []),
                "requirements": _json_loads(jd.requirements, []),
                "skills": _json_loads(jd.skills, []),
            }

    def update_parsed_jd(self, role_id: str, jd_data: Dict[str, Any]):
        existing = self.get_parsed_jd(role_id) or {}
        existing.update(jd_data)
        self.save_parsed_jd(role_id, existing)

    # ---------- Candidates ----------
    def save_candidate_pdf(self, role_id: str, file) -> tuple:
        candidate_id = str(uuid.uuid4())
        candidate_dir = self._get_candidate_dir(role_id, candidate_id)
        pdf_path = candidate_dir / "resume.pdf"
        with open(pdf_path, "wb") as f:
            f.write(file.file.read())
        return pdf_path, candidate_id

    def create_candidate(self, role_id: str, candidate_data: Dict[str, Any], candidate_id: str = None) -> str:
        if not candidate_id:
            candidate_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        checklist = {
            "consent_form_sent": False,
            "consent_form_received": False,
            "updated_cv_received": False,
            "screening_interview_scheduled": False,
            "screening_interview_completed": False,
        }
        with self._get_session() as session:
            c = CandidateModel(
                id=candidate_id,
                role_id=role_id,
                name=candidate_data.get("name", ""),
                summary=candidate_data.get("summary", ""),
                skills=_json_dumps(candidate_data.get("skills", [])),
                experience=candidate_data.get("experience", ""),
                parsed_insights=_json_dumps(candidate_data.get("parsed_insights", {})),
                column="outreach",
                color="amber-transparent",
                created_at=now,
                updated_at=now,
                checklist=_json_dumps(checklist),
            )
            session.add(c)
            session.commit()
        return candidate_id

    def get_candidates(self, role_id: str) -> List[Dict[str, Any]]:
        with self._get_session() as session:
            candidates = session.query(CandidateModel).filter(CandidateModel.role_id == role_id).all()
            return [self._candidate_to_dict(c) for c in candidates]

    def _candidate_to_dict(self, c: CandidateModel) -> Dict[str, Any]:
        return {
            "id": c.id,
            "name": c.name or "",
            "summary": c.summary or "",
            "skills": _json_loads(c.skills, []),
            "experience": c.experience or "",
            "parsed_insights": _json_loads(c.parsed_insights, {}),
            "column": c.column or "outreach",
            "color": c.color or "amber-transparent",
            "created_at": c.created_at,
            "updated_at": c.updated_at,
            "outreach_sent": c.outreach_sent or False,
            "outreach_message": c.outreach_message,
            "checklist": _json_loads(c.checklist, {}),
            "consent_form_sent": c.consent_form_sent or False,
            "consent_form_received": c.consent_form_received or False,
            "email_status": c.email_status,
            "not_pushing_forward": c.not_pushing_forward or False,
            "sent_to_client": c.sent_to_client or False,
            "consent_email": _json_loads(c.consent_email) if c.consent_email else None,
            "consent_reply": _json_loads(c.consent_reply) if c.consent_reply else None,
            "simulated_email": _json_loads(c.simulated_email) if c.simulated_email else None,
            "outreach_reply": _json_loads(c.outreach_reply) if c.outreach_reply else None,
        }

    def get_candidate(self, role_id: str, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self._get_session() as session:
            c = session.query(CandidateModel).filter(
                CandidateModel.role_id == role_id,
                CandidateModel.id == candidate_id
            ).first()
            return self._candidate_to_dict(c) if c else None

    def delete_candidate(self, role_id: str, candidate_id: str) -> bool:
        """Delete a candidate (and their interview via cascade). Returns True if deleted."""
        with self._get_session() as session:
            c = session.query(CandidateModel).filter(
                CandidateModel.role_id == role_id,
                CandidateModel.id == candidate_id
            ).first()
            if not c:
                return False
            session.delete(c)
            session.commit()
        return True

    def update_candidate_status(self, role_id: str, candidate_id: str, status: Dict[str, Any]):
        with self._get_session() as session:
            c = session.query(CandidateModel).filter(
                CandidateModel.role_id == role_id,
                CandidateModel.id == candidate_id
            ).first()
            if not c:
                return
            if status.get("column") == "follow-up" and not _json_loads(c.checklist):
                status["checklist"] = {
                    "consent_form_sent": False,
                    "consent_form_received": False,
                    "updated_cv_received": False,
                    "screening_interview_scheduled": False,
                    "screening_interview_completed": False,
                }
            if "checklist" in status and isinstance(status.get("checklist"), dict):
                current = _json_loads(c.checklist, {})
                current.update(status["checklist"])
                status = {k: v for k, v in status.items() if k != "checklist"}
                c.checklist = _json_dumps(current)
                if current.get("screening_interview_completed"):
                    c.column = "evaluation"
            for k, v in status.items():
                if k in ("skills", "parsed_insights", "checklist", "consent_email", "consent_reply", "simulated_email", "outreach_reply"):
                    if v is not None:
                        setattr(c, k, _json_dumps(v) if isinstance(v, (dict, list)) else v)
                elif hasattr(c, k):
                    setattr(c, k, v)
            c.updated_at = datetime.now().isoformat()
            session.commit()

    def save_outreach_message(self, role_id: str, candidate_id: str, message: str):
        self.update_candidate_status(role_id, candidate_id, {"outreach_message": message, "outreach_sent": True})

    def update_outreach_message(self, role_id: str, candidate_id: str, message: str):
        with self._get_session() as session:
            c = session.query(CandidateModel).filter(
                CandidateModel.role_id == role_id,
                CandidateModel.id == candidate_id
            ).first()
            if c:
                c.outreach_message = message
                c.updated_at = datetime.now().isoformat()
                session.commit()

    def record_outreach_reply(
        self,
        role_id: str,
        candidate_id: str,
        reply_data: Dict[str, Any],
        move_to_follow_up_if_positive: bool = True,
    ) -> Optional[Dict[str, Any]]:
        candidate = self.get_candidate(role_id, candidate_id)
        if not candidate:
            return None
        sentiment = reply_data.get("sentiment", "neutral")
        outreach_reply = {
            "content": reply_data.get("content") or reply_data.get("body") or "",
            "subject": reply_data.get("subject", ""),
            "sentiment": sentiment,
            "intent": reply_data.get("intent", "needs_info"),
            "analysis": reply_data.get("analysis", {}),
            "received_at": datetime.now().isoformat(),
        }
        updates = {"outreach_reply": outreach_reply}
        if move_to_follow_up_if_positive and sentiment == "positive":
            updates["column"] = "follow-up"
            existing = candidate.get("checklist") or {}
            updates["checklist"] = {
                **existing,
                "consent_form_sent": False,
                "consent_form_received": False,
                "updated_cv_received": False,
                "screening_interview_scheduled": False,
                "screening_interview_completed": False,
            }
        self.update_candidate_status(role_id, candidate_id, updates)
        return self.get_candidate(role_id, candidate_id)

    # ---------- HR Briefings ----------
    def save_hr_briefing(
        self, filename: Optional[str], content: bytes, content_type: Optional[str] = None
    ) -> tuple:
        briefing_id = str(uuid.uuid4())
        briefings_dir = self.base_dir / "hr_briefings" / briefing_id
        briefings_dir.mkdir(parents=True, exist_ok=True)
        ext = resolve_hr_briefing_audio_extension(filename, content_type, content)
        audio_path = briefings_dir / f"briefing{ext}"
        with open(audio_path, "wb") as f:
            f.write(content)
        return audio_path, briefing_id

    def create_hr_briefing(self, briefing_data: Dict[str, Any], role_ids: List[str], briefing_id: str = None) -> str:
        if not briefing_id:
            briefing_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._get_session() as session:
            b = HRBriefingModel(
                id=briefing_id,
                summary=briefing_data.get("summary", ""),
                extracted_fields=_json_dumps(briefing_data.get("extracted_fields", {})),
                transcription=briefing_data.get("transcription", ""),
                created_at=now,
            )
            session.add(b)
            for rid in role_ids:
                session.add(RoleHRBriefing(role_id=rid, briefing_id=briefing_id))
            session.commit()
        return briefing_id

    def get_all_hr_briefings(self) -> List[Dict[str, Any]]:
        with self._get_session() as session:
            briefings = session.query(HRBriefingModel).all()
            result = []
            for b in briefings:
                links = session.query(RoleHRBriefing).filter(RoleHRBriefing.briefing_id == b.id).all()
                role_ids = [l.role_id for l in links]
                # Resolve role titles so Assigned Roles display even when role not in main roles list
                assigned_roles = []
                for rid in role_ids:
                    r = session.query(RoleModel).filter(RoleModel.id == rid).first()
                    assigned_roles.append({"id": rid, "title": r.title if r else f"Unknown role ({rid[:8]})"})
                result.append({
                    "id": b.id,
                    "summary": b.summary,
                    "extracted_fields": _json_loads(b.extracted_fields, {}),
                    "transcription": b.transcription or "",
                    "role_ids": role_ids,
                    "assigned_roles": assigned_roles,
                    "created_at": b.created_at,
                })
            return result

    def get_role_hr_briefing(self, role_id: str) -> Optional[Dict[str, Any]]:
        with self._get_session() as session:
            link = session.query(RoleHRBriefing).filter(RoleHRBriefing.role_id == role_id).first()
            if not link:
                return None
            b = session.query(HRBriefingModel).filter(HRBriefingModel.id == link.briefing_id).first()
            if not b:
                return None
            all_links = session.query(RoleHRBriefing).filter(RoleHRBriefing.briefing_id == b.id).all()
            role_ids = [l.role_id for l in all_links]
            return {
                "id": b.id,
                "summary": b.summary,
                "extracted_fields": _json_loads(b.extracted_fields, {}),
                "transcription": b.transcription or "",
                "role_ids": role_ids,
                "created_at": b.created_at,
            }

    def update_hr_briefing_roles(self, briefing_id: str, role_ids: List[str]) -> bool:
        """Set the assigned roles for a briefing (replaces existing)."""
        with self._get_session() as session:
            b = session.query(HRBriefingModel).filter(HRBriefingModel.id == briefing_id).first()
            if not b:
                return False
            session.query(RoleHRBriefing).filter(RoleHRBriefing.briefing_id == briefing_id).delete()
            for rid in role_ids:
                session.add(RoleHRBriefing(role_id=rid, briefing_id=briefing_id))
            session.commit()
        return True

    # ---------- Interviews ----------
    def save_interview_audio(self, role_id: str, candidate_id: str, file) -> tuple:
        candidate_dir = self._get_candidate_dir(role_id, candidate_id)
        interviews_dir = candidate_dir / "interviews"
        interviews_dir.mkdir(parents=True, exist_ok=True)
        interview_id = str(uuid.uuid4())
        ext = Path(file.filename).suffix if file.filename else ".mp3"
        audio_path = interviews_dir / f"{interview_id}{ext}"
        with open(audio_path, "wb") as f:
            f.write(file.file.read())
        return audio_path, interview_id

    def save_interview_data(self, role_id: str, candidate_id: str, interview_data: Dict[str, Any]):
        now = datetime.now().isoformat()
        with self._get_session() as session:
            inv = session.query(InterviewModel).filter(
                InterviewModel.role_id == role_id,
                InterviewModel.candidate_id == candidate_id
            ).first()
            if inv:
                inv.summary = interview_data.get("summary", "")
                inv.transcription = interview_data.get("transcription", "")
                inv.fit_score = interview_data.get("fit_score")
                inv.key_points = _json_dumps(interview_data.get("key_points", []))
                inv.strengths = _json_dumps(interview_data.get("strengths", []))
                inv.concerns = _json_dumps(interview_data.get("concerns", []))
                inv.recommendation = interview_data.get("recommendation")
                inv.candidate_responses = _json_dumps(interview_data.get("candidate_responses", {}))
                inv.interview_completed = interview_data.get("interview_completed", True)
                inv.updated_at = now
            else:
                session.add(InterviewModel(
                    role_id=role_id,
                    candidate_id=candidate_id,
                    summary=interview_data.get("summary", ""),
                    transcription=interview_data.get("transcription", ""),
                    fit_score=interview_data.get("fit_score"),
                    key_points=_json_dumps(interview_data.get("key_points", [])),
                    strengths=_json_dumps(interview_data.get("strengths", [])),
                    concerns=_json_dumps(interview_data.get("concerns", [])),
                    recommendation=interview_data.get("recommendation"),
                    candidate_responses=_json_dumps(interview_data.get("candidate_responses", {})),
                    interview_completed=interview_data.get("interview_completed", True),
                    created_at=now,
                    updated_at=now,
                ))
            session.commit()

    def get_interview_data(self, role_id: str, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self._get_session() as session:
            inv = session.query(InterviewModel).filter(
                InterviewModel.role_id == role_id,
                InterviewModel.candidate_id == candidate_id
            ).first()
            if not inv:
                return None
            return {
                "summary": inv.summary,
                "transcription": inv.transcription,
                "fit_score": inv.fit_score,
                "key_points": _json_loads(inv.key_points, []),
                "strengths": _json_loads(inv.strengths, []),
                "concerns": _json_loads(inv.concerns, []),
                "recommendation": inv.recommendation,
                "candidate_responses": _json_loads(inv.candidate_responses, {}),
                "interview_completed": inv.interview_completed if inv.interview_completed is not None else True,
            }

    # ---------- Evaluation Chat ----------
    def save_evaluation_chat(self, role_id: str, messages: List[Dict[str, Any]]) -> bool:
        now = datetime.now().isoformat()
        with self._get_session() as session:
            chat = session.query(EvaluationChatModel).filter(EvaluationChatModel.role_id == role_id).first()
            if chat:
                chat.messages = _json_dumps(messages)
                chat.updated_at = now
            else:
                session.add(EvaluationChatModel(role_id=role_id, messages=_json_dumps(messages), updated_at=now))
            session.commit()
        return True

    def get_evaluation_chat(self, role_id: str) -> List[Dict[str, Any]]:
        with self._get_session() as session:
            chat = session.query(EvaluationChatModel).filter(EvaluationChatModel.role_id == role_id).first()
            if not chat or not chat.messages:
                return []
            return _json_loads(chat.messages, [])

    # ---------- Consents (generic) ----------
    def get_all_consents(self) -> List[Dict[str, Any]]:
        return []

    # ---------- Consent Templates ----------
    def get_all_consent_templates(self) -> List[Dict[str, Any]]:
        with self._get_session() as session:
            templates = session.query(ConsentTemplateModel).all()
            return [
                {"id": t.id, "name": t.name, "content": t.content, "created_at": t.created_at, "updated_at": t.updated_at}
                for t in templates
            ]

    def get_consent_template(self, content_id: str) -> Optional[Dict[str, Any]]:
        with self._get_session() as session:
            t = session.query(ConsentTemplateModel).filter(ConsentTemplateModel.id == content_id).first()
            if not t:
                return None
            return {"id": t.id, "name": t.name, "content": t.content, "created_at": t.created_at, "updated_at": t.updated_at}

    def save_consent_template(self, name: str, content: str, content_id: str = None) -> str:
        if not content_id:
            content_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._get_session() as session:
            t = session.query(ConsentTemplateModel).filter(ConsentTemplateModel.id == content_id).first()
            if t:
                t.name = name
                t.content = content
                t.updated_at = now
            else:
                session.add(ConsentTemplateModel(id=content_id, name=name, content=content, created_at=now, updated_at=now))
            session.commit()
        return content_id

    def delete_consent_template(self, content_id: str) -> bool:
        with self._get_session() as session:
            t = session.query(ConsentTemplateModel).filter(ConsentTemplateModel.id == content_id).first()
            if t:
                session.delete(t)
                session.commit()
                return True
        return False

    # ---------- Consent email & reply ----------
    def send_consent_email(self, role_id: str, candidate_id: str, consent_data: Dict[str, Any]) -> bool:
        role = self.get_role(role_id)
        candidate = self.get_candidate(role_id, candidate_id)
        if not candidate:
            return False
        role_title = (role or {}).get("title", consent_data.get("role_title", "Position"))
        subject = consent_data.get("subject", f"Consent Request - {role_title}")
        email_content = f"""Dear {consent_data.get('candidate_name', candidate.get('name', 'Candidate'))},

Thank you for your interest in the {role_title} role.

As part of our recruitment process, we need your consent to process your personal data. Please review the consent details below:

{consent_data.get('consent_content', '')}

Please reply to this email with either:
- "I CONSENT" if you agree to the terms above
- "I DO NOT CONSENT" if you do not agree

Best regards,
Recruitment Team"""
        consent_email = {
            "to": consent_data.get("email", f"{candidate.get('name', '')}@example.com"),
            "subject": subject,
            "content": email_content,
            "consent_content": consent_data.get("consent_content", ""),
            "consent_content_id": consent_data.get("consent_content_id", ""),
            "candidate_name": consent_data.get("candidate_name", candidate.get("name", "")),
            "sent_at": datetime.now().isoformat(),
            "status": "sent",
        }
        checklist = (candidate.get("checklist") or {}).copy()
        checklist["consent_form_sent"] = True
        self.update_candidate_status(role_id, candidate_id, {
            "consent_email": consent_email,
            "consent_form_sent": True,
            "email_status": "consent_sent",
            "checklist": checklist,
        })
        return True

    def record_consent_reply(self, role_id: str, candidate_id: str, reply_data: Dict[str, Any]) -> bool:
        candidate = self.get_candidate(role_id, candidate_id)
        if not candidate:
            return False
        consent_status = reply_data.get("consent_status", "consented")
        simulated_email = {
            "content": reply_data.get("content", ""),
            "sentiment": reply_data.get("sentiment", "positive"),
            "intent": reply_data.get("intent", "interested"),
            "analysis": reply_data.get("analysis", {}),
            "timestamp": datetime.now().isoformat(),
            "type": "consent_reply",
            "consent_status": consent_status,
            "consent_content": (candidate.get("consent_email") or {}).get("consent_content", ""),
            "consent_content_id": (candidate.get("consent_email") or {}).get("consent_content_id", ""),
        }
        consent_reply = {
            "received_at": datetime.now().isoformat(),
            "status": consent_status,
            "responded_by": candidate.get("name", ""),
            "response": reply_data.get("response", "I CONSENT" if consent_status == "consented" else "I DO NOT CONSENT"),
        }
        checklist = (candidate.get("checklist") or {}).copy()
        checklist["consent_form_received"] = consent_status == "consented"
        self.update_candidate_status(role_id, candidate_id, {
            "simulated_email": simulated_email,
            "consent_reply": consent_reply,
            "consent_form_received": consent_status == "consented",
            "email_status": "consent_received" if consent_status == "consented" else "consent_declined",
            "checklist": checklist,
        })
        return True
