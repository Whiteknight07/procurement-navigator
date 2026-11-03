"""The answer key: what a careful human (Jai) would expect from the case, written by hand.

Everything here comes from reading the case files, not from running the system. Where the case is
genuinely ambiguous the key accepts more than one answer and says why.
"""

from datetime import date

AS_OF = date(2026, 11, 3)

# ---------- traps planted in the case ----------
OTHER_CUSTOMERS = ["Meridian", "BluePeak"]          # prior customers; their answers are hints only
OTHER_CUSTOMER_SOURCES = ["prior_customer_answers"]  # file name fragment of the "CONTEXT_ONLY" docs
TEMPLATE_CONTROLS_PER_DEAL = 37                      # Q-024..Q-060: same boilerplate, number changed

# Draft answers already typed into the round 1 sheet
DRAFT_RETENTION = {"fact": "acme.retention_app_logs", "draft": "30 days?", "truth": "14 days"}
DRAFT_RTO = {"fact": "company.rto_rpo", "draft": "24h / 24h?"}   # matches a prior customer only

# ---------- evidence and time (as of 2026-11-03) ----------
EXPIRED = "company.insurance"            # certificate expired Oct 1, 2026
STALE = ("company.pen_test", 183)        # Feb 2026 report; Jai: ~6 months is the limit
NOT_VERIFIED = "company.pen_test_remediation"   # only Anthony's chat says "remediated later";
                                                # the Vanta file shows the findings OPEN
SOC2 = "company.soc2"                    # 2025 report final in Vanta; 2026 audit not final

# ---------- root causes ----------
ROOT_BLOCKER = "acme.residency_requirement"     # only Acme can say; fallback -> providers -> ... wait on it
UNDECIDED = "acme.fallback_routing"             # "probably disable ... Not finalized."
DPA = ("acme.dpa_status", "acme.subprocessors") # Legal: DPA blocked until subprocessors confirmed

# ---------- requirements discovery ----------
# Elena's Sep 21 email has four asks. Each must land in a requirement linked to one of these facts.
ELENA_ASKS = [
    ("personal-information scope", ["personal"], ["personal_info_categories", "pilot_data_scope"]),
    ("content leaving Canada during processing", ["canada", "leave"],
     ["cross_border_transfers", "inference_location", "residency_requirement"]),
    ("retention by data type", ["retention"],
     ["retention_source_files", "retention_app_logs", "retention_traces", "retention_prompts_responses"]),
    ("subprocessors with content access", ["subprocessor"], ["subprocessors"]),
]
# Marcus's Sep 14 instructions: how to answer, not something to answer.
MARCUS_INSTRUCTIONS = [["configuration"], ["owner"]]
NORTHSTAR_LAUNCH = "2026-11-16"
NORTHSTAR_CADENCE = "wednesday"

# ---------- round 2 (data/simulated/acme_round2.csv) ----------
# relations: accepted labels. has_ask: the matched requirement must contain this round-1 row.
# not_ask: must NOT contain it. facts_any: matched requirement must link one of these facts.
ROUND2_INPUT = "01_acme_round2.json"
ROUND2 = {
    "Q-004": dict(relations=["repeat_with_changes"], has_ask="acme:R1:Q-004", added=["delet"],
                  why="Retention restated, plus a new deletion-request part."),
    "Q-007": dict(relations=["repeat_with_changes"], has_ask="acme:R1:Q-007", added=["2026"],
                  why="Same SOC 2 ask, now asks for the 2026 report if final."),
    "Q-009": dict(relations=["repeat"], has_ask="acme:R1:Q-009",
                  why="Word-for-word the same as round 1 Q-009."),
    "Q-012": dict(relations=["new", "repeat", "repeat_with_changes"], not_ask="acme:R1:Q-012",
                  reused=True, facts_any=["ai_monitoring"],
                  why="Number reused: round 1 Q-012 was model providers, round 2 Q-012 is AI monitoring. "
                      "New, or matched to the predicted monitoring topic, are both fine."),
    "Q-021": dict(relations=["repeat_with_changes"], has_ask="acme:R1:Q-021",
                  why="Insurance again, now noting the expiry and asking for the new certificate."),
    "Q-061": dict(relations=["repeat_with_changes"],
                  facts_any=["personal_info_categories"],
                  why="Personal-information scope, conditional on real transcripts, plus minimisation. "
                      "It can attach to the PI-scope requirement or to Nina's 'what changes' requirement."),
    "Q-062": dict(relations=["repeat", "repeat_with_changes"], facts_any=["bcp_dr_testing"],
                  not_predicted=True,
                  why="Business continuity was predicted from the guidance doc; now it is actually asked."),
}

# ---------- story: all six inputs in order ----------
STORY_INPUTS = ["01_acme_round2.json", "02_elena_residency.json", "03_anthony_config.json",
                "04_anthony_soc2.json", "05_soc2_report.json", "06_insurance_renewal.json"]
