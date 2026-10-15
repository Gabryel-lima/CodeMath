"""Interface de linha de comando do CodeMath."""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

from . import api
from .latex_render import render_equations, render_html, render_markdown, render_tex
from .parser import dump, parse_source
from .rules import denote
from .translator import UnsupportedNode, translate
from .unicode_render import _SUP, cmd_to_text, render_text

FORMATOS = ("md", "html", "tex", "raw", "txt", "json")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codemath",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Traduz um programa Python para sua semântica denotacional "
                    "(LaTeX, Unicode ou JSON) e confere a tradução executando-a.",
        epilog="""exemplos:
  python3 main.py exemplos/06_euclides.py
  python3 main.py exemplos/06_euclides.py -f html -o euclides.html
  python3 main.py exemplos/06_euclides.py -f txt            # Unicode, sem LaTeX
  python3 main.py exemplos/06_euclides.py -f json --sigma "a=48,b=18"
  python3 main.py exemplos/05_fatorial.py --unfold 3
  python3 main.py exemplos/06_euclides.py --verificar --sigma "a=48,b=18"
  python3 main.py exemplos/06_euclides.py --rastro --sigma "a=48,b=18"
  python3 main.py exemplos/05_fatorial.py --hoare "fat > 0"
  python3 main.py exemplos/09_divisao_longa.py --hoare "a == q*b + r and r < b" \\
          --inv "a == q*b + r and 0 <= r and b > 0"

códigos de saída: 0 ok · 1 verificação falhou · 2 erro de sintaxe/argumento ·
3 construção fora do escopo
""")
    p.add_argument("arquivo", nargs="?", help="arquivo .py (padrão: stdin)")
    p.add_argument("-o", "--saida", help="escreve o resultado em um arquivo")
    p.add_argument("-f", "--formato", choices=FORMATOS, default="md",
                   help="formato de saída (padrão: md). txt = Unicode sem LaTeX; "
                        "json = tudo estruturado, com tex e text por fórmula")
    p.add_argument("--ast", action="store_true",
                   help="só imprime a AST do Python e sai")
    p.add_argument("--ir", action="store_true",
                   help="só imprime o IR sintático (após o açúcar de for/break) e sai")
    p.add_argument("--unfold", type=int, metavar="N",
                   help="mostra as aproximações de Kleene F^0..F^N de cada laço")
    p.add_argument("--hoare", metavar="POS",
                   help="calcula a precondição mais fraca para a pós-condição dada")
    p.add_argument("--inv", action="append", default=[], metavar="EXPR",
                   help="invariante proposto para o próximo laço, na ordem do texto "
                        "(repita para vários); as obrigações são testadas em amostras")
    p.add_argument("--verificar", action="store_true",
                   help="executa a denotação e compara com o Python real")
    p.add_argument("--rastro", action="store_true",
                   help="mostra os estados de cada volta dos laços (requer --sigma)")
    p.add_argument("--sigma", metavar="K=V,...", default=None,
                   help="estado inicial (ex.: \"a=48,b=18\")")
    p.add_argument("--amostras", type=int, default=0, metavar="N",
                   help="verifica também com N estados iniciais aleatórios")
    p.add_argument("--semente", type=int, default=20260824,
                   help="semente do gerador aleatório (reprodutibilidade)")
    p.add_argument("--max-passos", type=int, default=100_000, metavar="N",
                   help="limite de voltas antes de declarar divergência (padrão 100000)")
    return p


def parse_sigma(text: str) -> dict:
    """'a=48, b=18' -> {'a': 48, 'b': 18}. Valores são literais Python."""
    sigma = {}
    for item in filter(None, (t.strip() for t in text.split(","))):
        if "=" not in item:
            raise ValueError(f"esperado K=V, veio {item!r}")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k.isidentifier():
            raise ValueError(f"nome de variável inválido: {k!r}")
        try:
            sigma[k] = ast.literal_eval(v.strip())
        except (ValueError, SyntaxError):
            raise ValueError(f"valor inválido para {k}: {v.strip()!r}") from None
    return sigma


def _secoes(r: dict, campo: str):
    """Seções extras (Kleene, Hoare) no formato dos renderizadores:
    (título, nota, [(rótulo, fórmula)]), com a fórmula em `campo`."""
    secoes = []
    if r.get("kleene"):
        items = [(f"{k['funcional']}{str(k['k']).translate(_SUP)}(⊥)({k['estado']}) =", k[campo])
                 for k in r["kleene"]]
        nota = ("A cadeia F^0 ≤ F^1 ≤ F^2 ≤ … cresce: F^k acerta os estados que "
                "terminam em até k voltas do laço e devolve ⊥ nos demais. O menor "
                "ponto fixo é o supremo dessa cadeia.")
        secoes.append(("Aproximações de Kleene", nota, items))
    h = r.get("hoare")
    if h:
        items = [("precondição mais fraca  wp(c, Q) =", h["pre"][campo]),
                 ("pós-condição  Q =", h["pos"][campo])]
        for o in h["obrigacoes"]:
            rot = f"obrigação de prova ({o['tipo']} de {o['invariante']})"
            if "contraexemplo" in o:
                if o["contraexemplo"] is None and not o["testada_em"]:
                    rot += " — não testada (falta invariante, ou nenhum estado a avalia)"
                elif o["contraexemplo"] is None:
                    rot += f" — sem contraexemplo em {o['testada_em']} estados"
                else:
                    rot += f" — FALSA em {o['contraexemplo']}"
            items.append((rot, o[campo]))
        nota = ("Correção parcial: a tripla só diz o que vale SE o programa terminar. "
                "Os invariantes I_k não são calculáveis — precisam ser inventados "
                "(--inv propõe um e procura contraexemplo; não encontrar não é prova). "
                "É a mesma dificuldade do fix, vista de outro ângulo.")
        secoes.append(("Lógica de Hoare", nota, items))
    return secoes


def _verificar(r: dict, out=sys.stderr) -> bool:
    todas_ok = True
    for v in r.get("verificacao", []):
        marca = "ok  " if v["ok"] else ("?   " if v.get("inconclusivo") else "FALHA")
        print(f"[{marca}] σ₀ = {v['sigma0']}", file=out)
        if v.get("inconclusivo"):
            print("         inconclusivo: só um lado estourou o limite; "
                  "aumente --max-passos", file=out)
        if v.get("mesmo_erro"):
            print(f"         os dois lados falham igual: {v['mesmo_erro']}", file=out)
        else:
            for chave in ("python_erro", "denotacao_erro"):
                if chave in v:
                    print(f"         {chave}: {v[chave]}", file=out)
        for k, d in v.get("diferencas", {}).items():
            print(f"         {k}: python={d['python']!r} denotação={d['denotacao']!r}", file=out)
        if v["ok"] and "final" in v:
            print(f"         σ' = {v['final']}", file=out)
        todas_ok &= v["ok"]
    return todas_ok


def _rastro(r: dict, out=sys.stdout) -> None:
    if "rastro_erro" in r:
        print(f"rastro interrompido: {r['rastro_erro']}", file=out)
        return
    if not r.get("rastro"):
        print("(o programa não tem laços)", file=out)
    for it in r.get("rastro", []):
        print(f"{it['funcional']:<4} volta {it['volta']:>3}:  {it['text']}", file=out)
    if r.get("rastro_truncado"):
        print("…", file=out)
    print(f"final:  {api.state_text(r['final'])}", file=out)


def main(argv=None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:     # saída tem σ, ⟦⟧ e acentos; no Windows o padrão não é UTF-8
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = build_parser()
    args = ap.parse_args(argv)

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
        sigma = parse_sigma(args.sigma) if args.sigma is not None else None
    except ValueError as exc:
        print(f"erro em --sigma: {exc}", file=sys.stderr)
        return 2
    if args.rastro and sigma is None:
        print("erro: --rastro precisa de --sigma", file=sys.stderr)
        return 2
    if args.inv and not args.hoare:
        print("erro: --inv precisa de --hoare", file=sys.stderr)
        return 2
    if args.verificar and sigma is None and not args.amostras:
        sigma = {}

    try:
        if args.ir:
            print(cmd_to_text(translate(tree)))
            return 0
        r = api.analisar(source, sigma=sigma, unfold_n=args.unfold, pos=args.hoare,
                         invariantes=args.inv, amostras=args.amostras,
                         semente=args.semente, max_steps=args.max_passos)
    except UnsupportedNode as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 3
    except SyntaxError as exc:
        print(f"erro de sintaxe em --hoare/--inv: {exc.msg}", file=sys.stderr)
        return 2

    if args.verificar:
        return 0 if _verificar(r) else 1
    if args.rastro:
        _rastro(r)
        return 0

    cmd = translate(tree)
    den = denote(cmd)
    titulo = os.path.basename(args.arquivo) if args.arquivo else "stdin"
    if args.formato == "json":
        out = json.dumps(r, ensure_ascii=False, indent=2, default=repr) + "\n"
    elif args.formato == "txt":
        out = render_text(source, cmd, den, _secoes(r, "text"))
    elif args.formato == "tex":
        out = render_tex(source, cmd, den, _secoes(r, "tex"))
    elif args.formato == "html":
        out = render_html(source, cmd, den, _secoes(r, "tex"), titulo)
    elif args.formato == "raw":
        eqs = list(render_equations(cmd, den))
        for _, _, items in _secoes(r, "tex"):
            eqs += [b for _, b in items if b]
        out = "\n".join(eqs) + "\n"
    else:
        out = render_markdown(source, cmd, den, _secoes(r, "tex"))

    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"escrito em {args.saida}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    if args.amostras or sigma is not None:
        # verificacao pedida junto com um formato: avisa se falhou
        if not all(v["ok"] for v in r.get("verificacao", [])):
            print("aviso: a verificação falhou (rode com --verificar para detalhes)",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
