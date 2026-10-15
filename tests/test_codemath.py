"""Testes do nucleo. Rodar: python3 -m unittest discover -s tests"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemath import parse_source, translate, denote
from codemath.formula import (Assign, Atom, BinOp, BoolOp, Compare, Const, Func, If,
                              IfExp, ParAssign, Seq, Skip, UnOp, Var, While)
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
            "def g(a):\n    while a:\n        a -= 1\n    return a",  # laco em funcao
            "def h(a):\n    t = t + a\n    return t",  # local lida antes de escrita
            "def k(a):\n    if a:\n        return 1",  # pode terminar sem return
            "for i in [1, 2]:\n    x = i",          # so range
            "for i in range(0, 10, s):\n    x = i",  # passo nao literal
            "for i in range(0, 10, 0):\n    x = i",  # passo zero
            "lista.append(1)",                       # efeito colateral escondido
            "break",                                 # fora de laco
            "x = min(a)",                            # min de iteravel
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

    def test_atribuicao_multipla_avalia_uma_vez(self):
        # `x = y = x + 1`: como sequencia, y leria o x ja' atualizado
        c = ir("x = y = x + 1")
        e = BinOp("+", Var("x"), Const(1))
        self.assertEqual(c, ParAssign(("x", "y"), (e, e)))

    def test_range_com_passo_negativo(self):
        c = ir("for i in range(10, 0, -2):\n    s = i")
        guarda = c.commands[1].cond
        self.assertEqual(guarda, Compare(">", Var("#i"), Const(0)))

    def test_for_aninhado_com_mesmo_nome_usa_contadores_distintos(self):
        c = ir("for i in range(3):\n    for i in range(2):\n        t = i")
        externo = c.commands[0].target
        interno = c.commands[1].body.commands[1].target
        self.assertNotEqual(externo, interno)

    def test_limite_do_range_congelado_quando_o_corpo_o_muda(self):
        c = ir("for i in range(n):\n    n = n - 1")
        self.assertEqual(c.commands[1], Assign("#i.fim", Var("n")))
        self.assertEqual(c.commands[2].cond, Compare("<", Var("#i"), Var("#i.fim")))

    def test_limite_nao_congelado_sem_necessidade(self):
        c = ir("for i in range(n):\n    s = i")
        self.assertEqual(len(c.commands), 2)          # contador e while, so

    def test_break_vira_bandeira_na_guarda(self):
        c = ir("while x < 9:\n    if x == 3:\n        break\n    x = x + 1")
        self.assertEqual(c.commands[0], Assign("#.brk", Const(False)))
        guarda = c.commands[1].cond
        self.assertEqual(guarda.operands[0], UnOp("not", Var("#.brk")))

    def test_resto_do_corpo_protegido_depois_do_salto(self):
        c = ir("while x < 9:\n    if x == 3:\n        continue\n    x = x + 1")
        corpo = c.body.commands
        self.assertEqual(corpo[0], Assign("#.cont", Const(False)))
        self.assertEqual(corpo[2], If(UnOp("not", Var("#.cont")),
                                      Assign("x", BinOp("+", Var("x"), Const(1))), Skip()))

    def test_laco_sem_salto_nao_ganha_bandeira(self):
        self.assertIsInstance(ir("while x < 9:\n    x = x + 1"), While)

    def test_else_do_laco_so_sem_break(self):
        c = ir("while x:\n    break\nelse:\n    y = 1")
        self.assertEqual(c.commands[-1], If(UnOp("not", Var("#.brk")),
                                            Assign("y", Const(1)), Skip()))

    def test_ternario_e_builtins(self):
        self.assertEqual(ir("y = abs(x) if x else 0").value,
                         IfExp(Var("x"), Func("abs", (Var("x"),)), Const(0)))

    def test_funcao_com_locais_e_if(self):
        c = ir("def f(a):\n    t = a * a\n    if t > 9:\n        return t\n    return 0\n"
               "y = f(x)")
        quadrado = BinOp("*", Var("x"), Var("x"))
        self.assertEqual(c.value, IfExp(Compare(">", quadrado, Const(9)), quadrado, Const(0)))

    def test_statements_sem_efeito_viram_skip(self):
        for codigo in ['"""docstring do programa."""\nx = 1', "x = 1\nprint(x)",
                       "x = 1\n...", "x = 1\nx + 1"]:
            with self.subTest(codigo=codigo):
                self.assertEqual(ir(codigo), Assign("x", Const(1)))

    def test_erro_diz_linha_e_motivo(self):
        with self.assertRaises(UnsupportedNode) as ctx:
            ir("x = 1\nlista.append(x)")
        self.assertIn("linha 2", str(ctx.exception))
        self.assertIn("efeito colateral", str(ctx.exception))

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

    def test_whiles_em_sequencia_reusam_sigma(self):
        # escopos separados: o nome do estado ligado vem da profundidade
        laco = "while x < 3:\n    x = x + 1\n"
        d = denote(ir(laco * 5))
        self.assertEqual({f.state for f in d.functionals}, {r"\sigma"})

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

    def test_none_nao_e_bottom(self):
        # None e' um valor; \bot e' "indefinido" -- nao podem se confundir
        self.assertEqual(expr_to_latex(Const(None)), r"\mathbf{None}")

    def test_menos_unario_a_direita_ganha_parenteses(self):
        self.assertEqual(expr_to_latex(BinOp("-", Var("a"), UnOp("-", Var("b")))),
                         r"a - \left(-b\right)")

    def test_mod_misturado_com_produto(self):
        self.assertEqual(expr_to_latex(BinOp("*", BinOp("%", Var("a"), Var("b")), Var("c"))),
                         r"\left(a \bmod b\right) \cdot c")

    def test_corpo_composto_entre_parenteses(self):
        # sem parenteses nao da' para saber onde o laco termina
        eq = self.tex("while x < 3:\n    x = x + 1\n    y = x\nz = 0")[0]
        self.assertIn(r"\mathbf{do}\; \left(x := x + 1;\; y := x\right);\; z := 0", eq)

    def test_nomes_internos(self):
        from codemath.latex_render import ident
        self.assertEqual(ident("#i"), r"\hat{i}")
        self.assertEqual(ident("#i'"), r"\hat{i}'")
        self.assertEqual(ident("#i.fim"), r"\hat{i}_{\mathrm{fim}}")
        self.assertEqual(ident("#.brk"), r"\mathsf{brk}")


class TestUnicode(unittest.TestCase):
    def txt(self, code):
        from codemath.unicode_render import render_equations_text
        c = ir(code)
        return render_equations_text(c, denote(c))

    def test_equacao_principal(self):
        self.assertEqual(self.txt("x = 0\nwhile x < 10:\n    x = x + 1")[0],
                         "⟦x := 0; while x < 10 do x := x + 1⟧(σ) = fix(F₁)(σ[x ↦ 0])")

    def test_operadores(self):
        from codemath.unicode_render import expr_to_text
        e = ir("y = (a // b) % 2 ** n <= -c and not d").value
        self.assertEqual(expr_to_text(e), "⌊a / b⌋ mod 2^n ≤ −c ∧ ¬d")
        self.assertEqual(expr_to_text(ir("y = x ** 2").value), "x²")

    def test_mesma_regra_de_parenteses_do_latex(self):
        from codemath.unicode_render import expr_to_text
        self.assertEqual(expr_to_text(BinOp("-", Var("a"), BinOp("-", Var("b"), Var("c")))),
                         "a − (b − c)")
        self.assertEqual(expr_to_text(BinOp("/", Var("a"), BinOp("*", Var("b"), Var("c")))),
                         "a / (b · c)")

    def test_divisao_inteira_com_soma_no_numerador(self):
        # `(a + 5) // 10` saia como `⌊a + 5 / 10⌋`, que le a + 0,5
        from codemath.unicode_render import expr_to_text
        self.assertEqual(expr_to_text(ir("y = (a + 5) // 10").value), "⌊(a + 5) / 10⌋")
        self.assertEqual(expr_to_text(ir("y = a // (b + c)").value), "⌊a / (b + c)⌋")
        self.assertEqual(expr_to_text(ir("y = a // b").value), "⌊a / b⌋")

    def test_nomes_de_estado(self):
        from codemath.unicode_render import tex_name
        self.assertEqual(tex_name(r"\sigma_{12}"), "σ₁₂")
        self.assertEqual(tex_name(r"\sigma^{(4)}"), "σ⁽⁴⁾")


class TestAnalise(unittest.TestCase):
    def test_variavel_do_for_nao_e_livre(self):
        from codemath.analysis import free_vars
        self.assertEqual(free_vars(ir("s = 0\nfor i in range(n):\n    s = s + i")), {"n"})

    def test_escrita_em_um_so_ramo_nao_conta(self):
        from codemath.analysis import free_vars
        self.assertEqual(free_vars(ir("if c:\n    y = 1\nz = y")), {"c", "y"})

    def test_escrita_nos_dois_ramos_conta(self):
        from codemath.analysis import free_vars
        self.assertEqual(free_vars(ir("if c:\n    y = 1\nelse:\n    y = 2\nz = y")), {"c"})


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

    def test_mesmo_erro_dos_dois_lados_conta_como_acordo(self):
        from codemath.evaluator import compare
        code = "y = 1 // x"
        rel = compare(code, denote(ir(code)), {"x": 0})
        self.assertTrue(rel["ok"])
        self.assertEqual(rel["mesmo_erro"], "ZeroDivisionError")

    def test_tipo_diferente_e_divergencia(self):
        # 1 == 1.0 no Python, mas a denotacao nao pode trocar int por float
        from codemath.evaluator import compare
        c = ir("y = x")
        rel = compare("y = x / 1", denote(c), {"x": 2})
        self.assertFalse(rel["ok"])

    def test_rastro_do_laco(self):
        from codemath.evaluator import trace_denotation
        final, voltas = trace_denotation(denote(ir("while b != 0:\n    a, b = b, a % b")),
                                         {"a": 48, "b": 18})
        self.assertEqual([(v.state["a"], v.state["b"]) for v in voltas],
                         [(48, 18), (18, 12), (12, 6), (6, 0)])

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

    def test_invariantes_numerados_na_ordem_do_texto(self):
        # o calculo e' de tras para frente, mas I_k tem de ser o laco de F_k
        r = self.wp("while x < 3:\n    x = x + 1\nwhile y < 3:\n    y = y + 1", "x > 0")
        self.assertEqual(r.pre, Atom("I_{1}"))
        self.assertEqual(r.invariants, ["I_{1}", "I_{2}"])

    def test_invariante_dado_e_testado(self):
        from codemath.api import hoare
        prog = ir("q = 0\nr = a\nwhile r >= b:\n    r = r - b\n    q = q + 1")
        bom = hoare(prog, "a == q*b + r and r < b", ["a == q*b + r"])
        self.assertTrue(all(o["contraexemplo"] is None for o in bom["obrigacoes"]))
        ruim = hoare(prog, "a == q*b + r and r < b", ["q == 0"])
        self.assertIsNotNone(ruim["obrigacoes"][0]["contraexemplo"])

    def test_obrigacao_com_invariante_faltando_nao_conta_como_testada(self):
        from codemath.api import hoare
        prog = ir("while x < 3:\n    while y < 3:\n        y = y + 1\n    x = x + 1")
        r = hoare(prog, "x >= 3", ["x >= 0"])          # so' o invariante externo
        preservacao_externa = r["obrigacoes"][0]
        self.assertEqual(preservacao_externa["testada_em"], 0)

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


class TestApi(unittest.TestCase):
    def test_analise_completa(self):
        from codemath.api import analisar
        r = analisar("while b != 0:\n    a, b = b, a % b\nmdc = a",
                     sigma={"a": 48, "b": 18}, unfold_n=1, pos="mdc > 0")
        self.assertEqual(r["variaveis_livres"], ["a", "b"])
        self.assertTrue(all("tex" in e and "text" in e for e in r["equacoes"]))
        self.assertEqual(r["final"]["mdc"], 6)
        self.assertEqual(r["passos"][0], {"text": "a = 48, b = 18", "note": "entrada"})
        self.assertTrue(r["verificacao"][0]["ok"])
        self.assertEqual(len(r["kleene"]), 2)

    def test_json_serializavel(self):
        import json
        from codemath.api import analisar
        json.dumps(analisar("x = 1", sigma={}))


class TestCli(unittest.TestCase):
    def run_cli(self, *argv, stdin=""):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock
        from codemath.cli import main
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_codigos_de_saida(self):
        self.assertEqual(self.run_cli("-f", "txt", stdin="x = 1")[0], 0)
        self.assertEqual(self.run_cli(stdin="x = (")[0], 2)
        self.assertEqual(self.run_cli(stdin="x = [1]")[0], 3)
        self.assertEqual(self.run_cli("--sigma", "x=", stdin="y = x")[0], 2)
        self.assertEqual(self.run_cli("--hoare", "x >", stdin="y = x")[0], 2)
        self.assertEqual(self.run_cli("--rastro", stdin="y = x")[0], 2)

    def test_formatos(self):
        for f in ("md", "html", "tex", "raw", "txt", "json"):
            with self.subTest(formato=f):
                code, out, _ = self.run_cli("-f", f, "--unfold", "1", stdin="while x < 3:\n    x += 1")
                self.assertEqual(code, 0)
                self.assertTrue(out.strip())


if __name__ == "__main__":
    unittest.main()
