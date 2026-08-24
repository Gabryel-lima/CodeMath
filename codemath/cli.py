"""Interface de linha de comando do CodeMath."""
from __future__ import annotations

import argparse
import ast
import os
import random
import sys

from .analysis import free_vars
from .evaluator import compare
from .hoare import analyse
from .latex_render import (
    expr_to_latex, render_equations, render_html, render_markdown, render_tex,
    state_to_latex,
)
from .parser import dump, parse_source
from .rules import denote, unfold
from .translator import translate, UnsupportedNode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codemath",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Traduz um programa Python para sua semântica denotacional em LaTeX.",
        epilog="""exemplos:
  python3 main.py exemplos/euclides.py
  python3 main.py exemplos/euclides.py -f html -o euclides.html
  python3 main.py exemplos/fatorial.py --unfold 3
  python3 main.py exemplos/euclides.py --verificar --sigma "a=48,b=18"
  python3 main.py exemplos/fatorial.py --hoare "fat > 0"
""")
    p.add_argument("arquivo", nargs="?", help="arquivo .py (padrão: stdin)")
    p.add_argument("-o", "--saida", help="escreve o resultado em um arquivo")
    p.add_argument("-f", "--formato", choices=("md", "html", "tex", "raw"),
                   default="md", help="formato de saída (padrão: md)")
    p.add_argument("--ast", action="store_true",
                   help="só imprime a AST do Python e sai")
    p.add_argument("--unfold", type=int, metavar="N",
                   help="mostra as aproximações de Kleene F^0..F^N de cada laço")
    p.add_argument("--hoare", metavar="POS",
                   help="calcula a precondição mais fraca para a pós-condição dada")
    p.add_argument("--verificar", action="store_true",
                   help="executa a denotação e compara com o Python real")
    p.add_argument("--sigma", metavar="K=V,...", default="",
                   help="estado inicial para --verificar (ex.: \"a=48,b=18\")")
    p.add_argument("--amostras", type=int, default=0, metavar="N",
                   help="verifica também com N estados iniciais aleatórios")
    p.add_argument("--semente", type=int, default=20260824,
                   help="semente do gerador aleatório (reprodutibilidade)")
    return p


def parse_sigma(text: str) -> dict:
    sigma = {}
    for item in filter(None, (t.strip() for t in text.split(","))):
        if "=" not in item:
            raise ValueError(f"esperado K=V, veio {item!r}")
        k, v = item.split("=", 1)
        sigma[k.strip()] = ast.literal_eval(v.strip())
    return sigma


def _secao_unfold(den, n):
    items = []
    for fd in den.functionals:
        for k in range(n + 1):
            items.append((f"{fd.name}^{k}(⊥)({fd.state}) =",
                          state_to_latex(unfold(den, fd.name, k))))
    nota = ("A cadeia F^0 ≤ F^1 ≤ F^2 ≤ … cresce: F^k acerta os estados que "
            "terminam em até k voltas do laço e devolve ⊥ nos demais. O menor "
            "ponto fixo é o supremo dessa cadeia.")
    return ("Aproximações de Kleene", nota, items)


def _secao_hoare(cmd, post_src):
    post = translate(parse_source(f"__q = {post_src}")).value
    r = analyse(cmd, post)
    items = [("precondição mais fraca  wp(c, Q) =", expr_to_latex(r.pre)),
             ("pós-condição  Q =", expr_to_latex(r.post))]
    for o in r.obligations:
        items.append((f"obrigação de prova ({o.kind} de {o.invariant})",
                      expr_to_latex(o.formula)))
    nota = ("Correção parcial: a tripla só diz o que vale SE o programa terminar. "
            "Os invariantes I_k não são calculáveis — precisam ser inventados. "
            "É a mesma dificuldade do fix, vista de outro ângulo.")
    return ("Lógica de Hoare", nota, items)


def _verificar(source, cmd, den, args, out=sys.stderr) -> bool:
    estados = []
    if args.sigma or not args.amostras:
        estados.append(parse_sigma(args.sigma))
    if args.amostras:
        rng = random.Random(args.semente)
        livres = sorted(free_vars(cmd))
        for _ in range(args.amostras):
            estados.append({v: rng.randint(0, 12) for v in livres})
    todas_ok = True
    for sigma0 in estados:
        rel = compare(source, den, sigma0)
        marca = "ok  " if rel["ok"] else "FALHA"
        print(f"[{marca}] σ₀ = {rel['sigma0']}", file=out)
        for chave in ("python_erro", "denotacao_erro"):
            if chave in rel:
                print(f"         {chave}: {rel[chave]}", file=out)
        if rel.get("diferencas"):
            for k, (esperado, obtido) in rel["diferencas"].items():
                print(f"         {k}: python={esperado!r} denotação={obtido!r}", file=out)
        elif rel["ok"]:
            print(f"         σ' = {rel['denotacao']}", file=out)
        todas_ok &= rel["ok"]
    return todas_ok


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.arquivo:
        with open(args.arquivo, "r", encoding="utf-8") as fh:
            source = fh.read()
    else:
        source = sys.stdin.read()

    try:
        tree = parse_source(source, args.arquivo or "<stdin>")
    except SyntaxError as exc:
        print(f"erro de sintaxe: {exc}", file=sys.stderr)
        return 2

    if args.ast:
        print(dump(tree))
        return 0

    try:
        cmd = translate(tree)
    except UnsupportedNode as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 3

    den = denote(cmd)

    if args.verificar:
        return 0 if _verificar(source, cmd, den, args) else 1

    sections = []
    if args.unfold is not None and den.functionals:
        sections.append(_secao_unfold(den, args.unfold))
    if args.hoare:
        sections.append(_secao_hoare(cmd, args.hoare))

    titulo = os.path.basename(args.arquivo) if args.arquivo else "stdin"
    if args.formato == "tex":
        out = render_tex(source, cmd, den, sections)
    elif args.formato == "html":
        out = render_html(source, cmd, den, sections, titulo)
    elif args.formato == "raw":
        eqs = list(render_equations(cmd, den))
        for _, _, items in sections:
            eqs += [b for _, b in items if b]
        out = "\n".join(eqs) + "\n"
    else:
        out = render_markdown(source, cmd, den, sections)

    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"escrito em {args.saida}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
