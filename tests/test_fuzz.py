"""Teste diferencial: programas aleatorios, denotacao x Python real.

Os exemplos fixos so' cobrem o que alguem lembrou de escrever. Aqui um
gerador monta programas pequenos combinando TODAS as construcoes
suportadas (atribuicao multipla e simultanea, if/elif, for com passo
negativo, while, break, continue, else de laco, ternario, abs/min/max,
funcao pura) e o verificador confere cada um contra o Python.

Uma divergencia e' prova de bug na traducao. Foi assim que apareceram
`x = y = x + 1` e `for i` aninhado com o mesmo nome -- os dois davam
formula errada sem erro nenhum.

Rodar mais casos:  CODEMATH_FUZZ=5000 python3 -m unittest tests.test_fuzz
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemath import denote, parse_source, translate
from codemath.evaluator import compare

VARS = ("a", "b", "c")


class Gerador:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.contadores = 0          # nomes k0, k1... de whiles limitados
        self.prof_laco = 0

    def expr(self, d: int = 0) -> str:
        r = self.rng
        if d > 2 or r.random() < 0.3:
            return r.choice([str(r.randint(-3, 9)), r.choice(VARS)])
        k = r.randint(0, 9)
        if k <= 3:
            op = r.choice(["+", "-", "*", "//", "%"])
            direita = self.expr(d + 1)
            if op in ("//", "%") and r.random() < 0.8:
                # divisor quase sempre nao nulo: divisao por zero dos
                # dois lados "concorda", mas nao testa nada alem disso
                direita = str(r.choice([1, 2, 3, 5, -2, -3]))
            elif op == "*":
                # produto sempre por literal: `a = a * a` num laco eleva
                # ao quadrado a cada volta, e uma multiplicacao de
                # milhoes de digitos trava numa unica chamada em C, onde
                # nenhum limite de passos alcanca
                direita = str(r.randint(-3, 3))
            return f"({self.expr(d + 1)} {op} {direita})"
        if k == 4:
            return f"-{self.expr(d + 1)}"
        if k == 5:
            return f"({self.expr(d + 1)} if {self.cond(d + 1)} else {self.expr(d + 1)})"
        if k == 6:
            return f"abs({self.expr(d + 1)})"
        if k == 7:
            f = r.choice(["min", "max"])
            return f"{f}({self.expr(d + 1)}, {self.expr(d + 1)})"
        if k == 8:
            return f"dobro({self.expr(d + 1)})"
        return r.choice(VARS)

    def cond(self, d: int = 0) -> str:
        r = self.rng
        k = r.randint(0, 5)
        if k <= 3 or d > 2:
            op = r.choice(["<", "<=", ">", ">=", "==", "!="])
            return f"{self.expr(d + 1)} {op} {self.expr(d + 1)}"
        if k == 4:
            return f"not ({self.cond(d + 1)})"
        return f"({self.cond(d + 1)}) {r.choice(['and', 'or'])} ({self.cond(d + 1)})"

    def bloco(self, ind: int, d: int, n: int = 0) -> list:
        n = n or self.rng.randint(1, 3)
        out = []
        for _ in range(n):
            out += self.stmt(ind, d)
        return out

    def stmt(self, ind: int, d: int) -> list:
        r = self.rng
        p = "    " * ind
        k = r.randint(0, 11 if d < 2 else 4)
        if k <= 1:
            return [f"{p}{r.choice(VARS)} = {self.expr()}"]
        if k == 2:
            return [f"{p}{r.choice(VARS)} {r.choice(['+=', '-=', '//='])} {self.expr()}"]
        if k == 3:
            x, y = r.sample(VARS, 2)
            return [f"{p}{x}, {y} = {self.expr()}, {self.expr()}"]
        if k == 4:
            x, y = r.sample(VARS, 2)
            return [f"{p}{x} = {y} = {self.expr()}"]
        if k == 5 and self.prof_laco:
            return [f"{p}if {self.cond()}:", f"{p}    {r.choice(['break', 'continue'])}"]
        if k <= 6:
            out = [f"{p}if {self.cond()}:"] + self.bloco(ind + 1, d + 1)
            if r.random() < 0.3:
                out += [f"{p}elif {self.cond()}:"] + self.bloco(ind + 1, d + 1)
            if r.random() < 0.5:
                out += [f"{p}else:"] + self.bloco(ind + 1, d + 1)
            return out
        if k <= 8:
            var = r.choice(VARS + ("i", "i"))
            ini, fim = r.randint(-2, 4), r.choice([str(r.randint(-2, 6)), r.choice(VARS)])
            passo = r.choice(["", "", ", 2", ", -1", ", -2"])
            out = [f"{p}for {var} in range({ini}, {fim}{passo}):"]
            return out + self.laco(ind, d)
        # while limitado: o contador k so e' escrito aqui
        cnt = f"k{self.contadores}"
        self.contadores += 1
        out = [f"{p}{cnt} = 0", f"{p}while {cnt} < {r.randint(0, 5)} and ({self.cond()}):",
               f"{p}    {cnt} += 1"]
        return out + self.laco(ind, d)

    def laco(self, ind: int, d: int) -> list:
        self.prof_laco += 1
        out = self.bloco(ind + 1, d + 1)
        self.prof_laco -= 1
        if self.rng.random() < 0.25:
            out += [f"{'    ' * ind}else:"] + self.bloco(ind + 1, d + 1, 1)
        return out

    def programa(self) -> str:
        linhas = ["def dobro(x):", "    t = 2 * x", "    return t"]
        linhas += self.bloco(0, 0, self.rng.randint(2, 5))
        return "\n".join(linhas) + "\n"


def rodar(n: int, semente: int):
    """Gera n programas e devolve a lista de (programa, sigma0, relatorio)
    que divergiram."""
    rng = random.Random(semente)
    falhas = []
    for _ in range(n):
        src = Gerador(rng).programa()
        s0 = {v: rng.randint(-4, 8) for v in VARS}
        den = denote(translate(parse_source(src)))
        rel = compare(src, den, s0, max_steps=3_000)
        if not rel["ok"] and not rel.get("inconclusivo"):
            falhas.append((src, s0, rel))
    return falhas


class TestFuzz(unittest.TestCase):
    def test_denotacao_igual_ao_python(self):
        n = int(os.environ.get("CODEMATH_FUZZ", "400"))
        falhas = rodar(n, semente=1234)
        if falhas:
            src, s0, rel = falhas[0]
            self.fail(f"{len(falhas)} de {n} programas divergem. Primeiro:\n"
                      f"{src}\nσ₀ = {s0}\n{rel}")


if __name__ == "__main__":
    unittest.main()
