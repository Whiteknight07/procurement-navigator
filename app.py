"""Procurement Navigator: the Monday briefing, deal pages, facts, approvals and new inputs."""

from __future__ import annotations

import json
import shutil
import time
from datetime import date
from pathlib import Path

import streamlit as st

from pn import drafting, engine, ledger
from pn.planner import batch_message, fact_key, label

ROOT = Path(__file__).parent
INBOX = ROOT / "state" / "inbox"
SAMPLES = ROOT / "data" / "simulated" / "inputs"

st.set_page_config(page_title="Procurement Navigator", layout="wide")

COLOR = {"verified": "green", "ready": "green", "needs_confirmation": "orange", "needs_proof": "orange",
         "open": "orange", "conflicting": "red", "stale": "red", "expired": "red", "blocked": "red",
         "undecided": "violet", "unknown": "gray", "missing_text": "gray", "predicted": "blue",
         "umbrella": "gray", "unmapped": "gray", "update_owed": "orange", "at_risk": "red"}
NICE = {"needs_confirmation": "needs confirmation", "needs_proof": "needs proof",
        "missing_text": "no question text", "update_owed": "update owed", "at_risk": "at risk"}


def badge(status: str) -> str:
    return f":{COLOR.get(status, 'gray')}-background[{NICE.get(status, status)}]"


# ---------------- state ----------------

def snapshot(s) -> dict:
    return {"facts": {k: (f.status, f.value) for k, f in s.facts.items()},
            "reqs": {r.id: (r.status, r.title) for rs in s.reqs.values() for r in rs},
            "actions": {a.title for a in s.plan["actions"]}}


def rebuild(note: str | None = None):
    prev = st.session_state.get("state")
    as_of = st.session_state.get("as_of")
    with st.spinner("Rebuilding from inputs (new inputs go through the model, the rest is cached)..."):
        t = time.time()
        s = engine.build(as_of=as_of)
        st.session_state.build_secs = round(time.time() - t, 1)
    if prev is not None and note:
        st.session_state.changes = (note, diff(snapshot(prev), snapshot(s), s))
    st.session_state.state = s


def diff(a: dict, b: dict, s) -> list[str]:
    out = []
    for k, (st_b, v_b) in b["facts"].items():
        st_a, v_a = a["facts"].get(k, ("(new)", None))
        if (st_a, v_a) != (st_b, v_b) and (st_a != "unknown" or st_b != "unknown"):
            out.append(f"Fact **{label(k, s.catalog)}**: {badge(st_a)} → {badge(st_b)}"
                       + (f" · now \"{v_b}\"" if v_b and v_b != v_a else ""))
    for k, (st_b, t) in b["reqs"].items():
        st_a = a["reqs"].get(k, ("(new)", ""))[0]
        if st_a != st_b:
            out.append(f"Requirement **{t}** ({k}): {badge(st_a) if st_a != '(new)' else 'new'} → {badge(st_b)}")
    for t in sorted(b["actions"] - a["actions"]):
        out.append(f"New action: {t}")
    for t in sorted(a["actions"] - b["actions"]):
        out.append(f"Action no longer needed: ~~{t}~~")
    return out


if "state" not in st.session_state:
    st.session_state.as_of = None
    rebuild()
S = st.session_state.state

# ---------------- sidebar ----------------

with st.sidebar:
    st.title("Procurement Navigator")
    st.caption("Lantern AI · vendor reviews")
    page = st.radio("Screen", ["Monday briefing", "Deal page", "Facts", "Approvals", "Rounds",
                               "Add input"], label_visibility="collapsed")
    st.divider()
    new_date = st.date_input("As-of date (today)", value=S.as_of)
    if new_date != S.as_of:
        st.session_state.as_of = new_date
        rebuild(f"As-of date moved to {new_date:%b %d, %Y}")
        st.rerun()
    st.caption(f"Built in {st.session_state.get('build_secs', 0)}s · {len(S.sources)} sources · "
               f"{len(S.claims)} claims · {len(S.facts)} facts")
    if S.errors:
        st.error("\n".join(S.errors))
    if st.button("Reset demo (clear added inputs and ledger)"):
        for p in INBOX.glob("*.json"):
            p.unlink()
        ledger.reset()
        st.session_state.pop("changes", None)
        st.session_state.pop("drafts", None)
        rebuild()
        st.rerun()

if "changes" in st.session_state:
    note, changes = st.session_state.changes
    with st.container(border=True):
        c1, c2 = st.columns([6, 1])
        c1.markdown(f"**What changed** · {note}")
        if c2.button("Dismiss"):
            st.session_state.pop("changes")
            st.rerun()
        if changes:
            for line in changes[:25]:
                st.markdown(f"- {line}")
            if len(changes) > 25:
                st.caption(f"... and {len(changes) - 25} more")
        else:
            st.caption("Nothing changed.")

P = S.plan
DN = S.deal_names


def req_by_id(rid):
    for r in S.reqs.get(rid.split(":")[0], []):
        if r.id == rid:
            return r


# ---------------- Monday briefing ----------------

def briefing():
    st.header(f"Monday briefing · {S.as_of:%A %b %d, %Y}")
    cols = st.columns(len(DN))
    for col, (deal, name) in zip(cols, DN.items()):
        rs = [r for r in S.reqs[deal] if r.status not in ("predicted", "umbrella")]
        n = lambda s: sum(1 for r in rs if r.status == s)
        with col.container(border=True):
            st.subheader(name)
            for t in S.timeline[deal]:
                if t.date_text:
                    st.caption(f"Target: {t.text}")
            a, b, c, d = st.columns(4)
            a.metric("Ready", n("ready"))
            b.metric("Open", n("open"))
            c.metric("Blocked", n("blocked"))
            d.metric("No text", sum(len(r.ask_ids) for r in rs if r.status == "missing_text"))

    jai = [a for a in P["actions"] if a.who == "Jai"]
    st.subheader("What you need to do")
    if not jai and not P["batches"]:
        st.caption("Nothing for you right now.")
    for a in jai:
        st.markdown(f"- **{a.title}** · {', '.join(DN[d] for d in a.deals)} · {a.detail}")
    st.markdown(f"- **Send {len(P['batches'])} consolidated messages** (one per person, each item "
                f"with our current best answer, so they only confirm or correct). See *What moves things forward*.")

    left, right = st.columns(2)
    with left:
        st.subheader("Blockers")
        st.caption("Root causes: fix these and everything behind them can move.")
        for b in P["blockers"][:7]:
            f = S.facts[b["fact"]]
            with st.container(border=True):
                st.markdown(f"**{label(b['fact'], S.catalog)}** {badge(f.status)} · holds up "
                            f"**{len(b['reqs'])}** requirement(s)")
                st.caption(f.reason)
                for c in b["chains"][:2]:
                    st.caption(f"↳ {c}")
    with right:
        st.subheader("What's delaying")
        for d in P["delays"]:
            st.markdown(f"- {badge('expired' if d['kind'] in ('expired', 'deadline_passed', 'overdue_wait') else 'needs_proof')} "
                        f"{('**' + d['deal'] + '** · ') if d['deal'] else ''}{d['text']}")
        st.subheader("Waiting on")
        if not P["waits"]:
            st.caption("Nobody yet. Send the messages below and they show up here, with an overdue "
                       "flag based on each person's usual reply time.")
        for w in P["waits"]:
            st.markdown(f"- **{w['who']}** since {w['since']} · {len(w['facts'])} item(s) · "
                        f"usually {w['expected_days']} business days " +
                        (badge("expired") + " overdue" if w["overdue"] else ""))
        if P["updates"]:
            st.subheader("Answers already sent that changed")
            for u in P["updates"]:
                r = req_by_id(u["req"])
                st.markdown(f"- {badge(u['kind'])} **{r.title if r else u['req']}** "
                            f"({DN[u['deal']]}, sent {u['sent_on']}): " +
                            "; ".join(f"{label(c['fact'], S.catalog)} {c['was']['status']} → {c['now']['status']}"
                                      for c in u["changes"]))

    st.subheader("What moves things forward")
    st.caption("Only the frontier: things nobody is blocked from doing today. Ranked by how many "
               "requirements each unblocks, then slowest reply first (start the slow ones now).")
    for who, items in sorted(P["batches"].items(), key=lambda kv: -sum(a.unblocks for a in kv[1])):
        total = sum(a.unblocks for a in items)
        with st.expander(f"**{who}** · {len(items)} item(s) · unblocks {total} · "
                         f"replies in ~{items[0].reply_days} days"):
            for a in items:
                st.markdown(f"- {a.title} · {', '.join(DN[d] for d in a.deals)} · unblocks {a.unblocks}")
            msg = batch_message(who, items, S)
            st.text_area("Consolidated message (edit before sending)", msg, height=200,
                         key=f"msg-{who}")
            if st.button(f"Mark as sent to {who}", key=f"ask-{who}"):
                ledger.append({"type": "asked", "who": who, "date": str(S.as_of),
                               "facts": [k for a in items for k in a.facts], "note": msg[:200]})
                rebuild(f"Asked {who}")
                st.rerun()

    if S.draft_flags:
        st.subheader("Draft answers in the questionnaires that look wrong")
        for f in S.draft_flags:
            st.markdown(f"- {f['text']}")


# ---------------- Deal page ----------------

def deal_page():
    deal = st.selectbox("Deal", list(DN), format_func=DN.get)
    st.header(DN[deal])
    for t in S.timeline[deal]:
        st.caption(f"Timeline: {t.text}")
    for i in S.instructions[deal]:
        st.caption(f"Customer instruction: {i.text}")
    tabs = st.tabs(["1 · Required", "2 · Known", "3 · Unresolved", "4 · Blocking", "5 · Next"])
    rs = S.reqs[deal]
    keys = sorted({fact_key(deal, n, S.catalog) for r in rs for n in r.facts})
    with tabs[0]:
        st.caption("Every ask from every round and channel, merged into requirements.")
        for track in sorted({r.track for r in rs}):
            st.markdown(f"#### {track}")
            for r in [r for r in rs if r.track == track]:
                with st.expander(f"{r.id.split(':')[1]} · {r.title}  —  {NICE.get(r.status, r.status)}"):
                    st.markdown(f"{badge(r.status)} {r.ask}")
                    if r.reading:
                        st.info(f"How {DN[deal]} reads it: {r.reading}")
                    for i in r.ask_ids[:40]:
                        a = S.asks.get(i)
                        if a:
                            where = f"Round {a.round} {a.qid}" if a.qid else S.sources[a.source_id].title
                            st.caption(f"• {where}: \"{a.quote}\"" + (f" ({a.who})" if a.who else ""))
                    for h in r.history:
                        if h.get("round", 0) > 1:
                            st.caption(f"Round {h['round']} {h['qid']}: {h['relation']}"
                                       + (f" · added: {h['added_part']}" if h["added_part"] else "")
                                       + (f" · ⚠ {h['reused_number']}" if h.get("reused_number") else ""))
                    if r.facts:
                        st.markdown("Needs: " + ", ".join(
                            f"{label(fact_key(deal, n, S.catalog), S.catalog)} "
                            f"{badge(S.facts[fact_key(deal, n, S.catalog)].status)}" for n in r.facts))
    with tabs[1]:
        st.caption("Verified facts, each with the source that settles it.")
        for k in keys:
            f = S.facts[k]
            if f.status == "verified":
                src = S.sources[f.winner.source_id]
                st.markdown(f"- **{label(k, S.catalog)}**: {f.value}  \n  :gray[{f.winner.tier_reason} · "
                            f"{src.title} · \"{f.winner.quote}\"]" +
                            "".join(f"  \n  :orange[⚠ {w}]" for w in f.warnings[:2]))
    with tabs[2]:
        st.caption("Not settled yet, with why and who can settle it. Values here are internal best guesses, never sent.")
        order = ["conflicting", "expired", "stale", "undecided", "needs_proof", "needs_confirmation", "unknown"]
        for status in order:
            for k in keys:
                f = S.facts[k]
                if f.status == status:
                    owner = S.policy["team_owner"].get(f.spec["owner"], f.spec["owner"])
                    st.markdown(f"- {badge(f.status)} **{label(k, S.catalog)}** · owner {owner}"
                                + (f" · best guess \"{f.value}\"" if f.value else "")
                                + f"  \n  :gray[{f.reason}]"
                                + (f"  \n  :red[blocked by {', '.join(label(b, S.catalog) for b in f.blocked_by)}]" if f.blocked_by else ""))
    with tabs[3]:
        st.caption("Root causes for this deal, and the chain from each requirement down to it.")
        for b in P["blockers"]:
            mine = [x for x in b["reqs"] if x.startswith(deal + ":")]
            if not mine:
                continue
            f = S.facts[b["fact"]]
            with st.container(border=True):
                st.markdown(f"**{label(b['fact'], S.catalog)}** {badge(f.status)} · holds up {len(mine)}: "
                            + ", ".join(req_by_id(x).title for x in mine))
                st.caption(f.reason)
                for c in b["chains"]:
                    if c.lower().startswith(deal):
                        st.caption(f"↳ {c}")
    with tabs[4]:
        for a in P["actions"]:
            if deal in a.deals:
                st.markdown(f"- **{a.who}** · {a.title} · unblocks {a.unblocks}"
                            + (f" · waiting since {a.waiting_since}" if a.waiting_since else "")
                            + f"  \n  :gray[{a.detail}]")


# ---------------- Facts ----------------

def facts_page():
    st.header("Facts")
    st.caption("Each fact's value is chosen by fixed rules: trust tier first, then the most recent. "
               "Another customer's answer is shown as a hint and never wins.")
    c1, c2 = st.columns([1, 2])
    scope = c1.selectbox("Scope", ["company"] + list(DN), format_func=lambda x: "Whole company" if x == "company" else DN[x])
    keys = [k for k in S.facts if k.startswith(scope + ".")]
    k = c2.selectbox("Fact", keys, format_func=lambda x: f"{label(x, S.catalog)} · {NICE.get(S.facts[x].status, S.facts[x].status)}")
    f = S.facts[k]
    st.markdown(f"### {label(k, S.catalog)} {badge(f.status)}")
    st.markdown(f"*{f.spec['question']}* · owner **{f.spec['owner']}** · proof needed: **{f.spec['proof'].replace('_', ' ')}**"
                + (f" · max age {f.spec['max_age_days']} days" if f.spec.get("max_age_days") else ""))
    st.markdown(f"**Value:** {f.value or '—'}  \n**Why:** {f.reason}")
    for w in f.warnings:
        st.warning(w)
    if f.blocked_by:
        st.error("Blocked by " + ", ".join(label(b, S.catalog) for b in f.blocked_by))
    rows = []
    for c in f.claims + f.hints:
        rows.append({"winner": "★" if f.winner and c.id == f.winner.id else "", "tier": c.tier,
                     "trust": c.tier_reason, "value": c.value, "kind": c.assertion, "quote": c.quote,
                     "speaker": c.speaker or "", "date": str(getattr(c, 'eff_date', None) or ""),
                     "source": S.sources[c.source_id].title})
    st.dataframe(rows, use_container_width=True, hide_index=True)
    for fl in S.draft_flags:
        if fl["fact"] == k:
            st.error(fl["text"])


# ---------------- Approvals ----------------

def approvals_page():
    st.header("Approvals")
    st.caption("The model drafts from facts only. Customer-facing sentences can only state verified facts; "
               "everything else becomes a Pending line naming the owner. Code checks every citation.")
    deal = st.selectbox("Deal", list(DN), format_func=DN.get, key="ap-deal")
    rs = [r for r in S.reqs[deal] if r.status not in ("missing_text", "umbrella") and r.facts]
    rs.sort(key=lambda r: (r.status != "ready", r.id))
    r = st.selectbox("Requirement", rs, format_func=lambda r: f"{r.id.split(':')[1]} · {r.title} · {NICE.get(r.status, r.status)}")
    drafts = st.session_state.setdefault("drafts", {})
    st.markdown(f"{badge(r.status)} {r.ask}")
    if r.reading:
        st.info(f"How {DN[deal]} reads it: {r.reading}")
    if st.button("Draft answer", type="primary"):
        with st.spinner("Drafting and checking..."):
            drafts[r.id] = drafting.draft(r, S)
    d = drafts.get(r.id)
    if not d:
        return
    claims = {c.id: c for c in S.claims}
    with st.container(border=True):
        st.markdown(f"**Draft v{d['version']}** · for {DN[deal]}")
        for i, s in enumerate(d["sentences"]):
            flags = d["checks"]["per_sentence"][i]
            mark = "🟥" if any(not f.startswith("evidence partly") for f in flags) else ("🟧" if flags else "🟩")
            if s["kind"] == "pending":
                st.markdown(f"{mark} :orange[{s['text']}]")
            else:
                st.markdown(f"{mark} {s['text']}")
            chips = []
            for cid in s["claim_ids"]:
                c = claims.get(cid)
                if c:
                    src = S.sources[c.source_id]
                    tier = "Vanta ✓" if src.system == "vanta" else c.tier_reason
                    chips.append(f":blue-background[{src.title.split('/')[-1]} · {tier}]  \"{c.quote}\"")
            for ch in chips:
                st.caption(ch)
            for fl in flags:
                st.caption(f":red[⚠ {fl}]")
    with st.expander("Internal notes (Jai only, never sent)", expanded=True):
        for n in d["internal_notes"]:
            st.markdown(f"- {n}")
    for w in d["checks"]["warnings"]:
        st.warning(w)
    for e in d["checks"]["errors"]:
        st.error(e)
    sent = [e for e in S.ledger if e["type"] == "sent" and e["req"] == r.id]
    c1, c2 = st.columns(2)
    if c1.button("Approve and mark as sent", disabled=not d["checks"]["can_approve"], type="primary"):
        ledger.append({"type": "approved", "deal": deal, "req": r.id, "version": d["version"],
                       "date": str(S.as_of)})
        ledger.append({"type": "sent", "deal": deal, "req": r.id, "version": d["version"],
                       "text": d["text"], "facts": d["facts_used"], "date": str(S.as_of)})
        rebuild(f"Sent {r.title} to {DN[deal]}")
        st.rerun()
    if not d["checks"]["can_approve"]:
        c2.caption("Approval is disabled until the checker's errors are fixed.")
    for e in sent:
        st.caption(f"Sent {e['date']} (v{e['version']}): {e['text']}")


# ---------------- Rounds ----------------

def rounds_page():
    st.header("Questionnaire rounds")
    st.caption("Each question in a later round is matched to existing requirements by meaning, not by number.")
    for deal, recs in S.rounds.items():
        st.subheader(DN[deal])
        if not recs:
            st.caption("Only round 1 so far. Add a round on the Add input screen.")
            continue
        counts = {}
        for x in recs:
            counts[x["relation"]] = counts.get(x["relation"], 0) + 1
        st.markdown(" · ".join(f"**{v}** {k.replace('_', ' ')}" for k, v in counts.items()))
        for x in recs:
            r = req_by_id(x["requirement_id"])
            st.markdown(f"- **{x['qid']}** {badge('ready' if x['relation'] == 'repeat' else 'open' if x['relation'] != 'new' else 'predicted')} "
                        f"{x['relation'].replace('_', ' ')} → {r.title if r else x['requirement_id']}  \n"
                        f"  :gray[\"{x['question']}\"]"
                        + (f"  \n  added: {x['added_part']}" if x["added_part"] else "")
                        + (f"  \n  :red[⚠ {x['reused_number']}]" if x.get("reused_number") else ""))


# ---------------- Add input ----------------

def add_page():
    st.header("Add input")
    st.caption("Connectors are mocked: paste anything (email, Slack, a document, evidence, a new questionnaire round). "
               "It is stored as a new source; the engine rebuilds and shows what changed.")
    st.subheader("Demo inputs")
    for p in sorted(SAMPLES.glob("*.json")):
        d = json.loads(p.read_text())
        added = (INBOX / p.name).exists()
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{d['title']}** · {d['system']} · {d.get('author', '')} · {d.get('date', '')}  \n"
                    f":gray[{d['text'][:160]}{'...' if len(d['text']) > 160 else ''}]")
        if c2.button("Added" if added else "Add", key=p.name, disabled=added):
            shutil.copy(p, INBOX / p.name)
            rebuild(f"Added: {d['title']}")
            st.rerun()
    st.subheader("Your own input")
    with st.form("add"):
        c1, c2, c3 = st.columns(3)
        deal = c1.selectbox("About", ["company"] + list(DN), format_func=lambda x: "Whole company" if x == "company" else DN[x])
        system = c2.selectbox("Arrived as", ["email", "slack", "vanta", "drive", "notes", "questionnaire"])
        author = c3.text_input("From")
        c4, c5 = st.columns(2)
        when = c4.date_input("Date", value=S.as_of)
        rnd = c5.number_input("Questionnaire round", min_value=1, value=2)
        title = st.text_input("Title")
        text = st.text_area("Text (for a questionnaire: CSV with id,section,question)", height=160)
        if st.form_submit_button("Add and rebuild") and text.strip():
            INBOX.mkdir(parents=True, exist_ok=True)
            name = f"{int(time.time())}_user.json"
            (INBOX / name).write_text(json.dumps({
                "deal": deal, "system": system, "author": author, "date": str(when),
                "title": title or f"{system} from {author}", "text": text,
                **({"round": int(rnd)} if system == "questionnaire" else {})}, indent=1))
            rebuild(f"Added: {title or system}")
            st.rerun()


{"Monday briefing": briefing, "Deal page": deal_page, "Facts": facts_page, "Approvals": approvals_page,
 "Rounds": rounds_page, "Add input": add_page}[page]()
