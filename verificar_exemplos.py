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

VERDE, VERM, CINZA, FIM = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


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
    for path in sorted(glob.glob(os.path.join("exemplos", "*.py"))):
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
        estados, _ = sigmas(source)
        for s0 in estados:
            rel = compare(source, den, s0, max_steps=200_000)
            if rel["ok"]:
                final = {k: v for k, v in rel["denotacao"].items()
                         if not k.startswith("#")}
                print(f"{VERDE}✓{FIM} {nome:<24} σ₀={s0 or '∅'} "
                      f"{CINZA}→ {final}{FIM}")
            elif "denotacao_erro" in rel and "python_erro" in rel:
                # os dois lados falharam do mesmo jeito: é o caso divergente
                print(f"{CINZA}~ {nome:<24} σ₀={s0} → ambos não terminam{FIM}")
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
