from __future__ import annotations

import os
from io import BytesIO
from typing import Annotated, Any

from appwrite.client import Client
from appwrite.query import Query
from appwrite.services.databases import Databases
from appwrite.services.users import Users
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

import crud
from models import (
    ACUnitCreate,
    ACUnitUpdateMetrics,
    AuthContext,
    BarcodeParseRequest,
    ClientCreate,
    ClientUpdate,
    ExpenseApprovalUpdate,
    ExpenseCreate,
    InvoiceCreate,
    InvoiceStatusUpdate,
    ServiceReportCreate,
    ServiceReportUpdate,
)

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "HVAC Field Management System")
APP_ENV = os.getenv("APP_ENV", "production")
APP_DEBUG = os.getenv("APP_DEBUG", "false").lower() == "true"

required_env_vars = [
    "APPWRITE_ENDPOINT",
    "APPWRITE_PROJECT_ID",
    "APPWRITE_API_KEY",
]

missing_env_vars = [key for key in required_env_vars if not os.getenv(key)]

if missing_env_vars:
    raise RuntimeError(
        "Missing required environment variables: "
        + ", ".join(missing_env_vars)
    )

client = Client()
client.set_endpoint(os.getenv("APPWRITE_ENDPOINT"))
client.set_project(os.getenv("APPWRITE_PROJECT_ID"))
client.set_key(os.getenv("APPWRITE_API_KEY"))

databases = Databases(client)
users = Users(client)

DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID", "default_hvac_db")

app = FastAPI(
    title=APP_NAME,
    version="3.1.0-appwrite-scribus-local-pdf-dashboard",
    debug=APP_DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(crud.AppError)
async def app_error_handler(_: Request, exc: crud.AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "details": exc.details,
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if APP_DEBUG:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Unhandled server error",
                "details": str(exc),
            },
        )

    return JSONResponse(
        status_code=500,
        content={
            "error": "Unhandled server error",
        },
    )


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    prefix = "Bearer "

    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Authorization header must be Bearer token")

    token = authorization[len(prefix):].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Bearer token is empty")

    return token


async def get_auth_context(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    jwt = extract_bearer_token(authorization)

    try:
        return crud.resolve_auth_context(
            jwt=jwt,
            databases=databases,
            users=users,
            database_id=DATABASE_ID,
        )
    except crud.AppError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": exc.message,
                "details": exc.details,
            },
        ) from exc


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en" class="scroll-smooth">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MAK INFRATECH - Field Management</title>

  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          fontFamily: {
            sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif']
          },
          boxShadow: {
            glow: '0 20px 80px rgba(59, 130, 246, 0.25)'
          }
        }
      }
    }
  </script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">

  <style>
    .glass {
      background: rgba(255,255,255,0.78);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
    }
    .dark-glass {
      background: rgba(15,23,42,0.74);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
    }
    .no-scrollbar::-webkit-scrollbar { display: none; }
    .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
  </style>
</head>

<body class="min-h-screen bg-slate-950 font-sans text-slate-900">
  <div class="fixed inset-0 -z-10 overflow-hidden">
    <div class="absolute left-[-10%] top-[-10%] h-72 w-72 rounded-full bg-blue-500/30 blur-3xl"></div>
    <div class="absolute right-[-10%] top-[10%] h-96 w-96 rounded-full bg-cyan-400/20 blur-3xl"></div>
    <div class="absolute bottom-[-20%] left-[20%] h-[34rem] w-[34rem] rounded-full bg-indigo-500/20 blur-3xl"></div>
    <div class="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(30,64,175,0.38),transparent_45%),linear-gradient(135deg,#020617,#0f172a_45%,#111827)]"></div>
  </div>

  <header class="sticky top-0 z-40 border-b border-white/10 dark-glass text-white">
    <div class="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
      <div class="flex items-center gap-3">
        <div class="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-blue-600 shadow-glow">
          <span class="text-lg font-black text-white">MI</span>
        </div>
        <div>
          <h1 class="text-sm font-black tracking-wide sm:text-lg">MAK INFRATECH</h1>
          <p class="text-xs text-cyan-100/80">Field Management</p>
        </div>
      </div>

      <nav class="hidden items-center gap-1 md:flex">
        <button data-view="overview" class="nav-btn rounded-xl px-4 py-2 text-sm font-semibold text-white/90 hover:bg-white/10">Overview</button>
        <button data-view="technician" class="nav-btn rounded-xl px-4 py-2 text-sm font-semibold text-white/90 hover:bg-white/10">Technician View</button>
        <button data-view="assets" class="nav-btn rounded-xl px-4 py-2 text-sm font-semibold text-white/90 hover:bg-white/10">Assets</button>
        <button data-view="financials" class="nav-btn rounded-xl px-4 py-2 text-sm font-semibold text-white/90 hover:bg-white/10">Financials</button>
      </nav>

      <div class="flex items-center gap-2">
        <button id="tokenBtn" class="rounded-xl border border-cyan-300/30 bg-cyan-400/10 px-3 py-2 text-xs font-bold text-cyan-100 hover:bg-cyan-400/20 sm:px-4">
          Connect Appwrite
        </button>
        <button id="mobileMenuBtn" class="rounded-xl bg-white/10 p-2 text-white md:hidden">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7h16M4 12h16M4 17h16"/>
          </svg>
        </button>
      </div>
    </div>

    <div id="mobileNav" class="hidden border-t border-white/10 px-4 pb-3 md:hidden">
      <div class="grid grid-cols-2 gap-2 pt-3">
        <button data-view="overview" class="nav-btn rounded-xl bg-white/10 px-3 py-2 text-sm font-semibold text-white">Overview</button>
        <button data-view="technician" class="nav-btn rounded-xl bg-white/10 px-3 py-2 text-sm font-semibold text-white">Technician</button>
        <button data-view="assets" class="nav-btn rounded-xl bg-white/10 px-3 py-2 text-sm font-semibold text-white">Assets</button>
        <button data-view="financials" class="nav-btn rounded-xl bg-white/10 px-3 py-2 text-sm font-semibold text-white">Financials</button>
      </div>
    </div>
  </header>

  <main class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
    <section class="mb-6 overflow-hidden rounded-[2rem] border border-white/10 bg-white/[0.07] p-5 text-white shadow-2xl sm:p-8">
      <div class="grid gap-6 lg:grid-cols-[1.4fr_0.6fr] lg:items-center">
        <div>
          <div class="mb-4 inline-flex items-center gap-2 rounded-full border border-cyan-300/30 bg-cyan-300/10 px-3 py-1 text-xs font-bold text-cyan-100">
            <span class="h-2 w-2 rounded-full bg-emerald-400"></span>
            Live Operations Dashboard
          </div>
          <h2 class="max-w-3xl text-3xl font-black tracking-tight sm:text-5xl">
            HVAC service control center for customers, assets, technicians, AMC and reports.
          </h2>
          <p class="mt-4 max-w-2xl text-sm leading-6 text-slate-200 sm:text-base">
            Manage field work, scan assets, track maintenance complaints, create AMC schedules, and instantly download Scribus PDF service reports populated from Appwrite data.
          </p>
          <div class="mt-6 flex flex-col gap-3 sm:flex-row">
            <button id="openAddModalHero" class="rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-600 px-5 py-3 text-sm font-black text-white shadow-glow hover:scale-[1.01] active:scale-[0.99]">
              Add New Customer / Asset
            </button>
            <button data-view="technician" class="nav-btn rounded-2xl border border-white/15 bg-white/10 px-5 py-3 text-sm font-black text-white hover:bg-white/15">
              Open Technician View
            </button>
          </div>
        </div>

        <div class="rounded-[1.75rem] border border-white/10 bg-slate-900/70 p-5">
          <p class="text-xs font-bold uppercase tracking-[0.2em] text-cyan-200">Signed-in Profile</p>
          <div class="mt-4 space-y-3">
            <div>
              <p class="text-xs text-slate-400">Name</p>
              <p id="profileName" class="font-bold text-white">Not connected</p>
            </div>
            <div>
              <p class="text-xs text-slate-400">Role</p>
              <p id="profileRole" class="font-bold text-white">—</p>
            </div>
            <div>
              <p class="text-xs text-slate-400">User ID</p>
              <p id="profileId" class="break-all text-xs font-medium text-slate-300">—</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section id="overviewView" class="view-section">
      <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div class="glass rounded-[1.5rem] border border-white/60 p-5 shadow-xl">
          <div class="flex items-center justify-between">
            <p class="text-sm font-bold text-slate-500">Active Clients</p>
            <span class="rounded-xl bg-blue-100 px-3 py-1 text-xs font-black text-blue-700">CRM</span>
          </div>
          <p id="statClients" class="mt-4 text-4xl font-black text-slate-950">—</p>
          <p class="mt-2 text-xs text-slate-500">Walk-in and AMC customers</p>
        </div>

        <div class="glass rounded-[1.5rem] border border-white/60 p-5 shadow-xl">
          <div class="flex items-center justify-between">
            <p class="text-sm font-bold text-slate-500">Open Complaints</p>
            <span class="rounded-xl bg-amber-100 px-3 py-1 text-xs font-black text-amber-700">Jobs</span>
          </div>
          <p id="statOpenReports" class="mt-4 text-4xl font-black text-slate-950">—</p>
          <p class="mt-2 text-xs text-slate-500">Scheduled or in-progress reports</p>
        </div>

        <div class="glass rounded-[1.5rem] border border-white/60 p-5 shadow-xl">
          <div class="flex items-center justify-between">
            <p class="text-sm font-bold text-slate-500">AMC Contracts</p>
            <span class="rounded-xl bg-emerald-100 px-3 py-1 text-xs font-black text-emerald-700">AMC</span>
          </div>
          <p id="statAmc" class="mt-4 text-4xl font-black text-slate-950">—</p>
          <p class="mt-2 text-xs text-slate-500">Maintenance contracts tracked</p>
        </div>

        <div class="glass rounded-[1.5rem] border border-white/60 p-5 shadow-xl">
          <div class="flex items-center justify-between">
            <p class="text-sm font-bold text-slate-500">Assets Tracked</p>
            <span class="rounded-xl bg-indigo-100 px-3 py-1 text-xs font-black text-indigo-700">AC Units</span>
          </div>
          <p id="statAssets" class="mt-4 text-4xl font-black text-slate-950">—</p>
          <p class="mt-2 text-xs text-slate-500">Barcode-enabled equipment</p>
        </div>
      </div>

      <div class="mt-6 grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
        <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="text-lg font-black">Quick Actions</h3>
              <p class="text-sm text-slate-500">Fast operational shortcuts</p>
            </div>
          </div>

          <div class="mt-5 grid gap-3">
            <button id="openAddModal" class="group flex items-center justify-between rounded-2xl bg-slate-950 px-5 py-4 text-left text-white shadow-xl hover:bg-slate-800">
              <div>
                <p class="font-black">Add New Customer / Asset</p>
                <p class="text-xs text-slate-400">Create customer, AMC and AC unit barcode</p>
              </div>
              <span class="rounded-xl bg-cyan-400 px-3 py-2 text-sm font-black text-slate-950 group-hover:bg-cyan-300">Open</span>
            </button>

            <button data-view="technician" class="nav-btn group flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-5 py-4 text-left hover:bg-slate-50">
              <div>
                <p class="font-black">Generate Scribus Service Report PDF</p>
                <p class="text-xs text-slate-500">Select assigned report and download instantly</p>
              </div>
              <span class="rounded-xl bg-blue-100 px-3 py-2 text-sm font-black text-blue-700 group-hover:bg-blue-200">Go</span>
            </button>
          </div>
        </div>

        <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
          <div class="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
            <div>
              <h3 class="text-lg font-black">Assigned Service Reports</h3>
              <p class="text-sm text-slate-500">Live from Appwrite</p>
            </div>
            <button id="refreshBtn" class="rounded-xl bg-slate-950 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800">
              Refresh
            </button>
          </div>
          <div id="reportsList" class="mt-5 max-h-[28rem] space-y-3 overflow-auto pr-1 no-scrollbar">
            <div class="rounded-2xl border border-dashed border-slate-300 bg-white/60 p-5 text-sm text-slate-500">
              Connect Appwrite and refresh to load assigned service reports.
            </div>
          </div>
        </div>
      </div>
    </section>

    <section id="technicianView" class="view-section hidden">
      <div class="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
        <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
          <h3 class="text-xl font-black">Technician On-Site PDF Generator</h3>
          <p class="mt-2 text-sm text-slate-600">
            Choose your assigned service report, optionally update work notes, then download the populated local Scribus PDF.
          </p>

          <form id="techUpdateForm" class="mt-5 space-y-4">
            <div>
              <label class="mb-1 block text-sm font-bold text-slate-700">Service Report ID</label>
              <input id="techReportId" type="text" required class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none ring-blue-500/20 focus:ring-4" placeholder="Select from the list or paste report ID">
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold text-slate-700">Work Performed</label>
              <textarea id="workPerformed" rows="4" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none ring-blue-500/20 focus:ring-4" placeholder="Describe repairs, cleaning, gas charging, electrical checks..."></textarea>
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold text-slate-700">Technician Observations</label>
              <textarea id="techObservations" rows="4" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none ring-blue-500/20 focus:ring-4" placeholder="Site condition, spare parts needed, customer notes..."></textarea>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="mb-1 block text-sm font-bold text-slate-700">Pressure After Service</label>
                <input id="pressureAfter" type="number" min="0" step="0.01" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none ring-blue-500/20 focus:ring-4" placeholder="e.g. 68">
              </div>
              <div>
                <label class="mb-1 block text-sm font-bold text-slate-700">Ampere After Service</label>
                <input id="ampereAfter" type="number" min="0" step="0.01" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none ring-blue-500/20 focus:ring-4" placeholder="e.g. 5.4">
              </div>
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold text-slate-700">Status</label>
              <select id="reportStatus" class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none ring-blue-500/20 focus:ring-4">
                <option value="">Do not change</option>
                <option value="scheduled">Scheduled</option>
                <option value="in_progress">In Progress</option>
                <option value="completed">Completed</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <button type="submit" class="rounded-2xl bg-slate-950 px-5 py-3 text-sm font-black text-white hover:bg-slate-800">
                Save Field Notes
              </button>
              <button id="downloadPdfBtn" type="button" class="rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-600 px-5 py-3 text-sm font-black text-white shadow-glow">
                Download Scribus PDF
              </button>
            </div>
          </form>
        </div>

        <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="text-xl font-black">Mobile Job Queue</h3>
              <p class="text-sm text-slate-500">Tap a card to use it in the PDF form.</p>
            </div>
            <button id="refreshTechBtn" class="rounded-xl bg-blue-100 px-4 py-2 text-sm font-black text-blue-700">Reload</button>
          </div>
          <div id="techReportsList" class="mt-5 grid gap-3"></div>
        </div>
      </div>
    </section>

    <section id="assetsView" class="view-section hidden">
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <div class="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div>
            <h3 class="text-xl font-black">Asset Barcode Tools</h3>
            <p class="text-sm text-slate-500">Parse HVAC barcode text and locate AC unit records.</p>
          </div>
          <button id="openAddModalAssets" class="rounded-xl bg-slate-950 px-4 py-2 text-sm font-bold text-white">Add Asset</button>
        </div>

        <div class="mt-5 grid gap-4 lg:grid-cols-[1fr_1fr]">
          <div class="rounded-2xl border border-slate-200 bg-white p-4">
            <label class="mb-1 block text-sm font-bold text-slate-700">Barcode Value</label>
            <input id="barcodeInput" class="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none ring-blue-500/20 focus:ring-4" placeholder="HVAC:clientId:UNIT-01:uuid">
            <button id="parseBarcodeBtn" class="mt-3 w-full rounded-2xl bg-blue-600 px-4 py-3 text-sm font-black text-white">Parse / Find Asset</button>
          </div>
          <pre id="barcodeResult" class="min-h-48 overflow-auto rounded-2xl bg-slate-950 p-4 text-xs text-cyan-100">Barcode result will appear here.</pre>
        </div>
      </div>
    </section>

    <section id="financialsView" class="view-section hidden">
      <div class="glass rounded-[2rem] border border-white/60 p-5 shadow-xl">
        <h3 class="text-xl font-black">Financial Operations</h3>
        <p class="mt-2 text-sm text-slate-600">
          Invoices and expenses are available through the API routes. Use this panel as a quick reference for connected modules.
        </p>

        <div class="mt-5 grid gap-4 md:grid-cols-2">
          <div class="rounded-2xl bg-white p-5">
            <p class="text-sm font-black text-slate-500">Invoices</p>
            <p class="mt-2 text-2xl font-black">Client + Job Linked</p>
            <p class="mt-2 text-sm text-slate-500">Create invoices using <code class="rounded bg-slate-100 px-1">POST /invoices</code></p>
          </div>
          <div class="rounded-2xl bg-white p-5">
            <p class="text-sm font-black text-slate-500">Expenses</p>
            <p class="mt-2 text-2xl font-black">Field Cost Tracking</p>
            <p class="mt-2 text-sm text-slate-500">Submit expenses using <code class="rounded bg-slate-100 px-1">POST /expenses</code></p>
          </div>
        </div>
      </div>
    </section>
  </main>

  <div id="addModal" class="fixed inset-0 z-50 hidden overflow-y-auto bg-slate-950/70 p-4 backdrop-blur-sm">
    <div class="mx-auto my-6 max-w-4xl rounded-[2rem] bg-white shadow-2xl">
      <div class="flex items-center justify-between border-b border-slate-100 p-5">
        <div>
          <h3 class="text-xl font-black">Add New Customer / Asset</h3>
          <p class="text-sm text-slate-500">Creates a customer first, then optionally creates an AC unit asset.</p>
        </div>
        <button id="closeAddModal" class="rounded-xl bg-slate-100 px-3 py-2 text-sm font-black text-slate-700">Close</button>
      </div>

      <form id="addCustomerAssetForm" class="p-5">
        <div class="grid gap-5 lg:grid-cols-2">
          <div class="space-y-4">
            <h4 class="font-black text-slate-900">Customer Details</h4>

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="mb-1 block text-sm font-bold">Customer Type</label>
                <select id="customerType" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                  <option value="walk_in">Walk-in</option>
                  <option value="amc">AMC</option>
                </select>
              </div>
              <div>
                <label class="mb-1 block text-sm font-bold">Name</label>
                <input id="customerName" required class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="mb-1 block text-sm font-bold">Phone</label>
                <input id="customerPhone" required class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
              <div>
                <label class="mb-1 block text-sm font-bold">Email</label>
                <input id="customerEmail" type="email" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold">Address Line 1</label>
              <input id="addressLine1" required class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold">Address Line 2</label>
              <input id="addressLine2" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
            </div>

            <div class="grid gap-3 sm:grid-cols-3">
              <div>
                <label class="mb-1 block text-sm font-bold">City</label>
                <input id="city" required class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
              <div>
                <label class="mb-1 block text-sm font-bold">State</label>
                <input id="state" required class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
              <div>
                <label class="mb-1 block text-sm font-bold">Flat No.</label>
                <input id="flatNumber" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
            </div>

            <div id="amcFields" class="hidden rounded-2xl bg-slate-50 p-4">
              <h5 class="mb-3 font-black">AMC Details</h5>
              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="mb-1 block text-sm font-bold">Start Date</label>
                  <input id="contractStart" type="date" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                </div>
                <div>
                  <label class="mb-1 block text-sm font-bold">End Date</label>
                  <input id="contractEnd" type="date" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                </div>
                <div>
                  <label class="mb-1 block text-sm font-bold">Contract Value</label>
                  <input id="contractValue" type="number" min="0" step="0.01" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                </div>
                <div>
                  <label class="mb-1 block text-sm font-bold">EMI Count</label>
                  <input id="emiCount" type="number" min="1" value="1" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                </div>
                <div>
                  <label class="mb-1 block text-sm font-bold">PPM Count</label>
                  <input id="ppmCount" type="number" min="0" value="0" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                </div>
              </div>
            </div>
          </div>

          <div class="space-y-4">
            <h4 class="font-black text-slate-900">Asset Details</h4>

            <div class="rounded-2xl bg-blue-50 p-4 text-sm text-blue-800">
              Leave asset fields blank if you only want to create a customer.
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold">Assigned Unit Number</label>
              <input id="unitNumber" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm" placeholder="UNIT-01">
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="mb-1 block text-sm font-bold">AC Brand</label>
                <select id="brand" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                  <option value="Daikin">Daikin</option>
                  <option value="Carrier">Carrier</option>
                  <option value="LG">LG</option>
                  <option value="Samsung">Samsung</option>
                  <option value="Voltas">Voltas</option>
                  <option value="Blue Star">Blue Star</option>
                  <option value="Hitachi">Hitachi</option>
                  <option value="Panasonic">Panasonic</option>
                  <option value="Mitsubishi">Mitsubishi</option>
                  <option value="O General">O General</option>
                  <option value="Other">Other</option>
                </select>
              </div>
              <div>
                <label class="mb-1 block text-sm font-bold">Refrigerant Type</label>
                <select id="refrigerant" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                  <option value="R22">R22</option>
                  <option value="R32">R32</option>
                  <option value="R410A">R410A</option>
                  <option value="R134A">R134A</option>
                  <option value="R290">R290</option>
                  <option value="R407C">R407C</option>
                  <option value="Other">Other</option>
                </select>
              </div>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="mb-1 block text-sm font-bold">Pressure</label>
                <input id="pressure" type="number" min="0" step="0.01" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
              <div>
                <label class="mb-1 block text-sm font-bold">Ampere</label>
                <input id="ampere" type="number" min="0" step="0.01" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
              </div>
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold">Condition</label>
              <select id="condition" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                <option value="excellent">Excellent</option>
                <option value="good" selected>Good</option>
                <option value="fair">Fair</option>
                <option value="poor">Poor</option>
                <option value="needs_repair">Needs Repair</option>
                <option value="not_working">Not Working</option>
              </select>
            </div>

            <div>
              <label class="mb-1 block text-sm font-bold">Location Description</label>
              <textarea id="locationDescription" rows="3" class="form-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm" placeholder="Living room, server room, roof unit..."></textarea>
            </div>

            <pre id="addResult" class="max-h-48 overflow-auto rounded-2xl bg-slate-950 p-4 text-xs text-cyan-100">Submission result will appear here.</pre>
          </div>
        </div>

        <div class="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-end">
          <button type="button" id="resetAddForm" class="rounded-2xl border border-slate-200 px-5 py-3 text-sm font-black">Reset</button>
          <button type="submit" class="rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-600 px-5 py-3 text-sm font-black text-white shadow-glow">Create Customer / Asset</button>
        </div>
      </form>
    </div>
  </div>

  <div id="toast" class="fixed bottom-4 left-1/2 z-[60] hidden w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 rounded-2xl bg-slate-950 px-5 py-4 text-sm font-semibold text-white shadow-2xl"></div>

  <script>
    const state = {
      token: localStorage.getItem('mak_appwrite_jwt') || '',
      me: null,
      reports: []
    };

    const $ = (id) => document.getElementById(id);

    function toast(message, kind = 'info') {
      const el = $('toast');
      el.textContent = message;
      el.className = 'fixed bottom-4 left-1/2 z-[60] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 rounded-2xl px-5 py-4 text-sm font-semibold text-white shadow-2xl';
      if (kind === 'error') el.classList.add('bg-rose-600');
      else if (kind === 'success') el.classList.add('bg-emerald-600');
      else el.classList.add('bg-slate-950');
      el.classList.remove('hidden');
      setTimeout(() => el.classList.add('hidden'), 4200);
    }

    function headers(json = true) {
      const h = {};
      if (json) h['Content-Type'] = 'application/json';
      if (state.token) h['Authorization'] = `Bearer ${state.token}`;
      return h;
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {
          ...headers(options.body !== undefined && !(options.body instanceof FormData)),
          ...(options.headers || {})
        }
      });

      const contentType = response.headers.get('content-type') || '';
      if (!response.ok) {
        let err;
        if (contentType.includes('application/json')) {
          err = await response.json();
        } else {
          err = { error: await response.text() };
        }
        throw new Error(err.error || err.detail?.error || err.detail || response.statusText);
      }

      if (contentType.includes('application/json')) return response.json();
      return response;
    }

    function setView(name) {
      document.querySelectorAll('.view-section').forEach(el => el.classList.add('hidden'));
      const target = $(`${name}View`);
      if (target) target.classList.remove('hidden');

      document.querySelectorAll('.nav-btn').forEach(btn => {
        if (btn.dataset.view === name) {
          btn.classList.add('bg-white/15');
        } else {
          btn.classList.remove('bg-white/15');
        }
      });

      if (name === 'technician') loadReports();
      if (name === 'overview') loadDashboard();
    }

    async function connectToken() {
      const existing = state.token || '';
      const token = prompt(
        'Paste your Appwrite JWT generated from account.createJWT() in your frontend/client:',
        existing
      );

      if (token === null) return;

      state.token = token.trim();
      localStorage.setItem('mak_appwrite_jwt', state.token);

      if (!state.token) {
        toast('Token cleared.', 'info');
        return;
      }

      await loadMe();
      await loadDashboard();
      await loadReports();
    }

    async function loadMe() {
      if (!state.token) {
        $('profileName').textContent = 'Not connected';
        $('profileRole').textContent = '—';
        $('profileId').textContent = '—';
        return;
      }

      try {
        const me = await api('/me');
        state.me = me;
        $('profileName').textContent = me.full_name || '—';
        $('profileRole').textContent = me.role || '—';
        $('profileId').textContent = me.user_id || '—';
        $('tokenBtn').textContent = 'Connected';
      } catch (err) {
        $('tokenBtn').textContent = 'Connect Appwrite';
        toast(`Connection failed: ${err.message}`, 'error');
      }
    }

    async function loadDashboard() {
      if (!state.token) return;

      try {
        const stats = await api('/dashboard/stats');
        $('statClients').textContent = stats.active_clients;
        $('statOpenReports').textContent = stats.open_maintenance_complaints;
        $('statAmc').textContent = stats.amc_contracts;
        $('statAssets').textContent = stats.total_assets_tracked;
      } catch (err) {
        toast(`Stats failed: ${err.message}`, 'error');
      }
    }

    function reportCard(report, compact = false) {
      const client = report.client || {};
      const unit = report.ac_unit || {};
      const statusColor = report.status === 'completed'
        ? 'bg-emerald-100 text-emerald-700'
        : report.status === 'in_progress'
          ? 'bg-blue-100 text-blue-700'
          : report.status === 'cancelled'
            ? 'bg-rose-100 text-rose-700'
            : 'bg-amber-100 text-amber-700';

      const div = document.createElement('div');
      div.className = 'rounded-2xl border border-slate-200 bg-white p-4 shadow-sm';
      div.innerHTML = `
        <div class="flex items-start justify-between gap-3">
          <div>
            <p class="text-xs font-black uppercase tracking-wider text-slate-400">${report.report_number || report.$id}</p>
            <h4 class="mt-1 text-base font-black text-slate-950">${client.name || 'Client unavailable'}</h4>
            <p class="mt-1 text-xs text-slate-500">${report.nature_of_complaint || ''}</p>
          </div>
          <span class="shrink-0 rounded-xl px-3 py-1 text-xs font-black ${statusColor}">${report.status || 'scheduled'}</span>
        </div>
        <div class="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
          <p><span class="font-bold">Scheduled:</span> ${report.scheduled_at || '—'}</p>
          <p><span class="font-bold">Technician:</span> ${report.assigned_technician_name || '—'}</p>
          <p><span class="font-bold">Unit:</span> ${unit.unit_number || '—'}</p>
          <p><span class="font-bold">Brand:</span> ${unit.brand || '—'}</p>
        </div>
        <div class="mt-4 flex flex-col gap-2 sm:flex-row">
          <button data-report-id="${report.$id}" class="select-report-btn rounded-xl bg-slate-950 px-4 py-2 text-xs font-black text-white">Use in Technician Form</button>
          <button data-report-id="${report.$id}" class="download-report-btn rounded-xl bg-blue-600 px-4 py-2 text-xs font-black text-white">Download PDF</button>
        </div>
      `;

      div.querySelector('.select-report-btn').addEventListener('click', () => {
        $('techReportId').value = report.$id;
        setView('technician');
        toast('Report selected for technician form.', 'success');
      });

      div.querySelector('.download-report-btn').addEventListener('click', () => downloadPdf(report.$id));

      return div;
    }

    async function loadReports() {
      if (!state.token) return;

      try {
        const reports = await api('/service-reports/assigned');
        state.reports = reports || [];

        const list = $('reportsList');
        const techList = $('techReportsList');
        list.innerHTML = '';
        techList.innerHTML = '';

        if (!state.reports.length) {
          list.innerHTML = '<div class="rounded-2xl border border-dashed border-slate-300 bg-white/60 p-5 text-sm text-slate-500">No assigned reports found.</div>';
          techList.innerHTML = '<div class="rounded-2xl border border-dashed border-slate-300 bg-white/60 p-5 text-sm text-slate-500">No assigned reports found.</div>';
          return;
        }

        state.reports.forEach(report => {
          list.appendChild(reportCard(report));
          techList.appendChild(reportCard(report, true));
        });
      } catch (err) {
        toast(`Reports failed: ${err.message}`, 'error');
      }
    }

    async function downloadPdf(reportId) {
      if (!reportId) {
        toast('Service Report ID is required.', 'error');
        return;
      }

      try {
        const response = await fetch(`/service-reports/${encodeURIComponent(reportId)}/pdf`, {
          headers: headers(false)
        });

        if (!response.ok) {
          let message = response.statusText;
          try {
            const err = await response.json();
            message = err.error || err.detail?.error || err.detail || message;
          } catch (_) {}
          throw new Error(message);
        }

        const blob = await response.blob();
        const disposition = response.headers.get('content-disposition') || '';
        const match = disposition.match(/filename="([^"]+)"/);
        const filename = match ? match[1] : `service-report-${reportId}.pdf`;

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);

        toast('PDF downloaded successfully.', 'success');
      } catch (err) {
        toast(`PDF download failed: ${err.message}`, 'error');
      }
    }

    async function saveTechnicianNotes(event) {
      event.preventDefault();

      const reportId = $('techReportId').value.trim();
      if (!reportId) {
        toast('Select or enter a Service Report ID.', 'error');
        return;
      }

      const payload = {};
      if ($('workPerformed').value.trim()) payload.work_performed = $('workPerformed').value.trim();
      if ($('techObservations').value.trim()) payload.technician_observations = $('techObservations').value.trim();
      if ($('pressureAfter').value) payload.pressure_after_service = Number($('pressureAfter').value);
      if ($('ampereAfter').value) payload.ampere_after_service = Number($('ampereAfter').value);
      if ($('reportStatus').value) payload.status = $('reportStatus').value;

      if (Object.keys(payload).length === 0) {
        toast('No technician fields to update.', 'error');
        return;
      }

      try {
        await api(`/service-reports/${encodeURIComponent(reportId)}`, {
          method: 'PATCH',
          body: JSON.stringify(payload)
        });
        toast('Service report updated.', 'success');
        await loadReports();
      } catch (err) {
        toast(`Update failed: ${err.message}`, 'error');
      }
    }

    function openModal() {
      $('addModal').classList.remove('hidden');
    }

    function closeModal() {
      $('addModal').classList.add('hidden');
    }

    function nullableString(id) {
      const value = $(id).value.trim();
      return value ? value : null;
    }

    function nullableNumber(id) {
      const value = $(id).value;
      return value === '' ? null : Number(value);
    }

    async function createCustomerAsset(event) {
      event.preventDefault();

      const customerType = $('customerType').value;

      const clientPayload = {
        customer_type: customerType,
        name: $('customerName').value.trim(),
        phone: $('customerPhone').value.trim(),
        email: nullableString('customerEmail'),
        address_line1: $('addressLine1').value.trim(),
        address_line2: nullableString('addressLine2'),
        city: $('city').value.trim(),
        state: $('state').value.trim(),
        flat_number: nullableString('flatNumber')
      };

      if (customerType === 'amc') {
        clientPayload.amc_details = {
          contract_start_date: $('contractStart').value,
          contract_end_date: $('contractEnd').value,
          contract_value: Number($('contractValue').value),
          emi_count: Number($('emiCount').value || 1),
          ppm_count: Number($('ppmCount').value || 0)
        };
      }

      try {
        const createdClient = await api('/clients', {
          method: 'POST',
          body: JSON.stringify(clientPayload)
        });

        let createdAsset = null;

        if ($('unitNumber').value.trim()) {
          const assetPayload = {
            client_id: createdClient.$id,
            unit_number: $('unitNumber').value.trim(),
            brand: $('brand').value,
            refrigerant: $('refrigerant').value,
            pressure: nullableNumber('pressure'),
            ampere: nullableNumber('ampere'),
            condition: $('condition').value,
            location_description: nullableString('locationDescription')
          };

          createdAsset = await api('/ac-units', {
            method: 'POST',
            body: JSON.stringify(assetPayload)
          });
        }

        $('addResult').textContent = JSON.stringify({
          client: createdClient,
          asset: createdAsset
        }, null, 2);

        toast('Customer / asset created successfully.', 'success');
        await loadDashboard();
      } catch (err) {
        $('addResult').textContent = err.message;
        toast(`Create failed: ${err.message}`, 'error');
      }
    }

    async function parseBarcode() {
      const barcode = $('barcodeInput').value.trim();
      if (!barcode) {
        toast('Enter barcode value.', 'error');
        return;
      }

      try {
        const parsed = await api('/barcodes/parse', {
          method: 'POST',
          body: JSON.stringify({ barcode_value: barcode })
        });

        let found = null;
        if (parsed.valid) {
          try {
            found = await api(`/barcodes/${encodeURIComponent(barcode)}/ac-unit`);
          } catch (err) {
            found = { lookup_error: err.message };
          }
        }

        $('barcodeResult').textContent = JSON.stringify({ parsed, found }, null, 2);
      } catch (err) {
        $('barcodeResult').textContent = err.message;
      }
    }

    document.addEventListener('DOMContentLoaded', async () => {
      document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => setView(btn.dataset.view));
      });

      $('mobileMenuBtn').addEventListener('click', () => $('mobileNav').classList.toggle('hidden'));
      $('tokenBtn').addEventListener('click', connectToken);
      $('refreshBtn').addEventListener('click', async () => { await loadDashboard(); await loadReports(); });
      $('refreshTechBtn').addEventListener('click', loadReports);

      $('openAddModal').addEventListener('click', openModal);
      $('openAddModalHero').addEventListener('click', openModal);
      $('openAddModalAssets').addEventListener('click', openModal);
      $('closeAddModal').addEventListener('click', closeModal);

      $('customerType').addEventListener('change', () => {
        $('amcFields').classList.toggle('hidden', $('customerType').value !== 'amc');
      });

      $('resetAddForm').addEventListener('click', () => {
        $('addCustomerAssetForm').reset();
        $('amcFields').classList.add('hidden');
        $('addResult').textContent = 'Submission result will appear here.';
      });

      $('addCustomerAssetForm').addEventListener('submit', createCustomerAsset);
      $('techUpdateForm').addEventListener('submit', saveTechnicianNotes);
      $('downloadPdfBtn').addEventListener('click', () => downloadPdf($('techReportId').value.trim()));
      $('parseBarcodeBtn').addEventListener('click', parseBarcode);

      setView('overview');

      if (state.token) {
        await loadMe();
        await loadDashboard();
        await loadReports();
      }
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard_root() -> HTMLResponse:
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": APP_NAME,
        "environment": APP_ENV,
        "database_provider": "appwrite",
        "pdf_engine": "local-scribus-template-pypdf",
        "dashboard": "enabled",
    }


@app.get("/me")
async def me(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return ctx.model_dump(mode="json", exclude={"jwt"})


@app.get("/dashboard/stats")
async def dashboard_stats(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict[str, int]:
    clients = crud.list_clients(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
    )

    reports = crud.list_assigned_service_reports(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
    )

    open_reports = [
        report for report in reports
        if report.get("status") in {"scheduled", "in_progress"}
    ]

    amc_clients = [
        client_doc for client_doc in clients
        if client_doc.get("customer_type") == "amc"
    ]

    if ctx.role.value == "admin_staff":
        assets = crud.list_documents(
            databases=databases,
            database_id=DATABASE_ID,
            collection_id=crud.COLLECTION_AC_UNITS,
            queries=[Query.limit(500)],
        )
        total_assets = len(assets)
    else:
        assigned_client_ids = {
            report.get("client_id")
            for report in reports
            if report.get("client_id")
        }

        asset_ids: set[str] = set()
        for client_id in assigned_client_ids:
            client_assets = crud.list_documents(
                databases=databases,
                database_id=DATABASE_ID,
                collection_id=crud.COLLECTION_AC_UNITS,
                queries=[
                    Query.equal("client_id", client_id),
                    Query.limit(100),
                ],
            )
            for asset in client_assets:
                if asset.get("$id"):
                    asset_ids.add(asset["$id"])

        total_assets = len(asset_ids)

    return {
        "active_clients": len(clients),
        "open_maintenance_complaints": len(open_reports),
        "amc_contracts": len(amc_clients),
        "total_assets_tracked": total_assets,
    }


# ============================================================
# Clients
# ============================================================

@app.post("/clients")
async def create_client_endpoint(
    payload: ClientCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.create_client(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        payload=payload,
    )


@app.get("/clients")
async def list_clients_endpoint(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict]:
    return crud.list_clients(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
    )


@app.get("/clients/{client_id}")
async def get_client_endpoint(
    client_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.get_client(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        client_id=client_id,
    )


@app.patch("/clients/{client_id}")
async def update_client_endpoint(
    client_id: str,
    payload: ClientUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.update_client(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        client_id=client_id,
        payload=payload,
    )


# ============================================================
# AC Units / Barcode
# ============================================================

@app.post("/ac-units")
async def create_ac_unit_endpoint(
    payload: ACUnitCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.create_ac_unit(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        payload=payload,
    )


@app.patch("/ac-units/{unit_id}/metrics")
async def update_ac_unit_metrics_endpoint(
    unit_id: str,
    payload: ACUnitUpdateMetrics,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.update_ac_unit_metrics(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        unit_id=unit_id,
        payload=payload,
    )


@app.post("/barcodes/parse")
async def parse_barcode_endpoint(payload: BarcodeParseRequest) -> dict:
    return crud.parse_barcode_value(payload.barcode_value).model_dump(mode="json")


@app.get("/barcodes/{barcode_value}/ac-unit")
async def find_ac_unit_by_barcode_endpoint(
    barcode_value: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    parsed = crud.parse_barcode_value(barcode_value)

    if not parsed.valid:
        raise HTTPException(status_code=400, detail="Invalid barcode format")

    unit = crud.find_ac_unit_by_barcode(
        databases=databases,
        database_id=DATABASE_ID,
        barcode_value=barcode_value,
    )

    if not unit:
        raise HTTPException(status_code=404, detail="AC unit not found")

    if ctx.role.value != "admin_staff":
        crud.assert_technician_assigned_to_client(
            databases=databases,
            database_id=DATABASE_ID,
            technician_id=ctx.user_id,
            client_id=unit["client_id"],
        )

    return unit


# ============================================================
# Service Reports
# ============================================================

@app.post("/service-reports")
async def create_service_report_endpoint(
    payload: ServiceReportCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.create_service_report(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        payload=payload,
    )


@app.get("/service-reports/assigned")
async def list_assigned_service_reports_endpoint(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict]:
    return crud.list_assigned_service_reports(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
    )


@app.get("/service-reports/{report_id}")
async def get_service_report_endpoint(
    report_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.get_service_report(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        report_id=report_id,
    )


@app.patch("/service-reports/{report_id}")
async def update_service_report_endpoint(
    report_id: str,
    payload: ServiceReportUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.update_service_report(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        report_id=report_id,
        payload=payload,
    )


@app.get("/service-reports/{report_id}/pdf-payload")
async def get_service_report_pdf_payload_endpoint(
    report_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    report = crud.get_service_report(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        report_id=report_id,
    )

    return crud.build_service_report_pdf_payload(report=report, ctx=ctx)


@app.get("/service-reports/{report_id}/pdf")
async def download_service_report_pdf_endpoint(
    report_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> StreamingResponse:
    pdf_bytes, filename = crud.generate_service_report_pdf_bytes(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        report_id=report_id,
    )

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/service-reports/{report_id}/download")
async def download_service_report_pdf_alias_endpoint(
    report_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> StreamingResponse:
    pdf_bytes, filename = crud.generate_service_report_pdf_bytes(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        report_id=report_id,
    )

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/pdf-template/fields")
async def list_pdf_template_fields_endpoint(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    if ctx.role.value != "admin_staff":
        raise HTTPException(status_code=403, detail="Admin/Staff role required")

    template_path = crud.resolve_pdf_template_path()
    fields = crud.get_pdf_form_field_names(template_path)

    return {
        "template": str(template_path),
        "fields": sorted(fields),
        "expected_service_report_fields": [
            "service_report_number",
            "client_name",
            "full_address",
            "flat_number",
            "scheduled_date_time",
            "nature_of_complaint",
            "automated_staff_name",
            "automated_staff_id",
            "assigned_technician_name",
            "assigned_technician_id",
            "work_performed",
            "technician_observations",
            "ac_unit_id",
            "ac_unit_unit_number",
            "ac_unit_barcode_value",
            "ac_unit_brand",
            "ac_unit_refrigerant_type",
            "ac_unit_pressure",
            "ac_unit_ampere",
            "ac_unit_condition",
            "asset_metrics",
        ],
    }


# ============================================================
# Financials - Invoices
# ============================================================

@app.post("/invoices")
async def create_invoice_endpoint(
    payload: InvoiceCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.create_invoice(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        payload=payload,
    )


@app.patch("/invoices/{invoice_id}/status")
async def update_invoice_status_endpoint(
    invoice_id: str,
    payload: InvoiceStatusUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.update_invoice_status(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        invoice_id=invoice_id,
        payload=payload,
    )


# ============================================================
# Financials - Expenses
# ============================================================

@app.post("/expenses")
async def create_expense_endpoint(
    payload: ExpenseCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.create_expense(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        payload=payload,
    )


@app.get("/expenses")
async def list_expenses_endpoint(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict]:
    return crud.list_expenses(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
    )


@app.patch("/expenses/{expense_id}/approval")
async def approve_expense_endpoint(
    expense_id: str,
    payload: ExpenseApprovalUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return crud.approve_expense(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        expense_id=expense_id,
        payload=payload,
    )
