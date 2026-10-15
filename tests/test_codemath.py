"""Testes do nucleo. Rodar: python3 -m unittest discover -s tests"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemath import parse_source, translate, denote
from codemath.formula import (Assign, Atom, BinOp, BoolOp, Compare, Const, If,
                              ParAssign, Seq, Var, While)
from codemath.latex_render import expr_to_latex, render_equations, state_to_latex
from codemath.rules import CondState, FixApply, Lookup, Named, Update
from codemath.translator import UnsupportedNode


def ir(code):
    return translate(parse_source(code))


class TestTranslator(unittest.TestCase):
    def test_atribuicao(self):
        self.assertEqual(ir("x = 0"), Assign("x", Const(0)))

    def test_sequencia_achatada(self):
        c = ir("x = 0\ny = 1\nz = 2")
        self.assertIsInstance(c, Seq)
        self.assertEqual(len(c.commands), 3)

    def test_expressao_composta(self):
        c = ir("x = 1 + 2 * 3")
        self.assertEqual(c.value, BinOp("+", Const(1), BinOp("*", Const(2), Const(3))))

    def test_comparacao_encadeada_vira_conjuncao(self):
        c = ir("x = 0\nwhile 0 < x < 10:\n    x = x + 1")
        cond = c.commands[1].cond
        self.assertEqual(cond, BoolOp("and", (
            Compare("<", Const(0), Var("x")),
            Compare("<", Var("x"), Const(10)),
        )))

    def test_augassign(self):
        self.assertEqual(ir("x += 2"), Assign("x", BinOp("+", Var("x"), Const(2))))

    def test_if_sem_else_vira_skip(self):
        c = ir("if x > 0:\n    y = 1")
        self.assertIsInstance(c, If)
        self.assertEqual(c.else_branch.__class__.__name__, "Skip")

    def test_fora_do_escopo(self):
        for codigo in [
            "class C:\n    pass",
            "x = [1, 2]",
            "x[0] = 1",
            "def f(a):\n    return f(a)",          # recursao precisa de fix
            "def g(a):\n    b = a\n    return b",  # corpo com mais de um stmt
            "for i in [1, 2]:\n    x = i",          # so range
            "for i in range(0, 10, s):\n    x = i",  # passo nao literal
            "while x:\n    pass\nelse:\n    y = 1",
        ]:
            with self.subTest(codigo=codigo):
                with self.assertRaises(UnsupportedNode):
                    ir(codigo)

    def test_atribuicao_simultanea(self):
        c = ir("x, y = y, x")
        self.assertEqual(c, ParAssign(("x", "y"), (Var("y"), Var("x"))))

    def test_for_usa_contador_fresco(self):
        c = ir("for i in range(1, n):\n    s = s + i")
        self.assertEqual(c.commands[0].target, "#i")
        # o corpo comeca copiando o contador para a variavel do laco
        corpo = c.commands[1].body
        self.assertEqual(corpo.commands[0], Assign("i", Var("#i")))

    def test_funcao_pura_expandida(self):
        c = ir("def d(a):\n    return 2 * a\n\nz = d(x + 1)")
        self.assertEqual(c, Assign("z", BinOp("*", Const(2), BinOp("+", Var("x"), Const(1)))))


class TestRules(unittest.TestCase):
    def test_assign_atualiza_estado(self):
        d = denote(ir("x = 0"))
        self.assertEqual(d.result, Update(Named(r"\sigma"), "x", Const(0)))

    def test_leitura_resolve_update_mais_recente(self):
        # x := 0; y := x + 1  ==>  o x lido e' 0, nao sigma(x)
        d = denote(ir("x = 0\ny = x + 1"))
        self.assertEqual(d.result.value, BinOp("+", Const(0), Const(1)))

    def test_leitura_atravessa_update_de_outra_variavel(self):
        d = denote(ir("y = 0\nz = x + 1"))
        self.assertEqual(d.result.value.left, Lookup(Named(r"\sigma"), "x"))

    def test_while_gera_funcional(self):
        d = denote(ir("x = 0\nwhile x < 10:\n    x = x + 1"))
        self.assertIsInstance(d.result, FixApply)
        self.assertEqual(len(d.functionals), 1)
        f = d.functionals[0]
        self.assertEqual(f.cond, Compare("<", Lookup(Named(r"\sigma"), "x"), Const(10)))
        self.assertEqual(f.else_, Named(r"\sigma"))

    def test_whiles_aninhados_usam_estados_ligados_distintos(self):
        d = denote(ir("while x < 3:\n    while y < 3:\n        y = y + 1\n    x = x + 1"))
        nomes = {f.state for f in d.functionals}
        self.assertEqual(len(nomes), 2)

    def test_if_produz_estado_condicional(self):
        d = denote(ir("if x < 0:\n    y = 1\nelse:\n    y = 2"))
        self.assertIsInstance(d.result, CondState)

    def test_estado_opaco_ganha_nome(self):
        d = denote(ir("while x < 3:\n    x = x + 1\ny = x"))
        self.assertEqual(len(d.state_defs), 1)
        self.assertIsInstance(d.state_defs[0].value, FixApply)


class TestRender(unittest.TestCase):
    def tex(self, code):
        c = ir(code)
        return render_equations(c, denote(c))

    def test_precedencia_soma_dentro_de_produto(self):
        self.assertEqual(
            expr_to_latex(BinOp("*", BinOp("+", Var("a"), Var("b")), Var("c"))),
            r"\left(a + b\right) \cdot c",
        )

    def test_sem_parenteses_desnecessarios(self):
        self.assertEqual(
            expr_to_latex(BinOp("+", BinOp("*", Var("a"), Var("b")), Var("c"))),
            r"a \cdot b + c",
        )

    def test_subtracao_nao_associativa(self):
        self.assertEqual(
            expr_to_latex(BinOp("-", Var("a"), BinOp("-", Var("b"), Var("c")))),
            r"a - \left(b - c\right)",
        )

    def test_nao_simplifica_a_expressao(self):
        # o ponto de nao usar sympy: fidelidade ao fonte
        self.assertIn("+ 1 < 10", self.tex("x = 0\nwhile x + 1 < 10:\n    x = x + 1")[1])

    def test_nome_longo_em_mathit(self):
        self.assertEqual(expr_to_latex(Var("total")), r"\mathit{total}")

    def test_equacao_principal(self):
        eqs = self.tex("x = 0")
        self.assertEqual(eqs[0], r"[\![\,x := 0\,]\!](\sigma) = \sigma[x \mapsto 0]")


if __name__ == "__main__":
    unittest.main()


class TestSimultaneidade(unittest.TestCase):
    def test_swap_avalia_no_estado_original(self):
        d = denote(ir("x, y = y, x"))
        sigma = Named(r"\sigma")
        # ambos os valores sao lidos em sigma, nao no estado parcial
        self.assertEqual(d.result.value, Lookup(sigma, "x"))
        self.assertEqual(d.result.base.value, Lookup(sigma, "y"))

    def test_sequencial_e_diferente(self):
        par = denote(ir("x, y = y, x")).result
        seq = denote(ir("x = y\ny = x")).result
        self.assertNotEqual(par, seq)


class TestEvaluator(unittest.TestCase):
    def check(self, code, sigma0):
        from codemath.evaluator import compare
        c = ir(code)
        rel = compare(code, denote(c), sigma0)
        self.assertTrue(rel["ok"], rel)
        return rel["denotacao"]

    def test_while(self):
        self.assertEqual(self.check("x = 0\nwhile x < 10:\n    x = x + 1", {})["x"], 10)

    def test_swap(self):
        r = self.check("x, y = y, x", {"x": 1, "y": 2})
        self.assertEqual((r["x"], r["y"]), (2, 1))

    def test_euclides(self):
        r = self.check("while b != 0:\n    a, b = b, a % b", {"a": 48, "b": 18})
        self.assertEqual(r["a"], 6)

    def test_for_deixa_ultimo_valor_iterado(self):
        # o bug classico: com contador ingenuo, i valeria 5 aqui
        r = self.check("s = 0\nfor i in range(1, 5):\n    s = s + i", {})
        self.assertEqual((r["i"], r["s"]), (4, 10))

    def test_divergencia_detectada(self):
        from codemath.evaluator import Diverge, run_denotation
        d = denote(ir("while x > 0:\n    x = x + 1"))
        with self.assertRaises(Diverge):
            run_denotation(d, {"x": 1}, max_steps=500)


class TestKleene(unittest.TestCase):
    def setUp(self):
        from codemath.rules import unfold
        self.unfold = unfold
        self.den = denote(ir("while x < 3:\n    x = x + 1"))

    def test_aproximacao_zero_e_bottom(self):
        from codemath.rules import Bottom
        self.assertEqual(self.unfold(self.den, "F_{1}", 0), Bottom())

    def test_aproximacoes_crescem(self):
        # F^k acerta os estados que terminam em ate k voltas
        from codemath.evaluator import Diverge, Machine
        from codemath.rules import Named
        for k, esperado in [(1, False), (2, False), (3, False), (4, True)]:
            m = Machine(self.den)
            m.binds[r"\sigma"] = {"x": 0}
            try:
                m.state(self.unfold(self.den, "F_{1}", k))
                ok = True
            except Diverge:
                ok = False
            self.assertEqual(ok, esperado, f"F^{k} com x=0")


class TestHoare(unittest.TestCase):
    def wp(self, code, post):
        from codemath.hoare import analyse
        q = ir(f"__q = {post}").value
        return analyse(ir(code), q)

    def test_atribuicao_substitui(self):
        r = self.wp("x = x + 1", "x > 0")
        self.assertEqual(r.pre, Compare(">", BinOp("+", Var("x"), Const(1)), Const(0)))

    def test_while_gera_invariante_e_duas_obrigacoes(self):
        r = self.wp("while x < 10:\n    x = x + 1", "x == 10")
        self.assertIsInstance(r.pre, Atom)
        self.assertEqual(len(r.obligations), 2)
        self.assertEqual([o.kind for o in r.obligations], ["preservação", "saída"])

    def test_substituicao_em_invariante_fica_pendente(self):
        r = self.wp("while x < 10:\n    x = x + 1", "x == 10")
        preservacao = r.obligations[0].formula
        alvo = preservacao.operands[1]        # o consequente da implicacao
        self.assertEqual(alvo.subs, ((("x", BinOp("+", Var("x"), Const(1))),),))

    def test_swap_gera_bloco_simultaneo(self):
        r = self.wp("while b != 0:\n    a, b = b, a % b", "a > 0")
        alvo = r.obligations[0].formula.operands[1]
        self.assertEqual(len(alvo.subs), 1)          # um bloco...
        self.assertEqual(len(alvo.subs[0]), 2)       # ...com duas variaveis


class TestExemplos(unittest.TestCase):
    """A verificação de verdade: denotação == Python, em todos os exemplos."""

    def test_todos_os_exemplos(self):
        import glob
        from codemath.cli import parse_sigma
        from codemath.evaluator import compare
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "exemplos")
        arquivos = sorted(glob.glob(os.path.join(base, "*.py")))
        self.assertGreater(len(arquivos), 10)
        for path in arquivos:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            if "# esperado: diverge" in source:
                continue
            estados = [parse_sigma(l.split(":", 1)[1])
                       for l in source.splitlines() if l.strip().startswith("# sigma:")]
            cmd = translate(parse_source(source))
            den = denote(cmd)
            for s0 in (estados or [{}]):
                with self.subTest(exemplo=os.path.basename(path), sigma=s0):
                    rel = compare(source, den, s0, max_steps=200_000)
                    self.assertTrue(rel["ok"], rel)


class TestUnicode(unittest.TestCase):
    """Saída sem LaTeX (unicode_render): mesmas equações, mesma ordem, legíveis num quadro de vídeo."""

    def eqs(self, code):
        from codemath.unicode_render import render_equations_unicode
        cmd = ir(code)
        return render_equations_unicode(cmd, denote(cmd))

    def test_atribuicao(self):
        self.assertEqual(self.eqs("x = 0")[0], "⟦x := 0⟧(σ) = σ[x ↦ 0]")

    def test_troca_simultanea(self):
        self.assertEqual(self.eqs("x, y = y, x")[0], "⟦x, y := y, x⟧(σ) = σ[x ↦ σ(y)][y ↦ σ(x)]")

    def test_laco_vira_fix_e_funcional_em_duas_linhas(self):
        eqs = self.eqs("x = 0\nwhile x < 10:\n    x = x + 1")
        self.assertTrue(eqs[0].endswith("= fix(F₁)(σ[x ↦ 0])"), eqs[0])
        then_, else_ = eqs[1].split("\n")
        self.assertIn("F₁(f)(σ) = { f(σ[x ↦ σ(x) + 1]),  se σ(x) < 10", then_)
        self.assertEqual(else_.strip(), "{ σ,  caso contrário")
        self.assertEqual(then_.index("{"), else_.index("{"), "os ramos ficam alinhados")

    def test_sem_comando_latex_na_saida(self):
        import glob
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exemplos")
        for path in sorted(glob.glob(os.path.join(base, "*.py"))):
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            for eq in self.eqs(source):
                with self.subTest(exemplo=os.path.basename(path)):
                    self.assertNotRegex(eq, r"\\[a-zA-Z]|[{}]_|\^\{", eq)

    def test_pares_alinhados_com_o_latex(self):
        from codemath.unicode_render import equation_pairs
        cmd = ir("fat = 1\ni = 1\nwhile i <= n:\n    fat = fat * i\n    i = i + 1")
        pares = equation_pairs(cmd, denote(cmd))
        self.assertEqual(len(pares), len(render_equations(cmd, denote(cmd))))
        self.assertIn(r"\mathrm{fix}", pares[0][0])
        self.assertIn("fix(F₁)", pares[0][1])

    def test_operadores(self):
        from codemath.unicode_render import expr_to_unicode
        self.assertEqual(expr_to_unicode(ir("y = a // b").value), "⌊a / b⌋")
        self.assertEqual(expr_to_unicode(ir("y = x ** 2").value), "x²")
        self.assertEqual(expr_to_unicode(ir("y = a % b").value), "a mod b")
        self.assertEqual(expr_to_unicode(ir("y = a - (b - c)").value), "a − (b − c)")
