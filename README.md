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

## Important
This is a self-contained Django prototype. Payment collection and worker disbursement have Daraja integration points, but remain disabled until credentials, a live shortcode, a public HTTPS callback endpoint, and Safaricom product approval are configured. Production email, real-time chat, external property feeds, image storage, maps, identity checks and cloud infrastructure are not connected. The PDF's technical-stack page references React/Tailwind/TypeScript, Node.js/GraphQL/PostgreSQL and AWS; this project implements the documented experience using Django so it can be run locally.

## M-Pesa / Daraja setup

1. Copy `.env.example` to `.env` in the project root. This file is ignored by Git. Put Daraja credentials only in `.env` or your deployment's secret manager; never commit them or paste them into chat.
2. Create a Safaricom Daraja app and start with `DARAJA_ENV=sandbox`. Add its consumer key and consumer secret. Configure the Lipa na M-Pesa Online shortcode and passkey for rent collection.
3. Install `cloudflared`, then run `./run_mpesa_dev.sh`. It opens a Cloudflare Quick Tunnel to Django on port 8000, updates `PUBLIC_BASE_URL` in `.env` (and creates `DARAJA_CALLBACK_TOKEN` if missing), and starts Django. Safaricom cannot call `localhost`; the temporary HTTPS tunnel makes the callback endpoint reachable.
4. Keep the script running while testing. Quick Tunnel URLs are temporary and change when recreated; the next run updates `.env` again. The app includes the callback URL in each Daraja request. Tenants can choose a lease and rent month and request an STK prompt. A payment only becomes completed when its callback matches the stored checkout request, amount, and phone. The same lease/month cannot be charged twice after success.
5. Workers add their M-Pesa number under Profile. After a job is completed, a landlord can submit a B2C payout if the app's B2C variables are configured and `DARAJA_B2C_ENABLED=true`. The ledger marks it paid only on a successful B2C result callback. A manual external transfer can still be recorded by the landlord.

Requests with an uncertain network outcome are locked as **Initiation status unknown** or **Payout status unknown** to prevent accidental duplicate charges or transfers. Reconcile those against Safaricom's transaction records before an administrator clears or resolves the ledger entry.

### Configuration keys

`.env.example` lists every setting. The main collection settings are `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_SHORTCODE`, and `DARAJA_PASSKEY`. Worker payouts additionally need `DARAJA_B2C_SHORTCODE`, `DARAJA_B2C_INITIATOR_NAME`, and the encrypted `DARAJA_B2C_SECURITY_CREDENTIAL`. Keep B2C disabled until Safaricom has enabled B2C for the shortcode and the business has a funded disbursement account. B2C sends money from that configured business account; it does not debit an individual landlord's personal wallet. Rent STK collections also settle to the configured business shortcode, so settlement and landlord accounting must be arranged by the business before production.

Cloudflare Quick Tunnels are for development and testing. Install `cloudflared` using [Cloudflare's install guide](https://developers.cloudflare.com/tunnel/install/). The manual equivalent is `cloudflared tunnel --url http://localhost:8000`; the generated public URL only works while that process is running. See [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) for details.

Safaricom's Daraja documentation describes B2C as asynchronous: the initial response acknowledges the request, while the result callback carries the final outcome. It also requires a B2C shortcode or an eligible one account for live disbursements. See [Safaricom's B2C API documentation](https://developer.safaricom.co.ke/apis/BusinessToCustomer) and [Daraja developer portal](https://developer.safaricom.co.ke/).
