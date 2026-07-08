-- ============================================================
-- HVAC Field Management System - Supabase PostgreSQL Schema
-- ============================================================

create extension if not exists "pgcrypto";

-- ============================================================
-- ENUMS
-- ============================================================

do $$ begin
    create type public.user_role as enum ('admin_staff', 'technician');
exception when duplicate_object then null;
end $$;

do $$ begin
    create type public.customer_type as enum ('walk_in', 'amc');
exception when duplicate_object then null;
end $$;

do $$ begin
    create type public.service_report_status as enum (
        'scheduled',
        'in_progress',
        'completed',
        'cancelled'
    );
exception when duplicate_object then null;
end $$;

do $$ begin
    create type public.invoice_status as enum (
        'draft',
        'sent',
        'paid',
        'overdue',
        'cancelled'
    );
exception when duplicate_object then null;
end $$;

do $$ begin
    create type public.expense_category as enum (
        'fuel',
        'parts',
        'tools',
        'parking',
        'toll',
        'meals',
        'other'
    );
exception when duplicate_object then null;
end $$;

do $$ begin
    create type public.ac_brand as enum (
        'Daikin',
        'Carrier',
        'LG',
        'Samsung',
        'Voltas',
        'Blue Star',
        'Hitachi',
        'Panasonic',
        'Mitsubishi',
        'O General',
        'Other'
    );
exception when duplicate_object then null;
end $$;

do $$ begin
    create type public.refrigerant_type as enum (
        'R22',
        'R32',
        'R410A',
        'R134A',
        'R290',
        'R407C',
        'Other'
    );
exception when duplicate_object then null;
end $$;

do $$ begin
    create type public.ac_condition as enum (
        'excellent',
        'good',
        'fair',
        'poor',
        'needs_repair',
        'not_working'
    );
exception when duplicate_object then null;
end $$;

-- ============================================================
-- UPDATED_AT TRIGGER
-- ============================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ============================================================
-- PROFILES
-- ============================================================

create table if not exists public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    email text not null,
    full_name text not null,
    role public.user_role not null default 'technician',
    phone text,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (
        id,
        email,
        full_name,
        role,
        phone
    )
    values (
        new.id,
        new.email,
        coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email, '@', 1)),
        coalesce((new.raw_user_meta_data->>'role')::public.user_role, 'technician'),
        new.raw_user_meta_data->>'phone'
    )
    on conflict (id) do nothing;

    return new;
end;
$$;

drop trigger if exists trg_auth_user_created on auth.users;
create trigger trg_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_auth_user();

-- ============================================================
-- CLIENTS
-- ============================================================

create table if not exists public.clients (
    id uuid primary key default gen_random_uuid(),
    customer_type public.customer_type not null,
    name text not null,
    contact_person text,
    phone text not null,
    email text,
    address_line1 text not null,
    address_line2 text,
    city text not null,
    state text not null,
    postal_code text,
    flat_number text,
    notes text,
    created_by uuid references public.profiles(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_clients_customer_type on public.clients(customer_type);
create index if not exists idx_clients_created_by on public.clients(created_by);

drop trigger if exists trg_clients_updated_at on public.clients;
create trigger trg_clients_updated_at
before update on public.clients
for each row execute function public.set_updated_at();

-- ============================================================
-- AMC DETAILS
-- ============================================================

create table if not exists public.amc_details (
    id uuid primary key default gen_random_uuid(),
    client_id uuid not null unique references public.clients(id) on delete cascade,
    contract_start_date date not null,
    contract_end_date date not null,
    contract_value numeric(12,2) not null check (contract_value >= 0),
    emi_count integer not null check (emi_count >= 1),
    ppm_count integer not null check (ppm_count >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint chk_amc_dates check (contract_end_date >= contract_start_date)
);

drop trigger if exists trg_amc_details_updated_at on public.amc_details;
create trigger trg_amc_details_updated_at
before update on public.amc_details
for each row execute function public.set_updated_at();

create table if not exists public.amc_emi_schedule (
    id uuid primary key default gen_random_uuid(),
    amc_id uuid not null references public.amc_details(id) on delete cascade,
    installment_number integer not null check (installment_number >= 1),
    amount numeric(12,2) not null check (amount >= 0),
    due_date date not null,
    is_paid boolean not null default false,
    paid_at timestamptz,
    created_at timestamptz not null default now(),
    unique (amc_id, installment_number)
);

create table if not exists public.amc_ppm_schedule (
    id uuid primary key default gen_random_uuid(),
    amc_id uuid not null references public.amc_details(id) on delete cascade,
    visit_number integer not null check (visit_number >= 1),
    scheduled_date date not null,
    completed_at timestamptz,
    service_report_id uuid,
    created_at timestamptz not null default now(),
    unique (amc_id, visit_number)
);

create or replace function public.regenerate_amc_schedules()
returns trigger
language plpgsql
as $$
declare
    total_days integer;
    i integer;
    calculated_date date;
    base_amount numeric(12,2);
    final_amount numeric(12,2);
begin
    delete from public.amc_emi_schedule where amc_id = new.id;
    delete from public.amc_ppm_schedule where amc_id = new.id;

    total_days := new.contract_end_date - new.contract_start_date;

    if new.emi_count > 0 then
        base_amount := round(new.contract_value / new.emi_count, 2);

        for i in 1..new.emi_count loop
            if new.emi_count = 1 then
                calculated_date := new.contract_start_date;
            else
                calculated_date := new.contract_start_date + round((total_days::numeric * (i - 1)) / (new.emi_count - 1))::integer;
            end if;

            if i = new.emi_count then
                final_amount := new.contract_value - (base_amount * (new.emi_count - 1));
            else
                final_amount := base_amount;
            end if;

            insert into public.amc_emi_schedule (
                amc_id,
                installment_number,
                amount,
                due_date
            )
            values (
                new.id,
                i,
                final_amount,
                calculated_date
            );
        end loop;
    end if;

    if new.ppm_count > 0 then
        for i in 1..new.ppm_count loop
            if new.ppm_count = 1 then
                calculated_date := new.contract_start_date;
            else
                calculated_date := new.contract_start_date + round((total_days::numeric * (i - 1)) / (new.ppm_count - 1))::integer;
            end if;

            insert into public.amc_ppm_schedule (
                amc_id,
                visit_number,
                scheduled_date
            )
            values (
                new.id,
                i,
                calculated_date
            );
        end loop;
    end if;

    return new;
end;
$$;

drop trigger if exists trg_regenerate_amc_schedules on public.amc_details;
create trigger trg_regenerate_amc_schedules
after insert or update of contract_start_date, contract_end_date, contract_value, emi_count, ppm_count
on public.amc_details
for each row execute function public.regenerate_amc_schedules();

-- ============================================================
-- AC UNITS / ASSET INVENTORY
-- ============================================================

create table if not exists public.ac_units (
    id uuid primary key default gen_random_uuid(),
    client_id uuid not null references public.clients(id) on delete cascade,
    unit_number text not null,
    barcode_value text not null unique,
    brand public.ac_brand not null,
    refrigerant public.refrigerant_type not null,
    pressure numeric(8,2),
    ampere numeric(8,2),
    condition public.ac_condition not null default 'good',
    location_description text,
    last_serviced_at timestamptz,
    created_by uuid references public.profiles(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (client_id, unit_number)
);

create index if not exists idx_ac_units_client_id on public.ac_units(client_id);
create index if not exists idx_ac_units_barcode_value on public.ac_units(barcode_value);

drop trigger if exists trg_ac_units_updated_at on public.ac_units;
create trigger trg_ac_units_updated_at
before update on public.ac_units
for each row execute function public.set_updated_at();

-- ============================================================
-- SERVICE REPORTS
-- ============================================================

create sequence if not exists public.service_report_seq start 1 increment 1;

create table if not exists public.service_reports (
    id uuid primary key default gen_random_uuid(),
    report_number text unique,
    client_id uuid not null references public.clients(id) on delete cascade,
    ac_unit_id uuid references public.ac_units(id) on delete set null,
    assigned_technician_id uuid not null references public.profiles(id) on delete restrict,
    scheduled_at timestamptz not null,
    nature_of_complaint text not null,
    work_performed text,
    technician_observations text,
    status public.service_report_status not null default 'scheduled',
    scribe_payload jsonb,
    scribe_document_url text,
    completed_at timestamptz,
    created_by uuid references public.profiles(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_service_reports_client_id on public.service_reports(client_id);
create index if not exists idx_service_reports_assigned_technician_id on public.service_reports(assigned_technician_id);
create index if not exists idx_service_reports_scheduled_at on public.service_reports(scheduled_at);

create or replace function public.set_service_report_number()
returns trigger
language plpgsql
as $$
begin
    if new.report_number is null or length(trim(new.report_number)) = 0 then
        new.report_number :=
            'SR-' ||
            to_char(now(), 'YYYYMMDD') ||
            '-' ||
            lpad(nextval('public.service_report_seq')::text, 6, '0');
    end if;

    return new;
end;
$$;

drop trigger if exists trg_set_service_report_number on public.service_reports;
create trigger trg_set_service_report_number
before insert on public.service_reports
for each row execute function public.set_service_report_number();

drop trigger if exists trg_service_reports_updated_at on public.service_reports;
create trigger trg_service_reports_updated_at
before update on public.service_reports
for each row execute function public.set_updated_at();

alter table public.amc_ppm_schedule
drop constraint if exists fk_amc_ppm_service_report;

alter table public.amc_ppm_schedule
add constraint fk_amc_ppm_service_report
foreign key (service_report_id)
references public.service_reports(id)
on delete set null;

-- ============================================================
-- INVOICING
-- ============================================================

create sequence if not exists public.invoice_seq start 1 increment 1;

create table if not exists public.invoices (
    id uuid primary key default gen_random_uuid(),
    invoice_number text unique,
    client_id uuid not null references public.clients(id) on delete cascade,
    service_report_id uuid references public.service_reports(id) on delete set null,
    issue_date date not null default current_date,
    due_date date,
    subtotal numeric(12,2) not null default 0 check (subtotal >= 0),
    tax_amount numeric(12,2) not null default 0 check (tax_amount >= 0),
    total_amount numeric(12,2) not null default 0 check (total_amount >= 0),
    status public.invoice_status not null default 'draft',
    notes text,
    created_by uuid references public.profiles(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.invoice_items (
    id uuid primary key default gen_random_uuid(),
    invoice_id uuid not null references public.invoices(id) on delete cascade,
    description text not null,
    quantity numeric(10,2) not null default 1 check (quantity > 0),
    unit_price numeric(12,2) not null check (unit_price >= 0),
    line_total numeric(12,2) generated always as (quantity * unit_price) stored
);

create or replace function public.set_invoice_number()
returns trigger
language plpgsql
as $$
begin
    if new.invoice_number is null or length(trim(new.invoice_number)) = 0 then
        new.invoice_number :=
            'INV-' ||
            to_char(now(), 'YYYYMMDD') ||
            '-' ||
            lpad(nextval('public.invoice_seq')::text, 6, '0');
    end if;

    return new;
end;
$$;

drop trigger if exists trg_set_invoice_number on public.invoices;
create trigger trg_set_invoice_number
before insert on public.invoices
for each row execute function public.set_invoice_number();

drop trigger if exists trg_invoices_updated_at on public.invoices;
create trigger trg_invoices_updated_at
before update on public.invoices
for each row execute function public.set_updated_at();

-- ============================================================
-- EXPENSES
-- ============================================================

create table if not exists public.expenses (
    id uuid primary key default gen_random_uuid(),
    technician_id uuid not null references public.profiles(id) on delete restrict,
    service_report_id uuid references public.service_reports(id) on delete set null,
    category public.expense_category not null,
    amount numeric(12,2) not null check (amount >= 0),
    expense_date date not null default current_date,
    description text not null,
    receipt_url text,
    approved_by uuid references public.profiles(id) on delete set null,
    approved_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_expenses_technician_id on public.expenses(technician_id);
create index if not exists idx_expenses_service_report_id on public.expenses(service_report_id);

drop trigger if exists trg_expenses_updated_at on public.expenses;
create trigger trg_expenses_updated_at
before update on public.expenses
for each row execute function public.set_updated_at();

-- ============================================================
-- RLS HELPERS
-- ============================================================

create or replace function public.current_user_role()
returns public.user_role
language sql
stable
security definer
set search_path = public
as $$
    select role from public.profiles where id = auth.uid()
$$;

create or replace function public.is_admin_staff()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select public.current_user_role() = 'admin_staff'
$$;

-- ============================================================
-- ENABLE RLS
-- ============================================================

alter table public.profiles enable row level security;
alter table public.clients enable row level security;
alter table public.amc_details enable row level security;
alter table public.amc_emi_schedule enable row level security;
alter table public.amc_ppm_schedule enable row level security;
alter table public.ac_units enable row level security;
alter table public.service_reports enable row level security;
alter table public.invoices enable row level security;
alter table public.invoice_items enable row level security;
alter table public.expenses enable row level security;

-- ============================================================
-- RLS POLICIES
-- ============================================================

drop policy if exists profiles_select on public.profiles;
create policy profiles_select
on public.profiles
for select
to authenticated
using (
    id = auth.uid()
    or public.is_admin_staff()
);

drop policy if exists profiles_update_self on public.profiles;
create policy profiles_update_self
on public.profiles
for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

drop policy if exists profiles_admin_all on public.profiles;
create policy profiles_admin_all
on public.profiles
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists clients_admin_all on public.clients;
create policy clients_admin_all
on public.clients
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists clients_technician_read_assigned on public.clients;
create policy clients_technician_read_assigned
on public.clients
for select
to authenticated
using (
    exists (
        select 1
        from public.service_reports sr
        where sr.client_id = clients.id
          and sr.assigned_technician_id = auth.uid()
    )
);

drop policy if exists amc_admin_all on public.amc_details;
create policy amc_admin_all
on public.amc_details
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists amc_technician_read on public.amc_details;
create policy amc_technician_read
on public.amc_details
for select
to authenticated
using (
    exists (
        select 1
        from public.service_reports sr
        where sr.client_id = amc_details.client_id
          and sr.assigned_technician_id = auth.uid()
    )
);

drop policy if exists emi_admin_all on public.amc_emi_schedule;
create policy emi_admin_all
on public.amc_emi_schedule
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists ppm_admin_all on public.amc_ppm_schedule;
create policy ppm_admin_all
on public.amc_ppm_schedule
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists ppm_technician_read on public.amc_ppm_schedule;
create policy ppm_technician_read
on public.amc_ppm_schedule
for select
to authenticated
using (
    exists (
        select 1
        from public.amc_details ad
        join public.service_reports sr on sr.client_id = ad.client_id
        where ad.id = amc_ppm_schedule.amc_id
          and sr.assigned_technician_id = auth.uid()
    )
);

drop policy if exists ac_units_admin_all on public.ac_units;
create policy ac_units_admin_all
on public.ac_units
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists ac_units_technician_read_update_assigned on public.ac_units;
create policy ac_units_technician_read_update_assigned
on public.ac_units
for select
to authenticated
using (
    exists (
        select 1
        from public.service_reports sr
        where sr.client_id = ac_units.client_id
          and sr.assigned_technician_id = auth.uid()
    )
);

drop policy if exists ac_units_technician_update_assigned on public.ac_units;
create policy ac_units_technician_update_assigned
on public.ac_units
for update
to authenticated
using (
    exists (
        select 1
        from public.service_reports sr
        where sr.client_id = ac_units.client_id
          and sr.assigned_technician_id = auth.uid()
    )
)
with check (
    exists (
        select 1
        from public.service_reports sr
        where sr.client_id = ac_units.client_id
          and sr.assigned_technician_id = auth.uid()
    )
);

drop policy if exists service_reports_admin_all on public.service_reports;
create policy service_reports_admin_all
on public.service_reports
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists service_reports_technician_read_update_assigned on public.service_reports;
create policy service_reports_technician_read_update_assigned
on public.service_reports
for select
to authenticated
using (assigned_technician_id = auth.uid());

drop policy if exists service_reports_technician_update_assigned on public.service_reports;
create policy service_reports_technician_update_assigned
on public.service_reports
for update
to authenticated
using (assigned_technician_id = auth.uid())
with check (assigned_technician_id = auth.uid());

drop policy if exists invoices_admin_all on public.invoices;
create policy invoices_admin_all
on public.invoices
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists invoice_items_admin_all on public.invoice_items;
create policy invoice_items_admin_all
on public.invoice_items
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists expenses_admin_all on public.expenses;
create policy expenses_admin_all
on public.expenses
for all
to authenticated
using (public.is_admin_staff())
with check (public.is_admin_staff());

drop policy if exists expenses_technician_own_insert on public.expenses;
create policy expenses_technician_own_insert
on public.expenses
for insert
to authenticated
with check (technician_id = auth.uid());

drop policy if exists expenses_technician_own_select on public.expenses;
create policy expenses_technician_own_select
on public.expenses
for select
to authenticated
using (technician_id = auth.uid());

drop policy if exists expenses_technician_own_update_unapproved on public.expenses;
create policy expenses_technician_own_update_unapproved
on public.expenses
for update
to authenticated
using (
    technician_id = auth.uid()
    and approved_at is null
)
with check (
    technician_id = auth.uid()
    and approved_at is null
);
