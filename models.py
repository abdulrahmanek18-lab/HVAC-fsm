from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ============================================================
# Enums
# ============================================================

class StaffPosition(str, Enum):
    HVAC = "HVAC"
    Electrician = "Electrician"
    Plumber = "Plumber"
    Painter = "Painter"
    Accountant = "Accountant"
    Admin = "Admin"


class AppRole(str, Enum):
    Admin = "Admin"
    Accountant = "Accountant"
    Technician = "Technician"


class ScheduleType(str, Enum):
    EMI = "EMI"
    PPM = "PPM"


class ScheduleStatus(str, Enum):
    Pending = "Pending"
    Completed = "Completed"


class UnitType(str, Enum):
    Split = "Split"
    FCU = "FCU"
    FAHU = "FAHU"
    Package = "Package"


class WorkCategory(str, Enum):
    HVAC = "HVAC"
    Electrical = "Electrical"
    Plumbing = "Plumbing"
    Painting = "Painting"


# ============================================================
# Auth / Session
# ============================================================

class AuthContext(BaseModel):
    user_id: str
    email: Optional[EmailStr] = None
    staff_id: str
    name: str
    position: StaffPosition
    role: AppRole


class LoginRequest(BaseModel):
    jwt: str = Field(min_length=10)


class LoginResponse(BaseModel):
    ok: bool
    user: AuthContext


# ============================================================
# Settings
# ============================================================

class SettingsBase(BaseModel):
    company_name: str = Field(default="MAK INFRATECH", min_length=1)
    trn_number: Optional[str] = None
    vat_percentage: float = Field(default=5.0, ge=0, le=100)


class SettingsCreate(SettingsBase):
    pass


class SettingsUpdate(BaseModel):
    company_name: Optional[str] = Field(default=None, min_length=1)
    trn_number: Optional[str] = None
    vat_percentage: Optional[float] = Field(default=None, ge=0, le=100)


class SettingsOut(SettingsBase):
    id: str = Field(alias="$id")


# ============================================================
# Staff / HR Compliance
# ============================================================

class StaffDocuments(BaseModel):
    passport_no: Optional[str] = None
    passport_expiry: Optional[date] = None
    eid_no: Optional[str] = None
    eid_expiry: Optional[date] = None
    insurance_policy: Optional[str] = None
    insurance_expiry: Optional[date] = None


class StaffCreate(BaseModel):
    user_id: Optional[str] = None
    email: Optional[EmailStr] = None
    name: str = Field(min_length=1)
    position: StaffPosition
    base_salary: float = Field(default=0, ge=0)
    documents: StaffDocuments = Field(default_factory=StaffDocuments)
    storage_file_ids: list[str] = Field(default_factory=list)


class StaffUpdate(BaseModel):
    user_id: Optional[str] = None
    email: Optional[EmailStr] = None
    name: Optional[str] = Field(default=None, min_length=1)
    position: Optional[StaffPosition] = None
    base_salary: Optional[float] = Field(default=None, ge=0)
    documents: Optional[StaffDocuments] = None
    storage_file_ids: Optional[list[str]] = None


class StaffOut(StaffCreate):
    id: str = Field(alias="$id")


class ComplianceAlert(BaseModel):
    staff_id: str
    staff_name: str
    position: StaffPosition
    document_type: str
    expiry_date: date
    days_remaining: int


# ============================================================
# Customers & AMC Contracts
# ============================================================

class ContractCreate(BaseModel):
    customer_name: str = Field(min_length=1)
    building_villa_name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    contract_value: float = Field(ge=0)
    start_date: date
    end_date: date
    total_ppms_per_year: int = Field(ge=0)
    total_emis_per_year: int = Field(ge=1)

    @field_validator("end_date")
    @classmethod
    def validate_dates(cls, end_date: date, info: Any) -> date:
        start_date = info.data.get("start_date")
        if start_date and end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return end_date


class ContractUpdate(BaseModel):
    customer_name: Optional[str] = Field(default=None, min_length=1)
    building_villa_name: Optional[str] = Field(default=None, min_length=1)
    address: Optional[str] = Field(default=None, min_length=1)
    contract_value: Optional[float] = Field(default=None, ge=0)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    total_ppms_per_year: Optional[int] = Field(default=None, ge=0)
    total_emis_per_year: Optional[int] = Field(default=None, ge=1)


class ContractOut(ContractCreate):
    id: str = Field(alias="$id")


# ============================================================
# Automated Schedules
# ============================================================

class AutomatedScheduleCreate(BaseModel):
    contract_id: str
    type: ScheduleType
    sequence_number: int = Field(ge=1)
    due_date: date
    amount: Optional[float] = Field(default=None, ge=0)
    status: ScheduleStatus = ScheduleStatus.Pending


class AutomatedScheduleUpdate(BaseModel):
    due_date: Optional[date] = None
    amount: Optional[float] = Field(default=None, ge=0)
    status: Optional[ScheduleStatus] = None


class AutomatedScheduleOut(AutomatedScheduleCreate):
    id: str = Field(alias="$id")


# ============================================================
# Locations & Assets
# ============================================================

class AssetCreate(BaseModel):
    contract_id: str
    flat_villa_no: str = Field(min_length=1)
    unit_type: UnitType
    brand: str = Field(min_length=1)
    tonnage: Optional[float] = Field(default=None, ge=0)
    serial_no: Optional[str] = None


class AssetUpdate(BaseModel):
    flat_villa_no: Optional[str] = Field(default=None, min_length=1)
    unit_type: Optional[UnitType] = None
    brand: Optional[str] = Field(default=None, min_length=1)
    tonnage: Optional[float] = Field(default=None, ge=0)
    serial_no: Optional[str] = None


class AssetOut(AssetCreate):
    id: str = Field(alias="$id")


# ============================================================
# Maintenance Logs
# ============================================================

class MaintenanceParameters(BaseModel):
    suction_pressure: Optional[float] = None
    discharge_pressure: Optional[float] = None
    ampere_reading: Optional[float] = None
    materials_used: Optional[str] = None
    electrical_checklist: list[str] = Field(default_factory=list)
    plumbing_checklist: list[str] = Field(default_factory=list)
    painting_checklist: list[str] = Field(default_factory=list)
    hvac_checklist: list[str] = Field(default_factory=list)


class MaintenanceLogCreate(BaseModel):
    asset_id: str
    technician_id: Optional[str] = None
    work_category: WorkCategory
    job_description: str = Field(min_length=1)
    parameters: MaintenanceParameters = Field(default_factory=MaintenanceParameters)
    signature_url: Optional[str] = None
    image_file_ids: list[str] = Field(default_factory=list)


class MaintenanceLogUpdate(BaseModel):
    work_category: Optional[WorkCategory] = None
    job_description: Optional[str] = Field(default=None, min_length=1)
    parameters: Optional[MaintenanceParameters] = None
    signature_url: Optional[str] = None
    image_file_ids: Optional[list[str]] = None


class MaintenanceLogOut(MaintenanceLogCreate):
    id: str = Field(alias="$id")
    created_at: datetime


# ============================================================
# Dashboard
# ============================================================

class DashboardStats(BaseModel):
    active_contracts: int
    pending_emis: int
    pending_ppms: int
    assets_tracked: int
    staff_count: int
    compliance_alerts: int
    contract_value_total: Optional[float] = None
    pending_emi_amount: Optional[float] = None
