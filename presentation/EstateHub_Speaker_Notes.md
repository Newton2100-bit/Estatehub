# EstateHub — project progress presentation

**Presentation date:** Monday, 23 November 2026  
**Target duration:** 8–10 minutes, plus questions  
**Format:** 9 content slides, cover, and references  
**Presenter:** [Your name] · **Registration number:** [Your number]  
**Institution / department:** [Fill in] · **Supervisor:** [Fill in]

> Replace every square-bracket placeholder before presenting. This is a progress review: describe features as implemented in the prototype, and distinguish those from verified outcomes. The supplied EstateHub PDF is a product-design reference, not evidence that the proposed product has been validated with users.

## Timed talk track

### Slide 1 — EstateHub (0:20)

“Good morning/afternoon. I’m [name], and this is my fourth-year project progress presentation for EstateHub, a role-based property management platform. I’ll show the problem the project addresses, how the system is designed, what is implemented so far, and the work still needed before the prototype can be evaluated and prepared for deployment.”

### Slide 2 — One platform, several connected workflows (0:45)

“EstateHub brings together the steps that relate to a property: discovery, applications, leases, payments, maintenance, and worker coordination. The codebase now contains role-specific dashboards, month-to-month leases that renew automatically, and the main records needed to connect those tasks. A new lease records a one-time security deposit equal to the initial monthly rent. This is a local prototype, not a live property service. In particular, M-Pesa integration points exist, but real collection and payouts are disabled. The rest of the presentation explains that boundary and the next evidence I need to collect.”

### Slide 3 — The project targets a coordination gap (0:55)

“The project brief describes an ecosystem for landlords, tenants, workers, property managers, and administrators. I interpret the problem as coordinating work that crosses these roles: for example, a tenant reports a repair, a property team reviews it, and a worker carries it out. If status is held in separate records or communicated informally, it can be difficult to know what has happened. This is the project’s design problem statement, not a measured conclusion from interviews. User research and task evaluation are still required.”

### Slide 4 — From project aim to demonstrable objectives (0:55)

“The aim is to make those connected workflows visible in one role-aware application. The objectives are to represent the property and rental data, support the distinct user journeys, create monthly rent obligations, record the first-lease deposit, connect key state changes, and provide operational visibility through dashboards and reports. I will evaluate the work against concrete targets: whether users can complete each task, whether the records move to the correct status, whether access stays within the user’s role, and whether report totals agree with the underlying ledger.”

### Slide 5 — Five roles share one operational record (0:55)

“The current application has five roles: tenant, landlord, caretaker, worker, and admin. Caretaker is stored as the management role in the code. A tenant searches, applies, pays, and reports issues. A landlord manages properties, reviews applications, and coordinates leases and repairs. A caretaker handles assigned buildings. A worker requests and completes jobs. An administrator reviews landlord accounts and listings. The key design idea is that these roles operate on shared property records, while the screens and permitted actions vary by role.”

### Slide 6 — A server-rendered Django prototype (0:55)

“The implementation uses Django views, forms, models, and templates, with responsive CSS and a small amount of JavaScript. The ORM stores the records in SQLite for local development. The provided design reference names a React, Node, GraphQL, PostgreSQL, and AWS stack; this repository instead implements a Django application. That makes the current prototype straightforward to run as one integrated application, but the difference needs supervisor approval and a deployment database decision. Daraja calls are configuration-gated and should not be presented as live payment processing.”

### Slide 7 — The data model connects people, homes, and operations (0:50)

“The model follows the domain: a property contains floors and rental units; leases and bookings connect homes to tenants; active leases renew monthly until an approved move-out, the first lease records a deposit equal to one month’s rent, and payment records track each rent month, stay payments, deposits, or worker payouts; maintenance reports can lead to worker jobs. That structure supports an example end-to-end path from rental application to lease and rent schedule, and another from a maintenance report to a worker job. The Django migrations show how the schema has evolved. Short stays use bookings rather than the long-term lease flow.”

### Slide 8 — Core product areas are present in code (0:55)

“This slide separates implementation from validation. The repository includes registration, role-specific dashboards, property and unit management, applications, auto-renewing month-to-month leases, one-time first-lease deposits, overdue rent schedules and payment histories, maintenance, worker jobs, oversight, and role-specific reports with date filters and CSV export. The payment integration remains a prototype boundary. Django’s system check passes, but the repository does not currently contain an automated test suite. Therefore I’m reporting these as implemented code paths, not as proven reliability or user acceptance.”

### Slide 9 — Demonstrate one complete journey (1:00)

“For the demo, I’ll follow one journey instead of clicking through every feature. I’ll begin as a tenant and show an available listing and application, then switch to a landlord to review it and inspect the resulting one-month lease, one-time deposit obligation, and monthly rent schedule. I’ll show a maintenance report, its worker job, and the report view that summarizes activity. If sample records are used, I’ll identify them as sample data. I will not claim a live M-Pesa payment; the payment path is disabled until its credentials, account approval, and public callback setup are available.”

### Slide 10 — Next: validate, harden, and prepare deployment (1:10)

“The remaining work has four priorities. First, add automated tests around permissions, monthly renewals, deposit collection exactly once, overdue rent periods, maintenance, and job state changes. Second, run task-based walkthroughs with representative users and record completion, errors, and feedback. Third, harden configuration and infrastructure: development settings, secret handling, HTTPS, backups, and a production database need decisions. Finally, keep Daraja disabled until the business and Safaricom prerequisites are satisfied. I also need to confirm that the Django implementation is acceptable against the stack in the design reference.”

### Slide 11 — Conclusion and feedback (0:35)

“To conclude, the project has a working code foundation that connects the main property lifecycle across five roles. The next proof point is validation: showing that those workflows are correct, access is appropriate, and the reports are useful to the intended users. I’d especially welcome feedback on the stack choice, which user journey should be evaluated first, and what evidence the final assessment expects. Thank you.”

## Demo preparation checklist

- Fill in your name, registration number, institution, department, and supervisor on slide 1 and this note sheet.
- Start from a clean browser session and confirm the local server works before presenting.
- Prepare separate tenant, approved-landlord, caretaker, worker, and admin accounts. Check the correct landing page and permissions for each account.
- Use clearly identified sample records. `seed_estatehub` creates illustrative Seattle listings; the Githurai seed command requires a landlord account named `mwaura` and labels its images as demo stock photos.
- Walk through the tenant application → landlord review → lease path, then a maintenance report → worker job path, then `/reports/` and CSV export.
- The new migration creates a deposit marked “Due” for each existing active lease because older records did not track deposits. Reconcile any deposits already collected outside the system before treating these balances as outstanding.
- Do not enter real payment credentials or claim real M-Pesa settlement. The README says collection and payouts remain disabled pending credentials, a live shortcode, a public HTTPS callback, and Safaricom product approval.
- Keep a static screenshot or the slide deck ready in case the local demo environment fails.

## Likely questions and concise answers

**Why did you use Django when the reference lists a different stack?**  
“The reference is the design target; the current build is a Django implementation. Django let me implement the role and workflow logic in one integrated prototype. I’m making the difference explicit and will confirm alignment with my supervisor before finalizing the deployment direction.”

**What has been tested?**  
“The Django system check passes. Automated workflow tests for monthly renewal, deposit collection, and rent overdue status, plus user acceptance evaluation, are not yet complete. I’m not claiming validated outcomes; those are immediate next steps.”

**How are monthly renewals and deposits handled?**  
“An active lease automatically rolls forward by one month unless a move-out date is approved. The first lease creates one deposit obligation equal to the initial rent; it is not charged again on monthly renewals. Each rent month has its own paid, upcoming, due, or overdue status.”

**Can tenants pay rent through M-Pesa now?**  
“The code has Daraja integration points for STK collection and B2C payouts, but they are disabled until the required business credentials, shortcode, HTTPS callbacks, and product approval are configured.”

**How will you know whether the system succeeds?**  
“I’ll test task completion for representative roles, verify that permissions prevent cross-role access, check the status transitions, reconcile report totals with the payment ledger, and collect user feedback.”

**Why SQLite?**  
“It is the current local development database. It keeps the prototype easy to set up, but a production deployment needs a deliberate database, backup, and hosting decision.”

## References (numbered engineering style)

[1] EstateHub, *Product-design reference supplied with the project*, 83-page PDF, undated. Used as a design brief; verify author/date if your course requires full bibliographic metadata.

[2] EstateHub project repository, `README.md`, models, views, migrations, and settings, accessed 18 November 2026.

[3] University of Washington, Bioengineering Team Design, “Progress Presentation Guidelines,” BIOEN 404, Winter 2014. https://courses.washington.edu/bioeteam/404_ProgressPresentGuidelines.pdf

[4] University of Florida, Department of Electrical and Computer Engineering, “Guidelines for Oral Presentations,” EEL 4665/5666, 25 August 2015. https://mil.ufl.edu/5666/handouts/Fall15/oral_reports.pdf

[5] University of Missouri College of Engineering, “Presentations and Posters,” accessed 18 November 2026. https://engineering.missouri.edu/tech-toolbox/presentations-posters/

[6] Safaricom, “Daraja API Developer Portal.” https://developer.safaricom.co.ke/

## Presentation structure rationale

The deck uses a title, concise project summary, problem and objectives, design/method, implementation progress, evidence/demo plan, limitations, next steps, conclusion, and references. This follows university progress-presentation guidance emphasizing background, status, accomplishments, timeline or next steps, and open challenges [3], with a clear introduction/body/conclusion/future-work arc [4].
