#!/usr/bin/env python3
"""Roda todos os exemplos e confere a denotação contra o Python real.

Cada exemplo declara seus estados iniciais em comentários no topo:

    # sigma: a=48, b=18

Um `# sigma:` vazio significa "estado inicial vazio". Exemplos que
devem divergir são marcados com `# esperado: diverge`.
"""
from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codemath import denote, parse_source, translate
from codemath.cli import parse_sigma
from codemath.evaluator import compare

_COR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
VERDE, VERM, CINZA, FIM = (("\033[32m", "\033[31m", "\033[90m", "\033[0m") if _COR
                           else ("", "", "", ""))


def sigmas(source: str):
    out, diverge = [], False
    for line in source.splitlines():
        line = line.strip()
        if line.startswith("# esperado: diverge"):
            diverge = True
        elif line.startswith("# sigma:"):
            out.append(parse_sigma(line[len("# sigma:"):]))
    return out or [{}], diverge


def main() -> int:
    falhas = 0
    base = os.path.dirname(os.path.abspath(__file__))
    for path in sorted(glob.glob(os.path.join(base, "exemplos", "*.py"))):
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        nome = os.path.basename(path)
        try:
            cmd = translate(parse_source(source))
            den = denote(cmd)
        except Exception as exc:                     # noqa: BLE001
            print(f"{VERM}✗{FIM} {nome}: {exc}")
            falhas += 1
            continue
        estados, diverge = sigmas(source)
        for s0 in estados:
            rel = compare(source, den, s0, max_steps=200_000)
            divergiu = rel.get("mesmo_erro") == "Diverge"
            if rel["ok"] and divergiu != diverge:
                # concordam, mas nao com o que o exemplo anuncia
                falhas += 1
                esperado = "divergir" if diverge else "terminar"
                print(f"{VERM}✗ {nome:<24} σ₀={s0} → deveria {esperado}{FIM}")
            elif rel["ok"] and divergiu:
                print(f"{CINZA}~ {nome:<24} σ₀={s0} → ambos não terminam{FIM}")
            elif rel["ok"] and "mesmo_erro" in rel:
                print(f"{CINZA}~ {nome:<24} σ₀={s0} → ambos falham com "
                      f"{rel['mesmo_erro']}{FIM}")
            elif rel["ok"]:
                final = {k: v for k, v in rel["denotacao"].items()
                         if not k.startswith("#")}
                print(f"{VERDE}✓{FIM} {nome:<24} σ₀={s0 or '∅'} "
                      f"{CINZA}→ {final}{FIM}")
            elif rel.get("inconclusivo"):
                falhas += 1
                print(f"{VERM}? {nome:<24} σ₀={s0} → inconclusivo: só um lado "
                      f"estourou o limite de passos{FIM}")
            else:
                falhas += 1
                print(f"{VERM}✗ {nome:<24} σ₀={s0}{FIM}")
                for k, (esp, obt) in rel.get("diferencas", {}).items():
                    print(f"    {k}: python={esp!r} denotação={obt!r}")
                for c in ("python_erro", "denotacao_erro"):
                    if c in rel:
                        print(f"    {c}: {rel[c]}")
    print()
    print(f"{VERM if falhas else VERDE}{falhas} falha(s){FIM}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
