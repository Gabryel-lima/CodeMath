# CodeMath

Traduz um programa Python para a **semântica denotacional** equivalente,
com saída em LaTeX — e então **executa a fórmula** para conferir se ela
diz mesmo o que o programa faz.

Sem dependências externas: só a stdlib (`ast`).

```bash
python3 main.py exemplos/06_euclides.py                      # markdown
python3 main.py exemplos/06_euclides.py -f html -o saida.html # HTML com KaTeX
python3 main.py exemplos/05_fatorial.py -f txt               # Unicode, sem LaTeX (terminal, vídeo)
python3 main.py exemplos/05_fatorial.py --unfold 3            # aproximações de Kleene
python3 main.py exemplos/05_fatorial.py --hoare "fat > 0"     # lógica de Hoare
python3 main.py exemplos/06_euclides.py --verificar --sigma "a=48,b=18"
python3 main.py exemplos/05_fatorial.py --verificar --amostras 20

python3 galeria.py              # gera galeria.html com todos os exemplos
python3 verificar_exemplos.py   # confere todos contra o Python real
python3 -m unittest discover -s tests
```

## Exemplo

```python
x = 0
while x < 10:
    x = x + 1
```

$$[\![\,x := 0;\; \mathbf{while}\; x < 10 \;\mathbf{do}\; x := x + 1\,]\!](\sigma) = \mathrm{fix}(F_{1})(\sigma[x \mapsto 0])$$

onde

$$F_{1}(f)(\sigma) = \begin{cases}f\left(\sigma[x \mapsto \sigma(x) + 1]\right), & \text{se } \sigma(x) < 10 \\ \sigma, & \text{caso contrário}\end{cases}$$

## Arquitetura

```
main.py / codemath/cli.py    interface
codemath/parser.py           código-fonte -> ast.Module (stdlib)
codemath/formula.py          IR sintático (Expr, Cmd)
codemath/translator.py       ast.* -> IR sintático (um método por nó)
codemath/analysis.py         variáveis livres (o que precisa estar em σ₀)
codemath/rules.py            IR sintático -> IR semântico (StateExpr) + Kleene
codemath/hoare.py            IR sintático -> precondição mais fraca
codemath/evaluator.py        executa o IR semântico e compara com o Python
codemath/latex_render.py     IR semântico -> LaTeX / Markdown / HTML
codemath/unicode_render.py   IR semântico -> texto Unicode (σ[x ↦ 0], fix(F₁)(σ), ⟦…⟧)
```

Nenhuma string de LaTeX é produzida fora de `latex_render.py`. O
`unicode_render.py` segue a mesma estrutura e as mesmas precedências, para
quem mostra a fórmula onde não há TeX: `equation_pairs(cmd, den)` devolve
`(latex, unicode)` de cada equação, na mesma ordem (o EduMotion usa isso
para animar a semântica de um trecho de código).

## Regras implementadas

| construção | denotação |
|---|---|
| `pass` | ⟦skip⟧(σ) = σ |
| `x = e` | ⟦x := e⟧(σ) = σ[x ↦ ⟦e⟧(σ)] |
| `x, y = e1, e2` | ⟦…⟧(σ) = σ[x ↦ ⟦e1⟧(σ)][y ↦ ⟦e2⟧(σ)] — ambos lidos em σ |
| `c1; c2` | ⟦c1; c2⟧(σ) = ⟦c2⟧(⟦c1⟧(σ)) |
| `if b: c1 else: c2` | ⟦…⟧(σ) = ⟦c1⟧(σ) se ⟦b⟧(σ), senão ⟦c2⟧(σ) |
| `while b: c` | ⟦…⟧ = fix(F), F(f)(σ) = f(⟦c⟧(σ)) se ⟦b⟧(σ), senão σ |
| `for i in range(a,b,s)` | açúcar: k := a; while k<b do (i := k; c; k := k+s) |
| `def f(a): return e` | expansão por substituição (funções puras, sem recursão) |

`fix` é o **menor** ponto fixo de F no CPO das funções parciais
State ⇀ State ordenadas por extensão.

## Por que "menor" ponto fixo — e como ver isso

`--unfold N` mostra a cadeia de Kleene:

```
F⁰(⊥)      = ⊥
F¹(⊥)(σ)   = σ se ¬b(σ), ⊥ se b(σ)
F²(⊥)(σ)   = …
```

`Fᵏ(⊥)` é a função que acerta os estados que terminam em até *k* voltas
do laço e devolve ⊥ nos demais. A cadeia é crescente e seu supremo é o
menor ponto fixo. "Menor" importa porque F tem outros pontos fixos: em
`while x > 0: x = x + 1` (exemplo 16), a função identidade também
satisfaz F(f) = f. Escolher o menor é o que impede a semântica de
inventar um resultado para um programa que nunca produz nenhum.

## O verificador

Uma fórmula bonita não é garantia de fórmula correta. `evaluator.py`
interpreta o IR semântico — `fix` calculado por iteração, que é
exatamente percorrer a cadeia de Kleene — e compara com a execução
real do Python.

Coincidir em muitos estados é **evidência**, não prova. Divergir é
**prova** de que a tradução está errada. Foi assim que dois bugs foram
encontrados:

- `for i in range(1, n)` deixava `i = n` no fim, quando o Python deixa
  o último valor iterado (`n-1`). Daí o contador fresco `î`.
- `x, y = y, x` traduzido como sequência dava `x = y = σ(y)`. Daí o nó
  `ParAssign` com avaliação simultânea.

Ambos passavam em qualquer inspeção visual da fórmula.

## Lógica de Hoare

`--hoare "POS"` calcula a precondição mais fraca wp(c, Q) para trás.
Tudo é mecânico exceto o laço, que gera um invariante `I_k` opaco e
duas obrigações de prova:

```
(I ∧ b)  ⇒ wp(corpo, I)     preservação
(I ∧ ¬b) ⇒ Q                saída
```

`I` não é calculável — precisa ser inventado. É a mesma dificuldade do
`fix`, vista de outro ângulo, e é o motivo de verificação de programas
não ser automática.

Isto é **correção parcial**: a tripla não diz nada sobre o laço
terminar. `while True: pass` satisfaz {true} c {false}.

## Duas decisões de projeto

**Sem sympy.** Sympy canoniza e simplifica: reordena somas, reescreve
comparações, avalia constantes. Um tradutor de semântica precisa de
*fidelidade ao fonte* — `x + 1 < 10` deve sair como `x + 1 < 10`, não
como `x < 9`. Além disso, `sympy.Symbol("x")` é um símbolo livre, não
uma leitura de estado `σ(x)`; usá-lo como representação semântica
quebra assim que aparece composição com `fix`. O custo é um impressor
com tabela de precedência; o benefício é controle total e zero
dependências.

**Substituição, não composição literal.** `⟦c2⟧(⟦c1⟧(σ))` é a regra,
mas aninhado fica ilegível. `rules.py` resolve as leituras contra o
estado já conhecido (`σ[x↦0](x) = 0`). Quando o estado é *opaco* —
resultado de `fix` ou de um condicional, onde `s(x)` é irredutível —
ele ganha um nome (`σ₁ = …`) em vez de ser repetido em cada uso. Nomes
criados dentro de um funcional ficam no escopo daquele funcional, para
não capturar o σ ligado; whiles aninhados usam σ, τ, ρ, υ.

## Escopo

Dentro: atribuição, atribuição simultânea, aritmética (`+ - * / // % **`),
booleanos, comparações (inclusive encadeadas), `if`/`elif`/`else`,
`while`, `for … in range(…)`, sequência, `pass`, `x += e`,
funções puras de um único `return`.

Fora, com erro explícito: recursão, `break`/`continue`, listas, classes,
exceções, `while…else`, atribuição a índice ou atributo.

`break` e `continue` são o próximo passo caro, não o próximo passo
fácil: eles quebram a assinatura ⟦c⟧ : State → State. Um comando deixa
de devolver só um estado e passa a devolver um estado *mais um modo de
saída* (normal, break, continue) — ou, na formulação alternativa,
passa a receber continuações. Qualquer dos dois caminhos muda o tipo de
toda regra do sistema, não só a do laço.
