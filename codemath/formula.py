"""Estrutura intermediaria (IR) da semantica denotacional.

Nao ha strings de LaTeX aqui: isso e' papel de latex_render.py.
Duas familias de nos:

  Expr  -> expressoes puras, denotadas como funcoes do estado: [[e]](s)
  Cmd   -> comandos, denotados como funcoes de estado em estado: [[c]](s)
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Callable, Iterator


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


@dataclass(frozen=True)
class IfExp(Expr):
    """Expressao condicional do Python: `a if c else b`.

    [[a if c else b]](s) = [[a]](s) se [[c]](s), senao [[b]](s)
    So o ramo escolhido e' avaliado (importa para divisao por zero).
    """
    cond: Expr
    then_: Expr
    else_: Expr


@dataclass(frozen=True)
class Func(Expr):
    """Funcao matematica embutida: abs, min, max.

    Nao confundir com funcoes definidas pelo usuario: essas sao
    expandidas por substituicao no translator e nunca chegam ao IR.
    """
    name: str
    args: tuple


# Funcoes do Python aceitas como funcoes matematicas, com a aridade
# minima (min/max precisam de pelo menos dois argumentos: com um so,
# o argumento seria um iteravel, e listas estao fora do escopo).
BUILTINS = {"abs": (1, 1), "min": (2, None), "max": (2, None)}


# --------------------------------------------------------------------------
# Percurso generico de expressoes
# --------------------------------------------------------------------------
# Toda funcao que "troca folhas e reconstroi" (substituicao, leitura no
# estado, expansao de funcao) era um `if isinstance` por tipo de no,
# repetido em quatro modulos. Um no novo exigia mexer nos quatro -- e
# esquecer um so dava TypeError longe da causa. Agora cada uma trata so'
# as folhas que lhe interessam e delega o resto para `map_expr`.
#
# Um campo e' filho se for Expr ou tupla de Expr. Atom fica de fora de
# proposito: suas substituicoes pendentes nao sao subexpressoes livres
# (ver hoare.substitute).


def children(e: Expr) -> Iterator[Expr]:
    """Subexpressoes diretas de `e`, na ordem dos campos."""
    if isinstance(e, Atom):
        return
    for f in fields(e):
        v = getattr(e, f.name)
        if isinstance(v, Expr):
            yield v
        elif isinstance(v, tuple):
            for x in v:
                if isinstance(x, Expr):
                    yield x


def map_expr(e: Expr, fn: Callable[[Expr], Expr]) -> Expr:
    """Reconstroi `e` aplicando `fn` a cada filho direto.

    Nos sem filhos (Var, Const, Atom) voltam inalterados -- quem chama
    trata as folhas antes de delegar.
    """
    if isinstance(e, Atom):
        return e
    novos = {}
    for f in fields(e):
        v = getattr(e, f.name)
        if isinstance(v, Expr):
            novos[f.name] = fn(v)
        elif isinstance(v, tuple) and any(isinstance(x, Expr) for x in v):
            novos[f.name] = tuple(fn(x) if isinstance(x, Expr) else x for x in v)
    return replace(e, **novos) if novos else e


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
