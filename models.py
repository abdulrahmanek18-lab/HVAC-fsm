from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserRole(str, Enum):
    admin_staff = "admin_staff"
    technician = "technician"


class CustomerType(str, Enum):
    walk_in = "walk_in"
    amc = "amc"


class ServiceReportStatus(str, Enum):
    scheduled = "scheduled"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class InvoiceStatus(str, Enum):
    draft = "draft"
    sent = "sent"
    paid = "paid"
    overdue = "overdue"
    cancelled = "cancelled"


class ExpenseCategory(str, Enum):
    fuel = "fuel"
    parts = "parts"
    tools = "tools"
    parking = "parking"
    toll = "toll"
    meals = "meals"
    other = "other"


class ACBrand(str, Enum):
    Daikin = "Daikin"
    Carrier = "Carrier"
    LG = "LG"
    Samsung = "Samsung"
    Voltas = "Voltas"
    Blue_Star = "Blue Star"
    Hitachi = "Hitachi"
    Panasonic = "Panasonic"
    Mitsubishi = "Mitsubishi"
    O_General = "O General"
    Other = "Other"


class RefrigerantType(str, Enum):
    R22 = "R22"
    R32 = "R32"
    R410A = "R410A"
    R134A = "R134A"
    R290 = "R290"
    R407C = "R407C"
    Other = "Other"


class ACCondition(str, Enum):
    excellent = "excellent"
    good = "good"
    fair = "fair"
    poor = "poor"
    needs_repair = "needs_repair"
    not_working = "not_working"


class ProfileOut(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    phone: Optional[str] = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AuthContext(BaseModel):
    user_id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    access_token: str


class AMCDetailsCreate(BaseModel):
    contract_start_date: date
    contract_end_date: date
    contract_value: float = Field(ge=0)
    emi_count: int = Field(ge=1)
    ppm_count: int = Field(ge=0)

    @field_validator("contract_end_date")
    @classmethod
    def validate_contract_dates(cls, end_date: date, info: Any) -> date:
        start_date = info.data.get("contract_start_date")
        if start_date and end_date < start_date:
            raise ValueError("contract_end_date must be greater than or equal to contract_start_date")
        return end_date


class ClientCreate(BaseModel):
    customer_type: CustomerType
    name: str = Field(min_length=1, max_length=200)
    contact_person: Optional[str] = None
    phone: str = Field(min_length=5, max_length=40)
    email: Optional[EmailStr] = None
    address_line1: str = Field(min_length=1)
    address_line2: Optional[str] = None
    city: str = Field(min_length=1)
    state: str = Field(min_length=1)
    postal_code: Optional[str] = None
    flat_number: Optional[str] = None
    notes: Optional[str] = None
    amc_details: Optional[AMCDetailsCreate] = None

    @field_validator("amc_details")
    @classmethod
    def require_amc_details_for_amc(cls, amc_details: Optional[AMCDetailsCreate], info: Any) -> Optional[AMCDetailsCreate]:
        if info.data.get("customer_type") == CustomerType.amc and amc_details is None:
            raise ValueError("amc_details is required when customer_type is amc")
        return amc_details


class ClientOut(BaseModel):
    id: UUID
    customer_type: CustomerType
    name: str
    phone: str
    email: Optional[str] = None
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    postal_code: Optional[str] = None
    flat_number: Optional[str] = None
    created_at: datetime


class ACUnitCreate(BaseModel):
    client_id: UUID
    unit_number: str = Field(min_length=1, max_length=80)
    brand: ACBrand
    refrigerant: RefrigerantType
    pressure: Optional[float] = Field(default=None, ge=0)
    ampere: Optional[float] = Field(default=None, ge=0)
    condition: ACCondition = ACCondition.good
    location_description: Optional[str] = None


class ACUnitUpdateMetrics(BaseModel):
    pressure: Optional[float] = Field(default=None, ge=0)
    ampere: Optional[float] = Field(default=None, ge=0)
    condition: Optional[ACCondition] = None
    last_serviced_at: Optional[datetime] = None


class BarcodeParseRequest(BaseModel):
    barcode_value: str = Field(min_length=1)


class BarcodeParseResponse(BaseModel):
    valid: bool
    barcode_value: str
    client_id: Optional[UUID] = None
    unit_number: Optional[str] = None
    asset_uuid: Optional[UUID] = None


class ServiceReportCreate(BaseModel):
    client_id: UUID
    ac_unit_id: Optional[UUID] = None
    assigned_technician_id: Optional[UUID] = None
    scheduled_at: datetime
    nature_of_complaint: str = Field(min_length=1)


class ServiceReportUpdate(BaseModel):
    work_performed: Optional[str] = None
    technician_observations: Optional[str] = None
    status: Optional[ServiceReportStatus] = None
    completed_at: Optional[datetime] = None


class ScribeTemplateData(BaseModel):
    service_report_number: str
    client_name: str
    full_address: str
    flat_number: Optional[str] = None
    scheduled_date_time: str
    nature_of_complaint: str
    automated_staff_name: str
    automated_staff_id: str


class ScribeGenerateRequest(BaseModel):
    template_id: str
    data: ScribeTemplateData


class InvoiceItemCreate(BaseModel):
    description: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit_price: float = Field(ge=0)


class InvoiceCreate(BaseModel):
    client_id: UUID
    service_report_id: Optional[UUID] = None
    due_date: Optional[date] = None
    tax_amount: float = Field(default=0, ge=0)
    notes: Optional[str] = None
    items: list[InvoiceItemCreate] = Field(min_length=1)


class ExpenseCreate(BaseModel):
    service_report_id: Optional[UUID] = None
    category: ExpenseCategory
    amount: float = Field(ge=0)
    expense_date: date = Field(default_factory=date.today)
    description: str = Field(min_length=1)
    receipt_url: Optional[str] = None
