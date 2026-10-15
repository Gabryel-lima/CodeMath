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

import random
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .formula import (
    Atom, BinOp, BoolOp, Compare, Const, Expr, Func, IfExp, UnOp, Var, children,
)
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
_FUNC = {"abs": abs, "min": min, "max": max}


def eval_expr(e: Expr, read) -> object:
    """Avalia uma expressao com a semantica do Python.

    `read(no)` resolve as folhas que dependem de estado (Var ou
    Lookup); todo o resto -- operadores, curto-circuito, ramo unico do
    condicional -- e' comum ao avaliador da denotacao e ao das
    obrigacoes de Hoare.
    """
    if isinstance(e, Const):
        return e.value
    if isinstance(e, BinOp):
        return _BIN[e.op](eval_expr(e.left, read), eval_expr(e.right, read))
    if isinstance(e, Compare):
        return _CMP[e.op](eval_expr(e.left, read), eval_expr(e.right, read))
    if isinstance(e, UnOp):
        v = eval_expr(e.operand, read)
        return (-v) if e.op == "-" else (not v)
    if isinstance(e, BoolOp):
        if e.op == "->":                 # implicacao (so em Hoare)
            a, b = e.operands
            return (not eval_expr(a, read)) or bool(eval_expr(b, read))
        if e.op == "and":
            r = True
            for o in e.operands:          # curto-circuito, como no Python
                r = eval_expr(o, read)
                if not r:
                    return r
            return r
        r = False
        for o in e.operands:
            r = eval_expr(o, read)
            if r:
                return r
        return r
    if isinstance(e, IfExp):
        return eval_expr(e.then_ if eval_expr(e.cond, read) else e.else_, read)
    if isinstance(e, Func):
        return _FUNC[e.name](*(eval_expr(a, read) for a in e.args))
    if isinstance(e, Atom):
        raise Undefined(f"invariante {e.name} sem definicao")
    return read(e)


@dataclass
class Iteration:
    """Uma volta de um laco, registrada pelo Machine com `trace=True`."""
    functional: str          # F_k
    index: int               # 0 = estado de entrada (antes da 1a guarda)
    state: dict              # o estado ligado nesse ponto


class Machine:
    """Interpreta StateExpr/Expr sobre estados concretos (dicts)."""

    def __init__(self, den: Denotation, max_steps: int = 100_000,
                 trace: bool = False):
        self.den = den
        self.trace: Optional[List[Iteration]] = [] if trace else None
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
        return eval_expr(e, self._read)

    def _read(self, e: Expr):
        if isinstance(e, Lookup):
            st = self.state(e.state)
            if e.var not in st:
                raise Undefined(e.var)
            return st[e.var]
        if isinstance(e, Var):          # so aparece se subst nao rodou
            raise Undefined(e.name)
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
        volta = 0
        try:
            while True:
                if self.trace is not None:
                    self.trace.append(Iteration(fd.name, volta, dict(sigma)))
                volta += 1
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


def trace_denotation(den: Denotation, sigma0: dict,
                     max_steps: int = 100_000) -> tuple:
    """Como run_denotation, mas devolve (estado final, voltas).

    Cada volta e' o estado ligado no momento em que a guarda do laco e'
    testada: a volta 0 e' a entrada, a ultima e' a que sai. E' a
    sequencia sigma, F(sigma), F(F(sigma)), ... cujo limite e' fix(F)
    -- o material de um `expression_steps` no EduMotion.
    """
    m = Machine(den, max_steps, trace=True)
    m.binds[den.initial_state] = dict(sigma0)
    final = m.state(den.result)
    return final, m.trace


def run_python(source: str, sigma0: dict, max_steps: int = 100_000) -> dict:
    """Executa o programa original de verdade, para comparação.

    Com limite de passos: o lado Python de um exemplo divergente
    (`while x > 0: x = x + 1`) roda para sempre, e o verificador
    precisa sobreviver a isso. O contador usa sys.settrace, que
    dispara uma vez por linha executada.
    """
    # globals e locals precisam ser o MESMO dict: com dois escopos, o
    # corpo de um `def` procura os outros `def` em globals e nao acha.
    # So os builtins que o tradutor aceita; print vira no-op (a
    # traducao o trata como skip, e a saida poluiria o relatorio).
    builtins = {"range": range, "abs": abs, "min": min, "max": max,
                "print": lambda *a, **k: None}
    env = {"__builtins__": builtins}
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
    tipos = {}
    try:
        report["python"] = run_python(source, sigma0, max_steps)
    except Exception as exc:                      # noqa: BLE001
        report["python_erro"] = f"{type(exc).__name__}: {exc}"
        tipos["python"] = type(exc)
    try:
        report["denotacao"] = run_denotation(den, sigma0, max_steps)
    except Diverge as exc:
        report["denotacao_erro"] = f"divergiu: {exc}"
        tipos["denotacao"] = Diverge
    except Exception as exc:                      # noqa: BLE001
        report["denotacao_erro"] = f"{type(exc).__name__}: {exc}"
        tipos["denotacao"] = type(exc)

    if "python" in report and "denotacao" in report:
        d = report["denotacao"]
        ausente = object()
        diffs = {k: (v, d.get(k, "<ausente>"))
                 for k, v in report["python"].items()
                 if d.get(k, ausente) is ausente or d[k] != v
                 or type(d[k]) is not type(v)}
        report["diferencas"] = diffs
        report["ok"] = not diffs
    elif Diverge in tipos.values() and len(tipos) == 1:
        # Um lado estourou o limite e o outro terminou. Os limites contam
        # coisas diferentes (linhas executadas no Python, voltas de laco
        # na denotacao), entao perto do limite isso NAO prova nada:
        # aumente max_steps e rode de novo.
        report["inconclusivo"] = True
    elif len(tipos) == 2 and tipos["python"] is tipos["denotacao"]:
        # Os dois lados falham do mesmo jeito (os dois divergem, ou os
        # dois dividem por zero): a denotacao concorda com o programa,
        # inclusive em ser indefinida naquele estado.
        report["ok"] = True
        report["mesmo_erro"] = tipos["python"].__name__
    return report


# --------------------------------------------------------------------------
# Obrigacoes de Hoare: procurar contraexemplo (testar, nao provar)
# --------------------------------------------------------------------------
def _tem_atom(e: Expr) -> bool:
    return isinstance(e, Atom) or any(_tem_atom(c) for c in children(e))


@dataclass
class ObligationCheck:
    kind: str
    invariant: str
    tested: int = 0
    skipped: int = 0                         # estado onde a formula nem avalia
    counterexample: Optional[dict] = None


def check_obligations(result, variables: Iterable[str], samples: int = 200,
                      seed: int = 0, low: int = -6, high: int = 12,
                      extra_states: Iterable[dict] = ()) -> List[ObligationCheck]:
    """Avalia cada obrigacao (ja' instanciada com invariantes dados) em
    estados de amostra. Um estado que a torna falsa e' um contraexemplo:
    PROVA que o invariante proposto nao serve. Nenhum contraexemplo e'
    so' evidencia -- a obrigacao pode falhar fora da amostra.
    """
    nomes = sorted(set(variables))
    rng = random.Random(seed)
    estados = list(extra_states) + [
        {v: rng.randint(low, high) for v in nomes} for _ in range(samples)]
    out = []
    for o in result.obligations:
        chk = ObligationCheck(o.kind, o.invariant)
        if _tem_atom(o.formula):
            # Invariante ainda opaco dentro da formula. Avaliar mesmo assim
            # "passaria" sempre que o antecedente da implicacao fosse falso
            # -- acerto vacuo, que pareceria evidencia sem ser.
            chk.skipped = len(estados)
            out.append(chk)
            continue
        for st in estados:
            def read(e, st=st):
                if isinstance(e, Var):
                    if e.name not in st:
                        raise Undefined(e.name)
                    return st[e.name]
                raise TypeError(f"expressao desconhecida: {e!r}")
            try:
                ok = eval_expr(o.formula, read)
            except (ArithmeticError, Undefined, TypeError, ValueError):
                chk.skipped += 1
                continue
            chk.tested += 1
            if not ok:
                chk.counterexample = dict(st)
                break
        out.append(chk)
    return out
