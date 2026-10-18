# Member flag requests

Review branch: `codex/flag-requests-review`, based on bot release 50e1c0b.
This patch has not been deployed and has not changed production data.

Registered members can use `/flag country:Japan` (or US, GB, JP, etc.) in the
existing name-request channel. Country autocomplete shows English names and
two-letter codes; exact recognized names are accepted too. An optional reason
provides up to 400 characters of context for staff. No MKCentral account lookup,
locale/IP inference, production credentials or old database import is needed.
The existing registry is the verification authority; unregistered and restricted
members cannot submit flag requests. A member can only request their own flag.

Submitting a request does not change the flag. There is one pending flag request
per member, independent of verification/name requests. Already-selected countries
and invalid inputs are rejected. Members with existing flags can request a
correction through the same staff review. The name-change cooldown is unaffected;
no new flag cooldown policy is introduced. To replace an open flag request,
ask staff to reject it first, then resubmit.

Staff opens `/pending`, chooses the flag request (prefixed F, e.g. #F1), and sees
the current flag, proposed country and member context. Approve flag / Reject flag
buttons use the existing reviewer-bound permission checks. No self-review is
allowed, including for staff members. Flag IDs remain distinct from existing
numeric verification/name IDs. The numeric `/review` command continues to handle
only verification/name requests; use the `/pending` picker for flags.

Approval atomically updates only country metadata, its before/after audit and the
request's review status/actor/time. The audit reason references the approved
request and member context. Rejection records the reviewer/status and preserves
the current country. Competing approvals commit at most once. A failure writing
the audit rolls back the country and request status together.

The request captures its current country and country-audit revision. A subsequent
staff correction or cleared flag makes it stale, even when the effective country
is unchanged. Stale approvals are disabled/refused; staff rejects the stale
request and asks the member to resubmit. Unrelated members' changes do not
invalidate it. Member context, reviewers and request records remain private;
the existing public snapshot exposes only the approved country code.

The country appears in the public feed after up to its 60-second cache interval,
and then on the website after refresh. New table images use the approved flag;
old published images are not edited or resent. Open table confirmations may
require a refreshed preview after a country change. No MMR, ledger, name,
rank, role or member-sync job is changed by flag approval.

## Deployment and checks

Startup adds a new flag_requests table and pending-request index. Existing
verification/name requests and their CHECK constraint are not rebuilt or reset.
No new Railway variables, credentials, channel IDs, website change or dependency
is needed. Country-name data is bundled with its MIT attribution. Existing
requests and country metadata remain intact across restart.

Run with Python 3.13 and requirements-v2-backups.txt:

```text
python scripts/run_solo_tests.py
python -m unittest discover -s tests -p test_production_backups.py
```

Tests cover registered-only requests, country suggestions/validation, duplicate
pending requests, independent approval/rejection, stale/cleared flags, concurrent
approvals, audit rollback, additive startup, privacy and no accounting/role writes.
Actual Pycord command tests are stopped before login; they exercise submission,
channel/restriction checks, mixed queue IDs, review buttons, lost staff access,
stale approval and rejection. Existing empty-queue regressions remain enabled.

After an approved release, an actual registered member with a missing/incorrect
country can submit /flag. Check that the flag stays unchanged until another staff
member approves through /pending. Check the website after feed refresh. Use only
a genuine country correction; no fabricated production match or strike is needed.
A push to v2-production-integration may auto-deploy; the review branch is separate.
