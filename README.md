# EstateHub — Django v2

A more complete Django implementation inspired by the EstateHub PDF design system and screen set. The source describes a unified platform for landlords, tenants, maintenance workers, management and administrators, including property discovery, maintenance, payments, leases, analytics, support and API tooling.

## Included
- Responsive public marketing site
- Registration, authentication, roles and onboarding
- Tenant, landlord, worker, management and admin dashboards
- Property marketplace, search and property detail pages
- Landlord property submission workflow
- Maintenance request creation and tracking
- Worker job marketplace and job details
- Payments / transaction history
- Lease/document vault UI
- Analytics dashboards
- Profile and settings
- Django admin for all database entities
- SQLite demo database
- Seed command for sample EstateHub data

## Setup
```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py seed_estatehub
./.venv/bin/python manage.py createsuperuser
./.venv/bin/python manage.py runserver
```
Open http://127.0.0.1:8000/

Run Django commands through `.venv/bin/python` (or activate the environment first with `source .venv/bin/activate`). Running `./manage.py` without activating `.venv` uses whichever `python` is on your system `PATH`, which may not have the project's dependencies installed.

## Demo roles
Register as a tenant, landlord, or worker. Landlord accounts start pending until an admin approves them. Create an admin with `python manage.py createsuperuser`; superusers can use the platform oversight queue at `/oversight/` and Django Admin at `/admin/`.

## Core workflows
- Landlords submit property listings. Admins approve, reject, or remove submissions; only approved available listings appear in the tenant marketplace.
- Tenants apply to rent available listings. A landlord reviews the application, and approval creates an active lease and marks the property rented.
- Tenants can request a lease renewal with a proposed end date and rent, or submit a move-out notice. Landlords review and respond from the lease page; an approved move-out releases the unit after its agreed date is reached and finalized.
- Tenants with an active lease can submit complaints against that property. The landlord can turn a complaint into a worker job. Workers can accept, start, and complete jobs, updating the complaint status.
- Tenants can request M-Pesa rent payment for an active lease or a host-confirmed short stay, with duplicate protection and callback-confirmed receipts.
- Tenants see upcoming, due-today, overdue and paid rent months on their dashboard and payment page. Landlords set the monthly due day on each lease; it defaults to day 1 and clamps to the final day of shorter months.
- Landlords can record external worker transfers or submit automated Daraja B2C payouts after a job is complete.
- Admins can review landlord accounts and property submissions in `/oversight/`, and inspect or delete platform records in Django Admin.

## Role reports
Every signed-in role has a date-filtered report at `/reports/` with a CSV download. Landlords get a portfolio summary and a row for each property, including unit occupancy, active leases, contracted rent, confirmed stays, recorded receipts, and the current repair backlog. Tenants can review leases, due rent, payment receipts, applications, stays, and maintenance. Workers see assigned and completed jobs plus payout history. Caretakers see building condition, reports, and worker jobs for their assigned properties. Admins see platform totals and activity across accounts, listings, payments, maintenance, and jobs. Report access follows the user’s role and property assignments; financial totals represent completed ledger entries, not forecasts.

