"""Modo alternativo de saida: logica de Hoare por precondicao mais fraca.

Uma tripla {P} c {Q} diz: se P vale antes de executar c e c termina,
entao Q vale depois. A precondicao MAIS FRACA wp(c, Q) e' a P mais
permissiva com essa propriedade -- calcula-la e' mecanico para tudo
menos o laco.

    wp(skip, Q)                 = Q
    wp(x := e, Q)               = Q[x := e]
    wp(c1 ; c2, Q)              = wp(c1, wp(c2, Q))
    wp(if b then c1 else c2, Q) = (b => wp(c1,Q)) and (not b => wp(c2,Q))
    wp(while b do c, Q)         = I,  com as obrigacoes
                                     (I and b)     => wp(c, I)
                                     (I and not b) => Q

O laco e' o unico caso que nao se resolve sozinho: `I` e' um invariante
que precisa ser inventado, nao calculado. Essa e' a mesma dificuldade
do `fix` na semantica denotacional, vista de outro angulo -- e o motivo
de verificacao de programas nao ser automatica.

Atencao: isto e' correcao PARCIAL. A tripla nao diz nada sobre o laco
terminar; provar terminacao exige uma variante (um natural que decresce
a cada iteracao). `while True: pass` satisfaz {true} c {false}.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .formula import (
    Assign, Atom, BoolOp, Cmd, Const, Expr, If, ParAssign, Seq, Skip, UnOp,
    Var, While, map_expr,
)


@dataclass
class Obligation:
    """Uma obrigação de prova gerada pelo laço."""
    kind: str            # "preservação" ou "saída"
    invariant: str
    formula: Expr


@dataclass
class HoareResult:
    pre: Expr
    post: Expr
    obligations: List[Obligation]
    invariants: List[str]
    # invariante dado pelo usuario, por nome (I_k -> expressao)
    given: Dict[str, Expr] = field(default_factory=dict)


def _and(*parts: Expr) -> Expr:
    parts = [p for p in parts if p != Const(True)]
    if not parts:
        return Const(True)
    if len(parts) == 1:
        return parts[0]
    return BoolOp("and", tuple(parts))


def _implies(a: Expr, b: Expr) -> Expr:
    return BoolOp("->", (a, b))


def _not(e: Expr) -> Expr:
    return UnOp("not", e)


def substitute(expr: Expr, binding: dict) -> Expr:
    """Substituição simultânea de variáveis por expressões."""
    if isinstance(expr, Var):
        return binding.get(expr.name, expr)
    if isinstance(expr, Atom):
        # I[S] com [x := e] aplicado por fora vira I[S][x := e]:
        # a substituicao fica pendente porque I e' opaco.
        # cada bloco e' UMA substituicao simultanea: num swap,
        # I[x,y := y,x] nao e' I[x := y][y := x].
        return Atom(expr.name, expr.subs + (tuple(binding.items()),))
    return map_expr(expr, lambda e: substitute(e, binding))


def resolve_atom(atom: Atom, body: Expr) -> Expr:
    """Troca o predicado opaco pelo invariante dado, aplicando as
    substituicoes pendentes na ordem em que foram registradas."""
    out = body
    for block in atom.subs:
        out = substitute(out, dict(block))
    return out


def instantiate(expr: Expr, given: Dict[str, Expr]) -> Expr:
    """Substitui cada I_k conhecido pelo invariante concreto."""
    if isinstance(expr, Atom):
        if expr.name in given:
            return resolve_atom(expr, given[expr.name])
        return expr
    return map_expr(expr, lambda e: instantiate(e, given))


def _loops_in_order(c: Cmd) -> List[While]:
    """Os lacos em pre-ordem do texto -- a mesma ordem em que rules.py
    numera os funcionais F_1, F_2, ..."""
    if isinstance(c, While):
        return [c] + _loops_in_order(c.body)
    if isinstance(c, Seq):
        return [w for x in c.commands for w in _loops_in_order(x)]
    if isinstance(c, If):
        return _loops_in_order(c.then_branch) + _loops_in_order(c.else_branch)
    return []


class WeakestPrecondition:
    """Calcula wp de tras para frente.

    Os invariantes sao numerados na ordem do TEXTO (I_k e' o invariante
    do laco cujo funcional e' F_k), e nao na ordem em que o calculo os
    encontra -- que, por ser de tras para frente, poria I_1 no ultimo
    laco do programa.
    """

    def __init__(self, program: Cmd = Skip()):
        self.obligations: List[Obligation] = []
        self.invariants: List[str] = []
        self._numero = {id(w): k for k, w in enumerate(_loops_in_order(program), 1)}

    def _fresh_invariant(self, loop: While) -> Atom:
        k = self._numero.get(id(loop), len(self._numero) + len(self.invariants) + 1)
        name = f"I_{{{k}}}"
        self.invariants.append(name)
        return Atom(name)

    def wp(self, c: Cmd, q: Expr) -> Expr:
        if isinstance(c, Skip):
            return q
        if isinstance(c, Assign):
            return substitute(q, {c.target: c.value})
        if isinstance(c, ParAssign):
            return substitute(q, dict(zip(c.targets, c.values)))
        if isinstance(c, Seq):
            for cmd in reversed(c.commands):
                q = self.wp(cmd, q)
            return q
        if isinstance(c, If):
            return _and(_implies(c.cond, self.wp(c.then_branch, q)),
                        _implies(_not(c.cond), self.wp(c.else_branch, q)))
        if isinstance(c, While):
            inv = self._fresh_invariant(c)
            self.obligations.append(Obligation(
                "preservação", inv.name,
                _implies(_and(inv, c.cond), self.wp(c.body, inv))))
            self.obligations.append(Obligation(
                "saída", inv.name,
                _implies(_and(inv, _not(c.cond)), q)))
            return inv
        raise TypeError(f"comando desconhecido: {c!r}")


def analyse(cmd: Cmd, post: Expr,
            invariants: Optional[Sequence[Expr]] = None) -> HoareResult:
    """wp(cmd, post) e as obrigacoes de prova de cada laco.

    `invariants`, se dado, e' uma lista na ordem dos lacos no texto:
    o k-esimo vira I_k e as obrigacoes passam a ser formulas concretas
    (que `evaluator.check_obligations` testa em estados de amostra).
    """
    w = WeakestPrecondition(cmd)
    pre = w.wp(cmd, post)
    def key(o: Obligation):
        return (int(o.invariant.strip("I_{}")), o.kind != "preservação")
    obligations = sorted(w.obligations, key=key)
    invs = sorted(w.invariants, key=lambda n: int(n.strip("I_{}")))
    given: Dict[str, Expr] = {}
    if invariants:
        for k, inv in enumerate(invariants, 1):
            given[f"I_{{{k}}}"] = inv
        pre = instantiate(pre, given)
        obligations = [Obligation(o.kind, o.invariant, instantiate(o.formula, given))
                       for o in obligations]
    return HoareResult(pre, post, obligations, invs, given)
