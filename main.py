from __future__ import annotations

import os
from typing import Annotated

from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.users import Users
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
    "SCRIBE_TEMPLATE_ID",
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
SCRIBE_TEMPLATE_ID = os.getenv("SCRIBE_TEMPLATE_ID")


app = FastAPI(
    title=APP_NAME,
    version="2.0.0-appwrite",
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": APP_NAME,
        "environment": APP_ENV,
        "database_provider": "appwrite",
    }


@app.get("/me")
async def me(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    return ctx.model_dump(mode="json", exclude={"jwt"})


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


@app.get("/service-reports/{report_id}/scribe-payload")
async def get_scribe_payload_endpoint(
    report_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    report = crud.get_service_report(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        report_id=report_id,
    )

    payload = crud.build_scribe_payload(
        report=report,
        ctx=ctx,
        template_id=SCRIBE_TEMPLATE_ID,
    )

    return payload.model_dump(mode="json")


@app.post("/service-reports/{report_id}/scribe-pdf")
async def generate_scribe_pdf_endpoint(
    report_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    report = crud.get_service_report(
        databases=databases,
        database_id=DATABASE_ID,
        ctx=ctx,
        report_id=report_id,
    )

    payload = crud.build_scribe_payload(
        report=report,
        ctx=ctx,
        template_id=SCRIBE_TEMPLATE_ID,
    )

    return await crud.generate_scribe_document(
        databases=databases,
        database_id=DATABASE_ID,
        report_id=report_id,
        payload=payload,
    )


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
