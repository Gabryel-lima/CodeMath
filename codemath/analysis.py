"""Analises simples sobre o IR sintatico."""
from __future__ import annotations

from typing import FrozenSet, Set

from .formula import (
    Assign, Atom, Cmd, Expr, If, ParAssign, Seq, Skip, Var, While, children,
)


def expr_vars(e: Expr) -> Set[str]:
    """Variaveis lidas por uma expressao."""
    if isinstance(e, Var):
        return {e.name}
    if isinstance(e, Atom):
        return set()
    out: Set[str] = set()
    for c in children(e):
        out |= expr_vars(c)
    return out


def free_vars(c: Cmd) -> Set[str]:
    """Variaveis que o programa PODE ler sem ter escrito antes.

    Sao exatamente as que precisam existir no estado inicial. E' a
    analise classica de atribuicao definitiva: percorre o programa
    levando o conjunto do que COM CERTEZA ja' foi escrito.

      - em `c1; c2`, o que c1 escreve vale para c2;
      - depois de um `if`, so' vale o que os DOIS ramos escrevem;
      - depois de um `while`, so' vale o que ja' valia antes (o corpo
        pode rodar zero vezes), mas DENTRO do corpo as escritas
        anteriores da mesma volta contam.

    A versao antiga ignorava qualquer escrita dentro de laco, e por
    isso `for i in range(n): s = s + i` declarava `i` livre -- embora
    o corpo sempre comece com `i := #i`.
    """
    free: Set[str] = set()

    def leia(e: Expr, escrito: FrozenSet[str]) -> None:
        free.update(expr_vars(e) - escrito)

    def walk(cmd: Cmd, escrito: FrozenSet[str]) -> FrozenSet[str]:
        if isinstance(cmd, Skip):
            return escrito
        if isinstance(cmd, Assign):
            leia(cmd.value, escrito)
            return escrito | {cmd.target}
        if isinstance(cmd, ParAssign):
            for v in cmd.values:
                leia(v, escrito)
            return escrito | set(cmd.targets)
        if isinstance(cmd, Seq):
            for sub in cmd.commands:
                escrito = walk(sub, escrito)
            return escrito
        if isinstance(cmd, If):
            leia(cmd.cond, escrito)
            return walk(cmd.then_branch, escrito) & walk(cmd.else_branch, escrito)
        if isinstance(cmd, While):
            leia(cmd.cond, escrito)
            walk(cmd.body, escrito)
            return escrito
        raise TypeError(f"comando desconhecido: {cmd!r}")

    walk(c, frozenset())
    return free


def assigned_vars(c: Cmd) -> Set[str]:
    """Variaveis escritas em algum caminho do programa."""
    if isinstance(c, Assign):
        return {c.target}
    if isinstance(c, ParAssign):
        return set(c.targets)
    if isinstance(c, Seq):
        return set().union(*(assigned_vars(x) for x in c.commands))
    if isinstance(c, If):
        return assigned_vars(c.then_branch) | assigned_vars(c.else_branch)
    if isinstance(c, While):
        return assigned_vars(c.body)
    return set()


def is_internal(name: str) -> bool:
    """Variavel criada pela traducao (contador, limite, bandeira)."""
    return name.startswith("#")
