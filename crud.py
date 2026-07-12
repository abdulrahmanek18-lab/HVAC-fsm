from __future__ import annotations

import calendar
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from appwrite.client import Client
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.input_file import InputFile
from appwrite.query import Query
from appwrite.services.account import Account
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.services.users import Users
from pypdf import PdfReader, PdfWriter

from models import (
    AppRole,
    AssetCreate,
    AssetUpdate,
    AutomatedScheduleUpdate,
    ComplianceAlert,
    ContractCreate,
    ContractUpdate,
    MaintenanceLogCreate,
    MaintenanceLogUpdate,
    ScheduleStatus,
    ScheduleType,
    SettingsCreate,
    SettingsUpdate,
    StaffCreate,
    StaffPosition,
    StaffUpdate,
    AuthContext,
)


# ============================================================
# Appwrite Collection / Bucket IDs
# ============================================================

COLLECTION_SETTINGS = os.getenv("APPWRITE_COLLECTION_SETTINGS", "settings")
COLLECTION_STAFF = os.getenv("APPWRITE_COLLECTION_STAFF", "staff")
COLLECTION_CONTRACTS = os.getenv("APPWRITE_COLLECTION_CONTRACTS", "contracts")
COLLECTION_SCHEDULES = os.getenv("APPWRITE_COLLECTION_SCHEDULES", "automated_schedules")
COLLECTION_ASSETS = os.getenv("APPWRITE_COLLECTION_ASSETS", "assets")
COLLECTION_MAINTENANCE_LOGS = os.getenv("APPWRITE_COLLECTION_MAINTENANCE_LOGS", "maintenance_logs")

BUCKET_STAFF_DOCUMENTS = os.getenv("APPWRITE_BUCKET_STAFF_DOCUMENTS", "staff_documents")
BUCKET_MAINTENANCE_UPLOADS = os.getenv("APPWRITE_BUCKET_MAINTENANCE_UPLOADS", "maintenance_uploads")

PDF_TEMPLATE_FILENAME = os.getenv("SCRIBUS_TEMPLATE_FILE", "service_report_template.pdf")


# ============================================================
# Errors / Utilities
# ============================================================

class AppError(Exception):
    def __init__(self, message: str, status_code: int = 400, details: Optional[Any] = None) -> None:
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class ScheduleItem:
    type: ScheduleType
    sequence_number: int
    due_date: date
    amount: Optional[Decimal]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def clean_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def to_json_compatible(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, list):
        return [to_json_compatible(v) for v in value]
    if isinstance(value, dict):
        return {k: to_json_compatible(v) for k, v in value.items()}
    return value


def serialize_for_appwrite(data: dict[str, Any]) -> dict[str, Any]:
    return clean_none(to_json_compatible(data))


def appwrite_error(exc: AppwriteException, fallback: str = "Appwrite operation failed") -> AppError:
    code = int(getattr(exc, "code", None) or 500)
    message = getattr(exc, "message", None) or fallback
    return AppError(message, code, str(exc))


def document_id(doc: dict[str, Any]) -> str:
    return doc.get("$id") or doc.get("id")


# ============================================================
# Generic Appwrite CRUD
# ============================================================

def create_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    data: dict[str, Any],
    document_id_value: Optional[str] = None,
) -> dict[str, Any]:
    try:
        return databases.create_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id_value or ID.unique(),
            data=serialize_for_appwrite(data),
        )
    except AppwriteException as exc:
        raise appwrite_error(exc) from exc


def get_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    document_id_value: str,
) -> dict[str, Any]:
    try:
        return databases.get_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id_value,
        )
    except AppwriteException as exc:
        if int(getattr(exc, "code", 500)) == 404:
            raise AppError("Document not found", 404, str(exc)) from exc
        raise appwrite_error(exc) from exc


def update_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    document_id_value: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    try:
        return databases.update_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id_value,
            data=serialize_for_appwrite(data),
        )
    except AppwriteException as exc:
        raise appwrite_error(exc) from exc


def delete_document(
    databases: Databases,
    database_id: str,
    collection_id: str,
    document_id_value: str,
) -> None:
    try:
        databases.delete_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=document_id_value,
        )
    except AppwriteException as exc:
        raise appwrite_error(exc) from exc


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
        
        # 1. Extract the documents container safely
        if hasattr(response, 'documents'):
            docs = response.documents
        elif isinstance(response, dict):
            docs = response.get("documents", [])
        else:
            docs = []

        cleaned_documents = []
        for doc in docs:
            # Base dictionary initialization
            doc_dict = {}
            
            # 2. Extract the user schema values safely using Pydantic methods
            if hasattr(doc, 'model_dump'):
                doc_dict = doc.model_dump()
            elif hasattr(doc, 'dict'):
                doc_dict = doc.dict()
            elif isinstance(doc, dict):
                doc_dict = doc.copy()
            else:
                doc_dict = getattr(doc, '__dict__', {}).copy()

            # 3. THE GOLDEN FIX: Manually map Appwrite's internal system fields
            # If they aren't in the dict, pull them directly from the object attributes
            system_fields = ["id", "collection_id", "database_id", "created_at", "updated_at", "permissions"]
            for field in system_fields:
                # Appwrite objects use standard snake_case attributes (e.g., doc.id)
                if hasattr(doc, field):
                    val = getattr(doc, field)
                    # Convert internal keys back to Appwrite's standard $ format
                    if field == "id":
                        doc_dict["$id"] = val
                    elif field == "collection_id":
                        doc_dict["$collectionId"] = val
                    elif field == "database_id":
                        doc_dict["$databaseId"] = val
                    elif field == "created_at":
                        doc_dict["$createdAt"] = val
                    elif field == "updated_at":
                        doc_dict["$updatedAt"] = val
                    elif field == "permissions":
                        doc_dict["$permissions"] = val

            cleaned_documents.append(doc_dict)

        return cleaned_documents
        
    except AppwriteException as exc:
        raise appwrite_error(exc) from exc
def list_all_documents(
    databases: Databases,
    database_id: str,
    collection_id: str,
    queries: Optional[list[str]] = None,
    page_size: int = 100,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    all_docs: list[dict[str, Any]] = []
    cursor_after: Optional[str] = None

    for _ in range(max_pages):
        page_queries = list(queries or [])
        page_queries.append(Query.limit(page_size))
        if cursor_after:
            page_queries.append(Query.cursor_after(cursor_after))

        docs = list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=collection_id,
            queries=page_queries,
        )

        if not docs:
            break

        all_docs.extend(docs)
        if len(docs) < page_size:
            break

        cursor_after = docs[-1]["$id"]

    return all_docs


# ============================================================
# RBAC / Auth Resolution
# ============================================================

def position_to_role(position: StaffPosition | str) -> AppRole:
    value = position.value if isinstance(position, StaffPosition) else position

    if value == StaffPosition.Admin.value:
        return AppRole.Admin
    if value == StaffPosition.Accountant.value:
        return AppRole.Accountant

    return AppRole.Technician


def require_roles(ctx: AuthContext, allowed_roles: set[AppRole]) -> None:
    if ctx.role not in allowed_roles:
        raise AppError("You are not authorized to perform this action", 403)


def require_admin(ctx: AuthContext) -> None:
    require_roles(ctx, {AppRole.Admin})


def require_accounting_access(ctx: AuthContext) -> None:
    require_roles(ctx, {AppRole.Admin, AppRole.Accountant})


def create_jwt_account(jwt: str) -> Account:
    endpoint = os.getenv("APPWRITE_ENDPOINT")
    project_id = os.getenv("APPWRITE_PROJECT_ID")

    if not endpoint or not project_id:
        raise AppError("Appwrite endpoint/project environment variables are missing", 500)

    jwt_client = Client()
    jwt_client.set_endpoint(endpoint)
    jwt_client.set_project(project_id)
    jwt_client.set_jwt(jwt)

    return Account(jwt_client)


def verify_appwrite_jwt(jwt: str) -> dict[str, Any]:
    try:
        return create_jwt_account(jwt).get()
    except AppwriteException as exc:
        raise AppError("Invalid or expired Appwrite JWT", 401, str(exc)) from exc


def find_staff_for_user(
    databases: Databases,
    database_id: str,
    account_user: dict[str, Any],
) -> dict[str, Any]:
    user_id = account_user.get("$id")
    email = account_user.get("email")

    if user_id:
        by_user_id = list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_STAFF,
            queries=[Query.equal("user_id", user_id), Query.limit(1)],
        )
        # FIX: Safely unpack the Appwrite response whether it is an object or a dictionary
        if by_user_id:
            if hasattr(by_user_id, 'documents') and by_user_id.documents:
                return by_user_id.documents[0]
            elif isinstance(by_user_id, dict) and by_user_id.get('documents'):
                return by_user_id['documents'][0]
            elif isinstance(by_user_id, list) and by_user_id:
                return by_user_id[0]

    if email:
        by_email = list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_STAFF,
            queries=[Query.equal("email", email), Query.limit(1)],
        )
        # FIX: Safely unpack the Appwrite response whether it is an object or a dictionary
        if by_email:
            if hasattr(by_email, 'documents') and by_email.documents:
                return by_email.documents[0]
            elif isinstance(by_email, dict) and by_email.get('documents'):
                return by_email['documents'][0]
            elif isinstance(by_email, list) and by_email:
                return by_email[0]

    raise AppError("Staff record not found for this authenticated user", 404)
    if email:
        by_email = list_documents(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_STAFF,
            queries=[Query.equal("email", email), Query.limit(1)],
        )
        if by_email:
            return by_email[0]

    raise AppError(
        "No staff profile is linked to this Appwrite user. Ask an Admin to create/link your Staff record.",
        403,
        {"user_id": user_id, "email": email},
    )


def resolve_auth_context(
    jwt: str,
    databases: Databases,
    users: Users,
    database_id: str,
) -> AuthContext:
    account_user = verify_appwrite_jwt(jwt)

    user_id = account_user["$id"]
    try:
        admin_user = users.get(user_id=user_id)
        if admin_user.get("status") is False:
            raise AppError("Appwrite user account is disabled", 403)
    except AppwriteException:
        admin_user = account_user

    staff = find_staff_for_user(databases, database_id, account_user)
    position = StaffPosition(staff["position"])
    role = position_to_role(position)

    return AuthContext(
        user_id=user_id,
        email=account_user.get("email"),
        staff_id=staff["$id"],
        name=staff["name"],
        position=position,
        role=role,
    )


# ============================================================
# Settings
# ============================================================

def get_settings(databases: Databases, database_id: str) -> dict[str, Any]:
    docs = list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SETTINGS,
        queries=[Query.limit(1)],
    )

    if docs:
        return docs[0]

    return create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SETTINGS,
        data={
            "company_name": "MAK INFRATECH",
            "trn_number": "",
            "vat_percentage": 5.0,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )


def upsert_settings(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: SettingsCreate | SettingsUpdate,
) -> dict[str, Any]:
    require_admin(ctx)

    existing = get_settings(databases, database_id)
    data = payload.model_dump(exclude_unset=True, mode="json")
    data["updated_at"] = utc_now_iso()

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SETTINGS,
        document_id_value=existing["$id"],
        data=data,
    )


# ============================================================
# Staff / HR Compliance
# ============================================================

def create_staff(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: StaffCreate,
) -> dict[str, Any]:
    require_admin(ctx)

    return create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_STAFF,
        data={
            **payload.model_dump(mode="json"),
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )


def update_staff(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    staff_id: str,
    payload: StaffUpdate,
) -> dict[str, Any]:
    require_admin(ctx)

    data = payload.model_dump(exclude_unset=True, mode="json")
    if not data:
        raise AppError("No fields provided for update", 400)

    data["updated_at"] = utc_now_iso()

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_STAFF,
        document_id_value=staff_id,
        data=data,
    )


def list_staff(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
) -> list[dict[str, Any]]:
    require_roles(ctx, {AppRole.Admin, AppRole.Accountant})

    staff = list_all_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_STAFF,
        queries=[Query.order_asc("name")],
    )

    if ctx.role == AppRole.Accountant:
        return [
            {
                "$id": s["$id"],
                "name": s.get("name"),
                "position": s.get("position"),
                "base_salary": s.get("base_salary", 0),
            }
            for s in staff
        ]

    return staff


def parse_optional_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def get_staff_compliance_alerts(
    databases: Databases,
    database_id: str,
    within_days: int = 30,
) -> list[ComplianceAlert]:
    staff_docs = list_all_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_STAFF,
        queries=[Query.limit(100)],
    )

    today = today_utc()
    limit_date = today + timedelta(days=within_days)
    alerts: list[ComplianceAlert] = []

    expiry_fields = [
        ("passport_expiry", "Passport"),
        ("eid_expiry", "Emirates ID"),
        ("insurance_expiry", "Insurance"),
    ]

    for staff in staff_docs:
        documents = staff.get("documents") or {}

        for key, label in expiry_fields:
            expiry = parse_optional_date(documents.get(key))
            if not expiry:
                continue

            if today <= expiry <= limit_date:
                alerts.append(
                    ComplianceAlert(
                        staff_id=staff["$id"],
                        staff_name=staff.get("name", "Unknown"),
                        position=StaffPosition(staff.get("position", "HVAC")),
                        document_type=label,
                        expiry_date=expiry,
                        days_remaining=(expiry - today).days,
                    )
                )

    return sorted(alerts, key=lambda item: item.days_remaining)


# ============================================================
# Scheduling Engine
# ============================================================

def contract_days(start_date: date, end_date: date) -> int:
    return max(1, (end_date - start_date).days)


def contract_year_factor(start_date: date, end_date: date) -> Decimal:
    days = Decimal(contract_days(start_date, end_date) + 1)
    return max(Decimal("1.0"), days / Decimal("365"))


def lifecycle_count(per_year: int, start_date: date, end_date: date) -> int:
    if per_year <= 0:
        return 0
    years = contract_year_factor(start_date, end_date)
    return max(1, int((Decimal(per_year) * years).to_integral_value(rounding=ROUND_HALF_UP)))


def add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def evenly_spaced_dates(start_date: date, end_date: date, count: int) -> list[date]:
    if count <= 0:
        return []

    if count == 1:
        return [start_date]

    total_days = (end_date - start_date).days

    return [
        date.fromordinal(start_date.toordinal() + round((total_days * index) / (count - 1)))
        for index in range(count)
    ]


def generate_emi_schedule(contract: ContractCreate | dict[str, Any]) -> list[ScheduleItem]:
    if isinstance(contract, dict):
        start_date = parse_optional_date(contract["start_date"])
        end_date = parse_optional_date(contract["end_date"])
        contract_value = Decimal(str(contract["contract_value"]))
        emis_per_year = int(contract["total_emis_per_year"])
    else:
        start_date = contract.start_date
        end_date = contract.end_date
        contract_value = Decimal(str(contract.contract_value))
        emis_per_year = contract.total_emis_per_year

    if not start_date or not end_date:
        raise AppError("Contract start/end date is invalid", 400)

    count = lifecycle_count(emis_per_year, start_date, end_date)
    dates = evenly_spaced_dates(start_date, end_date, count)

    if dates:
        dates[0] = start_date

    base_amount = (contract_value / Decimal(count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    running_total = Decimal("0.00")

    items: list[ScheduleItem] = []

    for i, due_date in enumerate(dates, start=1):
        if i == count:
            amount = (contract_value - running_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            amount = base_amount
            running_total += amount

        items.append(
            ScheduleItem(
                type=ScheduleType.EMI,
                sequence_number=i,
                due_date=due_date,
                amount=amount,
            )
        )

    return items


def generate_ppm_schedule(contract: ContractCreate | dict[str, Any]) -> list[ScheduleItem]:
    if isinstance(contract, dict):
        start_date = parse_optional_date(contract["start_date"])
        end_date = parse_optional_date(contract["end_date"])
        ppms_per_year = int(contract.get("total_ppms_per_year", 0))
    else:
        start_date = contract.start_date
        end_date = contract.end_date
        ppms_per_year = contract.total_ppms_per_year

    if not start_date or not end_date:
        raise AppError("Contract start/end date is invalid", 400)

    if ppms_per_year <= 0:
        return []

    count = lifecycle_count(ppms_per_year, start_date, end_date)
    interval_months = max(1, round(12 / ppms_per_year))

    dates: list[date] = []
    for i in range(count):
        ppm_date = add_months(start_date, i * interval_months)
        if ppm_date > end_date:
            break
        dates.append(ppm_date)

    if not dates:
        dates = evenly_spaced_dates(start_date, end_date, count)

    return [
        ScheduleItem(
            type=ScheduleType.PPM,
            sequence_number=i,
            due_date=due_date,
            amount=None,
        )
        for i, due_date in enumerate(dates, start=1)
    ]


def delete_contract_schedules(
    databases: Databases,
    database_id: str,
    contract_id: str,
) -> None:
    existing = list_all_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SCHEDULES,
        queries=[Query.equal("contract_id", contract_id)],
    )

    for doc in existing:
        delete_document(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_SCHEDULES,
            document_id_value=doc["$id"],
        )


def regenerate_contract_schedules(
    databases: Databases,
    database_id: str,
    contract_id: str,
    contract_data: ContractCreate | dict[str, Any],
) -> list[dict[str, Any]]:
    delete_contract_schedules(databases, database_id, contract_id)

    items = [
        *generate_emi_schedule(contract_data),
        *generate_ppm_schedule(contract_data),
    ]

    created: list[dict[str, Any]] = []

    for item in items:
        created.append(
            create_document(
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_SCHEDULES,
                data={
                    "contract_id": contract_id,
                    "type": item.type.value,
                    "sequence_number": item.sequence_number,
                    "due_date": item.due_date.isoformat(),
                    "amount": float(item.amount) if item.amount is not None else None,
                    "status": ScheduleStatus.Pending.value,
                    "created_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                },
            )
        )

    return created


# ============================================================
# Contracts
# ============================================================

def create_contract(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: ContractCreate,
) -> dict[str, Any]:
    require_roles(ctx, {AppRole.Admin, AppRole.Accountant})

    contract = create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_CONTRACTS,
        data={
            **payload.model_dump(mode="json"),
            "created_by_id": ctx.staff_id,
            "created_by_name": ctx.name,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )

    schedules = regenerate_contract_schedules(databases, database_id, contract["$id"], payload)
    contract["schedules"] = schedules

    return contract


def update_contract(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    contract_id: str,
    payload: ContractUpdate,
) -> dict[str, Any]:
    require_roles(ctx, {AppRole.Admin, AppRole.Accountant})

    existing = get_document(databases, database_id, COLLECTION_CONTRACTS, contract_id)
    data = payload.model_dump(exclude_unset=True, mode="json")

    if not data:
        raise AppError("No fields provided for update", 400)

    merged = {**existing, **data}
    data["updated_at"] = utc_now_iso()

    updated = update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_CONTRACTS,
        document_id_value=contract_id,
        data=data,
    )

    schedule_fields = {
        "contract_value",
        "start_date",
        "end_date",
        "total_ppms_per_year",
        "total_emis_per_year",
    }

    if schedule_fields.intersection(data.keys()):
        updated["schedules"] = regenerate_contract_schedules(databases, database_id, contract_id, merged)

    return updated


def get_contract(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    contract_id: str,
) -> dict[str, Any]:
    contract = get_document(databases, database_id, COLLECTION_CONTRACTS, contract_id)

    if ctx.role == AppRole.Technician:
        return {
            "$id": contract["$id"],
            "customer_name": contract.get("customer_name"),
            "building_villa_name": contract.get("building_villa_name"),
            "address": contract.get("address"),
            "start_date": contract.get("start_date"),
            "end_date": contract.get("end_date"),
        }

    return contract


def list_contracts(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
) -> list[dict[str, Any]]:
    contracts = list_all_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_CONTRACTS,
        queries=[Query.order_desc("start_date")],
    )

    if ctx.role == AppRole.Technician:
        return [
            {
                "$id": c["$id"],
                "customer_name": c.get("customer_name"),
                "building_villa_name": c.get("building_villa_name"),
                "address": c.get("address"),
                "start_date": c.get("start_date"),
                "end_date": c.get("end_date"),
            }
            for c in contracts
        ]

    return contracts


def list_schedules(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    contract_id: Optional[str] = None,
    schedule_type: Optional[ScheduleType] = None,
) -> list[dict[str, Any]]:
    queries: list[str] = [Query.order_asc("due_date")]

    if contract_id:
        queries.append(Query.equal("contract_id", contract_id))

    if schedule_type:
        queries.append(Query.equal("type", schedule_type.value))

    schedules = list_all_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SCHEDULES,
        queries=queries,
    )

    if ctx.role == AppRole.Technician:
        return [
            {
                "$id": s["$id"],
                "contract_id": s.get("contract_id"),
                "type": s.get("type"),
                "sequence_number": s.get("sequence_number"),
                "due_date": s.get("due_date"),
                "status": s.get("status"),
            }
            for s in schedules
            if s.get("type") == ScheduleType.PPM.value
        ]

    return schedules


def update_schedule(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    schedule_id: str,
    payload: AutomatedScheduleUpdate,
) -> dict[str, Any]:
    if payload.amount is not None:
        require_accounting_access(ctx)

    data = payload.model_dump(exclude_unset=True, mode="json")
    if not data:
        raise AppError("No fields provided for update", 400)

    data["updated_at"] = utc_now_iso()

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SCHEDULES,
        document_id_value=schedule_id,
        data=data,
    )


# ============================================================
# Assets
# ============================================================

def create_asset(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: AssetCreate,
) -> dict[str, Any]:
    require_roles(ctx, {AppRole.Admin, AppRole.Technician})

    return create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_ASSETS,
        data={
            **payload.model_dump(mode="json"),
            "created_by_id": ctx.staff_id,
            "created_by_name": ctx.name,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    )


def update_asset(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    asset_id: str,
    payload: AssetUpdate,
) -> dict[str, Any]:
    require_roles(ctx, {AppRole.Admin, AppRole.Technician})

    data = payload.model_dump(exclude_unset=True, mode="json")
    if not data:
        raise AppError("No fields provided for update", 400)

    data["updated_at"] = utc_now_iso()

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_ASSETS,
        document_id_value=asset_id,
        data=data,
    )


def list_assets(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    contract_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    queries: list[str] = [Query.order_asc("flat_villa_no")]

    if contract_id:
        queries.append(Query.equal("contract_id", contract_id))

    return list_all_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_ASSETS,
        queries=queries,
    )


# ============================================================
# Maintenance Logs
# ============================================================

def create_maintenance_log(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    payload: MaintenanceLogCreate,
) -> dict[str, Any]:
    require_roles(ctx, {AppRole.Admin, AppRole.Technician})

    technician_id = payload.technician_id or ctx.staff_id

    if ctx.role == AppRole.Technician:
        technician_id = ctx.staff_id

    data = payload.model_dump(mode="json")
    data["technician_id"] = technician_id
    data["technician_name"] = ctx.name
    data["created_at"] = utc_now_iso()
    data["updated_at"] = utc_now_iso()

    return create_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_MAINTENANCE_LOGS,
        data=data,
    )


def update_maintenance_log(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    log_id: str,
    payload: MaintenanceLogUpdate,
) -> dict[str, Any]:
    require_roles(ctx, {AppRole.Admin, AppRole.Technician})

    existing = get_document(databases, database_id, COLLECTION_MAINTENANCE_LOGS, log_id)

    if ctx.role == AppRole.Technician and existing.get("technician_id") != ctx.staff_id:
        raise AppError("Technicians can only update their own maintenance logs", 403)

    data = payload.model_dump(exclude_unset=True, mode="json")
    if not data:
        raise AppError("No fields provided for update", 400)

    data["updated_at"] = utc_now_iso()

    return update_document(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_MAINTENANCE_LOGS,
        document_id_value=log_id,
        data=data,
    )


def list_maintenance_logs(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    asset_id: Optional[str] = None,
    technician_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    queries: list[str] = [Query.order_desc("created_at")]

    if asset_id:
        queries.append(Query.equal("asset_id", asset_id))

    if technician_id:
        queries.append(Query.equal("technician_id", technician_id))

    if ctx.role == AppRole.Technician:
        queries.append(Query.equal("technician_id", ctx.staff_id))

    logs = list_all_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_MAINTENANCE_LOGS,
        queries=queries,
    )

    if ctx.role == AppRole.Accountant:
        return []

    return logs


# ============================================================
# Storage Upload
# ============================================================

async def upload_file_to_storage(
    storage: Storage,
    ctx: AuthContext,
    bucket_id: str,
    filename: str,
    content: bytes,
    content_type: Optional[str] = None,
) -> dict[str, Any]:
    require_roles(ctx, {AppRole.Admin, AppRole.Technician})

    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)

    try:
        input_file = InputFile.from_bytes(
            content,
            filename=safe_filename,
            mime_type=content_type or "application/octet-stream",
        )

        return storage.create_file(
            bucket_id=bucket_id,
            file_id=ID.unique(),
            file=input_file,
        )
    except AppwriteException as exc:
        raise appwrite_error(exc) from exc


# ============================================================
# Dashboard Stats
# ============================================================

def dashboard_stats(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
) -> dict[str, Any]:
    contracts = list_all_documents(databases, database_id, COLLECTION_CONTRACTS)
    schedules = list_all_documents(databases, database_id, COLLECTION_SCHEDULES)
    assets = list_all_documents(databases, database_id, COLLECTION_ASSETS)
    staff = list_all_documents(databases, database_id, COLLECTION_STAFF)
    alerts = get_staff_compliance_alerts(databases, database_id)

    pending_emis = [
        s for s in schedules
        if s.get("type") == ScheduleType.EMI.value and s.get("status") == ScheduleStatus.Pending.value
    ]
    pending_ppms = [
        s for s in schedules
        if s.get("type") == ScheduleType.PPM.value and s.get("status") == ScheduleStatus.Pending.value
    ]

    stats = {
        "active_contracts": len(contracts),
        "pending_emis": len(pending_emis),
        "pending_ppms": len(pending_ppms),
        "assets_tracked": len(assets),
        "staff_count": len(staff),
        "compliance_alerts": len(alerts),
    }

    if ctx.role in {AppRole.Admin, AppRole.Accountant}:
        stats["contract_value_total"] = round(sum(float(c.get("contract_value") or 0) for c in contracts), 2)
        stats["pending_emi_amount"] = round(sum(float(s.get("amount") or 0) for s in pending_emis), 2)

    return stats


# ============================================================
# Scribus / pypdf PDF Engine
# ============================================================

def resolve_pdf_template_path() -> Path:
    template_path = Path.cwd() / PDF_TEMPLATE_FILENAME

    if not template_path.exists():
        raise AppError(
            "Scribus PDF template not found",
            500,
            f"Expected file at {template_path}",
        )

    return template_path


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def active_ppm_stage(
    databases: Databases,
    database_id: str,
    contract_id: str,
) -> Optional[dict[str, Any]]:
    schedules = list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_SCHEDULES,
        queries=[
            Query.equal("contract_id", contract_id),
            Query.equal("type", ScheduleType.PPM.value),
            Query.order_asc("due_date"),
            Query.limit(100),
        ],
    )

    pending = [s for s in schedules if s.get("status") == ScheduleStatus.Pending.value]
    if pending:
        return pending[0]

    return schedules[-1] if schedules else None


def latest_log_for_asset(
    databases: Databases,
    database_id: str,
    asset_id: str,
) -> Optional[dict[str, Any]]:
    logs = list_documents(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_MAINTENANCE_LOGS,
        queries=[
            Query.equal("asset_id", asset_id),
            Query.order_desc("created_at"),
            Query.limit(1),
        ],
    )

    return logs[0] if logs else None


def build_scribus_pdf_payload(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    contract_id: str,
) -> dict[str, str]:
    contract = get_document(databases, database_id, COLLECTION_CONTRACTS, contract_id)
    assets = list_assets(databases, database_id, ctx, contract_id)
    ppm = active_ppm_stage(databases, database_id, contract_id)

    payload: dict[str, str] = {
        "company_name": "MAK INFRATECH",
        "customer_name": stringify(contract.get("customer_name")),
        "building_villa_name": stringify(contract.get("building_villa_name")),
        "address": stringify(contract.get("address")),
        "contract_id": contract_id,
        "report_date": today_utc().isoformat(),
        "ppm_sequence_number": stringify(ppm.get("sequence_number") if ppm else ""),
        "ppm_due_date": stringify(ppm.get("due_date") if ppm else ""),
        "technician_name": ctx.name,
        "technician_id": ctx.staff_id,
    }

    for index, asset in enumerate(assets, start=1):
        if index > 40:
            break

        log = latest_log_for_asset(databases, database_id, asset["$id"]) or {}
        params = log.get("parameters") or {}

        prefix = f"asset_{index}"

        payload.update(
            {
                f"{prefix}_flat_villa_no": stringify(asset.get("flat_villa_no")),
                f"{prefix}_unit_type": stringify(asset.get("unit_type")),
                f"{prefix}_brand": stringify(asset.get("brand")),
                f"{prefix}_tonnage": stringify(asset.get("tonnage")),
                f"{prefix}_serial_no": stringify(asset.get("serial_no")),
                f"{prefix}_work_category": stringify(log.get("work_category")),
                f"{prefix}_job_description": stringify(log.get("job_description")),
                f"{prefix}_suction_pressure": stringify(params.get("suction_pressure")),
                f"{prefix}_discharge_pressure": stringify(params.get("discharge_pressure")),
                f"{prefix}_ampere_reading": stringify(params.get("ampere_reading")),
                f"{prefix}_materials_used": stringify(params.get("materials_used")),
            }
        )

    return payload


def generate_scribus_pdf_bytes(
    databases: Databases,
    database_id: str,
    ctx: AuthContext,
    contract_id: str,
) -> tuple[bytes, str]:
    require_roles(ctx, {AppRole.Admin, AppRole.Technician})

    payload = build_scribus_pdf_payload(databases, database_id, ctx, contract_id)
    template_path = resolve_pdf_template_path()

    try:
        reader = PdfReader(str(template_path))
        writer = PdfWriter()
        writer.append(reader)

        fields = reader.get_fields() or {}
        if not fields:
            raise AppError(
                "Scribus PDF template contains no fillable AcroForm fields",
                500,
            )

        try:
            writer.set_need_appearances_writer(True)
        except Exception:
            pass

        filtered_payload = {
            key: value
            for key, value in payload.items()
            if key in fields
        }

        if not filtered_payload:
            raise AppError(
                "No matching PDF field names were found in the Scribus template",
                500,
                {
                    "template_fields": sorted(fields.keys()),
                    "payload_fields": sorted(payload.keys()),
                },
            )

        for page in writer.pages:
            writer.update_page_form_field_values(page, filtered_payload)

        output = BytesIO()
        writer.write(output)

        safe_name = re.sub(
            r"[^A-Za-z0-9_-]",
            "_",
            f"{payload.get('customer_name', 'contract')}_{contract_id}",
        )

        return output.getvalue(), f"{safe_name}.pdf"

    except AppError:
        raise
    except Exception as exc:
        raise AppError("Failed to generate Scribus PDF", 500, str(exc)) from exc
