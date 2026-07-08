from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID

import httpx
from supabase import Client

from models import (
    ACUnitCreate,
    ACUnitUpdateMetrics,
    AuthContext,
    BarcodeParseResponse,
    ClientCreate,
    CustomerType,
    ExpenseCreate,
    InvoiceCreate,
    ServiceReportCreate,
    ServiceReportStatus,
    ServiceReportUpdate,
    ScribeGenerateRequest,
    ScribeTemplateData,
    UserRole,
)


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 400, details: Optional[Any] = None) -> None:
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class ScheduleItem:
    number: int
    amount: Optional[Decimal]
    scheduled_date: date


def _execute(query: Any) -> Any:
    try:
        response = query.execute()
        return response.data
    except Exception as exc:
        raise AppError("Database operation failed", 500, str(exc)) from exc


def require_admin_staff(ctx: AuthContext) -> None:
    if ctx.role != UserRole.admin_staff:
        raise AppError("Admin/Staff role required", 403)


def require_active_profile(profile: dict[str, Any]) -> None:
    if not profile.get("is_active", False):
        raise AppError("User profile is inactive", 403)


def get_profile(db: Client, user_id: UUID) -> dict[str, Any]:
    data = _execute(
        db.table("profiles")
        .select("*")
        .eq("id", str(user_id))
        .single()
    )
    if not data:
        raise AppError("Profile not found", 404)
    return data


def calculate_evenly_spaced_dates(start_date: date, end_date: date, count: int) -> list[date]:
    if count < 0:
        raise AppError("count cannot be negative", 400)
    if count == 0:
        return []
    if end_date < start_date:
        raise AppError("end_date cannot be before start_date", 400)
    if count == 1:
        return [start_date]

    total_days = (end_date - start_date).days
    return [
        start_date.fromordinal(start_date.toordinal() + round((total_days * index) / (count - 1)))
        for index in range(count)
    ]


def calculate_emi_schedule(
    contract_value: Decimal,
    start_date: date,
    end_date: date,
    emi_count: int,
) -> list[ScheduleItem]:
    if emi_count < 1:
        raise AppError("emi_count must be at least 1", 400)

    dates = calculate_evenly_spaced_dates(start_date, end_date, emi_count)
    base_amount = (contract_value / Decimal(emi_count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    schedule: list[ScheduleItem] = []
    running_total = Decimal("0.00")

    for index, due_date in enumerate(dates, start=1):
        if index == emi_count:
            amount = (contract_value - running_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            amount = base_amount
            running_total += amount

        schedule.append(
            ScheduleItem(
                number=index,
                amount=amount,
                scheduled_date=due_date,
            )
        )

    return schedule


def calculate_ppm_schedule(start_date: date, end_date: date, ppm_count: int) -> list[ScheduleItem]:
    return [
        ScheduleItem(number=index, amount=None, scheduled_date=scheduled_date)
        for index, scheduled_date in enumerate(
            calculate_evenly_spaced_dates(start_date, end_date, ppm_count),
            start=1,
        )
    ]


def generate_barcode_value(client_id: UUID, unit_number: str) -> str:
    safe_unit = re.sub(r"[^A-Za-z0-9_-]", "-", unit_number.strip()).upper()
    asset_uuid = uuid.uuid4()
    return f"HVAC:{client_id}:{safe_unit}:{asset_uuid}"


def parse_barcode_value(barcode_value: str) -> BarcodeParseResponse:
    pattern = re.compile(
        r"^HVAC:"
        r"(?P<client_id>[0-9a-fA-F-]{36}):"
        r"(?P<unit_number>[A-Za-z0-9_-]+):"
        r"(?P<asset_uuid>[0-9a-fA-F-]{36})$"
    )
    match = pattern.match(barcode_value.strip())

    if not match:
        return BarcodeParseResponse(valid=False, barcode_value=barcode_value)

    try:
        return BarcodeParseResponse(
            valid=True,
            barcode_value=barcode_value,
            client_id=UUID(match.group("client_id")),
            unit_number=match.group("unit_number"),
            asset_uuid=UUID(match.group("asset_uuid")),
        )
    except ValueError:
        return BarcodeParseResponse(valid=False, barcode_value=barcode_value)


def create_client(db: Client, ctx: AuthContext, payload: ClientCreate) -> dict[str, Any]:
    require_admin_staff(ctx)

    client_row = {
        "customer_type": payload.customer_type.value,
        "name": payload.name,
        "contact_person": payload.contact_person,
        "phone": payload.phone,
        "email": str(payload.email) if payload.email else None,
        "address_line1": payload.address_line1,
        "address_line2": payload.address_line2,
        "city": payload.city,
        "state": payload.state,
        "postal_code": payload.postal_code,
        "flat_number": payload.flat_number,
        "notes": payload.notes,
        "created_by": str(ctx.user_id),
    }

    inserted_client = _execute(
        db.table("clients")
        .insert(client_row)
        .execute()
    )

    if not inserted_client:
        raise AppError("Client creation failed", 500)

    client = inserted_client[0]

    if payload.customer_type == CustomerType.amc and payload.amc_details:
        amc = payload.amc_details
        amc_row = {
            "client_id": client["id"],
            "contract_start_date": amc.contract_start_date.isoformat(),
            "contract_end_date": amc.contract_end_date.isoformat(),
            "contract_value": amc.contract_value,
            "emi_count": amc.emi_count,
            "ppm_count": amc.ppm_count,
        }

        inserted_amc = _execute(
            db.table("amc_details")
            .insert(amc_row)
            .execute()
        )

        client["amc_details"] = inserted_amc[0] if inserted_amc else None

        if inserted_amc:
            amc_id = inserted_amc[0]["id"]
            client["emi_schedule"] = _execute(
                db.table("amc_emi_schedule")
                .select("*")
                .eq("amc_id", amc_id)
                .order("installment_number")
            )
            client["ppm_schedule"] = _execute(
                db.table("amc_ppm_schedule")
                .select("*")
                .eq("amc_id", amc_id)
                .order("visit_number")
            )

    return client


def list_clients(db: Client, ctx: AuthContext) -> list[dict[str, Any]]:
    query = db.table("clients").select("*").order("created_at", desc=True)
    return _execute(query)


def get_client(db: Client, client_id: UUID) -> dict[str, Any]:
    client = _execute(
        db.table("clients")
        .select("*")
        .eq("id", str(client_id))
        .single()
    )
    if not client:
        raise AppError("Client not found", 404)
    return client


def create_ac_unit(db: Client, ctx: AuthContext, payload: ACUnitCreate) -> dict[str, Any]:
    require_admin_staff(ctx)

    barcode_value = generate_barcode_value(payload.client_id, payload.unit_number)

    row = {
        "client_id": str(payload.client_id),
        "unit_number": payload.unit_number,
        "barcode_value": barcode_value,
        "brand": payload.brand.value,
        "refrigerant": payload.refrigerant.value,
        "pressure": payload.pressure,
        "ampere": payload.ampere,
        "condition": payload.condition.value,
        "location_description": payload.location_description,
        "created_by": str(ctx.user_id),
    }

    inserted = _execute(db.table("ac_units").insert(row))
    if not inserted:
        raise AppError("AC unit creation failed", 500)

    return inserted[0]


def update_ac_unit_metrics(db: Client, unit_id: UUID, payload: ACUnitUpdateMetrics) -> dict[str, Any]:
    update_data: dict[str, Any] = {}

    if payload.pressure is not None:
        update_data["pressure"] = payload.pressure
    if payload.ampere is not None:
        update_data["ampere"] = payload.ampere
    if payload.condition is not None:
        update_data["condition"] = payload.condition.value
    if payload.last_serviced_at is not None:
        update_data["last_serviced_at"] = payload.last_serviced_at.isoformat()

    if not update_data:
        raise AppError("No fields provided for update", 400)

    updated = _execute(
        db.table("ac_units")
        .update(update_data)
        .eq("id", str(unit_id))
    )

    if not updated:
        raise AppError("AC unit not found or not permitted", 404)

    return updated[0]


def create_service_report(db: Client, ctx: AuthContext, payload: ServiceReportCreate) -> dict[str, Any]:
    assigned_technician_id = payload.assigned_technician_id

    if ctx.role == UserRole.technician:
        assigned_technician_id = ctx.user_id
    elif ctx.role == UserRole.admin_staff and assigned_technician_id is None:
        raise AppError("assigned_technician_id is required when staff creates a service report", 400)

    row = {
        "client_id": str(payload.client_id),
        "ac_unit_id": str(payload.ac_unit_id) if payload.ac_unit_id else None,
        "assigned_technician_id": str(assigned_technician_id),
        "scheduled_at": payload.scheduled_at.isoformat(),
        "nature_of_complaint": payload.nature_of_complaint,
        "created_by": str(ctx.user_id),
    }

    inserted = _execute(db.table("service_reports").insert(row))
    if not inserted:
        raise AppError("Service report creation failed", 500)

    return inserted[0]


def list_assigned_service_reports(db: Client, ctx: AuthContext) -> list[dict[str, Any]]:
    if ctx.role == UserRole.admin_staff:
        return _execute(
            db.table("service_reports")
            .select("*, clients(*), ac_units(*)")
            .order("scheduled_at", desc=False)
        )

    return _execute(
        db.table("service_reports")
        .select("*, clients(*), ac_units(*)")
        .eq("assigned_technician_id", str(ctx.user_id))
        .order("scheduled_at", desc=False)
    )


def update_service_report(
    db: Client,
    report_id: UUID,
    payload: ServiceReportUpdate,
) -> dict[str, Any]:
    update_data: dict[str, Any] = {}

    if payload.work_performed is not None:
        update_data["work_performed"] = payload.work_performed
    if payload.technician_observations is not None:
        update_data["technician_observations"] = payload.technician_observations
    if payload.status is not None:
        update_data["status"] = payload.status.value
    if payload.completed_at is not None:
        update_data["completed_at"] = payload.completed_at.isoformat()

    if payload.status == ServiceReportStatus.completed and payload.completed_at is None:
        update_data["completed_at"] = datetime.utcnow().isoformat()

    if not update_data:
        raise AppError("No fields provided for update", 400)

    updated = _execute(
        db.table("service_reports")
        .update(update_data)
        .eq("id", str(report_id))
    )

    if not updated:
        raise AppError("Service report not found or not permitted", 404)

    return updated[0]


def get_service_report(db: Client, report_id: UUID) -> dict[str, Any]:
    data = _execute(
        db.table("service_reports")
        .select("*, clients(*), ac_units(*)")
        .eq("id", str(report_id))
        .single()
    )
    if not data:
        raise AppError("Service report not found", 404)
    return data


def build_full_address(client: dict[str, Any]) -> str:
    parts = [
        client.get("address_line1"),
        client.get("address_line2"),
        client.get("city"),
        client.get("state"),
        client.get("postal_code"),
    ]
    return ", ".join(str(part) for part in parts if part)


def build_scribe_payload(
    report: dict[str, Any],
    ctx: AuthContext,
    template_id: str,
) -> ScribeGenerateRequest:
    client = report.get("clients")
    if not client:
        raise AppError("Client data missing from service report", 500)

    scheduled_at = report["scheduled_at"]

    template_data = ScribeTemplateData(
        service_report_number=report["report_number"],
        client_name=client["name"],
        full_address=build_full_address(client),
        flat_number=client.get("flat_number"),
        scheduled_date_time=scheduled_at,
        nature_of_complaint=report["nature_of_complaint"],
        automated_staff_name=ctx.full_name,
        automated_staff_id=str(ctx.user_id),
    )

    return ScribeGenerateRequest(
        template_id=template_id,
        data=template_data,
    )


async def generate_scribe_document(
    db: Client,
    report_id: UUID,
    payload: ScribeGenerateRequest,
) -> dict[str, Any]:
    api_base_url = os.getenv("SCRIBE_API_BASE_URL", "").rstrip("/")
    api_key = os.getenv("SCRIBE_API_KEY", "")

    if not api_base_url:
        raise AppError("SCRIBE_API_BASE_URL is not configured", 500)
    if not api_key:
        raise AppError("SCRIBE_API_KEY is not configured", 500)

    endpoint = f"{api_base_url}/documents/generate"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload.model_dump(mode="json"),
            )

        if response.status_code >= 400:
            raise AppError(
                "Scribe document generation failed",
                response.status_code,
                response.text,
            )

        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type:
            result = response.json()
        else:
            result = {
                "content_type": content_type,
                "raw_response": response.text,
            }

        document_url = result.get("document_url") or result.get("download_url") or result.get("url")

        update_data: dict[str, Any] = {
            "scribe_payload": payload.model_dump(mode="json"),
        }

        if document_url:
            update_data["scribe_document_url"] = document_url

        _execute(
            db.table("service_reports")
            .update(update_data)
            .eq("id", str(report_id))
        )

        return result

    except AppError:
        raise
    except httpx.RequestError as exc:
        raise AppError("Unable to connect to Scribe API", 502, str(exc)) from exc


def create_invoice(db: Client, ctx: AuthContext, payload: InvoiceCreate) -> dict[str, Any]:
    require_admin_staff(ctx)

    subtotal = sum(item.quantity * item.unit_price for item in payload.items)
    total = subtotal + payload.tax_amount

    invoice_row = {
        "client_id": str(payload.client_id),
        "service_report_id": str(payload.service_report_id) if payload.service_report_id else None,
        "due_date": payload.due_date.isoformat() if payload.due_date else None,
        "subtotal": round(subtotal, 2),
        "tax_amount": round(payload.tax_amount, 2),
        "total_amount": round(total, 2),
        "notes": payload.notes,
        "created_by": str(ctx.user_id),
    }

    inserted_invoice = _execute(db.table("invoices").insert(invoice_row))
    if not inserted_invoice:
        raise AppError("Invoice creation failed", 500)

    invoice = inserted_invoice[0]

    item_rows = [
        {
            "invoice_id": invoice["id"],
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
        }
        for item in payload.items
    ]

    invoice["items"] = _execute(db.table("invoice_items").insert(item_rows))
    return invoice


def create_expense(db: Client, ctx: AuthContext, payload: ExpenseCreate) -> dict[str, Any]:
    row = {
        "technician_id": str(ctx.user_id),
        "service_report_id": str(payload.service_report_id) if payload.service_report_id else None,
        "category": payload.category.value,
        "amount": payload.amount,
        "expense_date": payload.expense_date.isoformat(),
        "description": payload.description,
        "receipt_url": payload.receipt_url,
    }

    inserted = _execute(db.table("expenses").insert(row))
    if not inserted:
        raise AppError("Expense creation failed", 500)

    return inserted[0]
