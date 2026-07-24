"""
MAK INFRATECH - FastAPI Web Application
Production-ready. Username/password auth with signed session cookies.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from io import BytesIO
from typing import Annotated, Optional

from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.services.users import Users
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pypdf import PdfReader

import crud
from models import (
    AppRole, AssetCreate, AssetUpdate, AutomatedScheduleUpdate,
    ContractCreate, ContractUpdate, LoginRequest, MaintenanceLogCreate,
    MaintenanceLogUpdate, ScheduleType, SettingsCreate, SettingsUpdate,
    StaffCreate, StaffDocuments, StaffPosition, StaffUpdate, UnitType, AuthContext,
)

load_dotenv()

# =============================================================================
# Environment / Appwrite Client
# =============================================================================
APP_NAME = os.getenv("APP_NAME", "MAK INFRATECH Field Management")
APP_ENV = os.getenv("APP_ENV", "production")
APP_DEBUG = os.getenv("APP_DEBUG", "false").lower() == "true"

APPWRITE_ENDPOINT = os.getenv("APPWRITE_ENDPOINT")
APPWRITE_PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID")
APPWRITE_API_KEY = os.getenv("APPWRITE_API_KEY")
DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID", "default_mak_infratech_db")

SESSION_SECRET = os.getenv("SESSION_SECRET", APPWRITE_API_KEY or "change-me-in-production")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "mak_fms_session")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true"

required = {
    "APPWRITE_ENDPOINT": APPWRITE_ENDPOINT,
    "APPWRITE_PROJECT_ID": APPWRITE_PROJECT_ID,
    "APPWRITE_API_KEY": APPWRITE_API_KEY,
}
missing = [key for key, value in required.items() if not value]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(APPWRITE_PROJECT_ID)
client.set_key(APPWRITE_API_KEY)

databases = Databases(client)
storage = Storage(client)
users = Users(client)

# =============================================================================
# FastAPI App
# =============================================================================
app = FastAPI(
    title=APP_NAME,
    version="4.1.0-production-rbac",
    debug=APP_DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# Error Handling
# =============================================================================
@app.exception_handler(crud.AppError)
async def app_error_handler(_: Request, exc: crud.AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "details": exc.details},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if APP_DEBUG:
        return JSONResponse(status_code=500, content={"error": "Unhandled server error", "details": str(exc)})
    return JSONResponse(status_code=500, content={"error": "Unhandled server error"})


# =============================================================================
# Secure Signed Session Cookies
# =============================================================================
def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_payload(payload: dict) -> str:
    body = b64url_encode(json.dumps(payload, separators=(",", ":"), default=str).encode())
    signature = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{b64url_encode(signature)}"


def unsign_payload(token: str) -> dict:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(b64url_decode(signature), expected):
            raise ValueError("Invalid session signature")
        payload = json.loads(b64url_decode(body).decode())
        for key in ("user_id", "staff_id", "name", "position", "role"):
            if key not in payload:
                raise ValueError(f"Missing session field: {key}")
        return payload
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid session: {exc}") from exc


def set_session_cookie(response: Response, ctx: AuthContext) -> None:
    payload = {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "staff_id": ctx.staff_id,
        "name": ctx.name,
        "position": ctx.position.value,
        "role": ctx.role.value,
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=sign_payload(payload),
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Authorization header must be Bearer token")
    token = authorization[len(prefix):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token is empty")
    return token


# =============================================================================
# Auth Context Dependency
# =============================================================================
async def get_current_context(
    authorization: Annotated[Optional[str], Header()] = None,
    session_cookie: Annotated[Optional[str], Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AuthContext:
    """Resolve AuthContext from session cookie or Bearer token."""
    # 1. Try session cookie first (username/password login)
    if session_cookie:
        try:
            payload = unsign_payload(session_cookie)
            return AuthContext(
                user_id=payload["user_id"],
                email=payload.get("email"),
                staff_id=payload["staff_id"],
                name=payload["name"],
                position=StaffPosition(payload["position"]),
                role=AppRole(payload["role"]),
            )
        except HTTPException:
            pass

    # 2. Try Bearer token (JWT/Appwrite auth)
    bearer = extract_bearer_token(authorization)
    if bearer:
        try:
            return crud.resolve_auth_context(
                jwt=bearer,
                databases=databases,
                users=users,
                database_id=DATABASE_ID,
            )
        except crud.AppError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    raise HTTPException(status_code=401, detail="Authentication required")


async def optional_context(
    session_cookie: Annotated[Optional[str], Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> Optional[AuthContext]:
    if not session_cookie:
        return None
    try:
        payload = unsign_payload(session_cookie)
        return AuthContext(
            user_id=payload["user_id"],
            email=payload.get("email"),
            staff_id=payload["staff_id"],
            name=payload["name"],
            position=StaffPosition(payload["position"]),
            role=AppRole(payload["role"]),
        )
    except Exception:
        return None



# =============================================================================
# HTML Shell & UI Components
# =============================================================================
def shell_html(title: str, body: str, ctx: Optional[AuthContext] = None) -> str:
    nav_links = ""
    if ctx:
        nav_links = f"""
        <a class="rounded-xl px-3 py-2 text-sm font-bold text-white/90 hover:bg-white/10" href="/">Dashboard</a>
        <a class="rounded-xl px-3 py-2 text-sm font-bold text-white/90 hover:bg-white/10" href="/contracts">Contracts</a>
        <a class="rounded-xl px-3 py-2 text-sm font-bold text-white/90 hover:bg-white/10" href="/assets">Assets</a>
        <a class="rounded-xl px-3 py-2 text-sm font-bold text-white/90 hover:bg-white/10" href="/staff">Staff</a>
        <a class="rounded-xl px-3 py-2 text-sm font-bold text-white/90 hover:bg-white/10" href="/technician">Technician</a>
        <a class="rounded-xl px-3 py-2 text-sm font-bold text-white/90 hover:bg-white/10" href="/logout">Logout</a>
        """

    return f"""<!doctype html>
<html lang="en" class="scroll-smooth">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: Inter, system-ui, sans-serif; }}
    .glass {{ background: rgba(255,255,255,.82); backdrop-filter: blur(18px); }}
    .darkglass {{ background: rgba(15,23,42,.78); backdrop-filter: blur(18px); }}
  </style>
</head>
<body class="min-h-screen bg-slate-950 text-slate-900">
  <div class="fixed inset-0 -z-10">
    <div class="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,.25),transparent_38%),radial-gradient(circle_at_top_right,rgba(59,130,246,.24),transparent_38%),linear-gradient(135deg,#020617,#0f172a,#111827)]"></div>
  </div>
  <header class="sticky top-0 z-40 border-b border-white/10 darkglass">
    <div class="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
      <a href="/" class="flex items-center gap-3">
        <div class="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-blue-700 shadow-[0_20px_80px_rgba(34,211,238,.25)]">
          <span class="text-lg font-black text-white">MI</span>
        </div>
        <div>
          <p class="text-sm font-black tracking-wide text-white sm:text-lg">MAK INFRATECH</p>
          <p class="text-xs font-semibold text-cyan-100/80">Field Management System</p>
        </div>
      </a>
      <nav class="hidden items-center gap-1 md:flex">{nav_links}</nav>
      <div class="text-right text-xs text-slate-300">
        {f"<p class=\"font-bold text-white\">{ctx.name}</p><p>{ctx.role.value} &middot; {ctx.position.value}</p>" if ctx else ""}
      </div>
    </div>
  </header>
  <main class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
    {body}
  </main>
</body>
</html>"""


def login_html() -> str:
    return shell_html(
        "Login - MAK INFRATECH",
        """
        <section class="mx-auto mt-10 max-w-xl rounded-[2rem] border border-white/10 bg-white p-6 shadow-2xl sm:p-8">
          <div class="mb-6">
            <p class="text-xs font-black uppercase tracking-[.25em] text-cyan-600">Secure Login</p>
            <h1 class="mt-2 text-3xl font-black text-slate-950">MAK INFRATECH FMS</h1>
            <p class="mt-2 text-sm leading-6 text-slate-600">Sign in using your assigned username and password.</p>
          </div>
          <form method="post" action="/login" class="space-y-4">
            <input type="text" name="username" placeholder="Username" required
              class="w-full rounded-2xl border border-slate-200 px-4 py-3 focus:border-cyan-500 focus:outline-none">
            <input type="password" name="password" placeholder="Password" required
              class="w-full rounded-2xl border border-slate-200 px-4 py-3 focus:border-cyan-500 focus:outline-none">
            <button type="submit"
              class="w-full rounded-2xl bg-slate-950 px-5 py-3 font-black text-white hover:bg-slate-800 transition">
              Login
            </button>
          </form>
        </section>
        """,
    )


def stat_card(label: str, value: str, tone: str) -> str:
    colors = {
        "blue": "bg-blue-100 text-blue-700",
        "cyan": "bg-cyan-100 text-cyan-700",
        "emerald": "bg-emerald-100 text-emerald-700",
        "amber": "bg-amber-100 text-amber-700",
        "rose": "bg-rose-100 text-rose-700",
        "indigo": "bg-indigo-100 text-indigo-700",
    }
    return f"""
    <div class="glass rounded-[1.5rem] border border-white/60 p-5 shadow-xl">
      <div class="flex items-center justify-between">
        <p class="text-sm font-bold text-slate-500">{label}</p>
        <span class="rounded-xl px-3 py-1 text-xs font-black {colors.get(tone, colors['blue'])}">LIVE</span>
      </div>
      <p class="mt-4 text-4xl font-black text-slate-950">{value}</p>
    </div>
    """


def admin_dashboard(ctx: AuthContext) -> str:
    stats = crud.dashboard_stats(databases, DATABASE_ID, ctx)
    alerts = crud.get_staff_compliance_alerts(databases, DATABASE_ID)

    alert_html = ""
    if alerts:
        alert_rows = "".join(
            f"""
            <div class="rounded-2xl border border-amber-200 bg-amber-50 p-4">
              <p class="font-black text-amber-900">{a.staff_name} &middot; {a.document_type}</p>
              <p class="text-sm text-amber-800">Expires on {a.expiry_date.isoformat()} &middot; {a.days_remaining} days remaining</p>
            </div>
            """
            for a in alerts
        )
        alert_html = f"""
        <section class="mb-6 rounded-[2rem] border border-amber-300 bg-amber-100/90 p-5 shadow-xl">
          <h2 class="text-xl font-black text-amber-950">Critical HR Compliance Expiry Alerts</h2>
          <div class="mt-4 grid gap-3 md:grid-cols-2">{alert_rows}</div>
        </section>
        """

    return f"""
    {alert_html}
    <section class="mb-6 rounded-[2rem] border border-white/10 bg-white/[.08] p-6 text-white shadow-2xl">
      <p class="text-xs font-black uppercase tracking-[.25em] text-cyan-200">Admin Command Center</p>
      <h1 class="mt-2 text-3xl font-black sm:text-5xl">Full telemetry, finance, HR and technical operations.</h1>
      <p class="mt-3 max-w-3xl text-sm text-slate-200">This Admin view includes business metrics, VAT/TRN settings, staff payroll, compliance and operational maintenance visibility.</p>
    </section>
    <section class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {stat_card("Active Contracts", str(stats["active_contracts"]), "blue")}
      {stat_card("Pending EMIs", str(stats["pending_emis"]), "emerald")}
      {stat_card("Pending PPMs", str(stats["pending_ppms"]), "amber")}
      {stat_card("Assets Tracked", str(stats["assets_tracked"]), "indigo")}
    </section>
    <section class="mt-6 grid gap-6 lg:grid-cols-2">
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <h2 class="text-xl font-black">Financial Summary</h2>
        <div class="mt-4 grid gap-3 sm:grid-cols-2">
          <div class="rounded-2xl bg-slate-950 p-5 text-white">
            <p class="text-sm text-slate-400">Total Contract Value</p>
            <p class="mt-2 text-3xl font-black">AED {stats.get("contract_value_total", 0):,.2f}</p>
          </div>
          <div class="rounded-2xl bg-emerald-600 p-5 text-white">
            <p class="text-sm text-emerald-100">Pending EMI Amount</p>
            <p class="mt-2 text-3xl font-black">AED {stats.get("pending_emi_amount", 0):,.2f}</p>
          </div>
        </div>
      </div>
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <h2 class="text-xl font-black">Quick Admin Actions</h2>
        <div class="mt-4 grid gap-3">
          <a href="/contracts" class="rounded-2xl bg-slate-950 px-5 py-4 font-black text-white text-center hover:bg-slate-800 transition">Create / Manage Contracts</a>
          <a href="/staff" class="rounded-2xl bg-purple-600 px-5 py-4 font-black text-white text-center hover:bg-purple-700 transition">Manage Staff & HR</a>
          <a href="/technician" class="rounded-2xl bg-blue-600 px-5 py-4 font-black text-white text-center hover:bg-blue-700 transition">Open Technician Console</a>
          <a href="/docs" class="rounded-2xl border border-slate-200 bg-white px-5 py-4 font-black text-slate-900 text-center hover:bg-slate-50 transition">API Documentation</a>
        </div>
      </div>
    </section>
    """


def accountant_dashboard(ctx: AuthContext) -> str:
    stats = crud.dashboard_stats(databases, DATABASE_ID, ctx)
    settings = crud.get_settings(databases, DATABASE_ID)

    return f"""
    <section class="mb-6 rounded-[2rem] border border-white/10 bg-white/[.08] p-6 text-white shadow-2xl">
      <p class="text-xs font-black uppercase tracking-[.25em] text-cyan-200">Accountant Matrix</p>
      <h1 class="mt-2 text-3xl font-black sm:text-5xl">Financial contracts, EMI collections, VAT and payroll.</h1>
      <p class="mt-3 text-sm text-slate-200">Technical equipment parameters are hidden from this view by server-side rendering.</p>
    </section>
    <section class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {stat_card("Active Contracts", str(stats["active_contracts"]), "blue")}
      {stat_card("Pending EMIs", str(stats["pending_emis"]), "emerald")}
      {stat_card("Total Contract Value", f'AED {stats.get("contract_value_total", 0):,.2f}', "indigo")}
      {stat_card("Pending EMI Amount", f'AED {stats.get("pending_emi_amount", 0):,.2f}', "amber")}
    </section>
    <section class="mt-6 grid gap-6 lg:grid-cols-2">
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <h2 class="text-xl font-black">Tax Settings</h2>
        <form method="post" action="/ui/settings" class="mt-4 space-y-3">
          <input name="company_name" value="{settings.get("company_name", "")}" class="w-full rounded-2xl border border-slate-200 px-4 py-3" placeholder="Company Name">
          <input name="trn_number" value="{settings.get("trn_number", "")}" class="w-full rounded-2xl border border-slate-200 px-4 py-3" placeholder="TRN Number">
          <input name="vat_percentage" type="number" step="0.01" value="{settings.get("vat_percentage", 5.0)}" class="w-full rounded-2xl border border-slate-200 px-4 py-3" placeholder="VAT %">
          <button class="rounded-2xl bg-slate-950 px-5 py-3 font-black text-white hover:bg-slate-800 transition">Save VAT/TRN</button>
        </form>
      </div>
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <h2 class="text-xl font-black">Financial Actions</h2>
        <div class="mt-4 grid gap-3">
          <a href="/contracts" class="rounded-2xl bg-slate-950 px-5 py-4 font-black text-white text-center hover:bg-slate-800 transition">View Contract Values & EMI Schedules</a>
          <a href="/api/schedules?type=EMI" class="rounded-2xl bg-emerald-600 px-5 py-4 font-black text-white text-center hover:bg-emerald-700 transition">EMI API Feed</a>
        </div>
      </div>
    </section>
    """



def technician_dashboard(ctx: AuthContext) -> str:
    contracts = crud.list_contracts(databases, DATABASE_ID, ctx)
    options = "".join(
        f'<option value="{c["$id"]}">{c.get("building_villa_name")} &middot; {c.get("customer_name")}</option>'
        for c in contracts
    )

    return f"""
    <section class="mb-6 rounded-[2rem] border border-white/10 bg-white/[.08] p-5 text-white shadow-2xl">
      <p class="text-xs font-black uppercase tracking-[.25em] text-cyan-200">Technician Mobile Console</p>
      <h1 class="mt-2 text-3xl font-black">On-site maintenance logging</h1>
      <p class="mt-3 text-sm text-slate-200">Cost structures, revenue, salaries and contract values are stripped out server-side.</p>
    </section>
    <section class="grid gap-6 lg:grid-cols-[.9fr_1.1fr]">
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <h2 class="text-xl font-black">Select Site</h2>
        <select id="contractSelect" class="mt-4 w-full rounded-2xl border border-slate-200 px-4 py-3">
          <option value="">Choose building / villa</option>
          {options}
        </select>
        <button onclick="loadAssets()" class="mt-3 w-full rounded-2xl bg-slate-950 px-5 py-3 font-black text-white hover:bg-slate-800 transition">Load Flats & Units</button>
        <button onclick="downloadPdf()" class="mt-3 w-full rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-700 px-5 py-3 font-black text-white shadow-[0_20px_80px_rgba(34,211,238,.25)] hover:opacity-90 transition">Generate Scribus PPM PDF</button>
      </div>
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <h2 class="text-xl font-black">Multi-Asset Technical Input Grid</h2>
        <div id="assetGrid" class="mt-4 space-y-4">
          <div class="rounded-2xl border border-dashed border-slate-300 bg-white/60 p-5 text-sm text-slate-500">Select a site and load assets.</div>
        </div>
      </div>
    </section>
    <script>
      async function api(path, options = {{}}) {{
        const res = await fetch(path, options);
        if (!res.ok) {{
          let msg = res.statusText;
          try {{ const e = await res.json(); msg = e.error || e.detail || msg; }} catch (_) {{}}
          alert(msg);
          throw new Error(msg);
        }}
        return res.json();
      }}
      async function loadAssets() {{
        const contractId = document.getElementById('contractSelect').value;
        if (!contractId) return alert('Select a site');
        const assets = await api(`/api/assets?contract_id=${{encodeURIComponent(contractId)}}`);
        const grid = document.getElementById('assetGrid');
        grid.innerHTML = '';
        if (!assets.length) {{
          grid.innerHTML = '<div class="rounded-2xl border border-dashed border-slate-300 bg-white/60 p-5 text-sm text-slate-500">No assets found.</div>';
          return;
        }}
        for (const asset of assets) {{
          const card = document.createElement('div');
          card.className = 'rounded-2xl border border-slate-200 bg-white p-4';
          card.innerHTML = `
            <div class="flex items-start justify-between gap-3">
              <div>
                <p class="text-xs font-black text-slate-400">${{asset.unit_type || ''}}</p>
                <h3 class="text-lg font-black">${{asset.flat_villa_no || ''}} &middot; ${{asset.brand || ''}}</h3>
                <p class="text-xs text-slate-500">Serial: ${{asset.serial_no || '&#8212;'}}</p>
              </div>
            </div>
            <form class="mt-4 space-y-3" onsubmit="submitLog(event, '${{asset.$id}}')">
              <select name="work_category" class="w-full rounded-xl border px-3 py-2">
                <option value="HVAC">HVAC</option>
                <option value="Electrical">Electrical</option>
                <option value="Plumbing">Plumbing</option>
                <option value="Painting">Painting</option>
              </select>
              <textarea name="job_description" required rows="2" class="w-full rounded-xl border px-3 py-2" placeholder="Job description"></textarea>
              <div class="grid grid-cols-3 gap-2">
                <input name="suction_pressure" type="number" step="0.01" class="rounded-xl border px-3 py-2" placeholder="Suction">
                <input name="discharge_pressure" type="number" step="0.01" class="rounded-xl border px-3 py-2" placeholder="Discharge">
                <input name="ampere_reading" type="number" step="0.01" class="rounded-xl border px-3 py-2" placeholder="Ampere">
              </div>
              <textarea name="materials_used" rows="2" class="w-full rounded-xl border px-3 py-2" placeholder="Materials used"></textarea>
              <div class="rounded-xl bg-slate-50 p-3 text-sm">
                <p class="font-black">Checklist</p>
                <label class="mt-2 block"><input type="checkbox" name="hvac_checklist" value="Filter cleaned"> Filter cleaned</label>
                <label class="block"><input type="checkbox" name="hvac_checklist" value="Drain checked"> Drain checked</label>
                <label class="block"><input type="checkbox" name="electrical_checklist" value="Breaker checked"> Breaker checked</label>
                <label class="block"><input type="checkbox" name="plumbing_checklist" value="Leakage checked"> Leakage checked</label>
                <label class="block"><input type="checkbox" name="painting_checklist" value="Touch-up required"> Touch-up required</label>
              </div>
              <input name="image" type="file" accept="image/*,application/pdf" class="w-full rounded-xl border px-3 py-2">
              <button class="w-full rounded-xl bg-blue-600 px-4 py-3 font-black text-white hover:bg-blue-700 transition">Save Maintenance Log</button>
            </form>
          `;
          grid.appendChild(card);
        }}
      }}
      function checklistValues(form, name) {{
        return Array.from(form.querySelectorAll(`input[name="${{name}}"]:checked`)).map(i => i.value);
      }}
      async function submitLog(event, assetId) {{
        event.preventDefault();
        const form = event.target;
        let fileIds = [];
        const fileInput = form.querySelector('input[name="image"]');
        if (fileInput.files.length) {{
          const fd = new FormData();
          fd.append('file', fileInput.files[0]);
          const upload = await fetch('/api/uploads/maintenance', {{ method: 'POST', body: fd }});
          if (!upload.ok) {{ alert('Upload failed'); return; }}
          const uploadData = await upload.json();
          fileIds.push(uploadData.$id);
        }}
        const payload = {{
          asset_id: assetId,
          work_category: form.work_category.value,
          job_description: form.job_description.value,
          image_file_ids: fileIds,
          parameters: {{
            suction_pressure: form.suction_pressure.value ? Number(form.suction_pressure.value) : null,
            discharge_pressure: form.discharge_pressure.value ? Number(form.discharge_pressure.value) : null,
            ampere_reading: form.ampere_reading.value ? Number(form.ampere_reading.value) : null,
            materials_used: form.materials_used.value || null,
            hvac_checklist: checklistValues(form, 'hvac_checklist'),
            electrical_checklist: checklistValues(form, 'electrical_checklist'),
            plumbing_checklist: checklistValues(form, 'plumbing_checklist'),
            painting_checklist: checklistValues(form, 'painting_checklist')
          }}
        }};
        const res = await fetch('/api/maintenance-logs', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload)
        }});
        if (!res.ok) {{
          const e = await res.json();
          alert(e.error || e.detail || 'Failed');
          return;
        }}
        alert('Maintenance log saved');
        form.reset();
      }}
      async function downloadPdf() {{
        const contractId = document.getElementById('contractSelect').value;
        if (!contractId) return alert('Select a site');
        const res = await fetch(`/api/generate-pdf/${{encodeURIComponent(contractId)}}`);
        if (!res.ok) {{ alert('PDF generation failed'); return; }}
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `service-report-${{contractId}}.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }}
    </script>
    """


def render_dashboard(ctx: AuthContext) -> str:
    if ctx.role == AppRole.Admin:
        body = admin_dashboard(ctx)
    elif ctx.role == AppRole.Accountant:
        body = accountant_dashboard(ctx)
    else:
        body = technician_dashboard(ctx)
    return shell_html("MAK INFRATECH Dashboard", body, ctx)


def contracts_page(ctx: AuthContext) -> str:
    contracts = crud.list_contracts(databases, DATABASE_ID, ctx)
    rows = ""
    for c in contracts:
        financial = "" if ctx.role == AppRole.Technician else f"<td class='px-4 py-3 font-bold'>AED {float(c.get('contract_value') or 0):,.2f}</td>"
        rows += f"""
        <tr class="border-b border-slate-100">
          <td class="px-4 py-3 font-bold">{c.get("customer_name")}</td>
          <td class="px-4 py-3">{c.get("building_villa_name")}</td>
          <td class="px-4 py-3">{c.get("start_date")} &rarr; {c.get("end_date")}</td>
          {financial}
          <td class="px-4 py-3">
            <a class="rounded-xl bg-blue-600 px-3 py-2 text-xs font-black text-white hover:bg-blue-700 transition" href="/api/generate-pdf/{c['$id']}">PDF</a>
          </td>
        </tr>
        """
    financial_header = "" if ctx.role == AppRole.Technician else "<th class='px-4 py-3 text-left'>Value</th>"
    create_form = ""
    if ctx.role in {AppRole.Admin, AppRole.Accountant}:
        create_form = """
        <section class="mb-6 rounded-[2rem] bg-white p-5 shadow-xl">
          <h2 class="text-xl font-black">Create Contract</h2>
          <form method="post" action="/ui/contracts" class="mt-4 grid gap-3 md:grid-cols-2">
            <input name="customer_name" required class="rounded-2xl border px-4 py-3" placeholder="Customer Name">
            <input name="building_villa_name" required class="rounded-2xl border px-4 py-3" placeholder="Building / Villa Name">
            <input name="address" required class="rounded-2xl border px-4 py-3 md:col-span-2" placeholder="Address">
            <input name="contract_value" required type="number" step="0.01" class="rounded-2xl border px-4 py-3" placeholder="Contract Value">
            <input name="start_date" required type="date" class="rounded-2xl border px-4 py-3">
            <input name="end_date" required type="date" class="rounded-2xl border px-4 py-3">
            <input name="total_ppms_per_year" required type="number" class="rounded-2xl border px-4 py-3" placeholder="PPMs / Year">
            <input name="total_emis_per_year" required type="number" class="rounded-2xl border px-4 py-3" placeholder="EMIs / Year">
            <button class="rounded-2xl bg-slate-950 px-5 py-3 font-black text-white hover:bg-slate-800 transition md:col-span-2">Create + Auto Schedule</button>
          </form>
        </section>
        """
    body = f"""
    <section class="mb-6 rounded-[2rem] border border-white/10 bg-white/[.08] p-6 text-white shadow-2xl">
      <h1 class="text-3xl font-black">Contracts</h1>
      <p class="mt-2 text-sm text-slate-200">Multi-unit building and villa contracts with automated EMI and PPM lifecycle schedules.</p>
    </section>
    {create_form}
    <section class="overflow-hidden rounded-[2rem] bg-white shadow-xl">
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-slate-100">
            <tr>
              <th class="px-4 py-3 text-left">Customer</th>
              <th class="px-4 py-3 text-left">Building / Villa</th>
              <th class="px-4 py-3 text-left">Period</th>
              {financial_header}
              <th class="px-4 py-3 text-left">Report</th>
            </tr>
          </thead>
          <tbody>{rows or "<tr><td class='px-4 py-6 text-slate-500' colspan='5'>No contracts found.</td></tr>"}</tbody>
        </table>
      </div>
    </section>
    """
    return shell_html("Contracts - MAK INFRATECH", body, ctx)



def staff_page(ctx: AuthContext) -> str:
    crud.require_roles(ctx, {AppRole.Admin, AppRole.Accountant})
    staff_records = crud.list_staff(databases, DATABASE_ID, ctx)
    compliance_alerts = crud.get_staff_compliance_alerts(databases, DATABASE_ID)

    alert_html = ""
    if ctx.role == AppRole.Admin and compliance_alerts:
        alert_rows = "".join(
            f"""
            <div class="rounded-2xl border border-amber-200 bg-white/70 p-4">
              <p class="font-black text-slate-950">{alert.staff_name}</p>
              <p class="text-sm text-slate-700">{alert.document_type} expires on {alert.expiry_date.isoformat()}</p>
              <p class="mt-1 text-xs font-bold text-amber-700">{alert.days_remaining} days remaining</p>
            </div>
            """
            for alert in compliance_alerts
        )
        alert_html = f"""
        <section class="mb-6 rounded-[2rem] border border-amber-300 bg-amber-100 p-5 shadow-xl">
          <h2 class="text-xl font-black text-amber-950">HR Compliance Alerts</h2>
          <div class="mt-4 grid gap-3 md:grid-cols-2">{alert_rows}</div>
        </section>
        """

    rows = ""
    for staff in staff_records:
        salary_column = ""
        compliance_column = ""
        if ctx.role in {AppRole.Admin, AppRole.Accountant}:
            salary_column = f"<td class='px-4 py-3 font-bold'>AED {float(staff.get('base_salary') or 0):,.2f}</td>"
        if ctx.role == AppRole.Admin:
            documents = staff.get("documents") or {}
            compliance_column = f"""
            <td class="px-4 py-3 text-xs text-slate-600">
              Passport: {documents.get("passport_expiry") or "&#8212;"}<br>
              EID: {documents.get("eid_expiry") or "&#8212;"}<br>
              Insurance: {documents.get("insurance_expiry") or "&#8212;"}
            </td>
            """
        rows += f"""
        <tr class="border-b border-slate-100">
          <td class="px-4 py-3 font-black">{staff.get("name")}</td>
          <td class="px-4 py-3">{staff.get("position")}</td>
          <td class="px-4 py-3">{staff.get("email") or "&#8212;"}</td>
          {salary_column}
          {compliance_column}
        </tr>
        """

    salary_header = "<th class='px-4 py-3 text-left'>Base Salary</th>" if ctx.role in {AppRole.Admin, AppRole.Accountant} else ""
    compliance_header = "<th class='px-4 py-3 text-left'>Document Expiry</th>" if ctx.role == AppRole.Admin else ""

    create_form = ""
    if ctx.role == AppRole.Admin:
        create_form = """
        <section class="mb-6 rounded-[2rem] bg-white p-5 shadow-xl">
          <h2 class="text-xl font-black">Create Staff Record</h2>
          <p class="mt-1 text-sm text-slate-500">Link staff to an Appwrite user by user_id or email. Position controls server-side RBAC.</p>
          <form method="post" action="/ui/staff" class="mt-4 grid gap-3 md:grid-cols-2">
            <input name="username" required class="rounded-2xl border px-4 py-3" placeholder="Username (for login)">
            <input name="password" required type="password" class="rounded-2xl border px-4 py-3" placeholder="Password (for login)">
            <input name="name" required class="rounded-2xl border px-4 py-3" placeholder="Staff Name">
            <input name="email" type="email" class="rounded-2xl border px-4 py-3" placeholder="Email">
            <input name="user_id" class="rounded-2xl border px-4 py-3" placeholder="Appwrite User ID">
            <select name="position" required class="rounded-2xl border px-4 py-3">
              <option value="HVAC">HVAC</option>
              <option value="Electrician">Electrician</option>
              <option value="Plumber">Plumber</option>
              <option value="Painter">Painter</option>
              <option value="Accountant">Accountant</option>
              <option value="Admin">Admin</option>
            </select>
            <input name="base_salary" type="number" step="0.01" value="0" class="rounded-2xl border px-4 py-3" placeholder="Base Salary">
            <input name="passport_no" class="rounded-2xl border px-4 py-3" placeholder="Passport No">
            <input name="passport_expiry" type="date" class="rounded-2xl border px-4 py-3">
            <input name="eid_no" class="rounded-2xl border px-4 py-3" placeholder="Emirates ID No">
            <input name="eid_expiry" type="date" class="rounded-2xl border px-4 py-3">
            <input name="insurance_policy" class="rounded-2xl border px-4 py-3" placeholder="Insurance Policy">
            <input name="insurance_expiry" type="date" class="rounded-2xl border px-4 py-3">
            <button class="rounded-2xl bg-slate-950 px-5 py-3 font-black text-white hover:bg-slate-800 transition md:col-span-2">Create Staff</button>
          </form>
        </section>
        """

    body = f"""
    <section class="mb-6 rounded-[2rem] border border-white/10 bg-white/[.08] p-6 text-white shadow-2xl">
      <h1 class="text-3xl font-black">Staff & HR Compliance</h1>
      <p class="mt-2 text-sm text-slate-200">Server-filtered view for payroll, staff roles, and document expiry compliance.</p>
    </section>
    {alert_html}
    {create_form}
    <section class="overflow-hidden rounded-[2rem] bg-white shadow-xl">
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-slate-100">
            <tr>
              <th class="px-4 py-3 text-left">Name</th>
              <th class="px-4 py-3 text-left">Position</th>
              <th class="px-4 py-3 text-left">Email</th>
              {salary_header}
              {compliance_header}
            </tr>
          </thead>
          <tbody>{rows or "<tr><td class='px-4 py-6 text-slate-500' colspan='5'>No staff found.</td></tr>"}</tbody>
        </table>
      </div>
    </section>
    """
    return shell_html("Staff - MAK INFRATECH", body, ctx)


def assets_page(ctx: AuthContext) -> str:
    crud.require_roles(ctx, {AppRole.Admin, AppRole.Technician})
    contracts = crud.list_contracts(databases, DATABASE_ID, ctx)
    contract_options = "".join(
        f'<option value="{c["$id"]}">{c.get("building_villa_name")} &middot; {c.get("customer_name")}</option>'
        for c in contracts
    )
    selected_contract_id = contracts[0]["$id"] if contracts else None
    assets = crud.list_assets(databases, DATABASE_ID, ctx, selected_contract_id) if selected_contract_id else []

    rows = "".join(
        f"""
        <tr class="border-b border-slate-100">
          <td class="px-4 py-3 font-black">{asset.get("flat_villa_no")}</td>
          <td class="px-4 py-3">{asset.get("unit_type")}</td>
          <td class="px-4 py-3">{asset.get("brand")}</td>
          <td class="px-4 py-3">{asset.get("tonnage") or "&#8212;"}</td>
          <td class="px-4 py-3">{asset.get("serial_no") or "&#8212;"}</td>
        </tr>
        """
        for asset in assets
    )

    form_html = ""
    if contracts:
        form_html = f"""
        <section class="mb-6 rounded-[2rem] bg-white p-5 shadow-xl">
          <h2 class="text-xl font-black">Add Location / Asset</h2>
          <form method="post" action="/ui/assets" class="mt-4 grid gap-3 md:grid-cols-2">
            <select name="contract_id" required class="rounded-2xl border px-4 py-3">{contract_options}</select>
            <input name="flat_villa_no" required class="rounded-2xl border px-4 py-3" placeholder="Flat / Villa No">
            <select name="unit_type" required class="rounded-2xl border px-4 py-3">
              <option value="Split">Split</option>
              <option value="FCU">FCU</option>
              <option value="FAHU">FAHU</option>
              <option value="Package">Package</option>
            </select>
            <input name="brand" required class="rounded-2xl border px-4 py-3" placeholder="Brand">
            <input name="tonnage" type="number" step="0.01" class="rounded-2xl border px-4 py-3" placeholder="Tonnage">
            <input name="serial_no" class="rounded-2xl border px-4 py-3" placeholder="Serial No">
            <button class="rounded-2xl bg-slate-950 px-5 py-3 font-black text-white hover:bg-slate-800 transition md:col-span-2">Add Asset</button>
          </form>
        </section>
        """

    body = f"""
    <section class="mb-6 rounded-[2rem] border border-white/10 bg-white/[.08] p-6 text-white shadow-2xl">
      <h1 class="text-3xl font-black">Locations & Assets</h1>
      <p class="mt-2 text-sm text-slate-200">Flats, villas, AC units and maintainable equipment by contract.</p>
    </section>
    {form_html}
    <section class="overflow-hidden rounded-[2rem] bg-white shadow-xl">
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-slate-100">
            <tr>
              <th class="px-4 py-3 text-left">Flat / Villa</th>
              <th class="px-4 py-3 text-left">Unit Type</th>
              <th class="px-4 py-3 text-left">Brand</th>
              <th class="px-4 py-3 text-left">Tonnage</th>
              <th class="px-4 py-3 text-left">Serial No</th>
            </tr>
          </thead>
          <tbody>{rows or "<tr><td class='px-4 py-6 text-slate-500' colspan='5'>No assets found.</td></tr>"}</tbody>
        </table>
      </div>
    </section>
    """
    return shell_html("Assets - MAK INFRATECH", body, ctx)



# =============================================================================
# UI Routes
# =============================================================================
@app.get("/", response_class=HTMLResponse)
async def root(ctx: Annotated[Optional[AuthContext], Depends(optional_context)]) -> HTMLResponse:
    if not ctx:
        return HTMLResponse(login_html())
    return HTMLResponse(render_dashboard(ctx))


@app.get("/login", response_class=HTMLResponse)
async def login_get() -> HTMLResponse:
    return HTMLResponse(login_html())


@app.post("/login")
async def login_post(
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> RedirectResponse:
    ctx = crud.login_user(
        username=username,
        password=password,
        databases=databases,
        database_id=DATABASE_ID,
    )
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, ctx)
    return response


@app.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


@app.get("/contracts", response_class=HTMLResponse)
async def contracts_ui(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> HTMLResponse:
    return HTMLResponse(contracts_page(ctx))


@app.get("/staff", response_class=HTMLResponse)
async def staff_ui(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> HTMLResponse:
    return HTMLResponse(staff_page(ctx))


@app.get("/assets", response_class=HTMLResponse)
async def assets_ui(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> HTMLResponse:
    return HTMLResponse(assets_page(ctx))


@app.get("/technician", response_class=HTMLResponse)
async def technician_ui(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> HTMLResponse:
    crud.require_roles(ctx, {AppRole.Admin, AppRole.Technician})
    return HTMLResponse(shell_html("Technician - MAK INFRATECH", technician_dashboard(ctx), ctx))


@app.post("/ui/contracts")
async def ui_create_contract(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    customer_name: Annotated[str, Form()],
    building_villa_name: Annotated[str, Form()],
    address: Annotated[str, Form()],
    contract_value: Annotated[float, Form()],
    start_date: Annotated[str, Form()],
    end_date: Annotated[str, Form()],
    total_ppms_per_year: Annotated[int, Form()],
    total_emis_per_year: Annotated[int, Form()],
) -> RedirectResponse:
    payload = ContractCreate(
        customer_name=customer_name,
        building_villa_name=building_villa_name,
        address=address,
        contract_value=contract_value,
        start_date=start_date,
        end_date=end_date,
        total_ppms_per_year=total_ppms_per_year,
        total_emis_per_year=total_emis_per_year,
    )
    crud.create_contract(databases, DATABASE_ID, ctx, payload)
    return RedirectResponse("/contracts", status_code=303)


@app.post("/ui/settings")
async def ui_update_settings(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    company_name: Annotated[str, Form()],
    trn_number: Annotated[str, Form()],
    vat_percentage: Annotated[float, Form()],
) -> RedirectResponse:
    payload = SettingsUpdate(
        company_name=company_name,
        trn_number=trn_number,
        vat_percentage=vat_percentage,
    )
    crud.upsert_settings(databases, DATABASE_ID, ctx, payload)
    return RedirectResponse("/", status_code=303)


@app.post("/ui/staff")
async def ui_create_staff(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    name: Annotated[str, Form()],
    position: Annotated[str, Form()],
    base_salary: Annotated[float, Form()] = 0,
    email: Annotated[Optional[str], Form()] = None,
    user_id: Annotated[Optional[str], Form()] = None,
    passport_no: Annotated[Optional[str], Form()] = None,
    passport_expiry: Annotated[Optional[str], Form()] = None,
    eid_no: Annotated[Optional[str], Form()] = None,
    eid_expiry: Annotated[Optional[str], Form()] = None,
    insurance_policy: Annotated[Optional[str], Form()] = None,
    insurance_expiry: Annotated[Optional[str], Form()] = None,
) -> RedirectResponse:
    payload = StaffCreate(
        user_id=user_id or None,
        email=email or None,
        username=username,
        password=password,
        name=name,
        position=StaffPosition(position),
        base_salary=base_salary,
        documents=StaffDocuments(
            passport_no=passport_no or None,
            passport_expiry=passport_expiry or None,
            eid_no=eid_no or None,
            eid_expiry=eid_expiry or None,
            insurance_policy=insurance_policy or None,
            insurance_expiry=insurance_expiry or None,
        ),
    )
    crud.create_staff(databases, DATABASE_ID, ctx, payload)
    return RedirectResponse("/staff", status_code=303)


@app.post("/ui/assets")
async def ui_create_asset(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    contract_id: Annotated[str, Form()],
    flat_villa_no: Annotated[str, Form()],
    unit_type: Annotated[str, Form()],
    brand: Annotated[str, Form()],
    tonnage: Annotated[Optional[float], Form()] = None,
    serial_no: Annotated[Optional[str], Form()] = None,
) -> RedirectResponse:
    payload = AssetCreate(
        contract_id=contract_id,
        flat_villa_no=flat_villa_no,
        unit_type=UnitType(unit_type),
        brand=brand,
        tonnage=tonnage,
        serial_no=serial_no or None,
    )
    crud.create_asset(databases, DATABASE_ID, ctx, payload)
    return RedirectResponse("/assets", status_code=303)


# =============================================================================
# API Auth / Health
# =============================================================================
@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": APP_NAME,
        "environment": APP_ENV,
        "database": "Appwrite",
        "rbac": "server-side-master-api-key",
        "pdf": "local-scribus-pypdf",
    }


@app.post("/api/login")
async def api_login(payload: LoginRequest, response: Response) -> dict:
    """API login for username/password."""
    ctx = crud.login_user(
        username=payload.username,
        password=payload.password,
        databases=databases,
        database_id=DATABASE_ID,
    )
    set_session_cookie(response, ctx)
    return {"ok": True, "user": ctx.model_dump(mode="json")}


@app.get("/api/me")
async def api_me(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> dict:
    return ctx.model_dump(mode="json")


@app.get("/api/dashboard/stats")
async def api_dashboard_stats(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> dict:
    return crud.dashboard_stats(databases, DATABASE_ID, ctx)


# =============================================================================
# Settings API
# =============================================================================
@app.get("/api/settings")
async def api_get_settings(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> dict:
    crud.require_roles(ctx, {AppRole.Admin, AppRole.Accountant})
    return crud.get_settings(databases, DATABASE_ID)


@app.post("/api/settings")
async def api_save_settings(
    payload: SettingsCreate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.upsert_settings(databases, DATABASE_ID, ctx, payload)


# =============================================================================
# Staff API
# =============================================================================
@app.post("/api/staff")
async def api_create_staff(
    payload: StaffCreate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.create_staff(databases, DATABASE_ID, ctx, payload)


@app.get("/api/staff")
async def api_list_staff(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> list[dict]:
    return crud.list_staff(databases, DATABASE_ID, ctx)


@app.patch("/api/staff/{staff_id}")
async def api_update_staff(
    staff_id: str,
    payload: StaffUpdate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.update_staff(databases, DATABASE_ID, ctx, staff_id, payload)


@app.delete("/api/staff/{staff_id}")
async def api_delete_staff(
    staff_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.delete_staff(databases, DATABASE_ID, ctx, staff_id)
    return {"ok": True}


@app.get("/api/staff/compliance-alerts")
async def api_staff_compliance(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> list[dict]:
    crud.require_roles(ctx, {AppRole.Admin})
    return [alert.model_dump(mode="json") for alert in crud.get_staff_compliance_alerts(databases, DATABASE_ID)]


# =============================================================================
# Contracts API
# =============================================================================
@app.post("/api/contracts")
async def api_create_contract(
    payload: ContractCreate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.create_contract(databases, DATABASE_ID, ctx, payload)


@app.get("/api/contracts")
async def api_list_contracts(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> list[dict]:
    return crud.list_contracts(databases, DATABASE_ID, ctx)


@app.get("/api/contracts/{contract_id}")
async def api_get_contract(
    contract_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.get_contract(databases, DATABASE_ID, ctx, contract_id)


@app.patch("/api/contracts/{contract_id}")
async def api_update_contract(
    contract_id: str,
    payload: ContractUpdate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.update_contract(databases, DATABASE_ID, ctx, contract_id, payload)


@app.delete("/api/contracts/{contract_id}")
async def api_delete_contract(
    contract_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.delete_contract(databases, DATABASE_ID, ctx, contract_id)
    return {"ok": True}


@app.post("/api/contracts/{contract_id}/regenerate-schedules")
async def api_regenerate_contract_schedules(
    contract_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.require_roles(ctx, {AppRole.Admin, AppRole.Accountant})
    contract = crud.get_document(databases, DATABASE_ID, crud.COLLECTION_CONTRACTS, contract_id)
    schedules = crud.regenerate_contract_schedules(databases, DATABASE_ID, contract_id, contract)
    return {"ok": True, "contract_id": contract_id, "generated_count": len(schedules), "schedules": schedules}


@app.get("/api/contracts/{contract_id}/schedules")
async def api_contract_schedules(
    contract_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> list[dict]:
    return crud.list_schedules(databases, DATABASE_ID, ctx, contract_id=contract_id)


@app.get("/api/contracts/{contract_id}/assets")
async def api_contract_assets(
    contract_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> list[dict]:
    return crud.list_assets(databases, DATABASE_ID, ctx, contract_id=contract_id)


@app.get("/api/contracts/{contract_id}/maintenance-summary")
async def api_contract_maintenance_summary(
    contract_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.require_roles(ctx, {AppRole.Admin, AppRole.Technician})
    contract = crud.get_contract(databases, DATABASE_ID, ctx, contract_id)
    assets = crud.list_assets(databases, DATABASE_ID, ctx, contract_id=contract_id)
    summaries = []
    for asset in assets:
        logs = crud.list_maintenance_logs(databases, DATABASE_ID, ctx, asset_id=asset["$id"])
        summaries.append({"asset": asset, "log_count": len(logs), "latest_log": logs[0] if logs else None})
    return {"contract": contract, "asset_count": len(assets), "assets": summaries}



# =============================================================================
# Schedules API
# =============================================================================
@app.get("/api/schedules")
async def api_list_schedules(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    contract_id: Optional[str] = None,
    type: Optional[ScheduleType] = None,
) -> list[dict]:
    return crud.list_schedules(databases, DATABASE_ID, ctx, contract_id, type)


@app.patch("/api/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: str,
    payload: AutomatedScheduleUpdate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.update_schedule(databases, DATABASE_ID, ctx, schedule_id, payload)


# =============================================================================
# Assets API
# =============================================================================
@app.post("/api/assets")
async def api_create_asset(
    payload: AssetCreate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.create_asset(databases, DATABASE_ID, ctx, payload)


@app.get("/api/assets")
async def api_list_assets(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    contract_id: Optional[str] = None,
) -> list[dict]:
    return crud.list_assets(databases, DATABASE_ID, ctx, contract_id)


@app.patch("/api/assets/{asset_id}")
async def api_update_asset(
    asset_id: str,
    payload: AssetUpdate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.update_asset(databases, DATABASE_ID, ctx, asset_id, payload)


@app.delete("/api/assets/{asset_id}")
async def api_delete_asset(
    asset_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.delete_asset(databases, DATABASE_ID, ctx, asset_id)
    return {"ok": True}


# =============================================================================
# Maintenance Logs API
# =============================================================================
@app.post("/api/maintenance-logs")
async def api_create_maintenance_log(
    payload: MaintenanceLogCreate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.create_maintenance_log(databases, DATABASE_ID, ctx, payload)


@app.get("/api/maintenance-logs")
async def api_list_maintenance_logs(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    asset_id: Optional[str] = None,
    technician_id: Optional[str] = None,
) -> list[dict]:
    return crud.list_maintenance_logs(databases, DATABASE_ID, ctx, asset_id, technician_id)


@app.patch("/api/maintenance-logs/{log_id}")
async def api_update_maintenance_log(
    log_id: str,
    payload: MaintenanceLogUpdate,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return crud.update_maintenance_log(databases, DATABASE_ID, ctx, log_id, payload)


@app.delete("/api/maintenance-logs/{log_id}")
async def api_delete_maintenance_log(
    log_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.delete_maintenance_log(databases, DATABASE_ID, ctx, log_id)
    return {"ok": True}


# =============================================================================
# Uploads API
# =============================================================================
@app.post("/api/uploads/maintenance")
async def api_upload_maintenance_file(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    file: UploadFile = File(...),
) -> dict:
    content = await file.read()
    return crud.upload_file_to_storage(
        storage=storage,
        ctx=ctx,
        bucket_id=crud.BUCKET_MAINTENANCE_UPLOADS,
        filename=file.filename or "maintenance_upload",
        content=content,
        content_type=file.content_type,
    )


@app.post("/api/uploads/staff-document")
async def api_upload_staff_document(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    file: UploadFile = File(...),
) -> dict:
    crud.require_admin(ctx)
    content = await file.read()
    return crud.upload_file_to_storage(
        storage=storage,
        ctx=ctx,
        bucket_id=crud.BUCKET_STAFF_DOCUMENTS,
        filename=file.filename or "staff_document",
        content=content,
        content_type=file.content_type,
    )


# =============================================================================
# PDF Generation API
# =============================================================================
@app.get("/api/generate-pdf/{contract_id}")
async def api_generate_pdf(
    contract_id: str,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> StreamingResponse:
    pdf_bytes, filename = crud.generate_scribus_pdf_bytes(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        contract_id=contract_id,
    )
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/pdf-template/fields")
async def api_pdf_fields(ctx: Annotated[AuthContext, Depends(get_current_context)]) -> dict:
    crud.require_admin(ctx)
    template_path = crud.resolve_pdf_template_path()
    reader = PdfReader(str(template_path))
    fields = reader.get_fields() or {}
    return {
        "template": str(template_path),
        "fields": sorted(fields.keys()),
        "example_loop_fields": [
            "asset_1_flat_villa_no", "asset_1_unit_type", "asset_1_brand",
            "asset_1_tonnage", "asset_1_serial_no", "asset_1_work_category",
            "asset_1_job_description", "asset_1_suction_pressure",
            "asset_1_discharge_pressure", "asset_1_ampere_reading",
            "asset_1_materials_used",
        ],
    }


# =============================================================================
# Admin Diagnostics API
# =============================================================================
@app.get("/api/admin/collections-summary")
async def api_collections_summary(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.require_admin(ctx)
    collections = {
        "settings": crud.COLLECTION_SETTINGS,
        "staff": crud.COLLECTION_STAFF,
        "contracts": crud.COLLECTION_CONTRACTS,
        "automated_schedules": crud.COLLECTION_SCHEDULES,
        "assets": crud.COLLECTION_ASSETS,
        "maintenance_logs": crud.COLLECTION_MAINTENANCE_LOGS,
    }
    summary = {}
    for label, collection_id in collections.items():
        docs = crud.list_documents(
            databases=databases,
            database_id=DATABASE_ID,
            collection_id=collection_id,
            queries=[],
        )
        summary[label] = {
            "collection_id": collection_id,
            "sample_count": len(docs),
        }
    return {"database_id": DATABASE_ID, "collections": summary}


@app.get("/api/admin/storage-summary")
async def api_storage_summary(
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    crud.require_admin(ctx)
    return {
        "staff_documents_bucket": crud.BUCKET_STAFF_DOCUMENTS,
        "maintenance_uploads_bucket": crud.BUCKET_MAINTENANCE_UPLOADS,
    }


# =============================================================================
# Startup Diagnostics
# =============================================================================
@app.on_event("startup")
async def startup_event() -> None:
    if APP_DEBUG:
        print("MAK INFRATECH FMS started")
        print(f"Environment: {APP_ENV}")
        print(f"Appwrite endpoint: {APPWRITE_ENDPOINT}")
        print(f"Appwrite project: {APPWRITE_PROJECT_ID}")
        print(f"Database ID: {DATABASE_ID}")
        print(f"Session cookie secure: {SESSION_COOKIE_SECURE}")


# =============================================================================
# Local Development Entrypoint
# =============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=APP_DEBUG,
    )
