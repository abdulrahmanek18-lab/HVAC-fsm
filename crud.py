from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import httpx
from appwrite.client import Client
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.query import Query
from appwrite.services.account import Account
from appwrite.services.databases import Databases
from appwrite.services.users import Users

from models import (
    ACUnitCreate,
    ACUnitUpdateMetrics,
    AuthContext,
    BarcodeParseResponse,
    ClientCreate,
    ClientUpdate,
    CustomerType,
    ExpenseApprovalUpdate,
    ExpenseCreate,
    InvoiceCreate,
    InvoiceStatusUpdate,
    ServiceReportCreate,
    ServiceReportStatus,
    ServiceReportUpdate,
    ScribeGenerateRequest,
    ScribeTemplateData,
    UserRole,
)


COLLECTION_PROFILES = os.getenv("APPWRITE_COLLECTION_PROFILES", "profiles")
COLLECTION_CLIENTS = os.getenv("APPWRITE_COLLECTION_CLIENTS", "clients")
COLLECTION_AMC_DETAILS = os.getenv("APPWRITE_COLLECTION_AMC_DETAILS", "amc_details")
COLLECTION_AMC_EMI_SCHEDULE = os.getenv("APPWRITE_COLLECTION_AMC_EMI_SCHEDULE", "amc_emi_schedule")
COLLECTION_AMC_PPM_SCHEDULE = os.getenv("APPWRITE_COLLECTION_AMC_PPM_SCHEDULE", "amc_ppm_schedule")
COLLECTION_AC_UNITS = os.getenv("APPWRITE_COLLECTION_AC_UNITS", "ac_units")
COLLECTION_SERVICE_REPORTS = os.getenv("APPWRITE_COLLECTION_SERVICE_REPORTS", "service_reports")
COLLECTION_INVOICES = os.getenv("APPWRITE_COLLECTION_INVOICES", "invoices")
COLLECTION_INVOICE_ITEMS = os.getenv("APPWRITE_COLLECTION_INVOICE_ITEMS", "invoice_items")
COLLECTION_EXPENSES = os.getenv("APPWRITE_COLLECTION_EXPENSES", "expenses")


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_document(document: dict[str, Any]) -> dict[str, Any]:
    return dict(document)


def clean_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def appwrite_error_to_app_error(exc: AppwriteException, fallback_message: str = "Appwrite operation failed") -> AppError:
    status_code = getattr(exc, "code", None) or 500
    message = getattr(exc, "message", None) or fallback_message
    return AppError(message=message, status_code=int(status_code), details=str(exc))


def get_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    document_id: str,
) -> dict[str, Any]:
    try:
        return databases.get_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id,
        )
    except AppwriteException as exc:
        if getattr(exc, "code", None) == 404:
            raise AppError("Document not found", 404, str(exc)) from exc
        raise appwrite_error_to_app_error(exc) from exc


def list_documents(
    databases: Databases,
    database_id: str,
    collection_id: str,
    queries: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    try:
        response = databases.list_documents(
            database_id=database_id,
            collection_id=collection_id,
            queries=queries or [],
        )
        return response.get("documents", [])
    except AppwriteException as exc:
        raise appwrite_error_to_app_error(exc) from exc


def create_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    data: dict[str, Any],
    document_id: Optional[str] = None,
) -> dict[str, Any]:
    try:
        return databases.create_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id or ID.unique(),
            data=clean_none(data),
        )
    except AppwriteException as exc:
        raise appwrite_error_to_app_error(exc) from exc


def update_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    document_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    try:
        return databases.update_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id,
            data=clean_none(data),
        )
    except AppwriteException as exc:
        raise appwrite_error_to_app_error(exc) from exc


def delete_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    document_id: str,
) -> None:
    try:
        databases.delete_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id,
        )
    except AppwriteException as exc:
        raise appwrite_error_to_app_error(exc) from exc


def require_admin_staff(ctx: AuthContext) -> None:
    if ctx.role != UserRole.admin_staff:
        raise AppError("Admin/Staff role required", 403)


def require_active_profile(profile: dict[str, Any]) -> None:
    if not bool(profile.get("is_active", True)):
        raise AppError("User profile is inactive", 403)


def build_account_client(jwt: str) -> Account:
    endpoint = os.getenv("APPWRITE_ENDPOINT")
    project_id = os.getenv("APPWRITE_PROJECT_ID")

    if not endpoint:
        raise AppError("APPWRITE_ENDPOINT is not configured", 500)
    if not project_id:
        raise AppError("APPWRITE_PROJECT_ID is not configured", 500)

    client = Client()
    client.set_endpoint(endpoint)
    client.set_project(project_id)
    client.set_jwt(jwt)

    return Account(client)


def get_account_user_from_jwt(jwt: str) -> dict[str, Any]:
    try:
        account = build_account_client(jwt)
        return account.get()
    except AppwriteException as exc:
        raise AppError("Invalid or expired Appwrite JWT", 401, str(exc)) from exc


def extract_role_from_user(user: dict[str, Any]) -> UserRole:
    prefs = user.get("prefs") or {}
    labels = user.get("labels") or []

    pref_role = prefs.get("role")
    if pref_role in {UserRole.admin_staff.value, UserRole.technician.value}:
        return UserRole(pref_role)

    for label in labels:
        if label in {UserRole.admin_staff.value, UserRole.technician.value}:
            return UserRole(label)

    return UserRole.technician


def get_or_create_profile(
    databases: Databases,
    users: Users,
    database_id: str,
    account_user: dict[str, Any],
) -> dict[str, Any]:
    user_id = account_user["$id"]

    try:
        profile = databases.get_document(
            database_id=database_id,
            collection_id=COLLECTION_PROFILES,
            document_id=user_id,
        )
        require_active_profile(profile)
        return profile
    except AppwriteException as exc:
        if getattr(exc, "code", None) != 404:
            raise appwrite_error_to_app_error(exc) from exc

    try:
        admin_user = users.get(user_id=user_id)
    except AppwriteException:
        admin_user = account_user

    role = extract_role_from_user(admin_user)
    full_name = (
        account_user.get("name")
        or admin_user.get("name")
        or account_user.get("email", "").split("@")[0]
        or "Appwrite User"
    )

    profile_data = {
        "user_id": user_id,
        "email": account_user.get("email") or admin_user.get("email"),
        "full_name": full_name,
        "role": role.value,
        "phone": (admin_user.get("phone") or None),
        "is_active": True,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }

    profile = create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_PROFILES,
        document_id=user_id,
        data=profile_data,
    )

    require_active_profile(profile)
    return profile


def resolve_auth_context(
    jwt: str,
    databases: Databases,
    users: Users,
    database_id: str,
) -> AuthContext:
    account_user = get_account_user_from_jwt(jwt)
    profile = get_or_create_profile(databases, users, database_id, account_user)

    return AuthContext(
        user_id=account_user["$id"],
        email=profile["email"],
        full_name=profile["full_name"],
        role=UserRole(profile["role"]),
        jwt=jwt,
    )


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
        date.fromordinal(start_date.toordinal() + round((total_days * index) / (count - 1)))
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
    base_amount = (contract_value / Decimal(emi_count)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    schedule: list[ScheduleItem] = []
    running_total = Decimal("0.00")

    for index, due_date in enumerate(dates, start=1):
        if index == emi_count:
            amount = (contract_value - running_total).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
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
    dates = calculate_evenly_spaced_dates(start_date, end_date, ppm_count)

    return [
        ScheduleItem(
            number=index,
            amount=None,
            scheduled_date=scheduled_date,
        )
        for index, scheduled_date in enumerate(dates, start=1)
    ]


def generate_barcode_value(client_id: str, unit_number: str) -> str:
    safe_unit_number = re.sub(r"[^A-Za-z0-9_-]", "-", unit_number.strip()).upper()
    asset_uuid = uuid.uuid4()
    return f"HVAC:{client_id}:{safe_unit_number}:{asset_uuid}"


def parse_barcode_value(barcode_value: str) -> BarcodeParseResponse:
    pattern = re.compile(
        r"^HVAC:"
        r"(?P<client_id>[A-Za-z0-9._-]+):"
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
            client_id=match.group("client_id"),
            unit_number=match.group("unit_number"),
            asset_uuid=uuid.UUID(match.group("asset_uuid")),
        )
    except ValueError:
        return BarcodeParseResponse(valid=False, barcode_value=barcode_value)


def generate_service_report_number() -> str:
    return f"SR-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def generate_invoice_number() -> str:
    return f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def create_client(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: ClientCreate,
) -> dict[str, Any]:
    require_admin_staff(ctx)

    client_data = {
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
        "created_by_id": ctx.user_id,
        "created_by_name": ctx.full_name,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }

    client_doc = create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_CLIENTS,
        data=client_data,
    )

    if payload.customer_type == CustomerType.amc and payload.amc_details:
        amc = payload.amc_details

        amc_doc = create_document(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_AMC_DETAILS,
            data={
                "client_id": client_doc["$id"],
                "contract_start_date": amc.contract_start_date.isoformat(),
                "contract_end_date": amc.contract_end_date.isoformat(),
                "contract_value": round(amc.contract_value, 2),
                "emi_count": amc.emi_count,
                "ppm_count": amc.ppm_count,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            },
        )

        emi_schedule = calculate_emi_schedule(
            contract_value=Decimal(str(amc.contract_value)),
            start_date=amc.contract_start_date,
            end_date=amc.contract_end_date,
            emi_count=amc.emi_count,
        )

        ppm_schedule = calculate_ppm_schedule(
            start_date=amc.contract_start_date,
            end_date=amc.contract_end_date,
            ppm_count=amc.ppm_count,
        )

        emi_docs: list[dict[str, Any]] = []
        for item in emi_schedule:
            emi_docs.append(
                create_document(
                    databases=databases,
                    database_id=database_id,
                    collection_id=COLLECTION_AMC_EMI_SCHEDULE,
                    data={
                        "amc_id": amc_doc["$id"],
                        "client_id": client_doc["$id"],
                        "installment_number": item.number,
                        "amount": float(item.amount or Decimal("0.00")),
                        "due_date": item.scheduled_date.isoformat(),
                        "is_paid": False,
                        "paid_at": None,
                        "created_at": utc_now_iso(),
                    },
                )
            )

        ppm_docs: list[dict[str, Any]] = []
        for item in ppm_schedule:
            ppm_docs.append(
                create_document(
                    databases=databases,
                    database_id=database_id,
                    collection_id=COLLECTION_AMC_PPM_SCHEDULE,
                    data={
                        "amc_id": amc_doc["$id"],
                        "client_id": client_doc["$id"],
                        "visit_number": item.number,
                        "scheduled_date": item.scheduled_date.isoformat(),
                        "completed_at": None,
                        "service_report_id": None,
                        "created_at": utc_now_iso(),
                    },
                )
            )

        client_doc["amc_details"] = amc_doc
        client_doc["emi_schedule"] = emi_docs
        client_doc["ppm_schedule"] = ppm_docs

    return client_doc


def update_client(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    client_id: str,
    payload: ClientUpdate,
) -> dict[str, Any]:
    require_admin_staff(ctx)

    update_data = payload.model_dump(exclude_unset=True, mode="json")
    if "email" in update_data and update_data["email"] is not None:
        update_data["email"] = str(update_data["email"])

    if not update_data:
        raise AppError("No fields provided for update", 400)

    update_data["updated_at"] = utc_now_iso()

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_CLIENTS,
        document_id=client_id,
        data=update_data,
    )


def get_client(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    client_id: str,
) -> dict[str, Any]:
    client_doc = get_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_CLIENTS,
        document_id=client_id,
    )

    if ctx.role == UserRole.admin_staff:
        return client_doc

    assigned_reports = list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SERVICE_REPORTS,
        queries=[
            Query.equal("client_id", client_id),
            Query.equal("assigned_technician_id", ctx.user_id),
            Query.limit(1),
        ],
    )

    if not assigned_reports:
        raise AppError("You are not assigned to this client", 403)

    return client_doc


def list_clients(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
) -> list[dict[str, Any]]:
    if ctx.role == UserRole.admin_staff:
        return list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_CLIENTS,
            queries=[
                Query.order_desc("$createdAt"),
                Query.limit(100),
            ],
        )

    reports = list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SERVICE_REPORTS,
        queries=[
            Query.equal("assigned_technician_id", ctx.user_id),
            Query.limit(100),
        ],
    )

    client_ids = sorted({report["client_id"] for report in reports if report.get("client_id")})
    clients: list[dict[str, Any]] = []

    for client_id in client_ids:
        try:
            clients.append(
                get_document(
                    databases=databases,
                    database_id=database_id,
                    collection_id=COLLECTION_CLIENTS,
                    document_id=client_id,
                )
            )
        except AppError:
            continue

    return clients


def create_ac_unit(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: ACUnitCreate,
) -> dict[str, Any]:
    require_admin_staff(ctx)

    barcode_value = generate_barcode_value(payload.client_id, payload.unit_number)

    return create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_AC_UNITS,
        data={
            "client_id": payload.client_id,
            "unit_number": payload.unit_number,
            "barcode_value": barcode_value,
            "brand": payload.brand.value,
            "refrigerant": payload.refrigerant.value,
            "pressure": payload.pressure,
            "ampere": payload.ampere,
            "condition": payload.condition.value,
            "location_description": payload.location_description,
            "last_serviced_at": None,
            "created_by_id": ctx.user_id,
            "created_by_name": ctx.full_name,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )


def assert_technician_assigned_to_client(
    databases: Databases,
    database_id: str,
    technician_id: str,
    client_id: str,
) -> None:
    reports = list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SERVICE_REPORTS,
        queries=[
            Query.equal("client_id", client_id),
            Query.equal("assigned_technician_id", technician_id),
            Query.limit(1),
        ],
    )

    if not reports:
        raise AppError("Technician is not assigned to this client", 403)


def update_ac_unit_metrics(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    unit_id: str,
    payload: ACUnitUpdateMetrics,
) -> dict[str, Any]:
    unit = get_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_AC_UNITS,
        document_id=unit_id,
    )

    if ctx.role != UserRole.admin_staff:
        assert_technician_assigned_to_client(
            databases=databases,
            database_id=database_id,
            technician_id=ctx.user_id,
            client_id=unit["client_id"],
        )

    update_data = payload.model_dump(exclude_unset=True, mode="json")

    if "condition" in update_data and update_data["condition"] is not None:
        update_data["condition"] = update_data["condition"]

    if "last_serviced_at" in update_data and update_data["last_serviced_at"] is not None:
        update_data["last_serviced_at"] = update_data["last_serviced_at"]

    if not update_data:
        raise AppError("No fields provided for update", 400)

    update_data["updated_at"] = utc_now_iso()

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_AC_UNITS,
        document_id=unit_id,
        data=update_data,
    )


def find_ac_unit_by_barcode(
    databases: Databases,
    database_id: str,
    barcode_value: str,
) -> Optional[dict[str, Any]]:
    results = list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_AC_UNITS,
        queries=[
            Query.equal("barcode_value", barcode_value),
            Query.limit(1),
        ],
    )

    return results[0] if results else None


def create_service_report(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: ServiceReportCreate,
) -> dict[str, Any]:
    assigned_technician_id = payload.assigned_technician_id

    if ctx.role == UserRole.technician:
        assigned_technician_id = ctx.user_id

    if ctx.role == UserRole.admin_staff and not assigned_technician_id:
        raise AppError("assigned_technician_id is required when staff creates a service report", 400)

    technician_profile = get_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_PROFILES,
        document_id=str(assigned_technician_id),
    )

    report_number = generate_service_report_number()

    return create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SERVICE_REPORTS,
        data={
            "report_number": report_number,
            "client_id": payload.client_id,
            "ac_unit_id": payload.ac_unit_id,
            "assigned_technician_id": assigned_technician_id,
            "assigned_technician_name": technician_profile["full_name"],
            "scheduled_at": payload.scheduled_at.isoformat(),
            "nature_of_complaint": payload.nature_of_complaint,
            "work_performed": None,
            "technician_observations": None,
            "pressure_after_service": None,
            "ampere_after_service": None,
            "status": ServiceReportStatus.scheduled.value,
            "scribe_payload": None,
            "scribe_document_url": None,
            "completed_at": None,
            "created_by_id": ctx.user_id,
            "created_by_name": ctx.full_name,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )


def get_service_report(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    report_id: str,
) -> dict[str, Any]:
    report = get_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SERVICE_REPORTS,
        document_id=report_id,
    )

    if ctx.role != UserRole.admin_staff and report.get("assigned_technician_id") != ctx.user_id:
        raise AppError("You are not assigned to this service report", 403)

    client_doc = get_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_CLIENTS,
        document_id=report["client_id"],
    )

    report["client"] = client_doc

    if report.get("ac_unit_id"):
        try:
            report["ac_unit"] = get_document(
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_AC_UNITS,
                document_id=report["ac_unit_id"],
            )
        except AppError:
            report["ac_unit"] = None

    return report


def list_assigned_service_reports(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
) -> list[dict[str, Any]]:
    if ctx.role == UserRole.admin_staff:
        reports = list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_SERVICE_REPORTS,
            queries=[
                Query.order_asc("scheduled_at"),
                Query.limit(100),
            ],
        )
    else:
        reports = list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_SERVICE_REPORTS,
            queries=[
                Query.equal("assigned_technician_id", ctx.user_id),
                Query.order_asc("scheduled_at"),
                Query.limit(100),
            ],
        )

    enriched_reports: list[dict[str, Any]] = []

    for report in reports:
        try:
            report["client"] = get_document(
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_CLIENTS,
                document_id=report["client_id"],
            )
        except AppError:
            report["client"] = None

        if report.get("ac_unit_id"):
            try:
                report["ac_unit"] = get_document(
                    databases=databases,
                    database_id=database_id,
                    collection_id=COLLECTION_AC_UNITS,
                    document_id=report["ac_unit_id"],
                )
            except AppError:
                report["ac_unit"] = None

        enriched_reports.append(report)

    return enriched_reports


def update_service_report(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    report_id: str,
    payload: ServiceReportUpdate,
) -> dict[str, Any]:
    existing = get_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SERVICE_REPORTS,
        document_id=report_id,
    )

    if ctx.role != UserRole.admin_staff and existing.get("assigned_technician_id") != ctx.user_id:
        raise AppError("You are not assigned to this service report", 403)

    update_data = payload.model_dump(exclude_unset=True, mode="json")

    if not update_data:
        raise AppError("No fields provided for update", 400)

    if update_data.get("status") == ServiceReportStatus.completed.value and not update_data.get("completed_at"):
        update_data["completed_at"] = utc_now_iso()

    update_data["updated_at"] = utc_now_iso()
    update_data["last_updated_by_id"] = ctx.user_id
    update_data["last_updated_by_name"] = ctx.full_name

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SERVICE_REPORTS,
        document_id=report_id,
        data=update_data,
    )


def build_full_address(client_doc: dict[str, Any]) -> str:
    parts = [
        client_doc.get("address_line1"),
        client_doc.get("address_line2"),
        client_doc.get("city"),
        client_doc.get("state"),
        client_doc.get("postal_code"),
    ]

    return ", ".join(str(part) for part in parts if part)


def build_scribe_payload(
    report: dict[str, Any],
    ctx: AuthContext,
    template_id: str,
) -> ScribeGenerateRequest:
    client_doc = report.get("client")

    if not client_doc:
        raise AppError("Client data missing from service report", 500)

    template_data = ScribeTemplateData(
        service_report_number=report["report_number"],
        client_name=client_doc["name"],
        full_address=build_full_address(client_doc),
        flat_number=client_doc.get("flat_number"),
        scheduled_date_time=str(report["scheduled_at"]),
        nature_of_complaint=report["nature_of_complaint"],
        automated_staff_name=ctx.full_name,
        automated_staff_id=ctx.user_id,
    )

    return ScribeGenerateRequest(
        template_id=template_id,
        data=template_data,
    )


async def generate_scribe_document(
    databases: Databases,
    database_id: str,
    report_id: str,
    payload: ScribeGenerateRequest,
) -> dict[str, Any]:
    api_base_url = os.getenv("SCRIBE_API_BASE_URL", "").rstrip("/")
    api_key = os.getenv("SCRIBE_API_KEY", "")
    generate_path = os.getenv("SCRIBE_DOCUMENT_GENERATE_PATH", "/documents/generate")

    if not api_base_url:
        raise AppError("SCRIBE_API_BASE_URL is not configured", 500)

    if not api_key:
        raise AppError("SCRIBE_API_KEY is not configured", 500)

    endpoint = f"{api_base_url}{generate_path}"

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

        if "application/json" in content_type.lower():
            result = response.json()
        else:
            result = {
                "content_type": content_type,
                "raw_response": response.text,
            }

        document_url = (
            result.get("document_url")
            or result.get("download_url")
            or result.get("url")
        )

        update_data: dict[str, Any] = {
            "scribe_payload": payload.model_dump(mode="json"),
            "updated_at": utc_now_iso(),
        }

        if document_url:
            update_data["scribe_document_url"] = document_url

        update_document(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_SERVICE_REPORTS,
            document_id=report_id,
            data=update_data,
        )

        return result

    except AppError:
        raise
    except httpx.RequestError as exc:
        raise AppError("Unable to connect to Scribe API", 502, str(exc)) from exc


def create_invoice(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: InvoiceCreate,
) -> dict[str, Any]:
    require_admin_staff(ctx)

    subtotal = sum(item.quantity * item.unit_price for item in payload.items)
    total_amount = subtotal + payload.tax_amount
    invoice_number = generate_invoice_number()

    invoice_doc = create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_INVOICES,
        data={
            "invoice_number": invoice_number,
            "client_id": payload.client_id,
            "service_report_id": payload.service_report_id,
            "issue_date": date.today().isoformat(),
            "due_date": payload.due_date.isoformat() if payload.due_date else None,
            "subtotal": round(subtotal, 2),
            "tax_amount": round(payload.tax_amount, 2),
            "total_amount": round(total_amount, 2),
            "status": "draft",
            "notes": payload.notes,
            "created_by_id": ctx.user_id,
            "created_by_name": ctx.full_name,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )

    item_docs: list[dict[str, Any]] = []

    for item in payload.items:
        item_docs.append(
            create_document(
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_INVOICE_ITEMS,
                data={
                    "invoice_id": invoice_doc["$id"],
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "line_total": round(item.quantity * item.unit_price, 2),
                    "created_at": utc_now_iso(),
                },
            )
        )

    invoice_doc["items"] = item_docs
    return invoice_doc


def update_invoice_status(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    invoice_id: str,
    payload: InvoiceStatusUpdate,
) -> dict[str, Any]:
    require_admin_staff(ctx)

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_INVOICES,
        document_id=invoice_id,
        data={
            "status": payload.status.value,
            "updated_at": utc_now_iso(),
        },
    )


def create_expense(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: ExpenseCreate,
) -> dict[str, Any]:
    if payload.service_report_id:
        report = get_document(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_SERVICE_REPORTS,
            document_id=payload.service_report_id,
        )

        if ctx.role != UserRole.admin_staff and report.get("assigned_technician_id") != ctx.user_id:
            raise AppError("You are not assigned to this service report", 403)

    return create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_EXPENSES,
        data={
            "technician_id": ctx.user_id,
            "technician_name": ctx.full_name,
            "service_report_id": payload.service_report_id,
            "category": payload.category.value,
            "amount": payload.amount,
            "expense_date": payload.expense_date.isoformat(),
            "description": payload.description,
            "receipt_url": payload.receipt_url,
            "approved": False,
            "approved_by_id": None,
            "approved_by_name": None,
            "approved_at": None,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )


def list_expenses(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
) -> list[dict[str, Any]]:
    if ctx.role == UserRole.admin_staff:
        return list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_EXPENSES,
            queries=[
                Query.order_desc("expense_date"),
                Query.limit(100),
            ],
        )

    return list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_EXPENSES,
        queries=[
            Query.equal("technician_id", ctx.user_id),
            Query.order_desc("expense_date"),
            Query.limit(100),
        ],
    )


def approve_expense(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    expense_id: str,
    payload: ExpenseApprovalUpdate,
) -> dict[str, Any]:
    require_admin_staff(ctx)

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_EXPENSES,
        document_id=expense_id,
        data={
            "approved": payload.approved,
            "approved_by_id": ctx.user_id if payload.approved else None,
            "approved_by_name": ctx.full_name if payload.approved else None,
            "approved_at": utc_now_iso() if payload.approved else None,
            "updated_at": utc_now_iso(),
        },
    )
