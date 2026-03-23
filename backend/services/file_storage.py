"""
File storage service for managing roles, candidates, interviews, and other data
"""
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from .audio_transcription import resolve_hr_briefing_audio_extension


def _resolve_data_dir() -> Path:
    """Resolve data directory relative to backend package, so it works regardless of cwd."""
    backend_dir = Path(__file__).resolve().parent.parent
    data_dir = backend_dir / "data"
    if data_dir.exists():
        return data_dir
    return Path("data")  # fallback to cwd-relative


class FileStorageService:
    def __init__(self, base_dir: str = None):
        self.base_dir = Path(base_dir) if base_dir else _resolve_data_dir()
        self.roles_dir = self.base_dir / "roles"
        self.consents_dir = self.base_dir / "consents"
        self.consent_templates_dir = self.base_dir / "consent_templates"
        self.interviews_dir = self.base_dir / "interviews"
        self.hr_briefings_dir = self.base_dir / "hr_briefings"
        
        # Create directories
        self.roles_dir.mkdir(parents=True, exist_ok=True)
        self.consents_dir.mkdir(parents=True, exist_ok=True)
        self.consent_templates_dir.mkdir(parents=True, exist_ok=True)
        self.interviews_dir.mkdir(parents=True, exist_ok=True)
        self.hr_briefings_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_role_dir(self, role_id: str) -> Path:
        """Get directory for a specific role"""
        role_dir = self.roles_dir / role_id
        role_dir.mkdir(parents=True, exist_ok=True)
        return role_dir
    
    def _get_candidate_dir(self, role_id: str, candidate_id: str) -> Path:
        """Get directory for a specific candidate"""
        candidate_dir = self._get_role_dir(role_id) / "candidates" / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        return candidate_dir
    
    def create_role(self, role_data: Dict[str, Any]) -> str:
        """Create a new role"""
        role_id = str(uuid.uuid4())
        role_dir = self._get_role_dir(role_id)
        
        role_info = {
            "id": role_id,
            "title": role_data.get("title", ""),
            "status": role_data.get("status", "New"),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "created_by_user_id": role_data.get("created_by_user_id"),
            "candidates_count": 0,
            "successful_candidates_count": 0,
            "hiring_budget": None,
            "vacancies": None,
            "urgency": None,
            "timeline": None,
            # D. Candidate Preference & Requirement Fields (default values)
            "candidate_requirement_fields": role_data.get("candidate_requirement_fields", [
                "expected_salary",
                "earliest_start_date",
                "work_authorization",
                "location_preferences",
                "notice_period"
            ]),
            # E. Evaluation Criteria (default values)
            "evaluation_criteria": role_data.get("evaluation_criteria", [
                "Must-haves",
                "Nice-to-haves",
                "Competencies",
                "Technical criteria",
                "Behavioral criteria"
            ]),
        }
        
        with open(role_dir / "role.json", "w") as f:
            json.dump(role_info, f, indent=2)
        
        return role_id
    
    def get_all_roles(self) -> List[Dict[str, Any]]:
        """Get all roles with accurate candidate counts"""
        roles = []
        if not self.roles_dir.exists():
            return roles
        
        for role_dir in self.roles_dir.iterdir():
            if role_dir.is_dir():
                role_file = role_dir / "role.json"
                if role_file.exists():
                    with open(role_file, "r", encoding="utf-8") as f:
                        role = json.load(f)
                    role_id = role.get("id", role_dir.name)
                    candidates = self.get_candidates(role_id)
                    role["candidates_count"] = len(candidates)
                    role["successful_candidates_count"] = sum(
                        1 for c in candidates if c.get("sent_to_client")
                    )
                    # Pipeline stage counts for dashboard (default column to outreach if missing)
                    role["outreach_count"] = sum(1 for c in candidates if (c.get("column") or "outreach") == "outreach")
                    role["follow_up_count"] = sum(1 for c in candidates if c.get("column") == "follow-up")
                    role["evaluation_count"] = sum(1 for c in candidates if c.get("column") == "evaluation")
                    role["sent_to_client_count"] = sum(1 for c in candidates if c.get("sent_to_client"))
                    role["not_pushing_forward_count"] = sum(1 for c in candidates if c.get("not_pushing_forward"))
                    role["has_jd"] = (role_dir / "jd_parsed.json").exists()
                    role["has_hr_briefing"] = self.get_role_hr_briefing(role_id) is not None
                    # Normalize: use created_by_user_id; if only legacy created_by_email, derive user id (part before @)
                    if not role.get("created_by_user_id") and role.get("created_by_email"):
                        role["created_by_user_id"] = (role["created_by_email"] or "").split("@")[0] or None
                    roles.append(role)
        
        return roles
    
    def get_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        """Get role by ID"""
        role_file = self._get_role_dir(role_id) / "role.json"
        if not role_file.exists():
            return None
        
        with open(role_file, "r") as f:
            role = json.load(f)
        if not role.get("created_by_user_id") and role.get("created_by_email"):
            role["created_by_user_id"] = (role["created_by_email"] or "").split("@")[0] or None
        return role
    
    def update_role(self, role_id: str, updates: Dict[str, Any]) -> bool:
        """Update role"""
        role = self.get_role(role_id)
        if not role:
            return False
        
        role.update(updates)
        role["updated_at"] = datetime.now().isoformat()
        
        role_file = self._get_role_dir(role_id) / "role.json"
        with open(role_file, "w") as f:
            json.dump(role, f, indent=2)
        
        return True
    
    def delete_role(self, role_id: str) -> bool:
        """Delete role"""
        role_dir = self._get_role_dir(role_id)
        if not role_dir.exists():
            return False
        
        import shutil
        shutil.rmtree(role_dir)
        return True
    
    def save_jd(self, role_id: str, file) -> Path:
        """Save JD PDF file"""
        role_dir = self._get_role_dir(role_id)
        jd_path = role_dir / "jd.pdf"
        
        with open(jd_path, "wb") as f:
            content = file.file.read()
            f.write(content)
        
        return jd_path
    
    def save_parsed_jd(self, role_id: str, parsed_jd: Dict[str, Any]):
        """Save parsed JD data"""
        role_dir = self._get_role_dir(role_id)
        jd_file = role_dir / "jd_parsed.json"
        
        with open(jd_file, "w") as f:
            json.dump(parsed_jd, f, indent=2)
    
    def get_parsed_jd(self, role_id: str) -> Optional[Dict[str, Any]]:
        """Get parsed JD"""
        role_dir = self._get_role_dir(role_id)
        jd_file = role_dir / "jd_parsed.json"
        
        if not jd_file.exists():
            return None
        
        with open(jd_file, "r") as f:
            return json.load(f)
    
    def update_parsed_jd(self, role_id: str, jd_data: Dict[str, Any]):
        """Update parsed JD"""
        existing_jd = self.get_parsed_jd(role_id) or {}
        existing_jd.update(jd_data)
        self.save_parsed_jd(role_id, existing_jd)
    
    def save_candidate_pdf(self, role_id: str, file) -> Path:
        """Save candidate PDF file"""
        candidate_id = str(uuid.uuid4())
        candidate_dir = self._get_candidate_dir(role_id, candidate_id)
        pdf_path = candidate_dir / "resume.pdf"
        
        with open(pdf_path, "wb") as f:
            content = file.file.read()
            f.write(content)
        
        return pdf_path, candidate_id
    
    def create_candidate(self, role_id: str, candidate_data: Dict[str, Any], candidate_id: str = None) -> str:
        """Create candidate card"""
        if not candidate_id:
            candidate_id = str(uuid.uuid4())
        candidate_dir = self._get_candidate_dir(role_id, candidate_id)
        
        candidate_info = {
            "id": candidate_id,
            "name": candidate_data.get("name", ""),
            "summary": candidate_data.get("summary", ""),
            "skills": candidate_data.get("skills", []),
            "experience": candidate_data.get("experience", ""),
            "parsed_insights": candidate_data.get("parsed_insights", {}),
            "column": "outreach",
            "color": "amber-transparent",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "outreach_sent": False,
            "outreach_message": None,
            "checklist": {
                "consent_form_sent": False,
                "consent_form_received": False,
                "updated_cv_received": False,
                "screening_interview_scheduled": False,
                "screening_interview_completed": False,
            },
        }
        
        with open(candidate_dir / "candidate.json", "w") as f:
            json.dump(candidate_info, f, indent=2)
        
        # Update role candidate count
        role = self.get_role(role_id)
        if role:
            role["candidates_count"] = role.get("candidates_count", 0) + 1
            self.update_role(role_id, role)
        
        return candidate_id
    
    def get_candidates(self, role_id: str) -> List[Dict[str, Any]]:
        """Get all candidates for a role"""
        role_dir = self._get_role_dir(role_id)
        candidates_dir = role_dir / "candidates"
        
        if not candidates_dir.exists():
            return []
        
        candidates = []
        for candidate_dir in candidates_dir.iterdir():
            if candidate_dir.is_dir():
                candidate_file = candidate_dir / "candidate.json"
                if candidate_file.exists():
                    with open(candidate_file, "r", encoding="utf-8") as f:
                        candidates.append(json.load(f))
        
        return candidates
    
    def get_candidate(self, role_id: str, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Get candidate by ID"""
        candidate_file = self._get_candidate_dir(role_id, candidate_id) / "candidate.json"
        if not candidate_file.exists():
            return None
        with open(candidate_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def delete_candidate(self, role_id: str, candidate_id: str) -> bool:
        """Delete a candidate and their data (interview, etc.). Returns True if deleted."""
        candidate_dir = self._get_role_dir(role_id) / "candidates" / candidate_id
        if not candidate_dir.exists() or not candidate_dir.is_dir():
            return False
        shutil.rmtree(candidate_dir, ignore_errors=True)
        role = self.get_role(role_id)
        if role:
            role["candidates_count"] = max(0, role.get("candidates_count", 1) - 1)
            self.update_role(role_id, role)
        return True
    
    def update_candidate_status(self, role_id: str, candidate_id: str, status: Dict[str, Any]):
        """Update candidate status"""
        candidate = self.get_candidate(role_id, candidate_id)
        if not candidate:
            return
        
        # Initialize checklist if moving to follow-up and checklist doesn't exist
        if status.get("column") == "follow-up" and "checklist" not in candidate:
            candidate["checklist"] = {
                "consent_form_sent": False,
                "consent_form_received": False,
                "updated_cv_received": False,
                "screening_interview_scheduled": False,
                "screening_interview_completed": False,
            }
        
        # Merge checklist if provided
        if "checklist" in status and isinstance(status["checklist"], dict):
            if "checklist" not in candidate:
                candidate["checklist"] = {}
            candidate["checklist"].update(status["checklist"])
            # Remove checklist from status so it doesn't overwrite the merged version
            status = {k: v for k, v in status.items() if k != "checklist"}
        
        candidate.update(status)
        candidate["updated_at"] = datetime.now().isoformat()
        # When screening interview is marked completed, move candidate to Evaluation
        if candidate.get("checklist", {}).get("screening_interview_completed"):
            candidate["column"] = "evaluation"
        candidate_file = self._get_candidate_dir(role_id, candidate_id) / "candidate.json"
        with open(candidate_file, "w") as f:
            json.dump(candidate, f, indent=2)
    
    def save_outreach_message(self, role_id: str, candidate_id: str, message: str):
        """Save outreach message and mark as sent"""
        candidate = self.get_candidate(role_id, candidate_id)
        if candidate:
            candidate["outreach_message"] = message
            candidate["outreach_sent"] = True
            self.update_candidate_status(role_id, candidate_id, candidate)

    def update_outreach_message(self, role_id: str, candidate_id: str, message: str):
        """Update outreach message (recruiter edits) without marking as sent"""
        candidate = self.get_candidate(role_id, candidate_id)
        if candidate:
            candidate["outreach_message"] = message
            candidate["updated_at"] = datetime.now().isoformat()
            candidate_file = self._get_candidate_dir(role_id, candidate_id) / "candidate.json"
            with open(candidate_file, "w", encoding="utf-8") as f:
                json.dump(candidate, f, indent=2)

    def record_outreach_reply(
        self,
        role_id: str,
        candidate_id: str,
        reply_data: Dict[str, Any],
        move_to_follow_up_if_positive: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Record a candidate's reply to outreach (from mailbox or simulation).
        reply_data: { content or body, subject?, sentiment, intent?, analysis? }
        If sentiment is positive and move_to_follow_up_if_positive, sets column to follow-up.
        """
        candidate = self.get_candidate(role_id, candidate_id)
        if not candidate:
            return None
        content = reply_data.get("content") or reply_data.get("body") or ""
        sentiment = reply_data.get("sentiment", "neutral")
        candidate["outreach_reply"] = {
            "content": content,
            "subject": reply_data.get("subject", ""),
            "sentiment": sentiment,
            "intent": reply_data.get("intent", "needs_info"),
            "analysis": reply_data.get("analysis", {}),
            "received_at": datetime.now().isoformat(),
        }
        if move_to_follow_up_if_positive and sentiment == "positive":
            candidate["column"] = "follow-up"
            if "checklist" not in candidate:
                candidate["checklist"] = {
                    "consent_form_sent": False,
                    "consent_form_received": False,
                    "updated_cv_received": False,
                    "screening_interview_scheduled": False,
                    "screening_interview_completed": False,
                }
        candidate["updated_at"] = datetime.now().isoformat()
        candidate_file = self._get_candidate_dir(role_id, candidate_id) / "candidate.json"
        with open(candidate_file, "w", encoding="utf-8") as f:
            json.dump(candidate, f, indent=2)
        return candidate

    def save_hr_briefing(
        self, filename: Optional[str], content: bytes, content_type: Optional[str] = None
    ):
        """Save HR briefing audio file (bytes from UploadFile.read())."""
        briefing_id = str(uuid.uuid4())
        briefing_dir = self.hr_briefings_dir / briefing_id
        briefing_dir.mkdir(parents=True, exist_ok=True)

        ext = resolve_hr_briefing_audio_extension(filename, content_type, content)
        audio_path = briefing_dir / f"briefing{ext}"

        with open(audio_path, "wb") as f:
            f.write(content)

        return audio_path, briefing_id
    
    def create_hr_briefing(self, briefing_data: Dict[str, Any], role_ids: List[str], briefing_id: str = None) -> str:
        """Create HR briefing record"""
        if not briefing_id:
            briefing_id = str(uuid.uuid4())
        briefing_dir = self.hr_briefings_dir / briefing_id
        briefing_dir.mkdir(parents=True, exist_ok=True)
        
        briefing_info = {
            "id": briefing_id,
            "summary": briefing_data.get("summary", ""),
            "extracted_fields": briefing_data.get("extracted_fields", {}),
            "transcription": briefing_data.get("transcription", ""),
            "role_ids": role_ids,
            "created_at": datetime.now().isoformat(),
        }
        
        with open(briefing_dir / "briefing.json", "w") as f:
            json.dump(briefing_info, f, indent=2)
        
        return briefing_id
    
    def get_all_hr_briefings(self) -> List[Dict[str, Any]]:
        """Get all HR briefings"""
        briefings = []
        if not self.hr_briefings_dir.exists():
            return briefings
        
        for briefing_dir in self.hr_briefings_dir.iterdir():
            if briefing_dir.is_dir():
                briefing_file = briefing_dir / "briefing.json"
                if briefing_file.exists():
                    with open(briefing_file, "r") as f:
                        briefings.append(json.load(f))
        
        return briefings
    
    def get_role_hr_briefing(self, role_id: str) -> Optional[Dict[str, Any]]:
        """Get HR briefing for a role"""
        all_briefings = self.get_all_hr_briefings()
        for briefing in all_briefings:
            if role_id in briefing.get("role_ids", []):
                return briefing
        return None

    def update_hr_briefing_roles(self, briefing_id: str, role_ids: List[str]) -> bool:
        """Set the assigned roles for a briefing (replaces existing)."""
        briefing_dir = self.hr_briefings_dir / briefing_id
        briefing_file = briefing_dir / "briefing.json"
        if not briefing_file.exists():
            return False
        with open(briefing_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["role_ids"] = list(role_ids)
        data["updated_at"] = datetime.now().isoformat()
        with open(briefing_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True

    def save_interview_audio(self, role_id: str, candidate_id: str, file) -> Path:
        """Save interview audio file"""
        candidate_dir = self._get_candidate_dir(role_id, candidate_id)
        interviews_dir = candidate_dir / "interviews"
        interviews_dir.mkdir(parents=True, exist_ok=True)
        
        interview_id = str(uuid.uuid4())
        ext = Path(file.filename).suffix if file.filename else ".mp3"
        audio_path = interviews_dir / f"{interview_id}{ext}"
        
        with open(audio_path, "wb") as f:
            content = file.file.read()
            f.write(content)
        
        return audio_path, interview_id
    
    def save_interview_data(self, role_id: str, candidate_id: str, interview_data: Dict[str, Any]):
        """Save interview data"""
        candidate_dir = self._get_candidate_dir(role_id, candidate_id)
        interview_file = candidate_dir / "interview.json"
        
        with open(interview_file, "w") as f:
            json.dump(interview_data, f, indent=2)
    
    def get_interview_data(self, role_id: str, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Get interview data"""
        candidate_dir = self._get_candidate_dir(role_id, candidate_id)
        interview_file = candidate_dir / "interview.json"
        
        if not interview_file.exists():
            return None
        
        with open(interview_file, "r") as f:
            return json.load(f)

    def save_evaluation_chat(self, role_id: str, messages: List[Dict[str, Any]]) -> bool:
        """Save evaluation chat messages for a role"""
        role_dir = self._get_role_dir(role_id)
        chat_file = role_dir / "evaluation_chat.json"
        data = {"messages": messages, "updated_at": datetime.now().isoformat()}
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True

    def get_evaluation_chat(self, role_id: str) -> List[Dict[str, Any]]:
        """Get evaluation chat messages for a role"""
        role_dir = self._get_role_dir(role_id)
        chat_file = role_dir / "evaluation_chat.json"
        if not chat_file.exists():
            return []
        with open(chat_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("messages", [])
    
    def get_all_consents(self) -> List[Dict[str, Any]]:
        """Get all consent forms"""
        consents = []
        if not self.consents_dir.exists():
            return consents
        
        for consent_file in self.consents_dir.glob("*.json"):
            with open(consent_file, "r") as f:
                consents.append(json.load(f))
        
        return consents

    # ------------------------------------------------------------------
    # Consent Templates (reusable consent content)
    # ------------------------------------------------------------------
    def get_all_consent_templates(self) -> List[Dict[str, Any]]:
        """Get all consent templates"""
        templates = []
        if not self.consent_templates_dir.exists():
            return templates
        for template_dir in self.consent_templates_dir.iterdir():
            if template_dir.is_dir():
                content_file = template_dir / "content.json"
                if content_file.exists():
                    with open(content_file, "r", encoding="utf-8") as f:
                        templates.append(json.load(f))
        return templates

    def get_consent_template(self, content_id: str) -> Optional[Dict[str, Any]]:
        """Get a consent template by ID"""
        content_file = self.consent_templates_dir / content_id / "content.json"
        if not content_file.exists():
            return None
        with open(content_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_consent_template(self, name: str, content: str, content_id: str = None) -> str:
        """Save or create a consent template"""
        if not content_id:
            content_id = str(uuid.uuid4())
        template_dir = self.consent_templates_dir / content_id
        template_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "id": content_id,
            "name": name,
            "content": content,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        with open(template_dir / "content.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return content_id

    def delete_consent_template(self, content_id: str) -> bool:
        """Delete a consent template"""
        import shutil
        template_dir = self.consent_templates_dir / content_id
        if template_dir.exists():
            shutil.rmtree(template_dir)
            return True
        return False

    def send_consent_email(self, role_id: str, candidate_id: str, consent_data: Dict[str, Any]) -> bool:
        """
        Record that a consent email was sent to a candidate.
        consent_data: { candidate_name, role_title, email, consent_content, consent_content_id, subject? }
        """
        candidate = self.get_candidate(role_id, candidate_id)
        if not candidate:
            return False
        role = self.get_role(role_id)
        role_title = role.get("title", consent_data.get("role_title", "Position"))
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
        candidate["consent_email"] = consent_email
        candidate["consent_form_sent"] = True
        candidate["email_status"] = "consent_sent"
        if "checklist" not in candidate:
            candidate["checklist"] = {}
        candidate["checklist"]["consent_form_sent"] = True
        candidate["updated_at"] = datetime.now().isoformat()
        candidate_file = self._get_candidate_dir(role_id, candidate_id) / "candidate.json"
        with open(candidate_file, "w") as f:
            json.dump(candidate, f, indent=2)
        return True

    def record_consent_reply(self, role_id: str, candidate_id: str, reply_data: Dict[str, Any]) -> bool:
        """
        Record a candidate's consent reply (from simulation or real email).
        reply_data: { content, sentiment?, intent?, consent_status: "consented"|"declined", response? }
        """
        candidate = self.get_candidate(role_id, candidate_id)
        if not candidate:
            return False
        consent_status = reply_data.get("consent_status", "consented")
        candidate["simulated_email"] = {
            "content": reply_data.get("content", ""),
            "sentiment": reply_data.get("sentiment", "positive"),
            "intent": reply_data.get("intent", "interested"),
            "analysis": reply_data.get("analysis", {}),
            "timestamp": datetime.now().isoformat(),
            "type": "consent_reply",
            "consent_status": consent_status,
            "consent_content": candidate.get("consent_email", {}).get("consent_content", ""),
            "consent_content_id": candidate.get("consent_email", {}).get("consent_content_id", ""),
        }
        candidate["consent_reply"] = {
            "received_at": datetime.now().isoformat(),
            "status": consent_status,
            "responded_by": candidate.get("name", ""),
            "response": reply_data.get("response", "I CONSENT" if consent_status == "consented" else "I DO NOT CONSENT"),
        }
        candidate["consent_form_received"] = consent_status == "consented"
        candidate["email_status"] = "consent_received" if consent_status == "consented" else "consent_declined"
        if "checklist" not in candidate:
            candidate["checklist"] = {}
        candidate["checklist"]["consent_form_received"] = consent_status == "consented"
        candidate["updated_at"] = datetime.now().isoformat()
        candidate_file = self._get_candidate_dir(role_id, candidate_id) / "candidate.json"
        with open(candidate_file, "w") as f:
            json.dump(candidate, f, indent=2)
        return True

