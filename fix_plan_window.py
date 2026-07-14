import re

path = "/home/shakeyshep/coaching-dashboard/index.html"
with open(path) as f:
    content = f.read()

# 1) Insert date-range filtering logic just before the render line
anchor = "// Training plan\n  document.getElementById('plan-weeks').innerHTML=PLAN.map(w=>"
pattern = re.compile(r"//\s*Training plan\s*\n\s*document\.getElementById\('plan-weeks'\)\.innerHTML\s*=\s*PLAN\.map\(w=>")
match = pattern.search(content)

if match:
    injection = """// Training plan
  const WEEK_RANGES=[{start:'2026-07-01',end:'2026-07-06'},{start:'2026-07-07',end:'2026-07-13'},{start:'2026-07-14',end:'2026-07-22'}];
  const _today=new Date(); _today.setHours(0,0,0,0);
  const PLAN_VISIBLE=PLAN.map((w,i)=>{
    const r=WEEK_RANGES[i]||{start:'2000-01-01',end:'2000-01-01'};
    const s=new Date(r.start), e=new Date(r.end); e.setHours(23,59,59,999);
    return {...w, _isCurrent:(_today>=s&&_today<=e), _isPast:(_today>e)};
  }).filter(w=>!w._isPast);
  document.getElementById('plan-weeks').innerHTML=PLAN_VISIBLE.map(w=>"""
    content = content[:match.start()] + injection + content[match.end():]

    # 2) Add a "CURRENT WEEK" marker into the week-title line
    old_title = '<div><div class="week-title">${w.week}</div><div class="week-type" style="color:${w.color}">${w.type}</div></div>'
    new_title = '<div><div class="week-title">${w.week}${w._isCurrent?\' <span style="color:#4ade80;font-size:11px;font-weight:700">● CURRENT</span>\':\'\'}</div><div class="week-type" style="color:${w.color}">${w.type}</div></div>'
    if old_title in content:
        content = content.replace(old_title, new_title, 1)
        title_ok = True
    else:
        title_ok = False

    with open(path, 'w') as f:
        f.write(content)
    print("Injected date-filter logic: OK")
    print("Added CURRENT WEEK badge:", title_ok)
else:
    print("Anchor not found - need to check exact whitespace/format")
