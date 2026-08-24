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

from dataclasses import dataclass
from typing import List, Tuple

from .formula import (
    Assign, Atom, BinOp, BoolOp, Cmd, Compare, Const, Expr, If, ParAssign,
    Seq, Skip, UnOp, Var, While,
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
    if isinstance(expr, Const):
        return expr
    if isinstance(expr, Atom):
        # I[S] com [x := e] aplicado por fora vira I[S][x := e]:
        # a substituicao fica pendente porque I e' opaco.
        # cada bloco e' UMA substituicao simultanea: num swap,
        # I[x,y := y,x] nao e' I[x := y][y := x].
        return Atom(expr.name, expr.subs + (tuple(binding.items()),))
    if isinstance(expr, BinOp):
        return BinOp(expr.op, substitute(expr.left, binding),
                     substitute(expr.right, binding))
    if isinstance(expr, Compare):
        return Compare(expr.op, substitute(expr.left, binding),
                       substitute(expr.right, binding))
    if isinstance(expr, UnOp):
        return UnOp(expr.op, substitute(expr.operand, binding))
    if isinstance(expr, BoolOp):
        return BoolOp(expr.op, tuple(substitute(o, binding) for o in expr.operands))
    raise TypeError(f"expressao desconhecida: {expr!r}")


class WeakestPrecondition:
    def __init__(self):
        self.obligations: List[Obligation] = []
        self.invariants: List[str] = []

    def _fresh_invariant(self) -> Atom:
        name = f"I_{{{len(self.invariants) + 1}}}"
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
            inv = self._fresh_invariant()
            self.obligations.append(Obligation(
                "preservação", inv.name,
                _implies(_and(inv, c.cond), self.wp(c.body, inv))))
            self.obligations.append(Obligation(
                "saída", inv.name,
                _implies(_and(inv, _not(c.cond)), q)))
            return inv
        raise TypeError(f"comando desconhecido: {c!r}")


def analyse(cmd: Cmd, post: Expr) -> HoareResult:
    w = WeakestPrecondition()
    pre = w.wp(cmd, post)
    return HoareResult(pre, post, w.obligations, w.invariants)
