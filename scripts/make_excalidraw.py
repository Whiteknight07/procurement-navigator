"""Write docs/architecture.excalidraw: the architecture as an editable Excalidraw scene."""
import json
import random
from pathlib import Path

random.seed(7)
EL = []
COL = {"model": ("#6741d9", "#e5dbff"), "code": ("#1971c2", "#d0ebff"), "jai": ("#2f9e44", "#d3f9d8"),
       "src": ("#495057", "#f1f3f5"), "hold": ("#343a40", "#fff9db"), "rule": ("#343a40", "#ffffff"),
       "note": ("#495057", "transparent")}


def base(kind, x, y, w, h, stroke, bg="transparent", **kw):
    e = {"id": f"e{len(EL)}", "type": kind, "x": x, "y": y, "width": w, "height": h, "angle": 0,
         "strokeColor": stroke, "backgroundColor": bg, "fillStyle": "solid", "strokeWidth": 2,
         "strokeStyle": "solid", "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
         "roundness": {"type": 3} if kind == "rectangle" else None, "seed": random.randint(1, 2**31),
         "version": 1, "versionNonce": random.randint(1, 2**31), "isDeleted": False, "boundElements": [],
         "updated": 1, "link": None, "locked": False}
    e.update(kw)
    EL.append(e)
    return e


def text(x, y, s, size=18, color="#1e1e1e", align="left", w=None, container=None, family=2):
    lines = s.split("\n")
    tw = w or max(len(l) for l in lines) * size * 0.53 + 4
    th = len(lines) * size * 1.25
    return base("text", x, y, tw, th, color, text=s, originalText=s, fontSize=size, fontFamily=family,
                textAlign=align, verticalAlign="middle" if container else "top",
                containerId=container, lineHeight=1.25, autoResize=True)


def box(x, y, w, h, kind, s, size=20, dashed=False):
    stroke, bg = COL[kind]
    r = base("rectangle", x, y, w, h, stroke, bg, strokeStyle="dashed" if dashed else "solid")
    lines = s.split("\n")
    th = len(lines) * size * 1.25
    t = text(x + 10, y + (h - th) / 2, s, size, align="center", w=w - 20, container=r["id"])
    r["boundElements"].append({"type": "text", "id": t["id"]})
    return r


def arrow(pts, label=None, dashed=False, lx=None, ly=None, color="#495057"):
    x0, y0 = pts[0]
    rel = [[px - x0, py - y0] for px, py in pts]
    xs, ys = [p[0] for p in rel], [p[1] for p in rel]
    base("arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys), color, points=rel,
         strokeStyle="dashed" if dashed else "solid", startBinding=None, endBinding=None,
         startArrowhead=None, endArrowhead="arrow", lastCommittedPoint=None, elbowed=False)
    if label:
        text(lx if lx is not None else x0 + 8, ly if ly is not None else y0 - 26, label, 16, "#495057")


def note(x, y, s, w=300):
    text(x, y, s, 16, "#495057", w=w)


# title and legend
text(0, -40, "Procurement Navigator: architecture", 40, family=1)
text(0, 15, "One rebuild, left to right. Top lane: what the customer asks. Bottom lane: what sources say is true.\n"
     "They meet at the planner. Colour says who decides.", 20, "#495057")
for i, (k, lab) in enumerate([("src", "Input, as it arrived"), ("model", "Model reads, code checks"),
                              ("code", "Code decides"), ("jai", "Jai decides"), ("hold", "Data the system holds")]):
    s, b = COL[k]
    base("rectangle", i * 300, 110, 28, 28, s, b)
    text(i * 300 + 40, 112, lab, 18)
base("rectangle", 1500, 110, 28, 28, "#343a40", "#ffffff", strokeStyle="dashed")
text(1540, 112, "Jai's rules (config)", 18)

LA, LS = 300, 680          # ask lane, statement lane
# lane labels
text(-20, LA - 60, "ASK LANE: what the customer wants answered", 18, "#868e96")
text(-20, LS - 60, "STATEMENT LANE: what sources say is true", 18, "#868e96")

# inputs
box(0, LA, 300, 120, "src", "Questionnaires\ncustomer spreadsheets,\none row per question")
box(0, LS, 300, 160, "src", "Everything else\n(20 sources)\ncustomer emails, Slack, Drive,\nVanta docs, notes, directory")
# read
box(400, LA, 300, 120, "code", "2a  Read rows\nCODE\none row = one ask")
box(400, LS, 300, 160, "model", "2b  Extract\nMODEL, then code checks\none call per source:\nstatements + asks")
note(400, LA - 130, "Why code? A spreadsheet already has an id\nand a question per row. A model could only\nlose or reword a row.")
note(400, LS + 175, "Why a model? Emails and Slack are prose.\nCode then checks every quote is really in\nthe source, and every question is in the\ncatalog. Anything else is dropped.", 320)
# holds
box(800, LA - 10, 300, 140, "hold", "ASKS: 184\n120 questionnaire rows\n30 from customer emails\n34 predicted")
box(800, LS, 300, 160, "hold", "STATEMENTS: 67\nwhat one source said\nabout one question,\nwith the exact quote")
note(800, LA - 130, "An ask = one thing the customer asked\nfor, exactly as they said it, before any\nmerging. The same need can arrive 3 times.")
note(800, LS + 175, "Never overwritten: four statements can\ndisagree about one question and all four\nstay. That is how conflicts show up.")
# judge
box(1200, LA - 10, 330, 140, "model", "3  Merge asks\nMODEL, then code checks\nsame need = one requirement,\nlinked to its questions")
box(1200, LS, 330, 160, "code", "4  Compare + resolve\nCODE decides\n(model only labels pairs:\nagree / conflict / adds detail)")
note(1200, LA - 130, "Why a separate call? 'Are these the same\nneed?' needs every ask of the deal at once.\nCode rejects any id the model was not shown.", 340)
note(1200, LS + 175, "Rules, in order: whose word counts (document >\nowner > plan/chat; hints never count), what\nsettles this question, is it out of date?\nEach question gets one status and a reason.", 360)
# results
box(1630, LA - 10, 310, 140, "hold", "REQUIREMENTS: 68\nthe customer's questions,\nin their words")
box(1630, LS, 310, 160, "hold", "QUESTIONS: 52\nour questions, from the catalog\n\n5 are FACTS (settled)\n47 show why not yet")
note(1630, LA - 130, "e.g. Acme Q-004 'Retention by data type'\nneeds 4 of our questions: app logs,\ntraces, prompts, source files.", 320)
note(1630, LS + 175, "A fact = a question the right person or\ndocument has settled. Not stored: worked\nout again on every rebuild.", 320)
arrow([[1785, LA + 132], [1785, LS - 4]], "needs", dashed=True, lx=1795, ly=(LA + LS) / 2)

# planner
box(2060, LA + 60, 360, 400, "code", "5  Planner\nCODE\n\nFor each requirement:\nready / open / blocked\n\nOne action per unsettled\nquestion, only where work\ncan start today\n\nRanked by answers unblocked\nOne message per person\nUpdates owed (from the ledger)", 19)
note(2460, LA + 70, "Why it exists: knowing what is unsettled\nis a list of 47 problems. The planner says\nwho to message first and which customer\nanswers each item unblocks.\n\nExample: 'Elena: ask about Acme residency,\nunblocks 6. Fallback, providers and\ntransfers cannot move until this is settled.'", 380)
box(2060, LA + 520, 360, 90, "rule", "App (Streamlit)\nBriefing, Deal, Approvals", 19)

# answer loop
AY = 1080
box(2060, AY, 360, 140, "model", "6  Draft + check\nMODEL, then code checks\nfrom facts only;\nevery sentence cites its quote")
box(1630, AY, 310, 140, "jai", "7  Jai approves\nreads each sentence\nnext to its proof.\nNothing sent without this.")
box(1200, AY, 330, 140, "hold", "LEDGER\nwhat was sent, when,\nand the facts it used")
note(1200, AY + 155, "Read back by the planner on every rebuild:\nif a fact behind a sent answer changes,\nit says 'update owed'.", 340)

# rules and cache
box(400, AY, 700, 140, "rule", "JAI'S RULES  (config/catalog.yaml, policy.yaml)\n52 questions, each with: owner, what settles it\n(a document, the owner, or the customer), max age,\nwhat it depends on", 19, dashed=True)
box(0, AY, 300, 140, "rule", "MODEL CACHE\nevery model answer (2b, 3, 4, 6)\nsaved by its exact prompt:\nsame input, same answer", 18, dashed=True)

# arrows: lanes
arrow([[302, LA + 60], [396, LA + 60]])
arrow([[302, LS + 80], [396, LS + 80]])
arrow([[702, LA + 60], [796, LA + 60]], "120", lx=735, ly=LA + 30)
arrow([[702, LS + 30], [750, LS + 30], [750, LA + 110], [796, LA + 110]],
      "64 asks from\ncustomer emails\n+ predicted", lx=762, ly=LA + 150)
arrow([[702, LS + 100], [796, LS + 100]], "55", lx=735, ly=LS + 70)
arrow([[550, LA + 122], [550, LA + 290], [1000, LA + 290], [1000, LS - 4]],
      "12 typed-in draft\nanswers, as hints", dashed=True, lx=410, ly=LA + 150)
arrow([[1102, LA + 60], [1196, LA + 60]])
arrow([[1102, LS + 80], [1196, LS + 80]])
arrow([[1532, LA + 60], [1626, LA + 60]])
arrow([[1532, LS + 80], [1626, LS + 80]])
arrow([[1942, LA + 60], [2000, LA + 60], [2000, LA + 160], [2056, LA + 160]])
arrow([[1942, LS + 80], [2000, LS + 80], [2000, LA + 380], [2056, LA + 380]])
arrow([[2240, LA + 462], [2240, LA + 516]])
arrow([[2240, LA + 612], [2240, AY - 4]], "Jai asks for a draft\nof a ready answer", lx=2255, ly=LA + 660)
arrow([[2058, AY + 70], [1944, AY + 70]])
arrow([[1628, AY + 70], [1534, AY + 70]])
arrow([[1365, AY + 142], [1365, AY + 260], [2560, AY + 260], [2560, LA + 300], [2424, LA + 300]],
      "what was sent + facts used", lx=2000, ly=AY + 230)
# rules feed
arrow([[550, AY - 2], [550, LS + 162]], dashed=True)
arrow([[900, AY - 2], [900, AY - 40], [1160, AY - 40], [1160, LS + 80], [1196, LS + 80]], dashed=True)
text(905, AY - 70, "questions + rules feed extract, merge and resolve", 16, "#495057")

doc = {"type": "excalidraw", "version": 2, "source": "procurement-navigator",
       "elements": EL, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}
out = Path(__file__).resolve().parent.parent / "docs" / "architecture.excalidraw"
out.write_text(json.dumps(doc, indent=1))
print(out, len(EL), "elements")
