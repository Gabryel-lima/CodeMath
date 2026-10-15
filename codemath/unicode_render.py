"""Renderizacao: IR (formula/rules) -> texto Unicode, sem LaTeX.

Irmao de `latex_render.py`, com a mesma estrutura (expressao, estado,
sintaxe, documento) e as mesmas regras de precedencia. Existe para quem
mostra a formula onde nao ha um compilador TeX: um terminal, uma legenda,
um quadro de video (o EduMotion usa isto como `text` de cada passo).

A saida tenta ser lida como a do LaTeX: `σ[x ↦ σ(x) + 1]`, `fix(F₁)(σ)`,
`⟦x := 0⟧(σ)`. O caso de `cases` vira linhas separadas por "\n", uma
por ramo, porque uma linha so com os dois ramos fica longa demais para
caber em tela.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .formula import (
    Assign, Atom, BinOp, BoolOp, Cmd, Compare, Const, Expr, If, ParAssign,
    Seq, Skip, UnOp, Var, While,
)
from .latex_render import _PREC, _ATOM
from .rules import (
    Bottom, CondState, Denotation, FixApply, FunApply, Lookup, Named,
    StateExpr, Update,
)

LB, RB = "⟦", "⟧"

_GREGO = {
    r"\sigma": "σ", r"\tau": "τ", r"\rho": "ρ", r"\upsilon": "υ",
    r"\bot": "⊥", r"\top": "⊤",
}
_SUB = str.maketrans("0123456789()+-", "₀₁₂₃₄₅₆₇₈₉₍₎₊₋")
_SUP = str.maketrans("0123456789()+-n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁽⁾⁺⁻ⁿ")

_BIN = {"+": "+", "-": "−", "*": "·"}
_CMP = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=", "!=": "≠"}
_BOOL = {"and": "∧", "or": "∨", "->": "⇒"}


def name_to_unicode(name: str) -> str:
    """Nome de estado/funcional da IR (que vem em notacao TeX) -> Unicode.

    `\\sigma` -> σ, `F_{1}` -> F₁, `\\sigma^{(2)}` -> σ⁽²⁾, `\\sigma_{1}` -> σ₁.
    """
    out = name
    for tex, uni in _GREGO.items():
        out = out.replace(tex, uni)

    def sub(m):
        corpo = m.group(1)
        return corpo.translate(_SUB) if all(c in "0123456789()+-" for c in corpo) else "_" + corpo

    def sup(m):
        corpo = m.group(1)
        return corpo.translate(_SUP) if all(c in "0123456789()+-n" for c in corpo) else "^" + corpo

    out = re.sub(r"_\{([^{}]*)\}", sub, out)
    out = re.sub(r"\^\{([^{}]*)\}", sup, out)
    return out


def ident(name: str) -> str:
    """Variavel em Unicode. `#x` (contador fresco de um `for`) vira x com circunflexo."""
    if name.startswith("#"):
        base = ident(name[1:])
        return base + "̂" if len(base) == 1 else "^" + base
    return name


def _wrap(e: Expr, outer: int, *, strict: bool = False) -> str:
    from .latex_render import _prec
    inner = _prec(e)
    s = expr_to_unicode(e)
    if inner < outer or (strict and inner == outer):
        return "(" + s + ")"
    return s


# --------------------------------------------------------------------------
# Expressoes
# --------------------------------------------------------------------------
def expr_to_unicode(e: Expr) -> str:
    if isinstance(e, Const):
        if e.value is True:
            return "verdadeiro"
        if e.value is False:
            return "falso"
        if e.value is None:
            return "⊥"
        return str(e.value)

    if isinstance(e, Var):
        return ident(e.name)

    if isinstance(e, Atom):
        out = name_to_unicode(e.name)
        for block in e.subs:
            out += ("[" + ", ".join(ident(v) for v, _ in block) + " ↦ "
                    + ", ".join(expr_to_unicode(x) for _, x in block) + "]")
        return out

    if isinstance(e, Lookup):
        return f"{state_to_unicode(e.state)}({ident(e.var)})"

    if isinstance(e, UnOp):
        if e.op == "-":
            return "−" + _wrap(e.operand, _PREC["neg"])
        if isinstance(e.operand, (Var, Const, Atom, Lookup)):
            return "¬" + expr_to_unicode(e.operand)
        return "¬(" + expr_to_unicode(e.operand) + ")"

    if isinstance(e, BinOp):
        p = _PREC[e.op]
        if e.op == "/":
            return _wrap(e.left, p) + " / " + _wrap(e.right, p, strict=True)
        if e.op == "//":
            return "⌊" + expr_to_unicode(e.left) + " / " + expr_to_unicode(e.right) + "⌋"
        if e.op == "%":
            return _wrap(e.left, p) + " mod " + _wrap(e.right, p, strict=True)
        if e.op == "**":
            base = _wrap(e.left, p, strict=True)
            if isinstance(e.right, Const) and isinstance(e.right.value, int) and e.right.value >= 0:
                return base + str(e.right.value).translate(_SUP)
            return base + "^" + _wrap(e.right, _ATOM)
        return (_wrap(e.left, p) + " " + _BIN[e.op] + " "
                + _wrap(e.right, p, strict=(e.op == "-")))

    if isinstance(e, Compare):
        p = _PREC[e.op]
        return (_wrap(e.left, p, strict=True) + " " + _CMP[e.op] + " "
                + _wrap(e.right, p, strict=True))

    if isinstance(e, BoolOp):
        p = _PREC[e.op]
        return (" " + _BOOL[e.op] + " ").join(_wrap(o, p) for o in e.operands)

    raise TypeError(f"expressao desconhecida: {e!r}")


# --------------------------------------------------------------------------
# Estados
# --------------------------------------------------------------------------
def _cases(then_: str, cond: str, else_: str, recuo: int) -> str:
    pad = " " * recuo
    return "{ " + then_ + ",  se " + cond + "\n" + pad + "{ " + else_ + ",  caso contrário"


def state_to_unicode(s: StateExpr, recuo: int = 0) -> str:
    if isinstance(s, Bottom):
        return "⊥"
    if isinstance(s, Named):
        return name_to_unicode(s.name)
    if isinstance(s, Update):
        return (state_to_unicode(s.base, recuo) + "[" + ident(s.var) + " ↦ "
                + expr_to_unicode(s.value) + "]")
    if isinstance(s, FixApply):
        return "fix(" + name_to_unicode(s.functional) + ")(" + state_to_unicode(s.arg, recuo) + ")"
    if isinstance(s, FunApply):
        return name_to_unicode(s.fun) + "(" + state_to_unicode(s.arg, recuo) + ")"
    if isinstance(s, CondState):
        return _cases(state_to_unicode(s.then_), expr_to_unicode(s.cond),
                      state_to_unicode(s.else_), recuo)
    raise TypeError(f"estado desconhecido: {s!r}")


# --------------------------------------------------------------------------
# Sintaxe abstrata (para aparecer dentro de ⟦ ⟧)
# --------------------------------------------------------------------------
def cmd_to_unicode(c: Cmd) -> str:
    if isinstance(c, Skip):
        return "skip"
    if isinstance(c, Assign):
        return ident(c.target) + " := " + expr_to_unicode(c.value)
    if isinstance(c, ParAssign):
        return (", ".join(ident(t) for t in c.targets) + " := "
                + ", ".join(expr_to_unicode(v) for v in c.values))
    if isinstance(c, Seq):
        return "; ".join(cmd_to_unicode(x) for x in c.commands)
    if isinstance(c, If):
        return ("if " + expr_to_unicode(c.cond) + " then " + cmd_to_unicode(c.then_branch)
                + " else " + cmd_to_unicode(c.else_branch))
    if isinstance(c, While):
        return "while " + expr_to_unicode(c.cond) + " do " + cmd_to_unicode(c.body)
    raise TypeError(f"comando desconhecido: {c!r}")


def denote_brackets(c: Cmd, state: str = r"\sigma") -> str:
    return LB + cmd_to_unicode(c) + RB + "(" + name_to_unicode(state) + ")"


# --------------------------------------------------------------------------
# Documento
# --------------------------------------------------------------------------
def render_equations_unicode(cmd: Cmd, den: Denotation) -> List[str]:
    """Mesmas equacoes de `latex_render.render_equations`, na mesma ordem, em Unicode."""
    eqs = [denote_brackets(cmd, den.initial_state) + " = " + state_to_unicode(den.result)]
    for sd in den.state_defs:
        cab = name_to_unicode(sd.name) + " = "
        eqs.append(cab + state_to_unicode(sd.value, len(cab)))
    for fd in den.functionals:
        cab = (name_to_unicode(fd.name) + "(" + fd.param + ")(" + name_to_unicode(fd.state) + ") = ")
        eqs.append(cab + _cases(state_to_unicode(fd.then_), expr_to_unicode(fd.cond),
                                state_to_unicode(fd.else_), len(cab)))
        for sd in fd.local_defs:
            cab = name_to_unicode(sd.name) + " = "
            eqs.append(cab + state_to_unicode(sd.value, len(cab)))
    return eqs


def equation_pairs(cmd: Cmd, den: Denotation) -> List[Tuple[str, str]]:
    """(LaTeX, Unicode) de cada equacao, alinhados: quem tem TeX usa o primeiro,
    quem nao tem mostra o segundo."""
    from .latex_render import render_equations
    return list(zip(render_equations(cmd, den), render_equations_unicode(cmd, den)))


def render_text(source: str, cmd: Cmd, den: Denotation) -> str:
    """Documento em texto puro: o programa e suas equacoes."""
    linhas = ["programa:", *("    " + l for l in source.strip().splitlines()), "", "semântica:"]
    for eq in render_equations_unicode(cmd, den):
        linhas += ["    " + l for l in eq.splitlines()]
        linhas.append("")
    return "\n".join(linhas).rstrip() + "\n"
