"""API estruturada: tudo o que o CodeMath sabe de um programa, em dados.

Feita para quem usa o CodeMath como biblioteca (o EduMotion, como
submodulo). Cada formula sai em DUAS formas, `tex` e `text` (Unicode),
porque o EduMotion desenha o `text` quando nao ha LaTeX instalado.

    from codemath.api import analisar
    r = analisar(open("euclides.py").read(), sigma={"a": 48, "b": 18})
    r["equacoes"][0]["text"]   # '⟦while b ≠ 0 do a, b := b, a mod b; ...'
    r["passos"]                # [{'text': 'a = 48, b = 18', 'note': 'entrada'}, ...]

`passos` tem o formato de `expression_steps` do EduMotion: a sequencia
de estados que o laco percorre, sigma, F(sigma), F(F(sigma)), ... --
a cadeia cujo limite e' fix(F), mostrada num exemplo concreto.

Erros de entrada sobem como excecao: SyntaxError (fonte invalido) e
translator.UnsupportedNode (construcao fora do escopo).
"""
from __future__ import annotations

import ast
import random
from typing import Dict, Iterable, List, Optional, Sequence

from . import latex_render as L
from . import unicode_render as U
from .analysis import free_vars, is_internal
from .evaluator import check_obligations, compare, trace_denotation
from .formula import Cmd, Expr
from .hoare import HoareResult, analyse
from .parser import parse_source
from .rules import Denotation, denote, unfold
from .translator import Translator, translate

VERSAO_API = 1


def parse_predicate(src: str) -> Expr:
    """Uma expressao Python (pos-condicao, invariante) -> IR."""
    tree = ast.parse(src.strip(), mode="eval")
    return Translator().expr(tree.body)


def formula(tex: str, text: str, **extra) -> dict:
    d = {"tex": tex, "text": text}
    d.update(extra)
    return d


def equations(cmd: Cmd, den: Denotation) -> List[dict]:
    """As equacoes da denotacao, cada uma com papel, nome, tex e text."""
    tex = L.render_equations(cmd, den)
    txt = U.render_equations_text(cmd, den)
    papeis = [("denotacao", "")]
    papeis += [("estado", U.tex_name(sd.name)) for sd in den.state_defs]
    for fd in den.functionals:
        papeis.append(("funcional", U.tex_name(fd.name)))
        papeis += [("estado", U.tex_name(sd.name)) for sd in fd.local_defs]
    return [formula(t, x, papel=p, nome=n) for (p, n), t, x in zip(papeis, tex, txt)]


def kleene(den: Denotation, n: int) -> List[dict]:
    """F_k^j(⊥) para j = 0..n, para cada laco."""
    out = []
    for fd in den.functionals:
        for k in range(n + 1):
            s = unfold(den, fd.name, k)
            out.append(formula(L.state_to_latex(s), U.state_to_text(s),
                               funcional=U.tex_name(fd.name), k=k,
                               estado=U.tex_name(fd.state)))
    return out


def hoare(cmd: Cmd, post: str, invariants: Sequence[str] = (),
          samples: int = 200, seed: int = 0) -> dict:
    """wp e obrigacoes; com invariantes, procura contraexemplos."""
    post_e = parse_predicate(post)
    invs = [parse_predicate(i) for i in invariants]
    r: HoareResult = analyse(cmd, post_e, invs or None)
    out = {
        "pos": formula(L.expr_to_latex(r.post), U.expr_to_text(r.post)),
        "pre": formula(L.expr_to_latex(r.pre), U.expr_to_text(r.pre)),
        "invariantes": [U.tex_name(i) for i in r.invariants],
        "obrigacoes": [],
    }
    checks = []
    if invs:
        from .analysis import expr_vars
        nomes = set().union(*(expr_vars(o.formula) for o in r.obligations)) if r.obligations else set()
        checks = check_obligations(r, nomes, samples=samples, seed=seed)
    for k, o in enumerate(r.obligations):
        item = formula(L.expr_to_latex(o.formula), U.expr_to_text(o.formula),
                       tipo=o.kind, invariante=U.tex_name(o.invariant))
        if checks:
            c = checks[k]
            item["testada_em"] = c.tested
            item["contraexemplo"] = c.counterexample
        out["obrigacoes"].append(item)
    return out


def state_text(state: dict) -> str:
    """'a = 48, b = 18' -- so as variaveis do programa, sem as internas."""
    return ", ".join(f"{k} = {v!r}" for k, v in state.items() if not is_internal(k))


def trace(den: Denotation, sigma0: dict, max_steps: int = 100_000,
          limite: int = 50) -> Dict[str, object]:
    """Estado final e voltas de cada laco a partir de sigma0."""
    final, voltas = trace_denotation(den, sigma0, max_steps)
    rastro = [{"funcional": U.tex_name(it.functional), "volta": it.index,
               "estado": {k: v for k, v in it.state.items() if not is_internal(k)},
               "text": state_text(it.state)} for it in voltas[:limite]]
    passos = []
    primeiro = den.functionals[0].name if den.functionals else None
    for it in voltas:
        if it.functional != primeiro:
            continue
        nota = "entrada" if it.index == 0 else f"volta {it.index}"
        passos.append({"text": state_text(it.state), "note": nota})
    if passos:
        passos[-1]["note"] += " (guarda falsa: sai do laço)"
    return {"final": {k: v for k, v in final.items() if not is_internal(k)},
            "rastro": rastro, "rastro_truncado": len(voltas) > limite,
            "passos": passos[:limite]}


def sample_states(cmd: Cmd, n: int, seed: int, low: int = 0, high: int = 12) -> List[dict]:
    rng = random.Random(seed)
    livres = sorted(free_vars(cmd))
    return [{v: rng.randint(low, high) for v in livres} for _ in range(n)]


def verify(source: str, den: Denotation, states: Iterable[dict],
           max_steps: int = 100_000) -> List[dict]:
    out = []
    for s0 in states:
        rel = compare(source, den, s0, max_steps)
        item = {"sigma0": rel["sigma0"], "ok": rel["ok"]}
        for k in ("python_erro", "denotacao_erro", "mesmo_erro", "inconclusivo"):
            if k in rel:
                item[k] = rel[k]
        if rel.get("diferencas"):
            item["diferencas"] = {k: {"python": a, "denotacao": b}
                                  for k, (a, b) in rel["diferencas"].items()}
        if "denotacao" in rel:
            item["final"] = {k: v for k, v in rel["denotacao"].items() if not is_internal(k)}
        out.append(item)
    return out


def analisar(source: str, *, sigma: Optional[dict] = None, unfold_n: Optional[int] = None,
             pos: Optional[str] = None, invariantes: Sequence[str] = (),
             amostras: int = 0, semente: int = 20260824,
             max_steps: int = 100_000) -> dict:
    """Analise completa de um programa.

    sigma        estado inicial: ativa `verificacao`, `rastro` e `passos`
    unfold_n     aproximacoes de Kleene F^0..F^n de cada laco
    pos          pos-condicao (expressao Python) para a logica de Hoare
    invariantes  um por laco, na ordem do texto (I_1, I_2, ...)
    amostras     verifica tambem em N estados aleatorios
    """
    cmd = translate(parse_source(source))
    den = denote(cmd)
    out: dict = {
        "versao_api": VERSAO_API,
        "programa": source,
        "sintaxe": formula(L.cmd_to_latex(cmd), U.cmd_to_text(cmd)),
        "variaveis_livres": sorted(free_vars(cmd)),
        "lacos": [U.tex_name(f.name) for f in den.functionals],
        "equacoes": equations(cmd, den),
    }
    if unfold_n is not None:
        out["kleene"] = kleene(den, unfold_n)
    if pos:
        out["hoare"] = hoare(cmd, pos, invariantes, seed=semente)
    estados = ([dict(sigma)] if sigma is not None else []) + sample_states(cmd, amostras, semente)
    if estados:
        out["verificacao"] = verify(source, den, estados, max_steps)
    if sigma is not None:
        try:
            out.update(trace(den, sigma, max_steps))
        except Exception as exc:                   # noqa: BLE001
            out["rastro_erro"] = f"{type(exc).__name__}: {exc}"
    return out
