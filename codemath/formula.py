"""Estrutura intermediaria (IR) da semantica denotacional.

Nao ha strings de LaTeX aqui: isso e' papel de latex_render.py.
Duas familias de nos:

  Expr  -> expressoes puras, denotadas como funcoes do estado: [[e]](s)
  Cmd   -> comandos, denotados como funcoes de estado em estado: [[c]](s)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# --------------------------------------------------------------------------
# Expressoes
# --------------------------------------------------------------------------
class Expr:
    """No de expressao. [[e]] : State -> Value"""


@dataclass(frozen=True)
class Var(Expr):
    """Leitura de variavel. [[x]](s) = s(x)"""
    name: str


@dataclass(frozen=True)
class Const(Expr):
    """Literal. [[n]](s) = n"""
    value: object


@dataclass(frozen=True)
class BinOp(Expr):
    """Operacao aritmetica binaria: + - * / // % **"""
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class UnOp(Expr):
    """Operacao unaria: - (negacao aritmetica) ou not (negacao logica)"""
    op: str
    operand: Expr


@dataclass(frozen=True)
class Compare(Expr):
    """Comparacao binaria: < <= > >= == !=

    Cadeias do Python (a < b < c) sao normalizadas para conjuncao
    pelo translator, entao aqui so existe a forma binaria.
    """
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class BoolOp(Expr):
    """Conjuncao ou disjuncao n-aria: 'and' | 'or'"""
    op: str
    operands: tuple


@dataclass(frozen=True)
class Atom(Expr):
    """Predicado opaco: um invariante `I` ainda por descobrir.

    Aparece so' na logica de Hoare (hoare.py), nunca na traducao de
    codigo -- o codigo nao tem como falar de um invariante.

    `subs` guarda as substituicoes pendentes, em ordem de aplicacao.
    Cada elemento e' um BLOCO simultaneo de pares (var, expr):
    Atom("I", ((("x", e),),)) e' o predicado I[x := e]. Sao pendentes
    porque I e' opaco -- nao da' para empurrar a substituicao para
    dentro de um predicado que ainda nao foi escrito.
    """
    name: str
    subs: tuple = ()


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------
class Cmd:
    """No de comando. [[c]] : State -> State"""


@dataclass(frozen=True)
class Skip(Cmd):
    """[[skip]](s) = s"""


@dataclass(frozen=True)
class Assign(Cmd):
    """[[x := e]](s) = s[x |-> [[e]](s)]"""
    target: str
    value: Expr


@dataclass(frozen=True)
class ParAssign(Cmd):
    """Atribuicao simultanea: x, y := e1, e2

    [[x,y := e1,e2]](s) = s[x |-> [[e1]](s)][y |-> [[e2]](s)]

    Nao e' a mesma coisa que a sequencia x := e1 ; y := e2 -- em
    `x, y = y, x` os dois lados direitos sao avaliados no estado
    ORIGINAL. A diferenca some em exemplos triviais e explode num swap.
    """
    targets: tuple
    values: tuple


@dataclass(frozen=True)
class Seq(Cmd):
    """[[c1; c2]](s) = [[c2]]([[c1]](s))"""
    commands: tuple


@dataclass(frozen=True)
class If(Cmd):
    """[[if b then c1 else c2]](s) = [[c1]](s) se [[b]](s), senao [[c2]](s)"""
    cond: Expr
    then_branch: Cmd
    else_branch: Cmd


@dataclass(frozen=True)
class While(Cmd):
    """[[while b do c]] = fix(F),
    F(f)(s) = f([[c]](s)) se [[b]](s), senao s
    """
    cond: Expr
    body: Cmd
