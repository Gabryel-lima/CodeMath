#!/usr/bin/env python3
"""Gera galeria.html: todos os exemplos, código e matemática lado a lado."""
from __future__ import annotations

import glob
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codemath import denote, parse_source, translate
from codemath.cli import parse_sigma
from codemath.evaluator import compare
from codemath.latex_render import render_equations

CABECA = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CodeMath — galeria</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"></script>
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true}]})"></script>
<style>
:root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1c1c1a;--mut:#6b6b66;--line:#e3e3df;
      --card:#fff;--accent:#8a5a2b;--ok:#2f7d4f}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#eeeeec;--mut:#a0a09a;
      --line:#2e2e2b;--card:#1e1e1c;--accent:#d9a066;--ok:#6fbf8e}}
*{box-sizing:border-box}
body{margin:0;padding:3rem 1.25rem 6rem;background:var(--bg);color:var(--fg);
     font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:64rem;margin:0 auto}
h1{font-size:1.75rem;margin:0 0 .3rem;letter-spacing:-.02em}
.sub{color:var(--mut);margin:0 0 2rem;max-width:44rem}
section{border-top:1px solid var(--line);padding:2.25rem 0 .5rem}
h2{font-size:1.05rem;margin:0 0 .2rem;font-weight:600}
.desc{color:var(--mut);font-size:.92rem;margin:.1rem 0 1rem}
.grid{display:grid;gap:1rem;grid-template-columns:minmax(0,22rem) minmax(0,1fr)}
@media (max-width:56rem){.grid{grid-template-columns:1fr}}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:0;
    padding:.9rem 1rem;overflow-x:auto;font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
.eqs{display:flex;flex-direction:column;gap:.5rem;min-width:0}
.eq{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
    border-radius:8px;padding:.7rem .9rem;overflow-x:auto}
.onde{color:var(--mut);font-size:.8rem;margin:.15rem 0 -.15rem}
.chk{font-size:.82rem;color:var(--ok);margin:.9rem 0 0;font-family:ui-monospace,monospace}
nav{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:2.5rem}
nav a{font-size:.8rem;color:var(--mut);text-decoration:none;border:1px solid var(--line);
      border-radius:999px;padding:.2rem .7rem;background:var(--card)}
nav a:hover{color:var(--fg);border-color:var(--accent)}
footer{color:var(--mut);font-size:.8rem;margin-top:4rem;border-top:1px solid var(--line);
       padding-top:1rem}
</style></head><body><main>
<h1>CodeMath — galeria</h1>
<p class="sub">Cada programa ao lado da sua semântica denotacional. A linha verde
no rodapé de cada exemplo é a verificação: a denotação foi executada de fato
(aplicando <code>fix</code> por iteração) e comparada com o resultado do Python real.</p>
"""


def cabecalho_do_exemplo(source: str):
    linhas, sigmas = [], []
    for l in source.splitlines():
        t = l.strip()
        if t.startswith("# sigma:"):
            sigmas.append(parse_sigma(t[len("# sigma:"):]))
        elif t.startswith("# esperado:"):
            continue
        elif t.startswith("#"):
            linhas.append(t.lstrip("# ").rstrip())
        else:
            break
    codigo = "\n".join(l for l in source.splitlines()
                       if not l.strip().startswith("#")).strip()
    return " ".join(linhas), sigmas, codigo


def main() -> int:
    partes = [CABECA]
    nav, corpo = [], []
    for path in sorted(glob.glob(os.path.join("exemplos", "*.py"))):
        nome = os.path.basename(path)[:-3]
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        desc, sigmas, codigo = cabecalho_do_exemplo(source)
        cmd = translate(parse_source(source))
        den = denote(cmd)
        eqs = render_equations(cmd, den)
        ident = nome.replace("_", "-")
        titulo = nome.split("_", 1)[1].replace("_", " ") if "_" in nome else nome
        nav.append(f'<a href="#{ident}">{html.escape(titulo)}</a>')

        blocos = [f'<div class="eq">$${eqs[0]}$$</div>']
        if len(eqs) > 1:
            blocos.append('<p class="onde">onde</p>')
            blocos += [f'<div class="eq">$${e}$$</div>' for e in eqs[1:]]

        checks = []
        if "# esperado: diverge" in source:
            checks.append("não termina: fix(F) = ⊥ em todo estado")
        else:
            for s0 in (sigmas or [{}]):
                rel = compare(source, den, s0, max_steps=200_000)
                final = {k: v for k, v in rel.get("denotacao", {}).items()
                         if not k.startswith("#")}
                marca = "✓" if rel["ok"] else "✗"
                checks.append(f"{marca} σ₀={s0 or '∅'} → {final}")

        corpo.append(
            f'<section id="{ident}"><h2>{html.escape(titulo)}</h2>'
            f'<p class="desc">{html.escape(desc)}</p>'
            f'<div class="grid"><pre>{html.escape(codigo)}</pre>'
            f'<div class="eqs">{"".join(blocos)}</div></div>'
            + "".join(f'<p class="chk">{html.escape(c)}</p>' for c in checks)
            + "</section>")

    partes.append("<nav>" + "".join(nav) + "</nav>")
    partes += corpo
    partes.append('<footer>Gerado por <code>python3 galeria.py</code>. '
                  'A matemática é renderizada com KaTeX (via CDN) — abra com internet.'
                  '</footer></main></body></html>')
    with open("galeria.html", "w", encoding="utf-8") as fh:
        fh.write("\n".join(partes))
    print("escrito em galeria.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
