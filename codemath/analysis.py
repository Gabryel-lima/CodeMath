"""Analises simples sobre o IR sintatico."""
from __future__ import annotations

from typing import Set

from .formula import (
    Assign, Atom, BinOp, BoolOp, Cmd, Compare, Const, Expr, If, ParAssign,
    Seq, Skip, UnOp, Var, While,
)


def expr_vars(e: Expr) -> Set[str]:
    if isinstance(e, Var):
        return {e.name}
    if isinstance(e, (Const, Atom)):
        return set()
    if isinstance(e, BinOp):
        return expr_vars(e.left) | expr_vars(e.right)
    if isinstance(e, Compare):
        return expr_vars(e.left) | expr_vars(e.right)
    if isinstance(e, UnOp):
        return expr_vars(e.operand)
    if isinstance(e, BoolOp):
        return set().union(*(expr_vars(o) for o in e.operands))
    raise TypeError(f"expressao desconhecida: {e!r}")


def free_vars(c: Cmd) -> Set[str]:
    """Variaveis que o programa LE sem ter escrito antes.

    Sao exatamente as que precisam existir no estado inicial. A conta
    e' conservadora: dentro de `while` e dos dois ramos de `if` nao se
    sabe se a atribuicao acontece, entao as escritas ali nao contam
    como definitivas.
    """
    free: Set[str] = set()
    written: Set[str] = set()

    def walk(cmd: Cmd, sure: bool) -> None:
        nonlocal free, written
        if isinstance(cmd, Skip):
            return
        if isinstance(cmd, Assign):
            free |= expr_vars(cmd.value) - written
            if sure:
                written.add(cmd.target)
            return
        if isinstance(cmd, ParAssign):
            for v in cmd.values:
                free |= expr_vars(v) - written
            if sure:
                written |= set(cmd.targets)
            return
        if isinstance(cmd, Seq):
            for sub in cmd.commands:
                walk(sub, sure)
            return
        if isinstance(cmd, If):
            free |= expr_vars(cmd.cond) - written
            walk(cmd.then_branch, False)
            walk(cmd.else_branch, False)
            return
        if isinstance(cmd, While):
            free |= expr_vars(cmd.cond) - written
            walk(cmd.body, False)
            return
        raise TypeError(f"comando desconhecido: {cmd!r}")

    walk(c, True)
    return free
