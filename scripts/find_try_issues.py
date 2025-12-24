import re
from pathlib import Path
p = Path('main.py')
text = p.read_text(encoding='utf-8', errors='replace')
lines = text.splitlines()

def leading_spaces(s):
    return len(s) - len(s.lstrip(' '))

try_positions = []
for i, line in enumerate(lines, start=1):
    if re.match(r'\s*try\s*:\s*$', line):
        indent = leading_spaces(line)
        try_positions.append((i, indent))

issues = []
for (lineno, indent) in try_positions:
    found = False
    for j in range(lineno+1, min(lineno+400, len(lines))):
        l = lines[j-1]
        # if we hit a line that's less indented than try and starts a new block (def/class/async def/return) before finding except/finally, it's an error
        if re.match(r'\s*except\b', l) and leading_spaces(l) == indent:
            found = True
            break
        if re.match(r'\s*finally\b', l) and leading_spaces(l) == indent:
            found = True
            break
        # If we encounter another top-level try at same indent, but no except/finally found yet -> issue
        if re.match(r'\s*try\s*:\s*$', l) and leading_spaces(l) <= indent:
            break
        # if we see 'async def' or 'def' at indentation <= indent, we probably left block
        if re.match(r'\s*(async\s+def|def|class|return|if|for|while|with)\b', l) and leading_spaces(l) <= indent and not re.match(r'\s*(except|finally)\b', l):
            break
    if not found:
        issues.append((lineno, indent))

print('Found try positions:', len(try_positions))
print('Potential problem try positions (no matching except/finally found nearby):')
for ln, ind in issues:
    print(f'Line {ln}, indent {ind}:', lines[ln-1].strip())
