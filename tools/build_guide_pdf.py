# Usage : python3 build_guide_pdf.py <guide.md> <sortie.html> ; puis Chromium (Playwright) : page.pdf(format A4, printBackground, preferCSSPageSize).
# Prérequis : pip install markdown ; polices Inter et DejaVu Sans Mono.
# Guide GLBGenerator (Markdown) → HTML mis en page « manuel coloré » → PDF (Chromium).
import html
import re
import sys

import markdown

SRC, OUT = sys.argv[1], sys.argv[2]
text = open(SRC, encoding="utf-8").read()

# --- Intro (avant le §1) : versions et conventions pour la page « À propos » ---
head, body = text.split("\n## 1.", 1)
body = "## 1." + body
version_line = re.search(r"^Version de l'addon : (.+)$", head, re.M).group(1)
intro_md = head.split("\n", 1)[1]
intro_md = re.sub(r"^Version de l'addon : .+$", "", intro_md, flags=re.M).replace("\n---\n", "\n")


def fix_indent(md):
    """Python-Markdown imbrique à 4 espaces ; le guide indente à 3 sous les listes numérotées."""
    out, fence = [], False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence:
            m = re.match(r"^( +)(\S.*)$", line)
            if m and len(m.group(1)) in (3, 5, 6):
                n = len(m.group(1))
                line = " " * {3: 4, 5: 6, 6: 8}[n] + m.group(2)
        out.append(line)
    return "\n".join(out)


body = body.replace("\n---\n", "\n")
# §17.2 : tableau des rôles et son paragraphe sortis de l'élément 3 (tableau impossible dans une liste en Python-Markdown) ;
# la liste reprend à 4 (sane_lists respecte le numéro de départ).
m = re.search(r"(?ms)^   \| Rôle \| Joueur.*?(?=^4\. \*\*Capsule)", body)
block = "\n".join(l[3:] if l.startswith("   ") else l for l in m.group(0).split("\n"))
body = body[:m.start()] + "\n" + block.rstrip() + "\n\n" + body[m.end():]
body = fix_indent(body)

# --- Parties et couleurs (le numéro des sections du guide est conservé) ---
PARTS = [
    ("Prendre en main", "#2563eb", "#dbeafe", range(1, 6)),
    ("Scènes du livre", "#ea580c", "#ffedd5", range(6, 14)),
    ("Dépannage", "#dc2626", "#fee2e2", range(14, 17)),
    ("Mini-jeu", "#059669", "#d1fae5", range(17, 18)),
    ("Aide-mémoire", "#7c3aed", "#ede9fe", range(18, 19)),
]


def part_of(n):
    for i, (name, c, light, rng) in enumerate(PARTS):
        if n in rng:
            return i, name, c, light
    return 0, *PARTS[0][:3]


md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
sections = re.split(r"(?m)^## ", body)[1:]

toc_parts = {}
chunks = []
for sec in sections:
    title, rest = sec.split("\n", 1)
    num = int(title.split(".", 1)[0])
    label = title.split(".", 1)[1].strip()
    pi, pname, color, light = part_of(num)
    toc_parts.setdefault(pi, []).append((num, label))
    md.reset()
    inner = md.convert(rest)
    first_of_part = num == PARTS[pi][3][0]
    divider = ""
    if first_of_part:
        divider = (f'<div class="part-divider" style="--c:{color};--l:{light}">'
                   f'<div class="part-kicker">Partie {pi + 1}</div><div class="part-name">{pname}</div>'
                   f'<div class="part-list">' + "".join(
                       f'<span>{n}</span>' for n in PARTS[pi][3]) + '</div></div>')
    title_html = md.convert(label)[3:-4]  # code inline éventuel
    chunks.append(
        f'<section class="chapter{" newpart" if first_of_part else ""}" id="s{num}" style="--c:{color};--l:{light}">'
        f'{divider}<h2><span class="num">{num}</span><span class="t">{title_html}</span></h2>{inner}</section>')

content = "\n".join(chunks)

# --- Retouches du HTML ---
# Sous-titres « 9.1 Le principe » : numéro en pastille.
content = re.sub(r"<h3>(\d+\.\d+) (.+?)</h3>", r'<h3><span class="snum">\1</span>\2</h3>', content)
# Cases à cocher de l'aide-mémoire.
content = re.sub(r"<li>\[ \] ", '<li class="check"><span class="box"></span>', content)
content = content.replace('<ul>\n<li class="check">', '<ul class="checklist">\n<li class="check">')
# Niveaux du tableau des messages (§14) : E, A, I, E / A, I / E…
LEVEL = {"E": ("err", "Erreur"), "A": ("warn", "Avert."), "I": ("info", "Info")}


def chips(m):
    parts = [p.strip() for p in m.group(1).split("/")]
    if not all(p in LEVEL for p in parts):
        return m.group(0)
    return "<td>" + "".join(f'<span class="chip {LEVEL[p][0]}">{p}</span>' for p in parts) + "</td>"


content = re.sub(r"<td>([EAI](?: / [EAI])*)</td>", chips, content)
# Numéro de contrôle en tête de ligne du §14 : pastille.
content = re.sub(r"<tr>\n<td>(\d+)</td>", r'<tr>\n<td><span class="code">\1</span></td>', content)
# Mises en garde.
content = re.sub(r"<p><strong>(Attention|Jamais de nom par défaut)</strong>",
                 r'<p class="callout warn"><strong>\1</strong>', content)
content = re.sub(r"<li>(.*?)<strong>Attention</strong>", r'<li>\1<strong class="attn">Attention</strong>', content)
content = content.replace("<p><strong>Toujours exporter par ce bouton</strong>",
                          '<p class="callout tip"><strong>Toujours exporter par ce bouton</strong>')
content = content.replace("<p><strong>En cas de doute, choisis Pistes NLA</strong>",
                          '<p class="callout tip"><strong>En cas de doute, choisis Pistes NLA</strong>')
# Questions du §15 : cartes.
content = re.sub(r'<p><strong>([^<]*?[.?])</strong>', r'<p class="faq-q"><strong>\1</strong>', content)

md.reset()
intro_html = md.convert(fix_indent(intro_md))

toc = []
for pi, items in sorted(toc_parts.items()):
    name, color, light, _ = PARTS[pi]
    rows = "".join(f'<a class="toc-row" href="#s{n}"><span class="toc-n">{n}</span><span>{html.escape(re.sub(r"`", "", l))}</span></a>'
                   for n, l in items)
    toc.append(f'<div class="toc-part" style="--c:{color};--l:{light}"><div class="toc-head">'
               f'<span>Partie {pi + 1}</span>{name}</div>{rows}</div>')

ver = markdown.markdown(version_line)[3:-4]
date = re.search(r"\d\d/\d\d/\d{4}", version_line).group(0)

page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Guide GLB Generator</title>
<style>
@page {{ size: A4; margin: 18mm 16mm 20mm 16mm; }}
@page :first {{ margin: 0; }}
:root {{ --ink:#1f2433; --muted:#5b6275; --line:#e4e7ef; --paper:#ffffff; }}
* {{ box-sizing: border-box; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin:0; font-family:'Inter',sans-serif; font-size:9.1pt; line-height:1.45; color:var(--ink); background:var(--paper); }}
code {{ font-family:'DejaVu Sans Mono',monospace; font-size:.86em; background:#f1f3f8; border:1px solid #e3e6ee;
        padding:.05em .35em; border-radius:4px; color:#2d3550; }}
strong {{ font-weight:650; }}
a {{ color:inherit; text-decoration:none; }}

/* Couverture */
.cover {{ height:297mm; width:210mm; position:relative; overflow:hidden; background:#151a2e; color:#fff; page-break-after:always; }}
.cover .band {{ position:absolute; left:0; right:0; height:22mm; }}
.cover .shape {{ position:absolute; border-radius:50%; opacity:.95; }}
.cover .title {{ position:absolute; left:20mm; top:86mm; right:20mm; }}
.cover .kicker {{ font-family:'Inter Display'; font-weight:600; letter-spacing:.18em; text-transform:uppercase; font-size:10pt; color:#9fb3ff; }}
.cover h1 {{ font-family:'Inter Display'; font-weight:800; font-size:54pt; line-height:1; margin:6mm 0 4mm; letter-spacing:-.02em; }}
.cover .sub {{ font-size:15pt; color:#d7dcf2; font-weight:400; }}
.cover .meta {{ position:absolute; left:20mm; bottom:24mm; display:flex; gap:4mm; flex-wrap:wrap; }}
.cover .pill {{ border:1.5px solid rgba(255,255,255,.35); border-radius:999px; padding:2mm 4.5mm; font-size:9.5pt; font-weight:500; }}
.cover .brand {{ position:absolute; right:20mm; bottom:24mm; text-align:right; font-size:9pt; color:#aab3d6; line-height:1.4; }}
.cover .brand b {{ color:#fff; font-family:'Inter Display'; font-size:12pt; font-weight:700; display:block; }}

/* À propos + sommaire */
.front {{ page-break-after:always; }}
.front h2.plain {{ font-family:'Inter Display'; font-size:20pt; font-weight:800; margin:0 0 3mm; letter-spacing:-.01em; }}
.about {{ background:#f6f7fb; border:1px solid var(--line); border-radius:4mm; padding:4mm 5mm; margin-bottom:6mm; font-size:8.8pt; }}
.about .ver {{ display:inline-block; background:#151a2e; color:#fff; border-radius:999px; padding:1mm 3.5mm; font-size:8.8pt; margin-bottom:2mm; }}
.about .ver code {{ background:transparent; border:0; color:#fff; }}
.about p {{ margin:1.6mm 0; }}
.toc {{ columns:2; column-gap:7mm; }}
.toc-part {{ break-inside:avoid; margin-bottom:4mm; border-radius:3.5mm; overflow:hidden; border:1px solid var(--line); }}
.toc-head {{ background:var(--c); color:#fff; font-family:'Inter Display'; font-weight:700; font-size:12pt; padding:2.6mm 4mm; }}
.toc-head span {{ display:block; font-size:7.5pt; letter-spacing:.14em; text-transform:uppercase; opacity:.8; font-weight:600; }}
.toc-row {{ display:flex; gap:3mm; align-items:baseline; padding:1.05mm 4mm; border-top:1px solid var(--line); font-size:9.2pt; }}
.toc-n {{ flex:0 0 7mm; height:5.5mm; border-radius:1.5mm; background:var(--l); color:var(--c); font-weight:700; text-align:center; line-height:5.5mm; font-size:8.5pt; }}

/* Parties */
.part-divider {{ break-inside:avoid; break-after:avoid; margin-top:4mm; }}
#s18 {{ break-before:page; }}
#s18 .part-divider {{ margin-top:0; }}
.part-divider {{ background:var(--c); color:#fff; border-radius:4mm; padding:6mm 7mm; margin-bottom:6mm; position:relative; overflow:hidden; }}
.part-divider::after {{ content:''; position:absolute; right:-14mm; top:-14mm; width:52mm; height:52mm; border-radius:50%; background:rgba(255,255,255,.14); }}
.part-kicker {{ font-size:8pt; letter-spacing:.18em; text-transform:uppercase; font-weight:600; opacity:.85; }}
.part-name {{ font-family:'Inter Display'; font-size:24pt; font-weight:800; letter-spacing:-.01em; }}
.part-list span {{ display:inline-block; margin:2mm 1.5mm 0 0; background:rgba(255,255,255,.2); border-radius:1.5mm; padding:.4mm 2.2mm; font-size:8.5pt; font-weight:600; }}

/* Titres */
h2 {{ display:block; break-inside:avoid; margin:7mm 0 3mm; padding-bottom:2mm; border-bottom:2.5px solid var(--c);
      break-after:avoid; font-family:'Inter Display'; font-size:15.5pt; font-weight:750; letter-spacing:-.01em; }}
.chapter.newpart h2 {{ margin-top:0; }}
.front + .chapter .part-divider {{ margin-top:0; }}
h2 .num {{ min-width:10mm; height:10mm; line-height:10mm; border-radius:3mm; background:var(--c); color:#fff; display:inline-block;
           text-align:center; font-size:13pt; font-weight:800; padding:0 2mm; margin-right:3.5mm; vertical-align:middle; }}
h2 .t {{ vertical-align:middle; }}
h2 code {{ font-size:.8em; }}
h3 {{ font-family:'Inter Display'; font-size:11.5pt; font-weight:700; margin:5mm 0 2mm; color:var(--ink); break-after:avoid; display:flex; align-items:center; gap:2.5mm; }}
h3 .snum {{ background:var(--l); color:var(--c); border-radius:1.5mm; padding:.3mm 2mm; font-size:9pt; font-weight:800; }}
p {{ margin:1.8mm 0; }}
ul, ol {{ margin:1.5mm 0; padding-left:6mm; }}
li {{ margin:.9mm 0; }}
li::marker {{ color:var(--c); font-weight:700; }}
ol > li {{ padding-left:1mm; }}

/* Tableaux */
table {{ width:100%; border-collapse:separate; border-spacing:0; margin:3mm 0; font-size:8.7pt; border:1px solid var(--line);
         border-radius:3mm; overflow:hidden; }}
thead th {{ background:var(--c); color:#fff; text-align:left; font-weight:650; padding:1.8mm 2.6mm; }}
td {{ padding:1.6mm 2.6mm; border-top:1px solid var(--line); vertical-align:top; }}
tbody tr:nth-child(even) td {{ background:#f8f9fc; }}
tr {{ break-inside:avoid; }}
.chip {{ display:inline-block; min-width:5mm; text-align:center; border-radius:1.2mm; font-weight:800; font-size:7.8pt; padding:.2mm 1.2mm; margin-right:.8mm; color:#fff; }}
.chip.err {{ background:#dc2626; }} .chip.warn {{ background:#f59e0b; }} .chip.info {{ background:#64748b; }}
td .code {{ display:inline-block; min-width:7mm; text-align:center; background:var(--l); color:var(--c); font-weight:800; border-radius:1.2mm; padding:.2mm 1mm; }}

/* Code */
pre {{ background:#151a2e; color:#e6e9f5; border-radius:3mm; padding:3.5mm 4.5mm; font-size:8.1pt; line-height:1.45; overflow:hidden;
       border-left:4px solid var(--c); break-inside:avoid; white-space:pre-wrap; }}
pre code {{ background:transparent; border:0; color:inherit; padding:0; font-size:1em; }}

/* Encadrés */
.callout {{ border-radius:3mm; padding:2.6mm 4mm; margin:3mm 0; }}
.callout.warn {{ background:#fff4e5; border-left:4px solid #f59e0b; }}
.callout.tip {{ background:var(--l); border-left:4px solid var(--c); }}
strong.attn {{ background:#f59e0b; color:#fff; border-radius:1mm; padding:0 1.4mm; }}
.faq-q {{ margin-top:3.5mm; padding:2mm 3.5mm; background:#f8f9fc; border-left:4px solid var(--c); border-radius:0 2mm 2mm 0; break-inside:avoid; }}
.faq-q strong:first-child {{ display:block; color:var(--c); margin-bottom:.6mm; }}

/* Aide-mémoire */
ul.checklist {{ list-style:none; padding-left:0; }}
li.check {{ position:relative; padding:2mm 3mm 2mm 10.5mm; border:1px solid var(--line); border-radius:2.5mm; margin:1.8mm 0; break-inside:avoid; }}
li.check .box {{ position:absolute; left:3.4mm; top:2.5mm; width:4.2mm; height:4.2mm; border:2px solid var(--c); border-radius:1.2mm; }}
</style></head><body>

<div class="cover">
  <div class="shape" style="width:120mm;height:120mm;right:-30mm;top:-30mm;background:#2563eb"></div>
  <div class="shape" style="width:70mm;height:70mm;right:60mm;top:20mm;background:#ea580c"></div>
  <div class="shape" style="width:46mm;height:46mm;right:22mm;top:92mm;background:#059669"></div>
  <div class="shape" style="width:26mm;height:26mm;right:76mm;top:94mm;background:#7c3aed"></div>
  <div class="shape" style="width:18mm;height:18mm;right:8mm;top:150mm;background:#dc2626"></div>
  <div class="title">
    <div class="kicker">Addon Blender · GLBInterpreter</div>
    <h1>GLB<br>Generator</h1>
    <div class="sub">Guide d'utilisation : scènes de livre augmenté et mini-jeu</div>
  </div>
  <div class="band" style="bottom:52mm;background:linear-gradient(90deg,#2563eb,#ea580c 35%,#dc2626 60%,#059669 80%,#7c3aed);height:3mm"></div>
  <div class="meta"><span class="pill">Addon v0.4</span><span class="pill">Blender 4.5</span><span class="pill">{date}</span></div>
  <div class="brand"><b>Éditions Liger</b>Livres augmentés</div>
</div>

<div class="front">
  <h2 class="plain">À propos de ce guide</h2>
  <div class="about"><div class="ver">{ver}</div>{intro_html}</div>
  <h2 class="plain">Sommaire</h2>
  <div class="toc">{''.join(toc)}</div>
</div>

{content}
</body></html>"""

open(OUT, "w", encoding="utf-8").write(page)
print("html ok", len(page))
# --- render.js (Node, Playwright) : node render.js <guide.html absolu> <guide.pdf> ---
# const { chromium } = require('playwright');
# (async () => {
#   const b = await chromium.launch();
#   const p = await b.newPage();
#   await p.goto('file://' + process.argv[2], { waitUntil: 'load' });
#   await p.pdf({ path: process.argv[3], format: 'A4', printBackground: true, preferCSSPageSize: true,
#     displayHeaderFooter: true, headerTemplate: '<span></span>',
#     footerTemplate: `<div style="width:100%;font-family:Inter,sans-serif;font-size:7.5pt;color:#8a90a3;padding:0 16mm;display:flex;justify-content:space-between">
#       <span>GLB Generator · Guide d'utilisation · v0.4</span><span class="pageNumber"></span></div>` });
#   await b.close();
# })();
