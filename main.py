from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from supabase import Client, create_client

import crud
from models import (
    ACUnitCreate,
    ACUnitUpdateMetrics,
    AuthContext,
    BarcodeParseRequest,
    ClientCreate,
    ExpenseCreate,
    InvoiceCreate,
    ServiceReportCreate,
    ServiceReportUpdate,
)

load_dotenv()


APP_NAME = os.getenv("APP_NAME", "HVAC Field Management System")
APP_ENV = os.getenv("APP_ENV", "development")
APP_DEBUG = os.getenv("APP_DEBUG", "false").lower() == "true"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SCRIBE_TEMPLATE_ID = os.environ["SCRIBE_TEMPLATE_ID"]

supabase_public: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_service: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    if SUPABASE_SERVICE_ROLE_KEY
    else None
)


app = FastAPI(
    title=APP_NAME,
    version="1.0.0",
    debug=APP_DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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


def get_user_db(access_token: str) -> Client:
    db = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    db.postgrest.auth(access_token)
    return db


async def get_auth_context(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    access_token = extract_bearer_token(authorization)

    try:
        user_response = supabase_public.auth.get_user(access_token)
        user = user_response.user
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired Supabase token") from exc

    if not user:
        raise HTTPException(status_code=401, detail="Invalid Supabase user")

    db_for_profile = supabase_service or get_user_db(access_token)

    try:
        profile = crud.get_profile(db_for_profile, UUID(user.id))
        crud.require_active_profile(profile)
    except crud.AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return AuthContext(
        user_id=UUID(user.id),
        email=profile["email"],
        full_name=profile["full_name"],
        role=profile["role"],
        access_token=access_token,
    )


def get_db(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> Client:
    return get_user_db(ctx.access_token)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": APP_NAME,
        "environment": APP_ENV,
    }


@app.get("/me")
async def me(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return ctx.model_dump(mode="json", exclude={"access_token"})


# ============================================================
# Clients
# ============================================================

@app.post("/clients")
async def create_client_endpoint(
    payload: ClientCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.create_client(db, ctx, payload)


@app.get("/clients")
async def list_clients_endpoint(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> list[dict]:
    return crud.list_clients(db, ctx)


@app.get("/clients/{client_id}")
async def get_client_endpoint(
    client_id: UUID,
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.get_client(db, client_id)


# ============================================================
# AC Units / Barcode
# ============================================================

@app.post("/ac-units")
async def create_ac_unit_endpoint(
    payload: ACUnitCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.create_ac_unit(db, ctx, payload)


@app.patch("/ac-units/{unit_id}/metrics")
async def update_ac_unit_metrics_endpoint(
    unit_id: UUID,
    payload: ACUnitUpdateMetrics,
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.update_ac_unit_metrics(db, unit_id, payload)


@app.post("/barcodes/parse")
async def parse_barcode_endpoint(payload: BarcodeParseRequest) -> dict:
    return crud.parse_barcode_value(payload.barcode_value).model_dump(mode="json")


# ============================================================
# Service Reports
# ============================================================

@app.post("/service-reports")
async def create_service_report_endpoint(
    payload: ServiceReportCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.create_service_report(db, ctx, payload)


@app.get("/service-reports/assigned")
async def list_assigned_reports_endpoint(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> list[dict]:
    return crud.list_assigned_service_reports(db, ctx)


@app.get("/service-reports/{report_id}")
async def get_service_report_endpoint(
    report_id: UUID,
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.get_service_report(db, report_id)


@app.patch("/service-reports/{report_id}")
async def update_service_report_endpoint(
    report_id: UUID,
    payload: ServiceReportUpdate,
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.update_service_report(db, report_id, payload)


@app.get("/service-reports/{report_id}/scribe-payload")
async def get_scribe_payload_endpoint(
    report_id: UUID,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    report = crud.get_service_report(db, report_id)
    payload = crud.build_scribe_payload(
        report=report,
        ctx=ctx,
        template_id=SCRIBE_TEMPLATE_ID,
    )
    return payload.model_dump(mode="json")


@app.post("/service-reports/{report_id}/scribe-pdf")
async def generate_scribe_pdf_endpoint(
    report_id: UUID,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    report = crud.get_service_report(db, report_id)
    payload = crud.build_scribe_payload(
        report=report,
        ctx=ctx,
        template_id=SCRIBE_TEMPLATE_ID,
    )
    return await crud.generate_scribe_document(db, report_id, payload)


# ============================================================
# Financials
# ============================================================

@app.post("/invoices")
async def create_invoice_endpoint(
    payload: InvoiceCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.create_invoice(db, ctx, payload)


@app.post("/expenses")
async def create_expense_endpoint(
    payload: ExpenseCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    return crud.create_expense(db, ctx, payload)
