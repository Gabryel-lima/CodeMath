"""Visitor: AST do Python -> IR de formula (codemath.formula).

Uma funcao por tipo de no. Nada de LaTeX aqui.
"""
from __future__ import annotations

import ast

from .formula import (
    Assign, BinOp, BoolOp, Cmd, Compare, Const, Expr, If, ParAssign, Seq,
    Skip, UnOp, Var, While,
)


class UnsupportedNode(Exception):
    """No fora do escopo do MVP."""

    def __init__(self, node: ast.AST):
        loc = getattr(node, "lineno", "?")
        super().__init__(
            f"construcao nao suportada no MVP: {type(node).__name__} (linha {loc})"
        )
        self.node = node


_ARITH = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
}
_CMP = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.Eq: "==", ast.NotEq: "!=",
}


class Translator:
    """Traduz uma ast.Module para o IR sintatico.

    Mantem uma tabela de funcoes puras definidas no proprio programa
    (`def f(x): return e`). Chamadas a elas sao expandidas por
    substituicao -- semantica valida para funcoes de primeira ordem
    sem efeitos colaterais e sem recursao.
    """

    def __init__(self):
        self.funcs = {}          # nome -> (params, corpo Expr)
        self._expanding = []     # pilha, para detectar recursao

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
        raise UnsupportedNode(node)

    def expr_BinOp(self, node: ast.BinOp) -> Expr:
        op = _ARITH.get(type(node.op))
        if op is None:
            raise UnsupportedNode(node)
        return BinOp(op, self.expr(node.left), self.expr(node.right))

    def expr_UnaryOp(self, node: ast.UnaryOp) -> Expr:
        if isinstance(node.op, ast.USub):
            return UnOp("-", self.expr(node.operand))
        if isinstance(node.op, ast.UAdd):
            return self.expr(node.operand)
        if isinstance(node.op, ast.Not):
            return UnOp("not", self.expr(node.operand))
        raise UnsupportedNode(node)

    def expr_Compare(self, node: ast.Compare) -> Expr:
        # Cadeias (a < b < c) viram conjuncao de comparacoes binarias.
        # Isso e' fiel porque no MVP expressoes sao puras: reavaliar
        # `b` nao tem efeito colateral.
        parts = []
        left = self.expr(node.left)
        for op_node, right_node in zip(node.ops, node.comparators):
            op = _CMP.get(type(op_node))
            if op is None:
                raise UnsupportedNode(node)
            right = self.expr(right_node)
            parts.append(Compare(op, left, right))
            left = right
        if len(parts) == 1:
            return parts[0]
        return BoolOp("and", tuple(parts))

    def expr_Call(self, node: ast.Call) -> Expr:
        if not isinstance(node.func, ast.Name):
            raise UnsupportedNode(node)
        name = node.func.id
        if name not in self.funcs:
            raise UnsupportedNode(node)
        if name in self._expanding:
            raise UnsupportedNode(node)   # recursao: precisa de fix
        if node.keywords:
            raise UnsupportedNode(node)
        params, body = self.funcs[name]
        if len(node.args) != len(params):
            raise UnsupportedNode(node)
        args = [self.expr(a) for a in node.args]
        binding = dict(zip(params, args))
        self._expanding.append(name)
        try:
            return _substitute(body, binding)
        finally:
            self._expanding.pop()

    def expr_BoolOp(self, node: ast.BoolOp) -> Expr:
        op = "and" if isinstance(node.op, ast.And) else "or"
        return BoolOp(op, tuple(self.expr(v) for v in node.values))

    # ---------------- comandos ----------------
    def block(self, body: list) -> Cmd:
        """Composicao sequencial de uma lista de statements."""
        cmds = []
        for stmt in body:
            cmd = self.stmt(stmt)
            if isinstance(cmd, Skip):
                continue
            if isinstance(cmd, Seq):
                cmds.extend(cmd.commands)
            else:
                cmds.append(cmd)
        if not cmds:
            return Skip()
        if len(cmds) == 1:
            return cmds[0]
        return Seq(tuple(cmds))

    def stmt(self, node: ast.stmt) -> Cmd:
        method = getattr(self, "stmt_" + type(node).__name__, None)
        if method is None:
            raise UnsupportedNode(node)
        return method(node)

    def stmt_Pass(self, node: ast.Pass) -> Cmd:
        return Skip()

    def stmt_Expr(self, node: ast.Expr) -> Cmd:
        # Expressao usada como statement: sem efeito no estado (MVP puro).
        return Skip()

    def stmt_Assign(self, node: ast.Assign) -> Cmd:
        # x, y = e1, e2  -> atribuicao SIMULTANEA
        if len(node.targets) == 1 and isinstance(node.targets[0], (ast.Tuple, ast.List)):
            tgt = node.targets[0]
            if not isinstance(node.value, (ast.Tuple, ast.List)):
                raise UnsupportedNode(node.value)
            if len(tgt.elts) != len(node.value.elts):
                raise UnsupportedNode(node)
            names = []
            for t in tgt.elts:
                if not isinstance(t, ast.Name):
                    raise UnsupportedNode(t)
                names.append(t.id)
            if len(set(names)) != len(names):
                raise UnsupportedNode(node)
            return ParAssign(tuple(names),
                             tuple(self.expr(v) for v in node.value.elts))
        value = self.expr(node.value)
        targets = []
        for t in node.targets:
            if not isinstance(t, ast.Name):
                raise UnsupportedNode(t)
            targets.append(t.id)
        if len(targets) == 1:
            return Assign(targets[0], value)
        # x = y = e  ==  x := e ; y := e
        return Seq(tuple(Assign(name, value) for name in targets))

    def stmt_AugAssign(self, node: ast.AugAssign) -> Cmd:
        if not isinstance(node.target, ast.Name):
            raise UnsupportedNode(node.target)
        op = _ARITH.get(type(node.op))
        if op is None:
            raise UnsupportedNode(node)
        return Assign(
            node.target.id,
            BinOp(op, Var(node.target.id), self.expr(node.value)),
        )

    def stmt_FunctionDef(self, node: ast.FunctionDef) -> Cmd:
        """Registra `def f(a, b): return e`. Nao gera comando algum."""
        a = node.args
        if (a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg
                or a.defaults or node.decorator_list):
            raise UnsupportedNode(node)
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            raise UnsupportedNode(node)   # so corpo de um unico return
        ret = node.body[0].value
        if ret is None:
            raise UnsupportedNode(node)
        params = [arg.arg for arg in a.args]
        self._expanding.append(node.name)   # recursao vira UnsupportedNode
        try:
            body = self.expr(ret)
        finally:
            self._expanding.pop()
        self.funcs[node.name] = (params, body)
        return Skip()

    def stmt_For(self, node: ast.For) -> Cmd:
        """`for i in range(a, b, s)` e' acucar sintatico:

            k := a ; while k < b do (i := k ; corpo ; k := k + s)   (s > 0)
            k := a ; while k > b do (i := k ; corpo ; k := k + s)   (s < 0)

        Dois cuidados que a versao ingenua erra:

        1. O passo precisa ser literal. Sem saber seu sinal nao da'
           para escolher a guarda, e guarda errada muda a denotacao.
        2. O contador e' uma variavel fresca `k`, e `i` recebe o valor
           DENTRO do corpo. Se `i` fosse o proprio contador, ao final
           do laco `i` valeria o primeiro valor que falha a guarda
           (6 em `range(1,6)`) em vez do ultimo valor iterado (5),
           que e' o que o Python deixa. E se o range for vazio, `i`
           nunca e' atribuida -- tambem como no Python.
        """
        if node.orelse:
            raise UnsupportedNode(node)
        if not isinstance(node.target, ast.Name):
            raise UnsupportedNode(node.target)
        it = node.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and it.func.id == "range" and not it.keywords
                and 1 <= len(it.args) <= 3):
            raise UnsupportedNode(it)
        if len(it.args) == 1:
            start, stop, step = Const(0), self.expr(it.args[0]), Const(1)
        elif len(it.args) == 2:
            start, stop, step = self.expr(it.args[0]), self.expr(it.args[1]), Const(1)
        else:
            start, stop = self.expr(it.args[0]), self.expr(it.args[1])
            step = self.expr(it.args[2])
            if not (isinstance(step, Const) and isinstance(step.value, int)
                    and step.value != 0):
                raise UnsupportedNode(it.args[2])
        var = node.target.id
        ctr = "#" + var          # fresco: "#" nao e' identificador Python
        guard = "<" if step.value > 0 else ">"
        body = self.block(node.body)
        parts = [Assign(var, Var(ctr))]
        if not isinstance(body, Skip):
            parts.append(body)
        parts.append(Assign(ctr, BinOp("+", Var(ctr), step)))
        return Seq((Assign(ctr, start),
                    While(Compare(guard, Var(ctr), stop), Seq(tuple(parts)))))

    def stmt_If(self, node: ast.If) -> Cmd:
        return If(
            self.expr(node.test),
            self.block(node.body),
            self.block(node.orelse) if node.orelse else Skip(),
        )

    def stmt_While(self, node: ast.While) -> Cmd:
        if node.orelse:
            raise UnsupportedNode(node)
        return While(self.expr(node.test), self.block(node.body))

    # ---------------- entrada ----------------
    def translate(self, module: ast.Module) -> Cmd:
        return self.block(module.body)


def translate(module: ast.Module) -> Cmd:
    return Translator().translate(module)


def _substitute(expr: Expr, binding: dict) -> Expr:
    """Troca Var(p) por binding[p]. Usado na expansao de funcoes puras."""
    if isinstance(expr, Var):
        return binding.get(expr.name, expr)
    if isinstance(expr, Const):
        return expr
    if isinstance(expr, BinOp):
        return BinOp(expr.op, _substitute(expr.left, binding),
                     _substitute(expr.right, binding))
    if isinstance(expr, Compare):
        return Compare(expr.op, _substitute(expr.left, binding),
                       _substitute(expr.right, binding))
    if isinstance(expr, UnOp):
        return UnOp(expr.op, _substitute(expr.operand, binding))
    if isinstance(expr, BoolOp):
        return BoolOp(expr.op, tuple(_substitute(o, binding) for o in expr.operands))
    raise TypeError(f"expressao desconhecida: {expr!r}")
