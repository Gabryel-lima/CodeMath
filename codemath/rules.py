"""Regras de semantica denotacional: IR sintatico -> IR semantico.

Cada funcao recebe um no de codemath.formula e devolve uma estrutura
StateExpr (ou Expr) representando sua denotacao. Nenhuma string de
LaTeX e' gerada aqui -- isso e' papel de latex_render.py.

Regras implementadas
--------------------
    [[skip]](s)            = s
    [[x := e]](s)          = s[x |-> [[e]](s)]
    [[c1 ; c2]](s)         = [[c2]]([[c1]](s))
    [[if b then c1 else c2]](s) = [[c1]](s)  se [[b]](s)
                                  [[c2]](s)  caso contrario
    [[while b do c]]       = fix(F),  F in (State->State) -> (State->State)
        F(f)(s) = f([[c]](s))  se [[b]](s)
                  s            caso contrario

O ponto fixo e' o menor ponto fixo de F no CPO das funcoes parciais
State -> State ordenadas por extensao; laco que nao termina denota a
funcao indefinida naquele estado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .formula import (
    Assign, BinOp, BoolOp, Cmd, Compare, Const, Expr, If, ParAssign, Seq,
    Skip, UnOp, Var, While,
)


# --------------------------------------------------------------------------
# IR semantico: expressoes de estado
# --------------------------------------------------------------------------
class StateExpr:
    """Um valor do dominio State."""


@dataclass(frozen=True)
class Named(StateExpr):
    """Um estado dado por nome: sigma, sigma_1, ..."""
    name: str


@dataclass(frozen=True)
class Update(StateExpr):
    """s[x |-> v]"""
    base: StateExpr
    var: str
    value: Expr


@dataclass(frozen=True)
class FixApply(StateExpr):
    """fix(F_k)(s)"""
    functional: str
    arg: StateExpr


@dataclass(frozen=True)
class CondState(StateExpr):
    """estado condicional: then_ se cond, senao else_"""
    cond: Expr
    then_: StateExpr
    else_: StateExpr


@dataclass(frozen=True)
class FunApply(StateExpr):
    """aplicacao da funcao-parametro do funcional: f(s)"""
    fun: str
    arg: StateExpr


@dataclass(frozen=True)
class Bottom(StateExpr):
    """O estado indefinido: o valor da funcao vazia. Aparece nas
    aproximacoes de Kleene F^0(bot) = bot."""


@dataclass(frozen=True)
class Lookup(Expr):
    """Leitura de variavel num estado concreto: s(x).

    Nao e' um no sintatico -- so aparece depois da substituicao feita
    por `subst`, quando ja se sabe em qual estado a expressao e'
    avaliada.
    """
    state: StateExpr
    var: str


# --------------------------------------------------------------------------
# Definicoes auxiliares emitidas durante a denotacao
# --------------------------------------------------------------------------
@dataclass
class StateDef:
    """Equacao auxiliar: name = value"""
    name: str
    value: StateExpr


@dataclass
class FunctionalDef:
    """Definicao do funcional de um while:

        F(f)(sigma) = f(body_state)  se cond
                      sigma          caso contrario

    `local_defs` sao equacoes auxiliares cujo lado direito menciona o
    estado ligado `state`; por isso pertencem a este escopo e nao ao
    escopo global.
    """
    name: str
    param: str          # nome da funcao-parametro, tipicamente "f"
    state: str          # nome do estado ligado, tipicamente "\\sigma"
    cond: Expr
    then_: StateExpr    # f([[corpo]](sigma))
    else_: StateExpr    # sigma
    local_defs: List["StateDef"] = None

    def __post_init__(self):
        if self.local_defs is None:
            self.local_defs = []


@dataclass
class Denotation:
    """Resultado completo da traducao de um programa."""
    result: StateExpr
    functionals: List[FunctionalDef]
    state_defs: List[StateDef]
    initial_state: str = "\\sigma"


# --------------------------------------------------------------------------
# Substituicao de variaveis sintaticas por leituras no estado
# --------------------------------------------------------------------------
def lookup(state: StateExpr, var: str) -> Expr:
    """Resolve s(x) simplificando updates conhecidos.

    s[x |-> v](x) = v          (o update mais recente vence)
    s[y |-> v](x) = s(x)       se y != x
    """
    while isinstance(state, Update):
        if state.var == var:
            return state.value
        state = state.base
    return Lookup(state, var)


def subst(expr: Expr, state: StateExpr) -> Expr:
    """Avalia sintaticamente [[e]](state): troca Var por Lookup resolvido."""
    if isinstance(expr, Var):
        return lookup(state, expr.name)
    if isinstance(expr, Const) or isinstance(expr, Lookup):
        return expr
    if isinstance(expr, BinOp):
        return BinOp(expr.op, subst(expr.left, state), subst(expr.right, state))
    if isinstance(expr, Compare):
        return Compare(expr.op, subst(expr.left, state), subst(expr.right, state))
    if isinstance(expr, UnOp):
        return UnOp(expr.op, subst(expr.operand, state))
    if isinstance(expr, BoolOp):
        return BoolOp(expr.op, tuple(subst(o, state) for o in expr.operands))
    raise TypeError(f"expressao desconhecida: {expr!r}")


# --------------------------------------------------------------------------
# Motor de denotacao
# --------------------------------------------------------------------------
class Denoter:
    def __init__(self, initial_state: str = "\\sigma"):
        self.initial_state = initial_state
        self.functionals: List[FunctionalDef] = []
        # pilha de escopos de equacoes auxiliares; o fundo e' o global
        self._scopes: List[List[StateDef]] = [[]]
        self._n_fun = 0
        self._n_state = 0

    # -- geradores de nome --
    def _fresh_functional(self) -> str:
        self._n_fun += 1
        return f"F_{{{self._n_fun}}}"

    _BOUND_NAMES = ("\\sigma", "\\tau", "\\rho", "\\upsilon")

    def _fresh_bound(self) -> str:
        """Nome do estado ligado dentro de um funcional.

        Whiles aninhados produzem funcionais aninhados; usar sempre
        \\sigma criaria captura de variavel na leitura da formula.
        """
        i = self._n_fun - 1
        if i < len(self._BOUND_NAMES):
            return self._BOUND_NAMES[i]
        return f"\\sigma^{{({i})}}"

    def _fresh_state(self) -> Named:
        self._n_state += 1
        return Named(f"\\sigma_{{{self._n_state}}}")

    def _bind(self, value: StateExpr) -> StateExpr:
        """Da nome a um estado opaco e registra a equacao no escopo atual.

        Estados opacos (resultado de `fix` ou de um condicional) nao
        admitem simplificacao de leitura: `s(x)` neles fica irredutivel.
        Se um estado desses for usado mais de uma vez, repetir a
        formula inteira em cada uso torna a saida ilegivel -- entao ele
        ganha um nome.
        """
        if not isinstance(value, (CondState, FixApply)):
            return value
        name = self._fresh_state()
        self._scopes[-1].append(StateDef(name.name, value))
        return name

    # -- regras --
    def cmd(self, node: Cmd, state: StateExpr) -> StateExpr:
        if isinstance(node, Skip):
            return state
        if isinstance(node, Assign):
            return self.rule_assign(node, state)
        if isinstance(node, ParAssign):
            return self.rule_par_assign(node, state)
        if isinstance(node, Seq):
            return self.rule_seq(node, state)
        if isinstance(node, If):
            return self.rule_if(node, state)
        if isinstance(node, While):
            return self.rule_while(node, state)
        raise TypeError(f"comando desconhecido: {node!r}")

    def rule_assign(self, node: Assign, state: StateExpr) -> StateExpr:
        return Update(state, node.target, subst(node.value, state))

    def rule_par_assign(self, node: ParAssign, state: StateExpr) -> StateExpr:
        """Todos os lados direitos sao avaliados no MESMO estado de
        entrada -- por isso `subst(v, state)` usa `state`, e nao o
        estado parcialmente atualizado."""
        result = state
        for target, value in zip(node.targets, node.values):
            result = Update(result, target, subst(value, state))
        return result

    def rule_seq(self, node: Seq, state: StateExpr) -> StateExpr:
        last = len(node.commands) - 1
        for i, c in enumerate(node.commands):
            state = self.cmd(c, state)
            if i != last:
                # o estado ainda vai ser lido pelos comandos seguintes
                state = self._bind(state)
        return state

    def rule_if(self, node: If, state: StateExpr) -> StateExpr:
        # o estado de entrada aparece tres vezes (guarda + dois ramos)
        state = self._bind(state)
        return CondState(
            subst(node.cond, state),
            self.cmd(node.then_branch, state),
            self.cmd(node.else_branch, state),
        )

    def rule_while(self, node: While, state: StateExpr) -> StateExpr:
        name = self._fresh_functional()
        bound = Named(self._fresh_bound())  # estado ligado dentro do funcional
        self._scopes.append([])
        body_state = self.cmd(node.body, bound)
        local = self._scopes.pop()
        self.functionals.append(
            FunctionalDef(
                name=name,
                param="f",
                state=bound.name,
                cond=subst(node.cond, bound),
                then_=FunApply("f", body_state),
                else_=bound,
                local_defs=local,
            )
        )
        return FixApply(name, state)


def denote(cmd: Cmd, initial_state: str = "\\sigma") -> Denotation:
    d = Denoter(initial_state)
    result = d.cmd(cmd, Named(initial_state))
    return Denotation(result, d.functionals, d._scopes[0], initial_state)


# --------------------------------------------------------------------------
# Aproximacoes de Kleene: por que "menor" ponto fixo
# --------------------------------------------------------------------------
def replace_state(node, old: str, new: StateExpr):
    """Substitui o estado ligado `old` por `new` dentro de node."""
    if isinstance(node, Named):
        return new if node.name == old else node
    if isinstance(node, Bottom):
        return node
    if isinstance(node, Update):
        return Update(replace_state(node.base, old, new), node.var,
                      replace_state(node.value, old, new))
    if isinstance(node, FixApply):
        return FixApply(node.functional, replace_state(node.arg, old, new))
    if isinstance(node, FunApply):
        return FunApply(node.fun, replace_state(node.arg, old, new))
    if isinstance(node, CondState):
        return CondState(replace_state(node.cond, old, new),
                         replace_state(node.then_, old, new),
                         replace_state(node.else_, old, new))
    if isinstance(node, Lookup):
        # resimplifica: sigma[x |-> e](x) volta a ser e
        return lookup(replace_state(node.state, old, new), node.var)
    if isinstance(node, Const):
        return node
    if isinstance(node, Var):
        return node
    if isinstance(node, BinOp):
        return BinOp(node.op, replace_state(node.left, old, new),
                     replace_state(node.right, old, new))
    if isinstance(node, Compare):
        return Compare(node.op, replace_state(node.left, old, new),
                       replace_state(node.right, old, new))
    if isinstance(node, UnOp):
        return UnOp(node.op, replace_state(node.operand, old, new))
    if isinstance(node, BoolOp):
        return BoolOp(node.op, tuple(replace_state(o, old, new) for o in node.operands))
    raise TypeError(f"no desconhecido: {node!r}")


def inline_defs(node, defs):
    """Expande sigma_k = ... dentro de node (para desdobrar sem escopos)."""
    table = {d.name: d.value for d in defs}
    changed = True
    while changed:
        changed = False
        for name, value in table.items():
            new = replace_state(node, name, value)
            if new != node:
                node, changed = new, True
    return node


def unfold(den: Denotation, functional: str, n: int) -> StateExpr:
    """F^n(bot)(sigma): a n-esima aproximacao de Kleene do laco.

    F^0(bot)          = bot            (indefinido em todo estado)
    F^{k+1}(bot)(s)   = F^k(bot)([[c]](s))  se b(s), senao s

    F^k(bot) e' a funcao que devolve o resultado certo para os estados
    que terminam em ate k voltas do laco, e bot para os demais. A
    cadeia F^0 <= F^1 <= F^2 <= ... e' crescente, e seu supremo e' o
    MENOR ponto fixo de F. "Menor" e' o que garante que nenhum estado
    ganhe um resultado inventado: um laco que nao termina em estado
    algum denota bot ali, e nao um valor arbitrario -- embora qualquer
    valor arbitrario tambem satisfaca F(f) = f nesse ponto.
    """
    fd = {f.name: f for f in den.functionals}[functional]
    cond = inline_defs(fd.cond, fd.local_defs)
    body = inline_defs(fd.then_.arg, fd.local_defs)

    def approx(k: int, state: StateExpr) -> StateExpr:
        if k == 0:
            return Bottom()
        c = replace_state(cond, fd.state, state)
        b = replace_state(body, fd.state, state)
        return CondState(c, approx(k - 1, b), state)

    return approx(n, Named(fd.state))
