"""Verificador: executa a DENOTACAO e compara com o Python real.

Motivacao. Uma formula bonita nao e' garantia de formula correta. Este
modulo interpreta o IR semantico produzido por `rules.denote` --
incluindo `fix`, calculado por iteracao -- e permite confrontar o
resultado com a execucao real do programa Python de origem.

Se as duas coincidirem para varios estados iniciais, isso e' evidencia
(nao prova) de que as regras de traducao estao certas. Uma divergencia
e' prova de que estao erradas.

O `fix` e' aproximado operacionalmente: iterar o corpo enquanto a
guarda vale e' exatamente calcular o limite da cadeia de Kleene
F^0(bot) <= F^1(bot) <= ... no ponto sigma. Se o laco nao terminar,
o limite e' indefinido naquele ponto -- aqui isso vira `Diverge`.
"""
from __future__ import annotations

import sys
from typing import Dict, Optional

from .formula import BinOp, BoolOp, Compare, Const, Expr, UnOp, Var
from .rules import (
    Bottom, CondState, Denotation, FixApply, FunApply, Lookup, Named,
    StateExpr, Update,
)


class Diverge(Exception):
    """O laço não terminou dentro do limite de passos (ou de fato não termina)."""


class Undefined(Exception):
    """Leitura de variável ausente do estado inicial."""


_BIN = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "//": lambda a, b: a // b,
    "%": lambda a, b: a % b,
    "**": lambda a, b: a ** b,
}
_CMP = {
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


class Machine:
    """Interpreta StateExpr/Expr sobre estados concretos (dicts)."""

    def __init__(self, den: Denotation, max_steps: int = 100_000):
        self.den = den
        self.max_steps = max_steps
        self.functionals = {f.name: f for f in den.functionals}
        # escopo de equacoes auxiliares: nome -> StateDef
        self.defs: Dict[str, object] = {sd.name: sd for sd in den.state_defs}
        for fd in den.functionals:
            for sd in fd.local_defs:
                self.defs[sd.name] = sd
        self.binds: Dict[str, dict] = {}
        self.steps = 0

    # ---- expressoes ----
    def expr(self, e: Expr):
        if isinstance(e, Const):
            return e.value
        if isinstance(e, Lookup):
            st = self.state(e.state)
            if e.var not in st:
                raise Undefined(e.var)
            return st[e.var]
        if isinstance(e, Var):          # so aparece se subst nao rodou
            raise Undefined(e.name)
        if isinstance(e, BinOp):
            return _BIN[e.op](self.expr(e.left), self.expr(e.right))
        if isinstance(e, Compare):
            return _CMP[e.op](self.expr(e.left), self.expr(e.right))
        if isinstance(e, UnOp):
            v = self.expr(e.operand)
            return (-v) if e.op == "-" else (not v)
        if isinstance(e, BoolOp):
            if e.op == "and":
                r = True
                for o in e.operands:      # curto-circuito, como no Python
                    r = self.expr(o)
                    if not r:
                        return r
                return r
            r = False
            for o in e.operands:
                r = self.expr(o)
                if r:
                    return r
            return r
        raise TypeError(f"expressao desconhecida: {e!r}")

    # ---- estados ----
    def state(self, s: StateExpr) -> dict:
        if isinstance(s, Bottom):
            raise Diverge("bottom")
        if isinstance(s, Named):
            if s.name in self.binds:
                return self.binds[s.name]
            sd = self.defs.get(s.name)
            if sd is None:
                raise Undefined(s.name)
            value = self.state(sd.value)
            self.binds[s.name] = value       # memoiza
            return value
        if isinstance(s, Update):
            base = dict(self.state(s.base))
            base[s.var] = self.expr(s.value)
            return base
        if isinstance(s, CondState):
            return self.state(s.then_) if self.expr(s.cond) else self.state(s.else_)
        if isinstance(s, FixApply):
            return self.fix(s)
        if isinstance(s, FunApply):
            raise TypeError("f(...) só faz sentido dentro de um funcional")
        raise TypeError(f"estado desconhecido: {s!r}")

    def fix(self, node: FixApply) -> dict:
        """Menor ponto fixo aplicado: itera o corpo enquanto a guarda vale."""
        fd = self.functionals[node.functional]
        sigma = self.state(node.arg)
        saved = self.binds.get(fd.state)
        try:
            while True:
                self.steps += 1
                if self.steps > self.max_steps:
                    raise Diverge(f"mais de {self.max_steps} passos em {fd.name}")
                self.binds[fd.state] = sigma
                # equacoes locais dependem do sigma corrente: invalidar cache
                for sd in fd.local_defs:
                    self.binds.pop(sd.name, None)
                if not self.expr(fd.cond):
                    return sigma
                sigma = self.state(node_arg_of(fd))
        finally:
            if saved is None:
                self.binds.pop(fd.state, None)
            else:
                self.binds[fd.state] = saved


def node_arg_of(fd):
    """O argumento de `f` no ramo verdadeiro: f(<estado do corpo>)."""
    then_ = fd.then_
    if isinstance(then_, FunApply):
        return then_.arg
    raise TypeError("ramo verdadeiro do funcional deveria ser f(...)")


def run_denotation(den: Denotation, sigma0: dict, max_steps: int = 100_000) -> dict:
    """Aplica a denotação ao estado inicial e devolve o estado final."""
    m = Machine(den, max_steps)
    m.binds[den.initial_state] = dict(sigma0)
    return m.state(den.result)


def run_python(source: str, sigma0: dict, max_steps: int = 100_000) -> dict:
    """Executa o programa original de verdade, para comparação.

    Com limite de passos: o lado Python de um exemplo divergente
    (`while x > 0: x = x + 1`) roda para sempre, e o verificador
    precisa sobreviver a isso. O contador usa sys.settrace, que
    dispara uma vez por linha executada.
    """
    # globals e locals precisam ser o MESMO dict: com dois escopos, o
    # corpo de um `def` procura os outros `def` em globals e nao acha.
    env = {"__builtins__": {"range": range}}
    env.update(sigma0)
    code = compile(source, "<verificacao>", "exec")
    contador = [0]

    def tracer(frame, event, arg):
        if event == "line":
            contador[0] += 1
            if contador[0] > max_steps:
                raise Diverge(f"mais de {max_steps} linhas executadas")
        return tracer

    anterior = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(code, env)
    finally:
        sys.settrace(anterior)
    return {k: v for k, v in env.items()
            if k != "__builtins__" and not callable(v)}


def compare(source: str, den: Denotation, sigma0: dict,
            max_steps: int = 100_000) -> dict:
    """Roda os dois lados e devolve um relatório.

    Compara apenas as variáveis que o Python produziu: a denotação
    carrega o estado inteiro, e variáveis nunca escritas podem faltar
    de um lado.
    """
    report = {"sigma0": dict(sigma0), "ok": False}
    try:
        report["python"] = run_python(source, sigma0, max_steps)
    except Exception as exc:                      # noqa: BLE001
        report["python_erro"] = f"{type(exc).__name__}: {exc}"
    try:
        report["denotacao"] = run_denotation(den, sigma0, max_steps)
    except Diverge as exc:
        report["denotacao_erro"] = f"divergiu: {exc}"
    except Exception as exc:                      # noqa: BLE001
        report["denotacao_erro"] = f"{type(exc).__name__}: {exc}"

    if "python" in report and "denotacao" in report:
        d = report["denotacao"]
        diffs = {k: (v, d.get(k, "<ausente>"))
                 for k, v in report["python"].items() if d.get(k) != v}
        report["diferencas"] = diffs
        report["ok"] = not diffs
    return report
