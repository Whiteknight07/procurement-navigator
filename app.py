"""Procurement Navigator: the Monday briefing, the deal screen, approvals and new inputs."""

from __future__ import annotations

import html
import json
import re
import shutil
import time
from collections import Counter
from pathlib import Path

import streamlit as st

from pn import drafting, engine, ledger, llm
from pn.planner import LIVE, batch_message, closure, fact_key, label

ROOT = Path(__file__).parent
INBOX = ROOT / "state" / "inbox"
SAMPLES = ROOT / "data" / "simulated" / "inputs"

st.set_page_config(page_title="Procurement Navigator", layout="wide")

# One colour per status family, used on every screen (theme colours are set in .streamlit/config.toml).
COLOR = {"verified": "green", "ready": "green",
         "unconfirmed": "orange", "open": "orange", "update_owed": "orange",
         "conflicting": "red", "out_of_date": "red", "blocked": "red", "at_risk": "red",
         "undecided": "violet",
         "unknown": "gray", "missing_text": "gray", "umbrella": "gray", "unmapped": "gray",
         "predicted": "blue"}
NICE = {"out_of_date": "out of date", "missing_text": "no question text", "update_owed": "update owed",
        "at_risk": "at risk", "umbrella": "covers others", "unmapped": "no question mapped"}
HEX = {"green": "#2E7D4F", "orange": "#B8770B", "red": "#B53B33", "violet": "#6A4C9C",
       "gray": "#7F8A85", "blue": "#24467A"}
MEANING = [  # the status key, in the order Jai should care about
    ("conflicting", "sources that count disagree"),
    ("out_of_date", "it was a fact, but the evidence is stale or expired"),
    ("undecided", "someone still has to make the call"),
    ("unconfirmed", "someone said it, but not a source that can settle it"),
    ("unknown", "nobody who counts has said anything yet"),
    ("verified", "settled: this is now a fact"),
]
UNSETTLED = [s for s, _ in MEANING if s != "verified"]   # worst first
LADDER = [  # whose word counts, strongest first (config/policy.yaml)
    ("Vanta evidence, or the customer's own statement", 1),
    ("The owner stating it as settled", 2),
    ("A deal document or draft, or other internal chat", 3),
    ("Hint: generic product doc, questionnaire draft, another customer's answer", 4),
]


def nice(status: str) -> str:
    return NICE.get(status, status)


def badge(status: str) -> str:
    return f":{COLOR.get(status, 'gray')}-badge[{nice(status)}]"


def trust(c, src) -> str:
    """Short trust label for a claim, in the words Jai uses."""
    r = c.tier_reason
    if src.system == "vanta" or r.startswith("the evidence store"):
        return "Vanta evidence"
    for prefix, short in (("the customer's own", "customer's own statement"),
                          ("the owner (", "owner confirmed"),
                          ("a document for this deal", "deal draft"),
                          ("internal chat", "internal chat"), ("our own guess", "internal chat"),
                          ("a questionnaire draft", "questionnaire draft (hint)"),
                          ("a generic product", "generic doc (hint)"),
                          ("another customer", "another customer's answer (hint, never counts)")):
        if r.startswith(prefix):
            return short
    return r or src.system


def impact_text(a) -> str:
    """Plain words for what an action moves, e.g. "6 answers wait on this; unlocks 2 now"."""
    i = P["impact"].get(a.title) or {"ready": 0, "unblocked": 0}
    parts = []
    if a.kind != "approve" and a.unblocks > max(i["ready"], 1):
        parts.append(f"{plural(a.unblocks, 'answer')} wait on this")
    if i["ready"]:
        parts.append(f"unlocks {plural(i['ready'], 'answer')} now")
    if i["unblocked"]:
        parts.append(f"clears {plural(i['unblocked'], 'blocked fact')}")
    return "; ".join(parts).capitalize()


def tidy(s: str) -> str:
    """Smooth punctuation where planner text joins a sentence to a clause."""
    return str(s).replace(".:", ":").replace(". (", " (")


def esc(s) -> str:
    return html.escape(str(s or ""))


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


st.html("""<style>
h1, h2, h3 { letter-spacing: -0.01em; }
.pn-lede { font-size: 1.12rem; line-height: 1.5; max-width: 46rem; margin: -0.4rem 0 0.6rem; }
.pn-bar { display: flex; height: 10px; border-radius: 2px; overflow: hidden; margin: 0.35rem 0 0.4rem;
          background: #E9EEE7; max-width: 30rem; }
.pn-bar span { display: block; height: 100%; }
.pn-sent { border-left: 4px solid var(--c); padding: 0.15rem 0 0.25rem 0.8rem; margin: 0 0 0.9rem;
           max-width: 52rem; }
.pn-sent p { margin: 0 0 0.3rem; font-size: 1.04rem; line-height: 1.45; }
.pn-sent .pn-pending { color: #7A5207; }
.pn-cite { font-size: 0.86rem; line-height: 1.4; color: #4A5551; margin: 0.15rem 0; }
.pn-cite b { color: #26302E; font-weight: 600; }
.pn-cite q { font-style: italic; }
.pn-flag { font-size: 0.86rem; color: #9A2F28; margin: 0.15rem 0; font-weight: 600; }
.pn-ladder { margin: 0.2rem 0 0.4rem; }
.pn-rung { display: flex; align-items: center; gap: 0.5rem; font-size: 0.84rem; line-height: 1.25;
           margin: 0 0 0.3rem; }
.pn-rung i { display: block; flex: none; height: 8px; background: #24467A; border-radius: 1px; }
.pn-rung.never { color: #7F8A85; text-decoration: line-through; }
.pn-rung.never i { background: repeating-linear-gradient(90deg, #B53B33 0 3px, transparent 3px 6px); }
.st-key-changes { border-left: 4px solid #24467A; padding-left: 0.9rem; background: #EEF2F8;
                  border-radius: 0 0.3rem 0.3rem 0; padding: 0.6rem 0.9rem; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>""")


# ---------------- state ----------------

def snapshot(s) -> dict:
    return {"facts": {k: (f.status, f.value) for k, f in s.facts.items()},
            "reqs": {r.id: (r.status, r.title) for rs in s.reqs.values() for r in rs},
            "actions": {a.title for a in s.plan["actions"]}}


def rebuild(note: str | None = None):
    prev = st.session_state.get("state")
    as_of = st.session_state.get("as_of")
    with st.spinner("Rebuilding from all inputs. New inputs go through the model; the rest is cached."):
        t = time.time()
        s = engine.build(as_of=as_of)
        st.session_state.build_secs = round(time.time() - t, 1)
    if prev is not None and note:
        st.session_state.changes = (note, diff(snapshot(prev), snapshot(s), s))
    st.session_state.state = s
    st.session_state.pop("drafts", None)   # a draft made from the old facts must not be approved


def diff(a: dict, b: dict, s) -> list[str]:
    out = []
    for k, (st_b, v_b) in b["facts"].items():
        st_a, v_a = a["facts"].get(k, ("(new)", None))
        if (st_a, v_a) != (st_b, v_b) and (st_a != "unknown" or st_b != "unknown"):
            was = "new" if st_a == "(new)" else badge(st_a)
            out.append(f"**{label(k)}** went from {was} to {badge(st_b)}"
                       + (f", now \"{v_b}\"" if v_b and v_b != v_a else ""))
    for k, (st_b, t) in b["reqs"].items():
        st_a = a["reqs"].get(k, ("(new)", ""))[0]
        if st_a != st_b:
            was = "new" if st_a == "(new)" else badge(st_a)
            out.append(f"Requirement **{t}** ({k.split(':')[1]}) went from {was} to {badge(st_b)}")
    for t in sorted(b["actions"] - a["actions"]):
        out.append(f"New to do: {t}")
    for t in sorted(a["actions"] - b["actions"]):
        out.append(f"No longer needed: ~~{t}~~")
    return out


if "state" not in st.session_state:
    st.session_state.as_of = None
    rebuild()
S = st.session_state.state
P = S.plan
DN = S.deal_names
PAGES = ["Monday briefing", "Deal", "Approvals", "Add input"]


def go_approve(req_id: str):
    """Jump from the briefing straight to the approval card for one requirement."""
    st.session_state.nav = "Approvals"
    st.session_state["ap-deal"] = req_id.split(":")[0]
    st.session_state["ap-req"] = req_id


def ladder_html() -> str:
    rows = []
    for text, tier in LADDER:
        width = 40 if tier == 4 else 64 - (tier - 1) * 16
        cls = "pn-rung never" if tier == 4 else "pn-rung"
        rows.append(f'<div class="{cls}"><i style="width:{width}px"></i><span>{esc(text)}</span></div>')
    return '<div class="pn-ladder">' + "".join(rows) + "</div>"


def status_key():
    st.markdown("**Whose word counts.** Higher beats lower; on a tie, the most recent wins. "
                "Each question also says what can settle it: a Vanta document, the owner, or the customer.")
    st.html(ladder_html())
    st.caption("Hints are shown so you can see them, and never become our answer.")
    st.markdown("**Where each question stands**")
    st.markdown("  \n".join(f"{badge(s)} {m}" for s, m in MEANING))


# ---------------- sidebar ----------------

with st.sidebar:
    st.title("Procurement Navigator")
    st.caption("Lantern AI vendor reviews, for Jai")
    page = st.radio("Screen", PAGES, key="nav", label_visibility="collapsed")
    st.divider()
    new_date = st.date_input("Today's date", value=S.as_of,
                             help="Move it to see deadlines pass and evidence expire.")
    if new_date != S.as_of:
        st.session_state.as_of = new_date
        rebuild(f"Today's date moved to {new_date:%-d %B %Y}")
        st.rerun()
    st.caption(f"Rebuilt in {st.session_state.get('build_secs', 0)}s from {len(S.sources)} sources "
               f"({len(S.claims)} statements about {len(S.facts)} questions, "
               f"{sum(f.status == 'verified' for f in S.facts.values())} settled as facts).")
    if S.errors:
        st.error("\n".join(S.errors))
    with st.expander("How a question becomes a fact"):
        status_key()
    if st.button("Clear added inputs and the ledger",
                 help="Resets the demo: removes inputs you added and every approval, send and ask."):
        for p in INBOX.glob("*.json"):
            p.unlink()
        ledger.reset()
        st.session_state.pop("changes", None)
        st.session_state.pop("drafts", None)
        rebuild()
        st.rerun()

if "changes" in st.session_state:
    note, changes = st.session_state.changes
    with st.container(key="changes"):
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        c1.markdown(f"#### What changed after: {note}")
        if c2.button("Dismiss", width="stretch"):
            st.session_state.pop("changes")
            st.rerun()
        if changes:
            st.markdown("\n".join(f"- {line}" for line in changes[:25]))
            if len(changes) > 25:
                st.caption(f"And {len(changes) - 25} more.")
        else:
            st.caption("Nothing changed: the new input agreed with what we already knew.")


def needed_by(keys) -> str:
    """Requirements that need any of these questions, e.g. "Q-004 Retention by data type, Q-012 …"."""
    keys = set(keys)
    rs = [r for d, lst in S.reqs.items() for r in lst if LIVE(r)   # same answers "unblocks" counts,
          and keys & closure([fact_key(d, n, S.catalog) for n in r.facts], S.facts)]   # chains included
    names = [f"{r.id.split(':')[1]} {r.title}" + ("" if len(S.reqs) < 2 else f" ({DN[r.deal]})") for r in rs]
    return ", ".join(names[:5]) + (f", and {len(names) - 5} more" if len(names) > 5 else "")


def req_by_id(rid):
    for r in S.reqs.get(rid.split(":")[0], []):
        if r.id == rid:
            return r


def bar(counts: list[tuple[str, int]]) -> str:
    total = sum(n for _, n in counts) or 1
    return '<div class="pn-bar">' + "".join(
        f'<span style="width:{100 * n / total:.1f}%;background:{HEX[COLOR[s]]}" title="{n} {nice(s)}"></span>'
        for s, n in counts if n) + "</div>"


DELAY = {"deadline_passed": ("red", "deadline passed"), "deadline_close": ("orange", "deadline close"),
         "expires_before_target": ("orange", "expires before target"), "out_of_date": ("red", "out of date"),
         "overdue_wait": ("red", "overdue")}


# ---------------- Monday briefing ----------------

def briefing():
    jai = [a for a in P["actions"] if a.who == "Jai"]
    approve = [a for a in jai if a.kind == "approve"]
    updates = [a for a in jai if a.kind == "send_update"]
    blockers = P["blockers"]
    held = len({r for b in blockers for r in b["reqs"]})
    st.title(f"Briefing for {S.as_of:%A %-d %B}")
    parts = []
    if updates:
        parts.append(f"{plural(len(updates), 'answer')} we already sent {'needs' if len(updates) == 1 else 'need'} an update")
    parts.append(f"{plural(len(approve), 'answer is', 'answers are')} ready for you to approve")
    parts.append(f"{plural(len(blockers), 'root blocker')} hold up {plural(held, 'requirement')}")
    parts.append(f"{plural(len(P['batches']), 'person', 'people')} need a message from you")
    st.html(f'<p class="pn-lede">{esc(", ".join(parts[:-1]) + ", and " + parts[-1])}.</p>')

    cols = st.columns(len(DN), gap="large")
    for col, (deal, name) in zip(cols, DN.items()):
        rs = [r for r in S.reqs[deal] if r.status not in ("predicted", "umbrella")]
        n = lambda s: sum(1 for r in rs if r.status == s)
        no_text = sum(len(r.ask_ids) for r in rs if r.status == "missing_text")
        with col:
            st.subheader(name)
            for d in P["delays"]:
                if d["deal"] == deal and d["kind"] in ("deadline_passed", "deadline_close"):
                    c, t = DELAY[d["kind"]]
                    st.markdown(f":{c}-badge[{t}] {tidy(d['text'])}")
            st.html(bar([("ready", n("ready")), ("open", n("open")), ("blocked", n("blocked"))]))
            st.markdown(f"**{n('ready')}** ready, **{n('open')}** open, **{n('blocked')}** blocked"
                        + (f". {no_text} questionnaire rows have no real question text yet." if no_text else "."))

    st.divider()
    if True:   # one column: what needs Jai personally
        st.header("For you to do")
        if not jai and not P["batches"]:
            st.caption("Nothing needs you right now.")
        for a in updates:
            st.markdown(f"{badge('update_owed')} **{a.title}**  \n:gray[{a.detail}]")
        for u in P["updates"]:
            if u["kind"] == "at_risk":
                r = req_by_id(u["req"])
                st.markdown(f"{badge('at_risk')} **{r.title if r else u['req']}**, sent to {DN[u['deal']]} on "
                            f"{u['sent_on']}, may now be out of date: "
                            + "; ".join(f"{label(c['fact'])} is now {nice(c['now']['status'])}"
                                        for c in u["changes"])
                            + ". Hold the update until it is verified.")
        for a in approve:
            c1, c2 = st.columns([3, 1.6], vertical_alignment="center")
            c1.markdown(f"{badge('ready')} **{a.title.removeprefix('Approve answer: ')}** for {DN[a.deals[0]]}")
            c2.button("Review answer", key=f"go-{a.req}", on_click=go_approve, args=(a.req,),
                      width="stretch")
        if P["batches"]:
            st.markdown(f"**Send {plural(len(P['batches']), 'message')}**, one per person, below. Each item "
                        "carries our current best answer so they only confirm or correct it.")
        if S.draft_flags:
            st.subheader("Answers already typed into the questionnaires: don't send these")
            st.caption("Someone at Lantern pre-filled these rows before any checking. Each one is either wrong "
                       "or has nothing behind it, often copied from an old doc or another customer.")
            for f in S.draft_flags:
                text = re.sub(r"^\[\w+\] ", "", f["text"])
                st.markdown(f"- **{label(f['fact'])}**: {text}")
    st.divider()
    st.header("What moves things forward")
    st.caption("Only things someone can do today. People whose answers unlock the most answers come first; "
               "slow responders come before quick ones so their clock starts now.")
    def gain(items):
        imp = [P["impact"].get(a.title, {"ready": 0, "unblocked": 0}) for a in items]
        return (-sum(a.unblocks for a in items), -sum(i["ready"] for i in imp), -sum(i["unblocked"] for i in imp))

    for who, items in sorted(P["batches"].items(), key=lambda kv: gain(kv[1])):
        total = sum(a.unblocks for a in items)
        with st.expander(f"**{who}**: {plural(len(items), 'item')}, unblocks {total}, "
                         f"usually replies in {items[0].reply_days:g} business days"):
            for a in items:
                imp = impact_text(a)
                need = needed_by(a.facts)
                above = sorted({k for bl in P["blockers"] if bl["fact"] in a.facts
                                for c in bl["chains"] for k in c.split(" → ")[:-1]})
                st.markdown(f"- {a.title} ({' and '.join(DN[d] for d in a.deals)}), unblocks {a.unblocks}"
                            + (f"  \n  :green[{imp}]" if imp else "")
                            + (f"  \n  :gray[Answers it feeds: {need}]" if need else "")
                            + (f"  \n  :red[Root blocker: {', '.join(above)} cannot move until this is settled]"
                               if above else ""))
            msg = batch_message(who, items, S)
            st.text_area("Message to send (edit it first if you like)", msg, height=200, key=f"msg-{who}")
            if st.button(f"I sent this to {who}", key=f"ask-{who}",
                         help="Records the ask so the briefing tracks the wait and flags it when overdue."):
                ledger.append({"type": "asked", "who": who, "date": str(S.as_of),
                               "facts": [k for a in items for k in a.facts], "note": msg[:200]})
                rebuild(f"Asked {who}")
                st.rerun()

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("What's delaying")
        rest = [d for d in P["delays"] if d["kind"] not in ("deadline_passed", "deadline_close")]
        if not rest:
            st.caption("Nothing beyond the deadlines above.")
        for d in rest:
            c, t = DELAY.get(d["kind"], ("orange", d["kind"]))
            deals = " and ".join(DN.get(x.strip(), x) for x in d["deal"].split(",")) if d["deal"] else ""
            st.markdown(f":{c}-badge[{t}] {tidy(d['text'])}" + (f"  \n:gray[Affects {deals}]" if deals else ""))
    with right:
        st.subheader("Waiting on")
        if not P["waits"]:
            st.caption("Nobody yet. Once you mark a message as sent, the person shows up here, flagged "
                       "if they take longer than they usually do.")
        for w in P["waits"]:
            st.markdown((":red-badge[overdue] " if w["overdue"] else "")
                        + f"**{w['who']}**, asked {w['since']} about {plural(len(w['facts']), 'item')}. "
                        f"Usually replies in {w['expected_days']:g} business days.")


# ---------------- Deal page ----------------

def deal_page():
    deal = st.selectbox("Deal", list(DN), format_func=DN.get, key="deal")
    st.title(DN[deal])
    for t in S.timeline[deal]:
        st.markdown(f"**Timeline:** {t.text}")
    for i in S.instructions[deal]:
        st.markdown(f"**{DN[deal]} asked us to:** {i.text}")
    tabs = st.tabs(["What's required", "What we know", "What's unresolved", "What's blocking", "What to do next",
                    "Questions", "Rounds"])
    rs = S.reqs[deal]
    keys = sorted({fact_key(deal, n, S.catalog) for r in rs for n in r.facts})
    with tabs[0]:
        st.caption("Every ask from every round and channel, merged into one list of requirements.")
        for track in sorted({r.track for r in rs}):
            st.subheader(track)
            for r in [r for r in rs if r.track == track]:
                with st.expander(f"{badge(r.status)} {r.id.split(':')[1]} {r.title}"):
                    st.markdown(r.ask)
                    if r.reading:
                        st.info(f"How {DN[deal]} reads it: {r.reading}")
                    for i in r.ask_ids[:40]:
                        a = S.asks.get(i)
                        if a:
                            where = f"Round {a.round} {a.qid}" if a.qid else S.sources[a.source_id].title
                            st.caption(f"{where}: \"{a.quote}\"" + (f" ({a.who})" if a.who else ""))
                    for h in r.history:   # later rounds only
                        st.caption(f"Round {h['round']} {h['qid']} is a {h['relation'].replace('_', ' ')}"
                                   + (f": {h['added_part']}" if h["added_part"] else "")
                                   + (f". Warning: {h['reused_number']}" if h["reused_number"] else ""))
                    if r.facts:   # each question this answer needs: the proof if settled, else who can settle it
                        st.markdown("**Needs these questions settled**")
                        for n in r.facts:
                            f = S.facts[fact_key(deal, n, S.catalog)]
                            line = f"{badge(f.status)} **{label(f.key)}**"
                            if f.status == "verified":
                                src = S.sources[f.winner.source_id]
                                line += (f": {f.value}  \n:gray[{trust(f.winner, src)} in "
                                         f"{src.title.split('/')[-1]}: \"{f.winner.quote}\"]")
                            else:
                                who = S.policy["team_owner"].get(f.spec["owner"], f.spec["owner"])
                                line += f", ask {who}  \n:gray[{f.reason}]"
                            st.markdown(line)
    with tabs[1]:
        st.caption("Facts: questions that are settled, each with the statement that settles it.")
        shown = 0
        for k in keys:
            f = S.facts[k]
            if f.status == "verified":
                shown += 1
                src = S.sources[f.winner.source_id]
                st.markdown(f"{badge('verified')} **{label(k)}**: {f.value}  \n"
                            f":gray[{trust(f.winner, src)} in {src.title}: \"{f.winner.quote}\"]"
                            + "".join(f"  \n:orange[Caveat: {w}]" for w in f.warnings[:2]))
        if not shown:
            st.caption("Nothing is verified for this deal yet.")
    with tabs[2]:
        st.caption("Questions that are not facts yet, worst first, with why and who can settle them. "
                   "Best guesses here are internal and never sent.")
        for status in UNSETTLED:
            for k in keys:
                f = S.facts[k]
                if f.status == status:
                    owner = S.policy["team_owner"].get(f.spec["owner"], f.spec["owner"])
                    st.markdown(f"{badge(f.status)} **{label(k)}**, owner {owner}"
                                + (f". Best guess \"{f.value}\"" if f.value else "")
                                + f"  \n:gray[{f.reason}]"
                                + (f"  \n:blue[Needed by: {needed_by([k])}]" if needed_by([k]) else "")
                                + (f"  \n:red[Blocked by {', '.join(label(b) for b in f.blocked_by)}]"
                                   if f.blocked_by else ""))
    with tabs[3]:
        st.caption("Root causes for this deal, and the chain from each requirement down to it.")
        for b in P["blockers"]:
            mine = [x for x in b["reqs"] if x.startswith(deal + ":")]
            if not mine:
                continue
            f = S.facts[b["fact"]]
            st.markdown(f"{badge(f.status)} **{label(b['fact'])}** holds up "
                        f"{plural(len(mine), 'requirement')}: " + ", ".join(req_by_id(x).title for x in mine)
                        + f"  \n:gray[{f.reason}]")
            for c in b["chains"]:
                if c.lower().startswith(deal):
                    st.caption(f"↳ {c}")
    with tabs[4]:
        st.caption("Ranked by how many answers each step would unlock if it succeeded, then by how many "
                   "requirements it unblocks.")
        for a in P["actions"]:
            if deal in a.deals:
                imp = impact_text(a)
                st.markdown(f"**{a.who}**: {a.title}, unblocks {a.unblocks}"
                            + (f". :green[{imp}]" if imp else "")
                            + (f". Waiting since {a.waiting_since}" if a.waiting_since else "")
                            + (" :red-badge[overdue]" if a.overdue else "")
                            + f"  \n:gray[{a.detail}]")
    with tabs[5]:
        fact_detail(deal)
    with tabs[6]:
        rounds(deal)


def fact_detail(deal: str):
    """One question: every statement about it, and whether it is settled as a fact."""
    main, key = st.columns([2.2, 1], gap="large")
    with key:
        status_key()
    with main:
        keys = [k for k in S.facts if k.startswith(deal + ".")] + \
               [k for k in S.facts if k.startswith("company.")]
        k = st.selectbox("Question", keys, key=f"fact-{deal}",
                         format_func=lambda x: f"{label(x)} ({nice(S.facts[x].status)})")
        f = S.facts[k]
        st.subheader(label(k))
        st.markdown(f"{badge(f.status)} **{f.value or 'No value yet'}**  \n{f.reason}")
        owner = S.policy["team_owner"].get(f.spec["owner"], f.spec["owner"])
        settles = {"document": "a document in Vanta", "customer": "the customer saying it",
                   "owner_confirmation": f"the owner ({owner}, {f.spec['owner']}) saying it"}.get(
            f.spec["proof"], f.spec["proof"].replace("_", " "))
        st.markdown(f"*{f.spec['question']}*  \nSettled by **{settles}**"
                    + (f", no older than {f.spec['max_age_days']} days" if f.spec.get("max_age_days") else "")
                    + ".")
        for w in f.warnings:
            st.warning(w)
        if f.blocked_by:
            st.error("Blocked by " + ", ".join(label(b) for b in f.blocked_by))
        for fl in S.draft_flags:
            if fl["fact"] == k:
                st.error(fl["text"])
        rows = []
        for c in f.claims + f.hints:
            src = S.sources[c.source_id]
            rows.append({"Best": "yes" if f.winner and c.id == f.winner.id else "",
                         "Source": trust(c, src), "Says": c.value, "Quote": c.quote, "Kind": c.assertion,
                         "Said by": c.speaker or "", "Date": str(c.eff_date or ""),
                         "From": src.title})
        st.markdown("**Every statement about it**")
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.caption(f"No source mentions this yet. Ask {f.spec['owner']}.")


# ---------------- Approvals ----------------

def approvals_page():
    st.title("Approvals")
    st.caption("The model drafts from facts only. A sentence sent to the customer may state only verified "
               "facts; anything else becomes a pending line naming the owner. Code checks every citation.")
    c1, c2 = st.columns([1, 2])
    deal = c1.selectbox("Deal", list(DN), format_func=DN.get, key="ap-deal")
    rs = [r for r in S.reqs[deal] if r.status not in ("missing_text", "umbrella") and r.facts]
    rs.sort(key=lambda r: (r.status != "ready", r.id))
    by_id = {r.id: r for r in rs}
    if st.session_state.get("ap-req") not in by_id:
        st.session_state.pop("ap-req", None)
    rid = c2.selectbox("Requirement", list(by_id), key="ap-req",
                       format_func=lambda i: f"{i.split(':')[1]} {by_id[i].title} ({nice(by_id[i].status)})")
    r = by_id[rid]
    drafts = st.session_state.setdefault("drafts", {})
    st.markdown(f"{badge(r.status)} {r.ask}")
    if r.reading:
        st.info(f"How {DN[deal]} reads it: {r.reading}")
    if st.button("Draft and check an answer", type="primary"):
        with st.spinner("Drafting from facts, then checking every sentence against its citations."):
            try:
                drafts[r.id] = drafting.draft(r, S)
            except llm.LLMError as e:
                st.error(f"Could not draft this answer: {e}. Drafting needs the model: set "
                         "OPENROUTER_API_KEY in .env and try again.")
            except Exception as e:  # noqa: BLE001 - show a message, never a traceback
                st.error(f"Drafting failed ({type(e).__name__}: {e}). Drafting needs the model: check "
                         "OPENROUTER_API_KEY in .env and try again.")
    d = drafts.get(r.id)
    if not d:
        return
    approval_card(d, deal, r)


def approval_card(d, deal, r):
    claims = {c.id: c for c in S.claims}
    per = d["checks"]["per_sentence"]
    bad = sum(1 for fl in per if fl)
    pend = sum(1 for s in d["sentences"] if s["kind"] == "pending")
    with st.container(border=True):
        st.subheader(f"Draft answer to {r.id.split(':')[1]}: {r.title}")
        st.caption(f"One answer for {DN[deal]}, written by the model and shown one sentence at a time. Under each "
                   "sentence is its proof: what kind of source said it, the file, and the exact quote. "
                   "Approving records it as sent; you paste it into the questionnaire or reply yourself.")
        verdict = ("Ready to approve." if d["checks"]["can_approve"] else
                   "Cannot be approved until the red problems are fixed.")
        st.markdown(f"{plural(len(d['sentences']), 'sentence')}: {pend} pending, {bad} with a problem. "
                    f"**{verdict}**  \n:gray[Version {d['version']}]")
        out = []
        for i, s in enumerate(d["sentences"]):
            flags = per[i]
            c = HEX["red"] if flags else HEX["orange"] if s["kind"] == "pending" else HEX["green"]
            text = esc(s["text"])
            body = f'<p class="pn-pending">{text}</p>' if s["kind"] == "pending" else f"<p>{text}</p>"
            for cid in s["claim_ids"]:
                cl = claims.get(cid)
                if cl:
                    src = S.sources[cl.source_id]
                    body += (f'<div class="pn-cite">Proof: <b>{esc(trust(cl, src))}</b> in '
                             f'{esc(src.title.split("/")[-1])}: <q>{esc(cl.quote)}</q></div>')
                else:
                    body += f'<div class="pn-flag">Cites {esc(cid)}, which does not exist</div>'
            if s["kind"] == "pending":
                body += '<div class="pn-cite">Pending line: names the owner, states no value.</div>'
            for fl in flags:
                body += f'<div class="pn-flag">Checker: {esc(fl)}</div>'
            out.append(f'<div class="pn-sent" style="--c:{c}">{body}</div>')
        st.html("".join(out))
        for e in d["checks"]["errors"]:
            st.error(e)
        for w in d["checks"]["warnings"]:
            st.warning(w)
        sent = [e for e in S.ledger if e["type"] == "sent" and e["req"] == r.id]
        now = {k: {"value": S.facts[k].value, "status": S.facts[k].status} for k in d["facts_used"] if k in S.facts}
        stale = now != d["facts_used"]
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        if c1.button(f"Approve and mark as sent to {DN[deal]}", disabled=stale or not d["checks"]["can_approve"],
                     type="primary"):
            ledger.append({"type": "sent", "deal": deal, "req": r.id, "version": d["version"],
                           "text": d["text"], "facts": d["facts_used"], "date": str(S.as_of)})
            rebuild(f"Sent {r.title} to {DN[deal]}")
            st.rerun()
        if stale:
            c2.error("Facts changed since this draft, redraft.")
        elif not d["checks"]["can_approve"]:
            c2.caption("Approval stays off until the checker's red problems are fixed.")
        for e in sent:
            st.caption(f"Already sent on {e['date']} (version {e['version']}): {e['text']}")
    if d["internal_notes"]:
        with st.expander("Internal notes, for you only and never sent", expanded=True):
            for n in d["internal_notes"]:
                st.markdown(f"- {n}")


def rounds(deal: str):
    """The round diff: each later-round question matched by meaning, not by number."""
    st.caption("Each question in a later round is matched to existing requirements by meaning, not by number.")
    recs = S.rounds.get(deal, [])
    if not recs:
        st.caption("Only round 1 so far. Add a new round on the Add input screen.")
        return
    counts = Counter(x["relation"] for x in recs)
    st.markdown(", ".join(f"**{v}** {k.replace('_', ' ')}" for k, v in counts.items()))
    for x in recs:
        r = req_by_id(x["requirement_id"])
        rel = x["relation"]
        color = "green" if rel == "repeat" else "blue" if rel == "new" else "orange"
        st.markdown(f"**{x['qid']}** :{color}-badge[{rel.replace('_', ' ')}] "
                    f"matches **{r.title if r else x['requirement_id']}**  \n"
                    f":gray[\"{x['question']}\"]"
                    + (f"  \nNew or changed: {x['added_part']}" if x["added_part"] else "")
                    + (f"  \n:red[Warning: {x['reused_number']}]" if x.get("reused_number") else ""))


# ---------------- Add input ----------------

def add_page():
    st.title("Add input")
    st.caption("Connectors are mocked. Paste anything: an email, a Slack thread, a document, evidence, or a new "
               "questionnaire round. It is stored as a new source, the engine rebuilds, and the top of the "
               "screen shows what changed.")
    st.header("Demo inputs")
    for p in sorted(SAMPLES.glob("*.json")):
        d = json.loads(p.read_text())
        added = (INBOX / p.name).exists()
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        meta = d["system"].capitalize() + (f" from {d['author']}" if d.get("author") else "") \
            + (f", {d['date']}" if d.get("date") else "")
        c1.markdown(f"**{d['title']}**  \n{meta}  \n"
                    f":gray[{d['text'][:160]}{'...' if len(d['text']) > 160 else ''}]")
        if c2.button("Added" if added else "Add and rebuild", key=p.name, disabled=added,
                     width="stretch"):
            shutil.copy(p, INBOX / p.name)
            rebuild(f"Added: {d['title']}")
            st.rerun()
    st.header("Your own input")
    with st.form("add"):
        c1, c2, c3 = st.columns(3)
        deal = c1.selectbox("About", ["company"] + list(DN),
                            format_func=lambda x: "Whole company" if x == "company" else DN[x])
        system = c2.selectbox("Arrived as", ["email", "slack", "drive", "notes", "questionnaire"])
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


{"Monday briefing": briefing, "Deal": deal_page, "Approvals": approvals_page, "Add input": add_page}[page]()
