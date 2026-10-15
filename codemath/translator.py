"""Visitor: AST do Python -> IR de formula (codemath.formula).

Uma funcao por tipo de no. Nada de LaTeX aqui.

Variaveis internas
------------------
Algumas construcoes so' se traduzem com variaveis que o programa nao
tem. Os nomes comecam com `#`, que nao e' identificador Python, entao
nunca colidem com variaveis do usuario:

    #i        contador fresco do `for i in range(...)`
    #i.fim    limite do range, congelado na entrada do laco
    #.brk     bandeira de `break` do laco
    #.cont    bandeira de `continue` do laco

Um apostrofo no fim (`#i'`, `#.brk''`) separa lacos ANINHADOS que
usariam o mesmo nome. Lacos em sequencia reaproveitam o nome: cada um
reinicia suas variaveis antes de comecar. latex_render.ident e unicode_render.ident sabem
desenhar cada forma.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .analysis import assigned_vars
from .formula import (
    BUILTINS, Assign, BinOp, BoolOp, Cmd, Compare, Const, Expr, Func, If,
    IfExp, ParAssign, Seq, Skip, UnOp, Var, While, children, map_expr,
)


class UnsupportedNode(Exception):
    """Construcao fora do escopo. Diz o que e', onde esta e, se houver,
    o que fazer em vez disso."""

    def __init__(self, node: ast.AST, motivo: str = ""):
        loc = getattr(node, "lineno", "?")
        col = getattr(node, "col_offset", None)
        onde = f"linha {loc}" + (f", coluna {col + 1}" if col is not None else "")
        msg = f"construcao nao suportada: {type(node).__name__} ({onde})"
        if motivo:
            msg += f" -- {motivo}"
        super().__init__(msg)
        self.node = node
        self.motivo = motivo


_ARITH = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
}
_CMP = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.Eq: "==", ast.NotEq: "!=",
}


@dataclass
class _Laco:
    """Contexto de um laco em traducao.

    `nomes` sao as variaveis internas deste laco; as bandeiras so' sao
    criadas no primeiro `break`/`continue` -- laco sem salto nao ganha
    variavel nenhuma.
    """
    nomes: Set[str] = field(default_factory=set)
    brk: Optional[str] = None
    cont: Optional[str] = None


@dataclass
class _Funcao:
    """Funcao pura do programa: params e o corpo ja' como expressao."""
    params: Tuple[str, ...]
    body: Expr


class Translator:
    """Traduz uma ast.Module para o IR sintatico.

    Mantem uma tabela de funcoes puras definidas no proprio programa.
    Chamadas a elas sao expandidas por substituicao -- semantica valida
    para funcoes de primeira ordem sem efeitos colaterais e sem
    recursao.
    """

    def __init__(self):
        self.funcs: Dict[str, _Funcao] = {}
        self._expanding: List[str] = []    # pilha, para detectar recursao
        self._lacos: List[_Laco] = []      # lacos abertos, do externo ao interno

    # ---------------- nomes frescos ----------------
    def _fresco(self, base: str, laco: _Laco) -> str:
        """`base`, ou `base'`, `base''`... o primeiro que nenhum laco
        ABERTO usa. Basta isso: o laco interno nao pode pisar no
        contador do externo, mas dois lacos em sequencia podem dividir
        o nome, porque cada um o reinicia antes de comecar."""
        em_uso = set(laco.nomes).union(*(l.nomes for l in self._lacos))
        nome = base
        while nome in em_uso:
            nome += "'"
        laco.nomes.add(nome)
        return nome

    # ---------------- expressoes ----------------
    def expr(self, node: ast.expr) -> Expr:
        method = getattr(self, "expr_" + type(node).__name__, None)
        if method is None:
            raise UnsupportedNode(node)
        return method(node)

    def expr_Name(self, node: ast.Name) -> Expr:
        return Var(node.id)

    def expr_Constant(self, node: ast.Constant) -> Expr:
        if isinstance(node.value, (int, float, bool)) or node.value is None:
            return Const(node.value)
        raise UnsupportedNode(node, "so numeros, booleanos e None")

    def expr_BinOp(self, node: ast.BinOp) -> Expr:
        op = _ARITH.get(type(node.op))
        if op is None:
            raise UnsupportedNode(node, f"operador {type(node.op).__name__}")
        return BinOp(op, self.expr(node.left), self.expr(node.right))

    def expr_UnaryOp(self, node: ast.UnaryOp) -> Expr:
        if isinstance(node.op, ast.USub):
            return UnOp("-", self.expr(node.operand))
        if isinstance(node.op, ast.UAdd):
            return self.expr(node.operand)
        if isinstance(node.op, ast.Not):
            return UnOp("not", self.expr(node.operand))
        raise UnsupportedNode(node, "operador bit a bit")

    def expr_Compare(self, node: ast.Compare) -> Expr:
        # Cadeias (a < b < c) viram conjuncao de comparacoes binarias.
        # Isso e' fiel porque expressoes sao puras: reavaliar `b` nao
        # tem efeito colateral.
        parts = []
        left = self.expr(node.left)
        for op_node, right_node in zip(node.ops, node.comparators):
            op = _CMP.get(type(op_node))
            if op is None:
                raise UnsupportedNode(node, f"comparacao {type(op_node).__name__}")
            right = self.expr(right_node)
            parts.append(Compare(op, left, right))
            left = right
        if len(parts) == 1:
            return parts[0]
        return BoolOp("and", tuple(parts))

    def expr_IfExp(self, node: ast.IfExp) -> Expr:
        return IfExp(self.expr(node.test), self.expr(node.body),
                     self.expr(node.orelse))

    def expr_Call(self, node: ast.Call) -> Expr:
        if not isinstance(node.func, ast.Name):
            raise UnsupportedNode(node, "so chamadas a funcoes pelo nome")
        name = node.func.id
        if name in self.funcs:
            return self._expandir(node, name)
        if name in self._expanding:
            raise UnsupportedNode(node, "recursao precisa de ponto fixo de funcoes")
        if name in BUILTINS:
            if node.keywords:
                raise UnsupportedNode(node, f"{name} com argumento nomeado")
            minimo, maximo = BUILTINS[name]
            n = len(node.args)
            if n < minimo or (maximo is not None and n > maximo):
                raise UnsupportedNode(node, f"{name} com {n} argumento(s)")
            return Func(name, tuple(self.expr(a) for a in node.args))
        raise UnsupportedNode(node, f"funcao desconhecida {name!r}")

    def _expandir(self, node: ast.Call, name: str) -> Expr:
        f = self.funcs[name]
        if any(k.arg is None for k in node.keywords):
            raise UnsupportedNode(node, "**kwargs")
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise UnsupportedNode(node, "*args")
        args: Dict[str, Expr] = dict(zip(f.params, (self.expr(a) for a in node.args)))
        for kw in node.keywords:
            if kw.arg not in f.params or kw.arg in args:
                raise UnsupportedNode(node, f"argumento {kw.arg!r} invalido")
            args[kw.arg] = self.expr(kw.value)
        if len(node.args) > len(f.params) or set(args) != set(f.params):
            raise UnsupportedNode(node, f"{name} espera {len(f.params)} argumento(s)")
        return _substitute(f.body, args)

    def expr_BoolOp(self, node: ast.BoolOp) -> Expr:
        op = "and" if isinstance(node.op, ast.And) else "or"
        return BoolOp(op, tuple(self.expr(v) for v in node.values))

    # ---------------- comandos ----------------
    def block(self, body: list) -> Cmd:
        """Composicao sequencial de uma lista de statements.

        Dentro de um laco, um statement que PODE ter executado `break`
        ou `continue` (atribuiu a bandeira do laco) protege o resto do
        bloco com `if not bandeira`. Depois de um `break`/`continue`
        direto, o resto do bloco e' inalcancavel e e' descartado.
        """
        cmds: List[Cmd] = []
        for k, stmt in enumerate(body):
            cmd = self.stmt(stmt)
            _anexar(cmds, cmd)
            if isinstance(stmt, (ast.Break, ast.Continue)):
                break
            resto = body[k + 1:]
            if not resto or not self._lacos:
                continue
            flags = _bandeiras_escritas(cmd, self._lacos[-1])
            if flags:
                cauda = self.block(resto)
                if not isinstance(cauda, Skip):
                    cmds.append(If(_nenhuma(flags), cauda, Skip()))
                break
        return _seq(cmds)

    def stmt(self, node: ast.stmt) -> Cmd:
        method = getattr(self, "stmt_" + type(node).__name__, None)
        if method is None:
            raise UnsupportedNode(node)
        return method(node)

    def stmt_Pass(self, node: ast.Pass) -> Cmd:
        return Skip()

    def stmt_Expr(self, node: ast.Expr) -> Cmd:
        """Expressao usada como statement.

        `print(...)`, docstring e qualquer expressao que o IR aceite
        (as do IR sao puras por construcao) nao mudam o estado: viram
        skip. O resto pode mudar (um `lista.append(x)`), e recusar e'
        mais honesto do que fingir que nada aconteceu.
        """
        v = node.value
        if isinstance(v, ast.Constant):          # docstring, `...`
            return Skip()
        if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id == "print"):
            return Skip()
        try:
            self.expr(v)
        except UnsupportedNode:
            raise UnsupportedNode(node, "expressao com possivel efeito colateral") from None
        return Skip()

    def stmt_Assign(self, node: ast.Assign) -> Cmd:
        # x, y = e1, e2  -> atribuicao SIMULTANEA
        if len(node.targets) == 1 and isinstance(node.targets[0], (ast.Tuple, ast.List)):
            tgt = node.targets[0]
            if not isinstance(node.value, (ast.Tuple, ast.List)):
                raise UnsupportedNode(node.value, "desempacotar exige tupla literal")
            if len(tgt.elts) != len(node.value.elts):
                raise UnsupportedNode(node, "quantidades diferentes dos dois lados")
            names = [_nome_alvo(t) for t in tgt.elts]
            if len(set(names)) != len(names):
                raise UnsupportedNode(node, "variavel repetida no lado esquerdo")
            return ParAssign(tuple(names),
                             tuple(self.expr(v) for v in node.value.elts))
        value = self.expr(node.value)
        targets = [_nome_alvo(t) for t in node.targets]
        if len(targets) == 1:
            return Assign(targets[0], value)
        # x = y = e: o Python avalia `e` UMA vez e atribui a todos. Como
        # sequencia (x := e; y := e) estaria errado quando `e` le algum
        # dos alvos: `x = y = x + 1` daria y = x + 2. Repetidos (`x = x
        # = e`) recebem o mesmo valor, entao basta um.
        unicos = list(dict.fromkeys(targets))
        if len(unicos) == 1:
            return Assign(unicos[0], value)
        return ParAssign(tuple(unicos), tuple(value for _ in unicos))

    def stmt_AnnAssign(self, node: ast.AnnAssign) -> Cmd:
        """`x: int = e` -- a anotacao nao muda a semantica."""
        if node.value is None:
            return Skip()
        return Assign(_nome_alvo(node.target), self.expr(node.value))

    def stmt_AugAssign(self, node: ast.AugAssign) -> Cmd:
        name = _nome_alvo(node.target)
        op = _ARITH.get(type(node.op))
        if op is None:
            raise UnsupportedNode(node, f"operador {type(node.op).__name__}")
        return Assign(name, BinOp(op, Var(name), self.expr(node.value)))

    def stmt_FunctionDef(self, node: ast.FunctionDef) -> Cmd:
        """Registra uma funcao pura. Nao gera comando algum.

        O corpo precisa virar UMA expressao: atribuicoes locais sao
        substituidas adiante (let), e `if` com `return` nos ramos vira
        expressao condicional. Ver `_corpo_puro`.
        """
        a = node.args
        if (a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg
                or a.defaults or node.decorator_list):
            raise UnsupportedNode(node, "so parametros posicionais simples")
        params = tuple(arg.arg for arg in a.args)
        locais = _atribuidos(node.body) - set(params)
        self._expanding.append(node.name)   # recursao vira UnsupportedNode
        try:
            body = self._corpo_puro(node.body, {p: Var(p) for p in params}, locais, node)
        finally:
            self._expanding.pop()
        self.funcs[node.name] = _Funcao(params, body)
        return Skip()

    def _corpo_puro(self, stmts: list, env: Dict[str, Expr], locais: Set[str],
                    fn: ast.FunctionDef) -> Expr:
        """Corpo de funcao -> expressao, por substituicao.

            t = a * a        env = {t: a*a}
            return t + 1     ->  a*a + 1

            if c: return x   ->  x se c, senao <resto do corpo>
            <resto>

        `env` leva cada nome local ao seu valor ATUAL. Ler um local
        ainda nao atribuido e' UnboundLocalError no Python; aqui, erro
        de traducao, e nao uma leitura silenciosa da variavel global de
        mesmo nome.
        """
        for k, s in enumerate(stmts):
            if isinstance(s, ast.Return):
                if s.value is None:
                    raise UnsupportedNode(s, "return sem valor")
                return self._em_env(s.value, env, locais)
            if isinstance(s, ast.If):
                resto = stmts[k + 1:]
                cond = self._em_env(s.test, env, locais)
                then_ = self._corpo_puro(list(s.body) + resto, dict(env), locais, fn)
                else_ = self._corpo_puro(list(s.orelse) + resto, dict(env), locais, fn)
                return IfExp(cond, then_, else_)
            if isinstance(s, (ast.Pass,)) or (isinstance(s, ast.Expr)
                                              and isinstance(s.value, ast.Constant)):
                continue
            if isinstance(s, ast.Assign) and len(s.targets) == 1:
                tgt = s.targets[0]
                if isinstance(tgt, ast.Name):
                    env[tgt.id] = self._em_env(s.value, env, locais)
                    continue
                if (isinstance(tgt, ast.Tuple) and isinstance(s.value, ast.Tuple)
                        and len(tgt.elts) == len(s.value.elts)):
                    novos = [self._em_env(v, env, locais) for v in s.value.elts]
                    for t, v in zip(tgt.elts, novos):
                        env[_nome_alvo(t)] = v
                    continue
            if isinstance(s, ast.AugAssign):
                name = _nome_alvo(s.target)
                op = _ARITH.get(type(s.op))
                if op is None or name not in env:
                    raise UnsupportedNode(s)
                env[name] = BinOp(op, env[name], self._em_env(s.value, env, locais))
                continue
            raise UnsupportedNode(s, f"no corpo de {fn.name}: so atribuicao, if e return")
        raise UnsupportedNode(fn, f"{fn.name} pode terminar sem return")

    def _em_env(self, node: ast.expr, env: Dict[str, Expr], locais: Set[str]) -> Expr:
        e = self.expr(node)

        def go(x: Expr) -> Expr:
            if isinstance(x, Var):
                if x.name in env:
                    return env[x.name]
                if x.name in locais:
                    raise UnsupportedNode(node, f"{x.name!r} lida antes de ser atribuida")
                return x
            return map_expr(x, go)
        return go(e)

    # ---------------- lacos ----------------
    def _fechar_laco(self) -> None:
        """Tira o laco da pilha e passa os nomes dele para o pai.

        Sem isso, um laco interno que ja' fechou e um `break` posterior
        do externo podiam ganhar a mesma bandeira -- e o `break` do
        interno encerraria o externo.
        """
        laco = self._lacos.pop()
        if self._lacos:
            self._lacos[-1].nomes |= laco.nomes

    def stmt_Break(self, node: ast.Break) -> Cmd:
        if not self._lacos:
            raise UnsupportedNode(node, "break fora de laco")
        laco = self._lacos[-1]
        if laco.brk is None:
            laco.brk = self._fresco("#.brk", laco)
        return Assign(laco.brk, Const(True))

    def stmt_Continue(self, node: ast.Continue) -> Cmd:
        if not self._lacos:
            raise UnsupportedNode(node, "continue fora de laco")
        laco = self._lacos[-1]
        if laco.cont is None:
            laco.cont = self._fresco("#.cont", laco)
        return Assign(laco.cont, Const(True))

    def _montar_laco(self, laco: _Laco, cond: Expr, corpo: List[Cmd],
                     antes: List[Cmd], orelse: list) -> Cmd:
        """Fecha um laco ja' traduzido, com as bandeiras que ele usou.

            brk := false;                         (so se houve break)
            while not brk and b do
                (cont := false; corpo)            (so se houve continue)
            ; if not brk then <else>              (o else do Python)

        `break` e `continue` quebram a assinatura [[c]] : State -> State
        se tratados como saltos. Bandeiras sao a saida classica: o salto
        vira estado, e toda regra continua valendo sem mudar de tipo. O
        preco e' que a formula ganha variaveis que o programa nao tem.
        """
        if laco.cont:
            corpo = [Assign(laco.cont, Const(False))] + corpo
        guard = cond
        if laco.brk:
            antes = antes + [Assign(laco.brk, Const(False))]
            guard = BoolOp("and", (UnOp("not", Var(laco.brk)), cond))
        partes = antes + [While(guard, _seq(_achatar(corpo)))]
        if orelse:
            senao = self.block(orelse)
            if not isinstance(senao, Skip):
                partes.append(If(UnOp("not", Var(laco.brk)), senao, Skip())
                              if laco.brk else senao)
        return _seq(_achatar(partes))

    def stmt_While(self, node: ast.While) -> Cmd:
        cond = self.expr(node.test)
        laco = _Laco()
        self._lacos.append(laco)
        try:
            corpo = self.block(node.body)
        finally:
            self._fechar_laco()
        return self._montar_laco(laco, cond, [corpo], [], node.orelse)

    def stmt_For(self, node: ast.For) -> Cmd:
        """`for i in range(a, b, s)` e' acucar sintatico:

            k := a ; while k < b do (i := k ; corpo ; k := k + s)   (s > 0)
            k := a ; while k > b do (i := k ; corpo ; k := k + s)   (s < 0)

        Cuidados que a versao ingenua erra:

        1. O passo precisa ser literal (inclusive negativo, `-1`). Sem
           saber o sinal nao da' para escolher a guarda.
        2. O contador e' uma variavel fresca `k`, e `i` recebe o valor
           DENTRO do corpo. Se `i` fosse o proprio contador, ao final
           do laco `i` valeria o primeiro valor que falha a guarda
           (6 em `range(1,6)`) em vez do ultimo valor iterado (5),
           que e' o que o Python deixa. E se o range for vazio, `i`
           nunca e' atribuida -- tambem como no Python.
        3. O contador e' unico no programa: dois `for i` aninhados com
           o mesmo contador fariam o interno zerar o externo.
        4. `range(a, b)` e' avaliado UMA vez. Se o corpo muda uma
           variavel de `b` (`for i in range(n): n = n - 1`), a guarda
           `k < b` releria o `b` novo. Nesse caso o limite vai para
           uma variavel fresca `#i.fim`, congelada antes do laco.
        """
        if not isinstance(node.target, ast.Name):
            raise UnsupportedNode(node.target, "variavel do for precisa ser um nome")
        it = node.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and it.func.id == "range" and not it.keywords
                and 1 <= len(it.args) <= 3):
            raise UnsupportedNode(it, "so for ... in range(...)")
        if len(it.args) == 1:
            start, stop, step = Const(0), self.expr(it.args[0]), 1
        else:
            start, stop = self.expr(it.args[0]), self.expr(it.args[1])
            step = 1 if len(it.args) == 2 else _inteiro_literal(it.args[2])
            if step is None or step == 0:
                raise UnsupportedNode(it.args[2], "passo do range precisa ser inteiro literal nao nulo")
        var = node.target.id
        laco = _Laco()
        ctr = self._fresco("#" + var, laco)
        self._lacos.append(laco)
        try:
            corpo = self.block(node.body)
            antes: List[Cmd] = [Assign(ctr, start)]
            if _vars(stop) & (assigned_vars(corpo) | {var}):
                fim = self._fresco("#" + var + ".fim", laco)
                antes.append(Assign(fim, stop))
                stop = Var(fim)
        finally:
            self._fechar_laco()
        guard = Compare("<" if step > 0 else ">", Var(ctr), stop)
        incremento = Assign(ctr, BinOp("+", Var(ctr), Const(step)) if step > 0
                            else BinOp("-", Var(ctr), Const(-step)))
        # o incremento fica fora do bloco protegido: `continue` pula o
        # corpo, mas o contador ainda anda
        return self._montar_laco(laco, guard, [Assign(var, Var(ctr)), corpo, incremento],
                                 antes, node.orelse)

    def stmt_If(self, node: ast.If) -> Cmd:
        return If(
            self.expr(node.test),
            self.block(node.body),
            self.block(node.orelse) if node.orelse else Skip(),
        )

    # ---------------- entrada ----------------
    def translate(self, module: ast.Module) -> Cmd:
        return self.block(module.body)


def translate(module: ast.Module) -> Cmd:
    return Translator().translate(module)


# --------------------------------------------------------------------------
# Auxiliares
# --------------------------------------------------------------------------
def _nome_alvo(t: ast.expr) -> str:
    if not isinstance(t, ast.Name):
        raise UnsupportedNode(t, "so atribuicao a nomes (sem indice ou atributo)")
    return t.id


def _inteiro_literal(node: ast.expr):
    """O valor de `3` ou `-3` no fonte; None se nao for inteiro literal."""
    sinal = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sinal = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool)):
        return sinal * node.value
    return None


def _atribuidos(stmts: list) -> Set[str]:
    """Nomes atribuidos em qualquer ponto de uma lista de statements."""
    out: Set[str] = set()
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
    return out


def _vars(e: Expr) -> Set[str]:
    if isinstance(e, Var):
        return {e.name}
    out: Set[str] = set()
    for x in children(e):
        out |= _vars(x)
    return out


def _bandeiras_escritas(c: Cmd, laco: _Laco) -> List[str]:
    escritas = assigned_vars(c)
    return [f for f in (laco.brk, laco.cont) if f and f in escritas]


def _nenhuma(flags: List[str]) -> Expr:
    """not f  |  not (f1 or f2)"""
    if len(flags) == 1:
        return UnOp("not", Var(flags[0]))
    return UnOp("not", BoolOp("or", tuple(Var(f) for f in flags)))


def _anexar(cmds: List[Cmd], cmd: Cmd) -> None:
    if isinstance(cmd, Skip):
        return
    if isinstance(cmd, Seq):
        cmds.extend(cmd.commands)
    else:
        cmds.append(cmd)


def _achatar(cmds: List[Cmd]) -> List[Cmd]:
    out: List[Cmd] = []
    for c in cmds:
        _anexar(out, c)
    return out


def _seq(cmds: List[Cmd]) -> Cmd:
    if not cmds:
        return Skip()
    if len(cmds) == 1:
        return cmds[0]
    return Seq(tuple(cmds))


def _substitute(expr: Expr, binding: dict) -> Expr:
    """Troca Var(p) por binding[p], simultaneamente. Expansao de funcoes."""
    if isinstance(expr, Var):
        return binding.get(expr.name, expr)
    return map_expr(expr, lambda x: _substitute(x, binding))
