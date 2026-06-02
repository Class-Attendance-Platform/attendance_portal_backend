"""
Offline QR Attendance Server
=============================
Runs on the teacher's device (laptop or phone via termux).
Students on the same WiFi hit this server to submit attendance.

Flow:
  1. Teacher POSTs /session/start  →  gets session_id + QR code (base64 PNG)
  2. QR code is shown on teacher's screen / sent to student apps (deep link)
  3. Students POST /checkin  →  server verifies with real backend, logs locally
  4. Teacher GETs /session/status  →  sees live submissions
  5. Teacher POSTs /session/stop  →  server bulk-commits all logs to real backend

Auth:
  - Teacher endpoints require X-Teacher-Token header (set during start)
  - Student checkin requires no auth (they're on local WiFi)
"""

import base64
import io
import os
import time
import uuid
import secrets
from datetime import datetime
from typing import Optional

import httpx
import qrcode
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from decouple import config

# ── Config ────────────────────────────────────────────────────────────────────

BACKEND_URL = config('BACKEND_URL', default='http://127.0.0.1:8000')
PORT        = int(config('PORT', default=8001))

app = FastAPI(
    title='Offline QR Attendance Server',
    description='Local WiFi attendance server for offline QR mode.',
    version='1.0.0',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── In-memory state ───────────────────────────────────────────────────────────
# Only one active session at a time per server instance.

class SessionState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.session_id: Optional[str]       = None
        self.course_info_id: Optional[str]   = None
        self.teacher_token: Optional[str]    = None
        self.backend_token: Optional[str]    = None   # JWT from real backend
        self.start_time: Optional[float]     = None
        self.end_time: Optional[float]       = None
        self.is_active: bool                 = False
        # { student_int_id: { "name": str, "mac": str, "time": str } }
        self.submissions: dict               = {}
        # Set of mac addresses already bound { mac: student_int_id }
        self.mac_bindings: dict              = {}

    @property
    def time_left(self) -> int:
        if not self.end_time:
            return 0
        return max(0, int(self.end_time - time.time()))

    @property
    def is_expired(self) -> bool:
        return self.end_time is not None and time.time() > self.end_time


state = SessionState()


# ── Schemas ───────────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    course_info_id:  str
    duration_seconds: int = 300
    backend_token:   str       # Teacher's JWT from the real backend
    teacher_local_ip: Optional[str] = None  # Override auto-detected IP


class CheckinRequest(BaseModel):
    student_id:  int    # numeric student id e.g. 2302001
    mac_address: str


class StopSessionRequest(BaseModel):
    pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    """Best-effort local IP detection."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def generate_qr_base64(data: str) -> str:
    """Generate QR code and return as base64 PNG string."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img    = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('utf-8')


async def verify_device_with_backend(student_id: int, mac_address: str, backend_token: str) -> dict:
    """
    Calls real backend to verify device binding.
    Returns { "status": "verified"|"unbound"|"mismatch", "student_id": int }
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f'{BACKEND_URL}/api/student/verify-device/{student_id}/',
            params={'mac_address': mac_address},
            headers={'Authorization': f'Bearer {backend_token}'},
        )
        if resp.status_code == 200:
            return resp.json()
        return {'status': 'error', 'message': resp.text}


async def commit_to_backend(backend_token: str, course_info_id: str, submissions: dict) -> dict:
    """
    Bulk commits offline submissions to the real backend.
    Calls the session start (offline) → checkin for each student → stop.
    """
    results = {'committed': [], 'failed': []}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Start an offline session on the real backend
        start_resp = await client.post(
            f'{BACKEND_URL}/api/sessions/start/',
            json={
                'course_info_id': course_info_id,
                'mode': 'QR_OFFLINE',
                'duration_seconds': 60,  # Short — we'll stop it immediately
            },
            headers={'Authorization': f'Bearer {backend_token}'},
        )

        if start_resp.status_code != 201:
            return {'error': f'Failed to start backend session: {start_resp.text}'}

        session_data = start_resp.json()
        session_id   = session_data['session']['id']

        # Mark each submitted student as present via manual mark
        for student_int_id, info in submissions.items():
            mark_resp = await client.post(
                f'{BACKEND_URL}/api/sessions/{session_id}/mark/',
                json={
                    'student_id': info['profile_uuid'],
                    'status': 'PRESENT',
                    'notes': f'QR offline attendance. MAC: {info["mac"]}',
                },
                headers={'Authorization': f'Bearer {backend_token}'},
            )
            if mark_resp.status_code == 200:
                results['committed'].append(int(student_int_id))
            else:
                results['failed'].append(int(student_int_id))

        # Stop the session — commits to DB
        await client.post(
            f'{BACKEND_URL}/api/sessions/{session_id}/stop/',
            headers={'Authorization': f'Bearer {backend_token}'},
        )

    return results


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get('/', response_class=HTMLResponse)
async def root():
    """Simple status page."""
    active_html = ''
    if state.is_active:
        active_html = f"""
        <div class="active">
            <h2>✅ Session Active</h2>
            <p>Course: {state.course_info_id}</p>
            <p>Time left: {state.time_left}s</p>
            <p>Submissions: {len(state.submissions)}</p>
        </div>
        """
    else:
        active_html = '<div class="inactive"><h2>⏸ No Active Session</h2></div>'

    return f"""
    <html>
    <head>
        <title>Offline Attendance Server</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; }}
            .active {{ background: #d4edda; padding: 20px; border-radius: 8px; }}
            .inactive {{ background: #f8d7da; padding: 20px; border-radius: 8px; }}
            h1 {{ color: #1F4E79; }}
        </style>
        <meta http-equiv="refresh" content="5">
    </head>
    <body>
        <h1>📡 Offline Attendance Server</h1>
        <p>Server IP: <strong>{get_local_ip()}:{PORT}</strong></p>
        {active_html}
        <hr>
        <p><small>API docs: <a href="/docs">/docs</a></small></p>
    </body>
    </html>
    """


@app.post('/session/start')
async def start_session(body: StartSessionRequest):
    """Teacher starts an offline attendance session."""
    if state.is_active and not state.is_expired:
        raise HTTPException(status_code=400, detail='A session is already active. Stop it first.')

    # Reset state
    state.reset()

    # Generate a unique teacher token for this session
    teacher_token = secrets.token_hex(16)

    # Detect local IP
    local_ip = body.teacher_local_ip or get_local_ip()

    # Build the deep link / QR payload
    # React Native app will intercept this deep link
    session_id = str(uuid.uuid4())
    checkin_url = f'attendanceapp://offline-checkin?server=http://{local_ip}:{PORT}&session={session_id}'

    # Generate QR code
    qr_b64 = generate_qr_base64(checkin_url)

    # Set state
    state.session_id      = session_id
    state.course_info_id  = body.course_info_id
    state.teacher_token   = teacher_token
    state.backend_token   = body.backend_token
    state.start_time      = time.time()
    state.end_time        = time.time() + body.duration_seconds
    state.is_active       = True
    state.submissions     = {}
    state.mac_bindings    = {}

    return {
        'success': True,
        'session': {
            'session_id':       session_id,
            'course_info_id':   body.course_info_id,
            'duration_seconds': body.duration_seconds,
            'time_left':        body.duration_seconds,
            'server_url':       f'http://{local_ip}:{PORT}',
            'checkin_url':      checkin_url,
            'teacher_token':    teacher_token,
        },
        'qr_code': f'data:image/png;base64,{qr_b64}',
    }


@app.get('/session/status')
async def session_status(x_teacher_token: Optional[str] = Header(None)):
    """Get live session status and submissions."""
    if not state.is_active:
        return {'success': True, 'active': False}

    if state.is_expired:
        state.is_active = False
        return {'success': True, 'active': False, 'message': 'Session expired.'}

    # Verify teacher token
    if x_teacher_token != state.teacher_token:
        raise HTTPException(status_code=403, detail='Invalid teacher token.')

    return {
        'success':   True,
        'active':    True,
        'session_id': state.session_id,
        'time_left': state.time_left,
        'submissions': [
            {
                'student_id': int(sid),
                'name':       info['name'],
                'time':       info['time'],
            }
            for sid, info in state.submissions.items()
        ],
    }


@app.post('/checkin')
async def checkin(body: CheckinRequest):
    """
    Student submits attendance.
    Called by the React Native app when on the same WiFi.
    """
    # Check session active
    if not state.is_active or state.is_expired:
        if state.is_expired:
            state.is_active = False
        raise HTTPException(status_code=410, detail='No active session or session expired.')

    student_key = str(body.student_id)

    # Check already submitted
    if student_key in state.submissions:
        raise HTTPException(status_code=409, detail='Attendance already recorded.')

    # Check MAC binding locally first
    if body.mac_address in state.mac_bindings:
        bound_student = state.mac_bindings[body.mac_address]
        if bound_student != body.student_id:
            raise HTTPException(
                status_code=403,
                detail='This device is already used by another student in this session.'
            )

    # Verify with real backend
    try:
        verify = await verify_device_with_backend(
            body.student_id,
            body.mac_address,
            state.backend_token,
        )
    except Exception as e:
        # If backend is unreachable, allow checkin (true offline mode)
        # but flag it
        verify = {'status': 'backend_unreachable'}

    status_val = verify.get('status', 'error')

    if status_val == 'mismatch':
        raise HTTPException(status_code=403, detail='Device not registered for this student.')

    if status_val == 'error':
        raise HTTPException(status_code=400, detail=f'Verification error: {verify.get("message")}')

    # Record submission
    state.submissions[student_key] = {
        'name':         verify.get('name', f'Student {body.student_id}'),
        'mac':          body.mac_address,
        'time':         datetime.now().strftime('%H:%M:%S'),
        'profile_uuid': verify.get('profile_uuid', ''),
        'verified':     status_val in ('verified', 'unbound', 'backend_unreachable'),
    }

    # Bind MAC locally for this session
    state.mac_bindings[body.mac_address] = body.student_id

    return {
        'success': True,
        'message': f'Attendance recorded for student {body.student_id}.',
        'time':    state.submissions[student_key]['time'],
    }


@app.post('/session/stop')
async def stop_session(x_teacher_token: Optional[str] = Header(None)):
    """
    Teacher stops the session.
    Commits all submissions to the real backend.
    """
    if not state.is_active and not state.submissions:
        raise HTTPException(status_code=400, detail='No active session.')

    # Verify teacher token
    if x_teacher_token != state.teacher_token:
        raise HTTPException(status_code=403, detail='Invalid teacher token.')

    submissions_snapshot = dict(state.submissions)
    course_info_id       = state.course_info_id
    backend_token        = state.backend_token

    # Mark inactive immediately
    state.is_active = False

    # Commit to real backend
    if submissions_snapshot:
        try:
            results = await commit_to_backend(backend_token, course_info_id, submissions_snapshot)
        except Exception as e:
            results = {'error': str(e)}
    else:
        results = {'committed': [], 'failed': []}

    state.reset()

    return {
        'success':         True,
        'message':         'Session stopped and attendance committed to backend.',
        'total_submitted': len(submissions_snapshot),
        'commit_results':  results,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    port = PORT
    print(f'\n🚀 Offline Attendance Server starting...')
    print(f'📡 Local IP: {get_local_ip()}')
    print(f'🌐 Running at: http://{get_local_ip()}:{port}')
    print(f'📖 API docs:   http://{get_local_ip()}:{port}/docs\n')
    uvicorn.run('main:app', host='0.0.0.0', port=port, reload=False)
