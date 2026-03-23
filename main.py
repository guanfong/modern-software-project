import logging
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root before auth_service reads JWT_SECRET at import time.
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

logger = logging.getLogger(__name__)

# CrewAI v1+ compat (Task.execute -> execute_sync); import before any agent module.
import backend.agents.crew_compat  # noqa: F401

from backend.services.auth_service import verify_user_password, get_user_by_id, create_access_token, decode_token, create_user, count_users, list_users, update_user_email
from backend.agents.jd_parser import JDParserAgent
from backend.agents.hr_briefing_agent import HRBriefingAgent
from backend.agents.candidate_parser import CandidateParserAgent
from backend.agents.outreach_writer import OutreachWriterAgent
from backend.agents.email_monitor import EmailMonitorAgent
from backend.agents.interview_assistant import InterviewAssistantAgent
from backend.agents.evaluation_agent import EvaluationAgent
from backend.agents.consent_engine import ConsentEngineAgent
from backend.agents.simulation_agent import SimulationAgent
from backend.services.file_storage import FileStorageService
from backend.services.db_storage import DatabaseStorageService
from backend.services.pdf_parser import PDFParserService
from backend.services.audio_transcription import AudioTranscriptionService

app = FastAPI(title="Agentic AI Recruiter API", version="1.0.0")


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Return 500 with error detail so frontend and logs can show the real cause."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "message": "Server error. Check backend terminal for traceback."},
    )

USE_DATABASE = os.getenv("USE_DATABASE", "true").lower() != "false"

allow_all_origins = os.getenv("ENVIRONMENT") != "production"


def _cors_allowed_origins() -> List[str]:
    """Production: set ALLOWED_ORIGINS to comma-separated HTTPS origins (e.g. Amplify app URL)."""
    raw = (os.getenv("ALLOWED_ORIGINS") or "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    if allow_all_origins:
        return ["*"]
    return ["http://localhost:3000", "http://localhost:3001"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require valid JWT for all /api/ routes except login and setup."""
    path = request.scope.get("path", "")
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in ("/api/auth/login", "/api/auth/setup", "/api/auth/needs-setup"):
        return await call_next(request)
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    token = auth.split(" ", 1)[1]
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    user = get_user_by_id(payload["sub"])
    if not user:
        return JSONResponse(status_code=401, content={"detail": "User not found"})
    request.state.user = user
    if path.startswith("/api/admin/"):
        if user.get("role") != "admin":
            return JSONResponse(status_code=403, content={"detail": "Admin required"})
    return await call_next(request)

if USE_DATABASE:
    file_storage = DatabaseStorageService()
    file_storage.init_db()
    try:
        file_storage_legacy = FileStorageService()
        legacy_roles = file_storage_legacy.get_all_roles()
        db_roles = file_storage.get_all_roles()
        if legacy_roles and not db_roles:
            from backend.scripts.migrate_to_db import migrate
            migrate()
    except Exception as e:
        import logging
        logging.warning(f"Migration check skipped or failed: {e}")
else:
    file_storage = FileStorageService()
pdf_parser = PDFParserService()
audio_transcription = AudioTranscriptionService()

jd_parser = JDParserAgent()
hr_briefing_agent = HRBriefingAgent()
candidate_parser = CandidateParserAgent()
outreach_writer = OutreachWriterAgent()
email_monitor = EmailMonitorAgent()
interview_assistant = InterviewAssistantAgent()
evaluation_agent = EvaluationAgent()
consent_engine = ConsentEngineAgent()
simulation_agent = SimulationAgent()


class RoleCreate(BaseModel):
    title: str
    status: str = "New"


class RoleUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    hiring_budget: Optional[float] = None
    vacancies: Optional[int] = None
    urgency: Optional[str] = None
    timeline: Optional[str] = None
    candidate_requirement_fields: Optional[List[str]] = None
    evaluation_criteria: Optional[List[str]] = None


class CandidateCreate(BaseModel):
    name: str
    role_id: str


class HRBriefingCreate(BaseModel):
    role_ids: List[str]
    summary: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class SetupRequest(BaseModel):
    email: str
    password: str


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: str = "user"


class UpdateUserEmailRequest(BaseModel):
    email: str


@app.get("/")
async def root():
    return {
        "message": "Agentic AI Recruiter API",
        "hint": "This is the backend. For the dashboard UI, run ngrok http 3000 (frontend) instead of 8000.",
    }

@app.get("/api/auth/needs-setup")
async def auth_needs_setup():
    """Return whether first-time setup is needed (no users yet). Safe to call without auth."""
    try:
        n = count_users()
        return {"needs_setup": n == 0}
    except Exception as e:
        logger.exception("needs_setup failed: %s", e)
        return JSONResponse(status_code=500, content={"detail": str(e), "needs_setup": False})


@app.post("/api/auth/setup")
async def auth_setup(body: SetupRequest):
    """Create the first admin user (only when no users exist)."""
    if count_users() > 0:
        raise HTTPException(status_code=400, detail="Setup already completed. Use login.")
    user_id = create_user(body.email, body.password, role="admin")
    if not user_id:
        raise HTTPException(status_code=400, detail="Could not create user.")
    user = get_user_by_id(user_id)
    token = create_access_token({"sub": user_id})
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest):
    """Login with email and password. Returns JWT and user."""
    user = verify_user_password(body.email.strip(), body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token({"sub": user["id"]})
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return current user (requires valid token)."""
    if not getattr(request.state, "user", None):
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Not authenticated")
        payload = decode_token(auth.split(" ", 1)[1])
        if not payload or "sub" not in payload:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = get_user_by_id(payload["sub"])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        request.state.user = user
    return request.state.user


@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    """List all users (admin only)."""
    return {"users": list_users()}


@app.post("/api/admin/users")
async def admin_create_user(request: Request, body: CreateUserRequest):
    """Create a new user (admin only)."""
    user_id = create_user(body.email, body.password, role=body.role)
    if not user_id:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = get_user_by_id(user_id)
    return {"message": "User created", "user": user}


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user_email(request: Request, user_id: str, body: UpdateUserEmailRequest):
    """Update a user's email (admin only)."""
    user = update_user_email(user_id, body.email)
    if not user:
        raise HTTPException(status_code=400, detail="User not found or email already in use")
    return {"message": "User updated", "user": user}


@app.get("/api/roles")
async def get_roles():
    """Get all roles"""
    try:
        roles = file_storage.get_all_roles()
        return {"roles": roles}
    except Exception as e:
        logger.exception("get_roles failed")
        return JSONResponse(
            status_code=500,
            content={"detail": str(e), "message": "Failed to load roles. See backend logs for details."},
        )


@app.post("/api/roles")
async def create_role(request: Request, role: RoleCreate):
    """Create a new role (creator email stored for display)."""
    data = role.dict()
    user = getattr(request.state, "user", None)
    if user:
        # User id = part of email before "@" (e.g. gftan.2023 from gftan.2023@mitb.smu.edu.sg)
        email = user.get("email") or ""
        data["created_by_user_id"] = email.split("@")[0] if "@" in email else (user.get("id") or "")
    role_id = file_storage.create_role(data)
    return {"role_id": role_id, "message": "Role created successfully"}


@app.get("/api/roles/{role_id}")
async def get_role(role_id: str):
    """Get role details"""
    role = file_storage.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@app.put("/api/roles/{role_id}")
async def update_role(role_id: str, role_update: RoleUpdate):
    """Update role details"""
    updated = file_storage.update_role(role_id, role_update.dict(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Role not found")
    return {"message": "Role updated successfully"}


@app.delete("/api/roles/{role_id}")
async def delete_role(role_id: str):
    """Delete a role"""
    success = file_storage.delete_role(role_id)
    if not success:
        raise HTTPException(status_code=404, detail="Role not found")
    return {"message": "Role deleted successfully"}


@app.post("/api/roles/{role_id}/jd")
async def upload_jd(role_id: str, file: UploadFile = File(...)):
    """Upload and parse job description PDF"""
    try:
        if not file.filename or not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="File must be a PDF")
        role = file_storage.get_role(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found. Make sure the role exists.")
        # Save file
        file_path = file_storage.save_jd(role_id, file)
        # Parse PDF
        pdf_content = pdf_parser.extract_text(file_path)
        if not pdf_content or len(pdf_content.strip()) == 0:
            raise HTTPException(
                status_code=400,
                detail="Could not extract text from the PDF. The file might be image-based (scanned), corrupted, or empty. Try a PDF with selectable text.",
            )
        # Use JD Parser Agent
        parsed_jd = await jd_parser.parse_jd(pdf_content)
        # Save parsed data
        file_storage.save_parsed_jd(role_id, parsed_jd)
        return {"message": "JD uploaded and parsed successfully", "jd": parsed_jd}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("JD upload failed: %s", e)
        return JSONResponse(
            status_code=500,
            content={"detail": str(e), "message": "JD upload failed. Check backend logs for details."},
        )


@app.get("/api/roles/{role_id}/jd")
async def get_jd(role_id: str):
    """Get parsed job description"""
    jd = file_storage.get_parsed_jd(role_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return jd


@app.put("/api/roles/{role_id}/jd")
async def update_jd(role_id: str, jd_data: Dict[str, Any]):
    """Update parsed JD fields"""
    file_storage.update_parsed_jd(role_id, jd_data)
    return {"message": "JD updated successfully"}


@app.post("/api/roles/{role_id}/candidates")
async def upload_candidate(role_id: str, file: UploadFile = File(...)):
    """Upload and parse candidate PDF"""
    try:
        if not file.filename or not file.filename.endswith('.pdf'):
            raise HTTPException(status_code=400, detail="File must be a PDF")
        
        # Save file (returns tuple: file_path, candidate_id)
        file_path, candidate_id = file_storage.save_candidate_pdf(role_id, file)
        
        # Parse PDF
        pdf_content = pdf_parser.extract_text(file_path)
        
        if not pdf_content or len(pdf_content.strip()) == 0:
            raise HTTPException(status_code=400, detail="Could not extract text from PDF. The PDF might be corrupted or image-based.")
        
        # Use Candidate Parser Agent
        parsed_candidate = await candidate_parser.parse_candidate(pdf_content)
        
        # Debug: Log parsed candidate data
        print(f"DEBUG: Parsed candidate - Name: '{parsed_candidate.get('name', 'NOT FOUND')}', Skills: {parsed_candidate.get('skills', [])[:3]}")
        
        # If name is missing, try to extract from PDF content
        if not parsed_candidate.get('name') or parsed_candidate.get('name', '').strip() == '':
            # Try to extract name from first few lines of PDF
            first_lines = pdf_content.split('\n')[:5]
            for line in first_lines:
                line = line.strip()
                # Simple heuristic: if line looks like a name (2-4 words, capitalized)
                if line and len(line.split()) >= 2 and len(line.split()) <= 4:
                    if line[0].isupper() and all(word[0].isupper() for word in line.split() if word):
                        parsed_candidate['name'] = line
                        print(f"DEBUG: Extracted name from PDF: '{line}'")
                        break
        
        # Create candidate card (reuse candidate_id from PDF save)
        candidate_id = file_storage.create_candidate(role_id, parsed_candidate, candidate_id=candidate_id)
        
        return {"message": "Candidate uploaded and parsed successfully", "candidate_id": candidate_id, "candidate": parsed_candidate}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error uploading candidate: {error_details}")
        raise HTTPException(status_code=500, detail=f"Error processing candidate PDF: {str(e)}")


@app.get("/api/roles/{role_id}/candidates")
async def get_candidates(role_id: str):
    """Get all candidates for a role"""
    candidates = file_storage.get_candidates(role_id)
    return {"candidates": candidates}


@app.get("/api/roles/{role_id}/candidates/{candidate_id}")
async def get_candidate(role_id: str, candidate_id: str):
    """Get candidate details"""
    candidate = file_storage.get_candidate(role_id, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@app.post("/api/roles/{role_id}/candidates/{candidate_id}/outreach")
async def generate_outreach(role_id: str, candidate_id: str, body: Optional[Dict[str, Any]] = Body(default=None)):
    """Generate personalized outreach message for candidate. Optional: recruiter_notes for customization hints."""
    role = file_storage.get_role(role_id)
    candidate = file_storage.get_candidate(role_id, candidate_id)
    if not role or not candidate:
        raise HTTPException(status_code=404, detail="Role or candidate not found")
    jd = file_storage.get_parsed_jd(role_id)
    briefing = file_storage.get_role_hr_briefing(role_id)
    recruiter_notes = (body or {}).get("recruiter_notes", "")
    outreach_message = await outreach_writer.generate_outreach(
        role, candidate, jd, briefing=briefing, recruiter_notes=recruiter_notes
    )
    file_storage.update_outreach_message(role_id, candidate_id, outreach_message)
    return {"outreach_message": outreach_message}


@app.post("/api/roles/{role_id}/candidates/{candidate_id}/outreach-notes")
async def generate_outreach_notes(role_id: str, candidate_id: str):
    """Generate AI-suggested recruiter notes for personalization"""
    role = file_storage.get_role(role_id)
    candidate = file_storage.get_candidate(role_id, candidate_id)
    if not role or not candidate:
        raise HTTPException(status_code=404, detail="Role or candidate not found")
    jd = file_storage.get_parsed_jd(role_id)
    briefing = file_storage.get_role_hr_briefing(role_id)
    notes = await outreach_writer.generate_recruiter_notes(role, candidate, jd, briefing)
    return {"recruiter_notes": notes}


@app.put("/api/roles/{role_id}/candidates/{candidate_id}/outreach")
async def update_outreach(role_id: str, candidate_id: str, body: Dict[str, Any]):
    """Save recruiter-edited outreach message"""
    message = body.get("outreach_message", "")
    if not message or not isinstance(message, str):
        raise HTTPException(status_code=400, detail="outreach_message is required")
    file_storage.update_outreach_message(role_id, candidate_id, message.strip())
    return {"outreach_message": message.strip(), "message": "Outreach saved"}


@app.put("/api/roles/{role_id}/candidates/{candidate_id}/status")
async def update_candidate_status(role_id: str, candidate_id: str, status: Dict[str, Any]):
    """Update candidate status (column, color, etc.)"""
    file_storage.update_candidate_status(role_id, candidate_id, status)
    return {"message": "Candidate status updated successfully"}


@app.delete("/api/roles/{role_id}/candidates/{candidate_id}")
async def delete_candidate(role_id: str, candidate_id: str):
    """Delete a candidate and their data (interview, etc.)."""
    if not file_storage.get_candidate(role_id, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found")
    success = file_storage.delete_candidate(role_id, candidate_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete candidate")
    return {"message": "Candidate deleted successfully"}


@app.post("/api/hr-briefings")
async def upload_hr_briefing(
    file: UploadFile = File(...),
    role_ids: Optional[str] = Form(None),
):
    """Upload HR briefing audio file"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio upload")
    role_id_list = (
        [r.strip() for r in role_ids.split(",") if r.strip()] if role_ids else []
    )
    # Save file (returns tuple: file_path, briefing_id)
    file_path, briefing_id = file_storage.save_hr_briefing(
        file.filename, content, file.content_type
    )

    # Transcribe audio (use async version)
    transcription = await audio_transcription.transcribe_async(file_path)

    # Use HR Briefing Agent
    briefing_data = await hr_briefing_agent.process_briefing(transcription)

    # Save briefing (reuse the briefing_id from file save)
    briefing_id = file_storage.create_hr_briefing(
        briefing_data,
        role_id_list,
        briefing_id=briefing_id,
    )

    return {"message": "HR briefing processed successfully", "briefing_id": briefing_id, "briefing": briefing_data}


@app.get("/api/hr-briefings")
async def get_hr_briefings():
    """Get all HR briefings"""
    briefings = file_storage.get_all_hr_briefings()
    return {"briefings": briefings}


@app.get("/api/roles/{role_id}/hr-briefing")
async def get_role_hr_briefing(role_id: str):
    """Get HR briefing for a role"""
    briefing = file_storage.get_role_hr_briefing(role_id)
    return {"briefing": briefing}


class HRBriefingRolesUpdate(BaseModel):
    role_ids: List[str] = []


@app.put("/api/hr-briefings/{briefing_id}/roles")
async def update_hr_briefing_roles(briefing_id: str, body: HRBriefingRolesUpdate):
    """Update assigned roles for a briefing (replaces existing)."""
    success = file_storage.update_hr_briefing_roles(briefing_id, body.role_ids)
    if not success:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return {"message": "Assigned roles updated", "role_ids": body.role_ids}


@app.post("/api/roles/{role_id}/candidates/{candidate_id}/interview")
async def upload_interview(role_id: str, candidate_id: str, file: UploadFile = File(...)):
    """Upload interview audio file"""
    # Save file (returns tuple: file_path, interview_id)
    file_path, interview_id = file_storage.save_interview_audio(role_id, candidate_id, file)
    
    # Get role data to pass requirement fields (synchronous call, no await)
    role = file_storage.get_role(role_id)
    
    # Transcribe audio (use async version)
    transcription = await audio_transcription.transcribe_async(file_path)
    
    # Use Interview Assistant Agent
    interview_data = await interview_assistant.process_interview(transcription, role_id, candidate_id, role)
    
    # Save interview data
    file_storage.save_interview_data(role_id, candidate_id, interview_data)
    # Move candidate to Evaluation when interview is completed
    if interview_data.get("interview_completed", True):
        file_storage.update_candidate_status(role_id, candidate_id, {"column": "evaluation"})
    return {"message": "Interview processed successfully", "interview": interview_data}


@app.get("/api/roles/{role_id}/candidates/{candidate_id}/interview")
async def get_interview(role_id: str, candidate_id: str):
    """Get interview data for a candidate"""
    interview = file_storage.get_interview_data(role_id, candidate_id)
    return {"interview": interview}


class InterviewUpdate(BaseModel):
    """Manual interview data (no audio required). Mark as completed for evaluation."""
    summary: Optional[str] = None
    transcription: Optional[str] = None
    candidate_responses: Optional[Dict[str, str]] = None
    fit_score: Optional[int] = None
    strengths: Optional[List[str]] = None
    concerns: Optional[List[str]] = None
    recommendation: Optional[str] = None  # yes | no | maybe
    interview_completed: Optional[bool] = True


@app.put("/api/roles/{role_id}/candidates/{candidate_id}/interview")
async def update_interview_manual(role_id: str, candidate_id: str, body: InterviewUpdate):
    """Save or update interview details manually and mark as completed (no audio upload)."""
    existing = file_storage.get_interview_data(role_id, candidate_id) or {}
    updated = {
        **existing,
        "summary": body.summary if body.summary is not None else existing.get("summary"),
        "transcription": body.transcription if body.transcription is not None else existing.get("transcription"),
        "candidate_responses": body.candidate_responses if body.candidate_responses is not None else existing.get("candidate_responses", {}),
        "fit_score": body.fit_score if body.fit_score is not None else existing.get("fit_score"),
        "strengths": body.strengths if body.strengths is not None else existing.get("strengths", []),
        "concerns": body.concerns if body.concerns is not None else existing.get("concerns", []),
        "recommendation": (lambda r: r if r in ("yes", "no", "maybe") else "maybe")((body.recommendation or existing.get("recommendation") or "maybe").lower()),
        "interview_completed": body.interview_completed if body.interview_completed is not None else True,
    }
    # Drop None values so we don't overwrite with None
    updated = {k: v for k, v in updated.items() if v is not None}
    file_storage.save_interview_data(role_id, candidate_id, updated)
    if updated.get("interview_completed", True):
        file_storage.update_candidate_status(role_id, candidate_id, {"column": "evaluation"})
    return {"message": "Interview saved", "interview": updated}


class InterviewGuidanceRequest(BaseModel):
    candidate: Optional[Dict[str, Any]] = {}
    jd: Optional[Dict[str, Any]] = {}
    briefing: Optional[Dict[str, Any]] = {}
    current_transcription: Optional[str] = ""


@app.post("/api/roles/{role_id}/candidates/{candidate_id}/interview/guidance")
async def get_interview_guidance(role_id: str, candidate_id: str, request_data: InterviewGuidanceRequest):
    """Get real-time interview guidance"""
    candidate = request_data.candidate or {}
    jd = request_data.jd or {}
    briefing = request_data.briefing or {}
    current_transcription = request_data.current_transcription or ""
    
    # Get role data to access candidate_requirement_fields
    role = file_storage.get_role(role_id)
    
    # Get existing interview to see what's already collected
    existing_interview = file_storage.get_interview_data(role_id, candidate_id)
    
    # Use Interview Assistant Agent to generate guidance
    guidance = await interview_assistant.generate_guidance(
        candidate, jd, briefing, current_transcription, existing_interview, role
    )
    
    return {"guidance": guidance}


@app.post("/api/roles/{role_id}/candidates/evaluate")
async def evaluate_candidate(role_id: str, query: Dict[str, Any]):
    """Evaluate candidate using LLM chat. Only includes candidates in Evaluation column who have completed interviews.
    Accepts conversation_history for context-aware follow-up questions."""
    role = file_storage.get_role(role_id)
    jd = file_storage.get_parsed_jd(role_id)
    briefing = file_storage.get_role_hr_briefing(role_id)
    all_candidates = file_storage.get_candidates(role_id)

    # Filter: only candidates in Evaluation column with completed interviews
    candidates_to_evaluate = []
    for c in all_candidates:
        if c.get("column") != "evaluation":
            continue
        if c.get("not_pushing_forward"):
            continue
        interview = file_storage.get_interview_data(role_id, c.get("id"))
        if not interview:
            continue
        cand = dict(c)
        cand["interview"] = interview
        candidates_to_evaluate.append(cand)

    if not candidates_to_evaluate:
        return {
            "response": "No candidates are currently eligible for evaluation. To use the Evaluation Chat:\n\n"
            "1. Move candidates to the **Evaluation** column (from Follow-up)\n"
            "2. Complete their interviews in the **Interview Helper** tab (upload or record interview audio)\n\n"
            "Only candidates in the Evaluation column with completed interviews can be evaluated."
        }

    conversation_history = query.get("conversation_history") or []
    if not isinstance(conversation_history, list):
        conversation_history = []

    response = await evaluation_agent.evaluate(
        query.get("question", ""),
        role, None, jd, briefing, None, candidates_to_evaluate,
        conversation_history=conversation_history
    )

    return {"response": response}


@app.get("/api/roles/{role_id}/evaluation-chat")
async def get_evaluation_chat(role_id: str):
    """Get saved evaluation chat messages for a role"""
    messages = file_storage.get_evaluation_chat(role_id)
    return {"messages": messages}


@app.put("/api/roles/{role_id}/evaluation-chat")
async def save_evaluation_chat(role_id: str, data: Dict[str, Any]):
    """Save evaluation chat messages for a role"""
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    file_storage.save_evaluation_chat(role_id, messages)
    return {"message": "Chat saved", "messages": messages}


@app.delete("/api/roles/{role_id}/evaluation-chat")
async def clear_evaluation_chat(role_id: str):
    """Clear evaluation chat for a role"""
    file_storage.save_evaluation_chat(role_id, [])
    return {"message": "Chat cleared"}


@app.post("/api/consents/generate")
async def generate_consent(consent_params: Dict[str, Any]):
    """Generate consent form"""
    consent_form = await consent_engine.generate_consent(consent_params)
    return {"consent_form": consent_form}


@app.get("/api/consents")
async def get_consents():
    """Get all consent forms"""
    consents = file_storage.get_all_consents()
    return {"consents": consents}


@app.get("/api/consent-templates")
async def get_consent_templates():
    """Get all consent templates"""
    templates = file_storage.get_all_consent_templates()
    return {"templates": templates}


@app.post("/api/consent-templates")
async def create_consent_template(data: Dict[str, Any]):
    """Create or update a consent template"""
    name = data.get("name", "Untitled")
    content = data.get("content", "")
    content_id = data.get("id")
    content_id = file_storage.save_consent_template(name, content, content_id)
    return {"id": content_id, "message": "Consent template saved"}


@app.delete("/api/consent-templates/{content_id}")
async def delete_consent_template(content_id: str):
    """Delete a consent template"""
    success = file_storage.delete_consent_template(content_id)
    if not success:
        raise HTTPException(status_code=404, detail="Consent template not found")
    return {"message": "Consent template deleted"}


@app.post("/api/roles/{role_id}/candidates/{candidate_id}/send-consent")
async def send_consent_email(role_id: str, candidate_id: str, consent_data: Dict[str, Any]):
    """Send consent form email to candidate (records the send)"""
    candidate = file_storage.get_candidate(role_id, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    success = file_storage.send_consent_email(role_id, candidate_id, consent_data)
    return {"message": "Consent email sent", "candidate": file_storage.get_candidate(role_id, candidate_id)}


@app.post("/api/roles/{role_id}/candidates/{candidate_id}/simulate-consent-reply")
async def simulate_consent_reply(role_id: str, candidate_id: str, params: Dict[str, Any]):
    """Simulate candidate consent reply (I CONSENT / I DO NOT CONSENT)"""
    candidate = file_storage.get_candidate(role_id, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    consent_status = params.get("consent_status", "consented")
    if consent_status == "consented":
        reply_content = f"Hi,\n\nI CONSENT\n\nBest regards,\n{candidate.get('name', 'Candidate')}"
        response_text = "I CONSENT"
    else:
        reply_content = f"Hi,\n\nI DO NOT CONSENT. Thank you for your time.\n\nBest regards,\n{candidate.get('name', 'Candidate')}"
        response_text = "I DO NOT CONSENT"
        
    analysis = await email_monitor.analyze_email(reply_content, candidate.get("name"))
    reply_data = {
        "content": reply_content,
        "sentiment": analysis.get("sentiment", "positive" if consent_status == "consented" else "neutral"),
        "intent": analysis.get("intent", "interested" if consent_status == "consented" else "not_interested"),
        "analysis": analysis,
        "consent_status": consent_status,
        "response": response_text,
    }
    file_storage.record_consent_reply(role_id, candidate_id, reply_data)
    return {"reply": reply_data, "candidate": file_storage.get_candidate(role_id, candidate_id)}


@app.post("/api/roles/{role_id}/candidates/{candidate_id}/simulate-outreach-reply")
async def simulate_outreach_reply(role_id: str, candidate_id: str, params: Dict[str, Any]):
    """
    Simulate a reply from the candidate to your outreach (as if tracked from Gmail/Outlook).
    reply_type: 'positive' (good / interested) or 'negative' (bad / not interested).
    On positive, candidate is moved to follow-up so you can send the consent form.
    """
    try:
        candidate = file_storage.get_candidate(role_id, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        reply_type_param = (params.get("reply_type") or "positive").lower()
        reply_type = "positive" if reply_type_param in ("positive", "good", "interested") else "negative"
        outreach_message = candidate.get("outreach_message") or "We would like to discuss a role with you."
        simulation_params = {
            "candidate_name": candidate.get("name", "Candidate"),
            "candidate_profile": (candidate.get("summary") or "")[:500],
            "outreach_message": (outreach_message or "")[:2000],
            "reply_type": reply_type,
        }
        reply = await simulation_agent.generate_candidate_reply(simulation_params)
        if not isinstance(reply, dict):
            reply = {"body": str(reply), "subject": "Re: Your outreach", "sentiment": reply_type}
        body = reply.get("body") or reply.get("content") or "Thank you for your message."
        try:
            analysis = await email_monitor.analyze_email(body, candidate.get("name"))
        except Exception:
            analysis = {
                "sentiment": "positive" if reply_type == "positive" else "negative",
                "intent": "interested" if reply_type == "positive" else "not_interested",
                "key_points": [],
                "recommended_action": "Follow up" if reply_type == "positive" else "No action",
            }
        reply_data = {
            "content": body,
            "subject": reply.get("subject", "Re: Your outreach"),
            "sentiment": analysis.get("sentiment", reply.get("sentiment", "positive" if reply_type == "positive" else "negative")),
            "intent": analysis.get("intent", "interested" if reply_type == "positive" else "not_interested"),
            "analysis": analysis,
        }
        file_storage.record_outreach_reply(role_id, candidate_id, reply_data, move_to_follow_up_if_positive=True)
        if reply_data.get("sentiment") != "positive":
            file_storage.update_candidate_status(role_id, candidate_id, {"not_pushing_forward": True})
        return {"reply": reply_data, "candidate": file_storage.get_candidate(role_id, candidate_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulate outreach reply failed: {str(e)}")


@app.post("/api/simulation/candidate-reply")
async def simulate_candidate_reply(simulation_params: Dict[str, Any]):
    """Simulate candidate reply"""
    reply = await simulation_agent.generate_candidate_reply(simulation_params)
    return {"reply": reply}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

