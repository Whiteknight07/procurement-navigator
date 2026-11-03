import sys, shutil, json, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
from pathlib import Path
from pn import engine, ledger, drafting
from pn.planner import label
INBOX = Path('state/inbox'); SAMPLES = Path('data/simulated/inputs')
for p in INBOX.glob('*.json'): p.unlink()
ledger.reset()
def snap(s): return {k: (f.status, f.value) for k, f in s.facts.items()}
t=time.time(); s = engine.build(); print('base build', round(time.time()-t,1), 'errors', s.errors, 'dropped', s.dropped)
for d, rs in s.reqs.items(): print(d, {st: sum(1 for r in rs if r.status==st) for st in set(r.status for r in rs)})
# draft + send SOC2 for acme
r = next(r for r in s.reqs['acme'] if 'soc2' in r.facts and r.status=='ready')
d = drafting.draft(r, s)
print('\nDRAFT', r.id, r.title); [print('  ', x['kind'], x['text'], x['claim_ids']) for x in d['sentences']]
print('  notes', d['internal_notes']); print('  checks', d['checks']['errors'], d['checks']['warnings'])
ledger.append({"type":"sent","deal":"acme","req":r.id,"version":d['version'],"text":d['text'],"facts":d['facts_used'],"date":str(s.as_of)})
prev = snap(s)
for p in sorted(SAMPLES.glob('*.json')):
    shutil.copy(p, INBOX / p.name)
    t=time.time(); s = engine.build(); 
    now = snap(s)
    print(f'\n### ADDED {p.name} ({round(time.time()-t,1)}s) errors={s.errors} dropped={s.dropped}')
    for k in now:
        if now[k] != prev.get(k): print(f'   {k}: {prev.get(k)} -> {now[k]}  | {s.facts[k].reason[:120]}')
    if s.rounds.get('acme') and p.name.startswith('01'):
        for x in s.rounds['acme']: print('   ROUND2', x['qid'], x['relation'], x['requirement_id'], '|', x['added_part'][:80], '|', x.get('reused_number'))
    for u in s.plan['updates']: print('   UPDATE', u['kind'], u['req'], [(c['fact'], c['was']['status'], c['now']['status']) for c in u['changes']])
    prev = now
print('\nFINAL TOP ACTIONS')
for a in s.plan['actions'][:12]: print(f"  {a.kind:16} {a.who:22} unblocks {a.unblocks:2} | {a.title}")
print('BLOCKERS', [(b['fact'], len(b['reqs'])) for b in s.plan['blockers'][:6]])
