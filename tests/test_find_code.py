"""Testes do find_code.py (busca de codigo por descricao).

Dois niveis: o autoteste interno do script (projeto de mentira, sem
depender deste repositorio) e perguntas reais sobre o codigo do CodeMath
com o resultado esperado entre os primeiros. Se um ajuste de peso ou de
sinonimo derrubar uma delas, a busca piorou.

Mexeu nos pesos (PESO_*, EXTRA_*, BONUS_*) ou em find_code.sinonimos?
Rode este arquivo. Pergunta nova que vale proteger vira uma linha em
PERGUNTAS.
"""
import os
import subprocess
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(RAIZ, "find_code.py")

# (pergunta, nome que tem de aparecer entre os N primeiros, N)
PERGUNTAS = [
    ("onde o for vira while", "stmt_For", 3),
    ("quem calcula o menor ponto fixo", "Machine.fix", 3),
    ("contraexemplo do invariante", "check_obligations", 3),
    ("parenteses no unicode", "_wrap", 3),
    ("estado de cada volta do laco", "trace_denotation", 5),
]


def rodar(*args):
    return subprocess.run([sys.executable, SCRIPT, "--sem-cache", "--cores", "nao", *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=RAIZ,
                          timeout=120)


class TestFindCode(unittest.TestCase):
    def test_autoteste(self):
        r = rodar("--autoteste")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_perguntas_reais(self):
        for pergunta, esperado, n in PERGUNTAS:
            with self.subTest(pergunta=pergunta):
                r = rodar("-n", str(n), "--esperar", esperado, pergunta)
                self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)

    def test_definicao_e_arvore(self):
        r = rodar("--def", "Translator.stmt_For")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("codemath/translator.py", r.stdout)
        r = rodar("--arvore", "denote", "--profundidade", "1")
        self.assertIn("Denoter.cmd", r.stdout)
        r = rodar("--arvore", "lookup", "--subir", "--profundidade", "1")
        self.assertIn("subst", r.stdout)

    def test_nome_inexistente_sugere(self):
        r = rodar("--def", "stmt_for_inexistente")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Parecidos", r.stdout)

    def test_mapa(self):
        r = rodar("--mapa", "codemath/rules.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("unfold", r.stdout)


if __name__ == "__main__":
    unittest.main()
