import sys, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
from pn import engine, planner
t = time.time()
st = engine.build()
print('build secs', round(time.time() - t, 1))
print('errors', st.errors); print('dropped', st.dropped)
for k, f in st.facts.items():
    print(f"{k:42} {f.status:18} {str(f.value)[:45]:45} | {f.reason[:110]} {'| BLOCKED '+','.join(f.blocked_by) if f.blocked_by else ''}")
print()
for fl in st.draft_flags: print('FLAG', fl)
for d, rs in st.reqs.items():
    print('\n#', d)
    for r in rs: print(' ', r.id, r.status, r.track, '|', r.title, '|', r.facts, '| asks', len(r.ask_ids))
    for t in st.timeline[d]: print('  TIMELINE', t.text, t.date_text)
    for t in st.instructions[d]: print('  INSTR', t.text)
p = st.plan
print('\nACTIONS')
for a in p['actions']: print(f"  {a.kind:16} {a.who:22} unblocks {a.unblocks:2} | {a.title} | {a.deals}")
print('\nBLOCKERS')
for b in p['blockers'][:8]: print('  ', b['fact'], len(b['reqs']), b['chains'][:3])
print('\nDELAYS'); [print('  ', d) for d in p['delays']]
