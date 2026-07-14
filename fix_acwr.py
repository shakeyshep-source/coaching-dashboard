import re

path = "/home/shakeyshep/coaching-dashboard/index.html"
with open(path) as f:
    content = f.read()

new_entry = '{date:"2026-07-07",name:"4x1km Reps",type:"Quality",effort:75,km:8.0,pace:217,cadence:85.0},'

pattern = r'\{date:"2026-06-29",name:"Afternoon Run".*?\},'
match = re.search(pattern, content)
if match:
    content = content[:match.end()] + '\n  ' + new_entry + content[match.end():]
    with open(path, 'w') as f:
        f.write(content)
    print("Inserted after run entry")
else:
    print("Still not found - pattern needs adjusting")
