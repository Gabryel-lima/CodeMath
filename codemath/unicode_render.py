"""Renderizacao: IR (formula/rules) -> texto Unicode, sem LaTeX.

Para onde nao ha LaTeX: terminal, legenda de video e o campo `text` das
equacoes do EduMotion (que, sem LaTeX instalado, desenha so o Unicode).
Mesma estrutura de latex_render e a MESMA regra de parenteses
(`latex_render.needs_parens`), para os dois nunca discordarem sobre o
que uma formula quer dizer.

    ⟦x := 0; while x < 10 do x := x + 1⟧(σ) = fix(F₁)(σ[x ↦ 0])
"""
from __future__ import annotations

import re
from typing import List

from .formula import (
    Assign, Atom, BinOp, BoolOp, Cmd, Compare, Const, Expr, Func, If, IfExp,
    ParAssign, Seq, Skip, UnOp, Var, While,
)
from .latex_render import _ATOM, _PREC, needs_parens, prec, split_internal
from .rules import (
    Bottom, CondState, Denotation, FixApply, FunApply, Lookup, Named,
    StateExpr, Update,
)

_SUB = str.maketrans("0123456789()+-", "₀₁₂₃₄₅₆₇₈₉₍₎₊₋")
_SUP = str.maketrans("0123456789()+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁽⁾⁺⁻")
_GREGO = {r"\sigma": "σ", r"\tau": "τ", r"\rho": "ρ", r"\upsilon": "υ"}

_BIN = {"+": "+", "-": "−", "*": "·", "/": "/", "%": " mod "}
_CMP = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=", "!=": "≠"}
_BOOL = {"and": "∧", "or": "∨", "->": "⇒"}


def tex_name(name: str) -> str:
    """Nome gerado pelas regras (em LaTeX) -> Unicode.

    F_{1} -> F₁, \\sigma_{2} -> σ₂, \\tau -> τ, \\sigma^{(4)} -> σ⁽⁴⁾
    """
    for tex, uni in _GREGO.items():
        name = name.replace(tex, uni)
    name = re.sub(r"_\{([0-9()+-]+)\}", lambda m: m.group(1).translate(_SUB), name)
    name = re.sub(r"\^\{([0-9()+-]+)\}", lambda m: m.group(1).translate(_SUP), name)
    return name


def ident(name: str) -> str:
    """Nome de variavel: `#i` -> î, `#i.fim` -> î_fim, `#.brk` -> brk."""
    parts = split_internal(name)
    if parts is None:
        return name
    kind, base, primes = parts
    if kind == "flag":
        out = base
    else:
        out = base + "̂" if len(base) == 1 else base + "ˆ"
        if kind != "ctr":
            out += "_" + kind
    return out + "′" * primes


def _wrap(e: Expr, outer: int, *, strict: bool = False, parent_op: str = "",
          side: str = "") -> str:
    s = expr_to_text(e)
    if isinstance(e, IfExp):          # ja' vem entre parenteses
        return s
    if needs_parens(e, outer, strict, parent_op, side):
        return "(" + s + ")"
    return s


def _exponent(e: Expr) -> str:
    """x² quando o expoente e' um inteiro pequeno; senao x^(e)."""
    if isinstance(e, Const) and isinstance(e.value, int) and not isinstance(e.value, bool):
        return str(e.value).translate(_SUP)
    s = expr_to_text(e)
    return "^" + (s if prec(e) == _ATOM else "(" + s + ")")


# --------------------------------------------------------------------------
# Expressoes
# --------------------------------------------------------------------------
def expr_to_text(e: Expr) -> str:
    if isinstance(e, Const):
        if e.value is True:
            return "true"
        if e.value is False:
            return "false"
        if e.value is None:
            return "None"
        if isinstance(e.value, (int, float)) and e.value < 0:
            return "−" + str(-e.value)
        return str(e.value)

    if isinstance(e, Var):
        return ident(e.name)

    if isinstance(e, Atom):
        out = tex_name(e.name)
        for block in e.subs:
            out += ("[" + ", ".join(ident(v) for v, _ in block) + " ↦ "
                    + ", ".join(expr_to_text(x) for _, x in block) + "]")
        return out

    if isinstance(e, Lookup):
        return f"{state_to_text(e.state)}({ident(e.var)})"

    if isinstance(e, UnOp):
        if e.op == "-":
            return "−" + _wrap(e.operand, _PREC["neg"])
        if prec(e.operand) == _ATOM:
            return "¬" + expr_to_text(e.operand)
        return "¬(" + expr_to_text(e.operand) + ")"

    if isinstance(e, BinOp):
        p = _PREC[e.op]
        if e.op == "//":
            # dentro de ⌊ ⌋ o `/` e' texto corrido: `⌊a + 5 / 10⌋` leria a + 0,5, entao
            # numerador e denominador seguem a regra de parenteses da divisao
            return ("⌊" + _wrap(e.left, p, parent_op=e.op) + " / "
                    + _wrap(e.right, p, strict=True, parent_op=e.op, side="right") + "⌋")
        if e.op == "**":
            return _wrap(e.left, p, strict=True) + _exponent(e.right)
        sep = _BIN[e.op] if e.op == "%" else " " + _BIN[e.op] + " "
        strict_right = e.op in ("-", "/", "%")
        return (_wrap(e.left, p, parent_op=e.op) + sep
                + _wrap(e.right, p, strict=strict_right, parent_op=e.op, side="right"))

    if isinstance(e, Compare):
        p = _PREC[e.op]
        return (_wrap(e.left, p, strict=True) + " " + _CMP[e.op] + " "
                + _wrap(e.right, p, strict=True))

    if isinstance(e, BoolOp):
        p = _PREC[e.op]
        sep = " " + _BOOL[e.op] + " "
        return sep.join(_wrap(o, p, parent_op=e.op) for o in e.operands)

    if isinstance(e, IfExp):
        # sempre entre parenteses: em texto corrido nao ha' chave de
        # casos para delimitar, e `⌊a se c, senão b / 2⌋` seria ambiguo
        return ("(" + expr_to_text(e.then_) + " se " + expr_to_text(e.cond)
                + ", senão " + expr_to_text(e.else_) + ")")

    if isinstance(e, Func):
        if e.name == "abs":
            return "|" + expr_to_text(e.args[0]) + "|"
        return e.name + "(" + ", ".join(expr_to_text(a) for a in e.args) + ")"

    raise TypeError(f"expressao desconhecida: {e!r}")


# --------------------------------------------------------------------------
# Estados
# --------------------------------------------------------------------------
def state_to_text(s: StateExpr) -> str:
    if isinstance(s, Bottom):
        return "⊥"
    if isinstance(s, Named):
        return tex_name(s.name)
    if isinstance(s, Update):
        return (state_to_text(s.base) + "[" + ident(s.var) + " ↦ "
                + expr_to_text(s.value) + "]")
    if isinstance(s, FixApply):
        return "fix(" + tex_name(s.functional) + ")(" + state_to_text(s.arg) + ")"
    if isinstance(s, FunApply):
        return s.fun + "(" + state_to_text(s.arg) + ")"
    if isinstance(s, CondState):
        return ("{ " + state_to_text(s.then_) + " se " + expr_to_text(s.cond)
                + "; senão " + state_to_text(s.else_) + " }")
    raise TypeError(f"estado desconhecido: {s!r}")


# --------------------------------------------------------------------------
# Sintaxe abstrata
# --------------------------------------------------------------------------
def _cmd_block(c: Cmd) -> str:
    s = cmd_to_text(c)
    return "(" + s + ")" if isinstance(c, Seq) else s


def cmd_to_text(c: Cmd) -> str:
    if isinstance(c, Skip):
        return "skip"
    if isinstance(c, Assign):
        return ident(c.target) + " := " + expr_to_text(c.value)
    if isinstance(c, ParAssign):
        return (", ".join(ident(t) for t in c.targets) + " := "
                + ", ".join(expr_to_text(v) for v in c.values))
    if isinstance(c, Seq):
        return "; ".join(cmd_to_text(x) for x in c.commands)
    if isinstance(c, If):
        return ("if " + expr_to_text(c.cond) + " then " + _cmd_block(c.then_branch)
                + " else " + _cmd_block(c.else_branch))
    if isinstance(c, While):
        return "while " + expr_to_text(c.cond) + " do " + _cmd_block(c.body)
    raise TypeError(f"comando desconhecido: {c!r}")


def denote_brackets(c: Cmd, state: str = r"\sigma") -> str:
    return "⟦" + cmd_to_text(c) + "⟧(" + tex_name(state) + ")"


# --------------------------------------------------------------------------
# Documento
# --------------------------------------------------------------------------
def functional_to_text(fd) -> str:
    st = tex_name(fd.state)
    return (f"{tex_name(fd.name)}({fd.param})({st}) = "
            f"{state_to_text(fd.then_)} se {expr_to_text(fd.cond)}; "
            f"senão {state_to_text(fd.else_)}")


def render_equations_text(cmd: Cmd, den: Denotation) -> List[str]:
    """As mesmas equacoes de latex_render.render_equations, em Unicode."""
    eqs = [denote_brackets(cmd, den.initial_state) + " = " + state_to_text(den.result)]
    for sd in den.state_defs:
        eqs.append(tex_name(sd.name) + " = " + state_to_text(sd.value))
    for fd in den.functionals:
        eqs.append(functional_to_text(fd))
        for sd in fd.local_defs:
            eqs.append(tex_name(sd.name) + " = " + state_to_text(sd.value))
    return eqs


def render_text(source: str, cmd: Cmd, den: Denotation, sections=()) -> str:
    """Documento em texto puro: as equacoes Unicode, uma por linha.

    `sections` traz (titulo, nota, itens) com itens (rotulo, texto).
    """
    eqs = render_equations_text(cmd, den)
    out = ["Programa:", ""] + ["    " + l for l in source.strip().splitlines()]
    out += ["", "Denotação:", "", "    " + eqs[0]]
    if len(eqs) > 1:
        out += ["", "onde", ""] + ["    " + e for e in eqs[1:]]
    for title, note, items in sections:
        out += ["", title + ":", ""]
        if note:
            out += [note, ""]
        for label, body in items:
            if label:
                out.append("  " + label)
            if body:
                out += ["      " + body, ""]
    return "\n".join(out).rstrip() + "\n"
