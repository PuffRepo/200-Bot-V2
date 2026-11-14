# MKCentral flags during verification

When staff opens a verification confirmation, the bot reads the country from
https://mkcentral.com/api/registry/players/{registry_id}. It accepts only a matching
public registry profile and a supported ISO country code. The confirmation shows
the country before approval. Approval records registration, country and source
audit in the same SQLite transaction. Rejections and failed approvals save nothing.

Staff still checks ownership and approves the Lounge name. No automatic identity
approval is introduced. There is no new token, API key or configuration setting.

An unavailable MKC service, hidden/missing profile or invalid country does not block
staff verification: the confirmation explains that no flag is available, and the
existing flag request/staff correction commands remain available. Existing explicit
country overrides are preserved. Name changes do not import a new country.

New flags appear in the existing public feed, website and future rendered tables.
Previously posted result images are unchanged. This does not backfill imported S9
players: their unresolved historical MKC links must not be guessed.

Validation: `python scripts/run_solo_tests.py` includes synthetic public-profile
fixtures, bounded HTTP reads, unavailable profiles, wrong IDs, self-approval denial,
transaction rollback, audit records and preservation of staff country corrections.
