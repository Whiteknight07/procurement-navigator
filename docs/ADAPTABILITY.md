# Adaptability test: a third, different customer

`data/cases/harbor/` is Harbor Regional Health, a US hospital network: HIPAA, a BAA, PHI, US-only data, SSO/SAML,
a 30-day breach notice, its own questionnaire style (SEC-1, PRIV-3) and its own column names. It is **not loaded by
default**, so the Acme and Northstar demo stays unchanged. To try it, apply `docs/adaptability/harbor_config.patch`
(adds the deal and 5 healthcare facts to the catalog) and rebuild.

## What carried over with no code change
- Stable ids from Harbor's own numbering (`harbor:SEC-1`), asks merged across questionnaire and emails.
- Deadlines (Nov 21 cut-off, Dec 3 committee) and the "18 days left, 19 requirements open" delay.
- US-only residency, settled from the customer's own words; the fallback → providers → inference → cross-border
  blocker chain.
- Slack conflict (logs 7 vs 14 days) shown as conflicting; the draft "30 days" flagged as another customer's
  (Cobalt) answer.
- Company facts (stale pen test, expired insurance) shared automatically with the new deal.

## What needed data only
- **Catalog entries** for `baa_status`, `provider_baas`, `phi_handling`, `sso_saml`, `breach_notification`.
  Before them, the healthcare asks were mapped to the nearest existing fact (BAA → personal info categories, SSO →
  access control), which hides the gap. After: 5 of 5 mapped to the right fact.

## What needed code (fixed)
- Harbor's headers (Ref, Domain, Control Question, Vendor Response) parsed to 0 rows. `sources.parse_questionnaire`
  now maps common header names; an empty questionnaire is reported in errors.

## Known costs and limits
- **Catalog edits re-extract everything.** The catalog is in every extraction prompt, so the edit re-ran 60 model
  calls (177 s, about $0.22), and re-extraction changed a few answers on deals nobody touched. Next: extract per fact
  group so a new fact only re-reads what it needs, and keep the old extraction when it still validates.
- **Requirement vs commitment.** "Notify within 30 days" (the customer's need) against "72 hours" (our commitment)
  shows as conflicting, though 72 hours meets it. Comparing a requirement to a commitment needs a rule per unit
  type (durations, money with currency), not a text label.
- **Company facts read every deal's text.** A deal's draft config can add a claim to a company fact; it shows the
  source, and trust rules keep it from verifying, but a catalog gap makes it more likely.
- **Guardrails.** "Do not tell Harbor we sign BAAs" is extracted as an internal note, not enforced as a hold on the
  draft. Next: a `hold` flag in the checker.

Cost for the whole Harbor run: 102 model calls, about $0.37.
