# CodeMath

Traduz um programa Python para a **semântica denotacional** equivalente,
com saída em LaTeX, Unicode ou JSON — e então **executa a fórmula** para
conferir se ela diz mesmo o que o programa faz.

Sem dependências externas: só a stdlib (`ast`). Python ≥ 3.9.

```bash
python3 main.py exemplos/06_euclides.py                       # markdown
python3 main.py exemplos/06_euclides.py -f html -o saida.html # HTML com KaTeX
python3 main.py exemplos/06_euclides.py -f txt                # Unicode, sem LaTeX
python3 main.py exemplos/06_euclides.py -f json --sigma "a=48,b=18"   # tudo, estruturado
python3 main.py exemplos/05_fatorial.py --unfold 3            # aproximações de Kleene
python3 main.py exemplos/05_fatorial.py --hoare "fat > 0"     # lógica de Hoare
python3 main.py exemplos/09_divisao_longa.py --hoare "a == q*b + r and r < b" \
        --inv "a == q*b + r and 0 <= r"                       # testa o invariante proposto
python3 main.py exemplos/06_euclides.py --verificar --sigma "a=48,b=18"
python3 main.py exemplos/05_fatorial.py --verificar --amostras 20
python3 main.py exemplos/06_euclides.py --rastro --sigma "a=48,b=18"  # estado a cada volta
python3 main.py exemplos/17_menor_divisor.py --ir             # IR após o açúcar (for, break)

python3 galeria.py              # gera galeria.html com todos os exemplos
python3 verificar_exemplos.py   # confere todos contra o Python real
python3 -m unittest discover -s tests
CODEMATH_FUZZ=20000 python3 -m unittest tests.test_fuzz      # fuzz diferencial mais longo
```

Códigos de saída da CLI: `0` ok · `1` verificação falhou · `2` erro de
sintaxe ou de argumento · `3` construção fora do escopo.

## Exemplo

```python
x = 0
while x < 10:
    x = x + 1
```

$$[\![\,x := 0;\; \mathbf{while}\; x < 10 \;\mathbf{do}\; x := x + 1\,]\!](\sigma) = \mathrm{fix}(F_{1})(\sigma[x \mapsto 0])$$

onde

$$F_{1}(f)(\sigma) = \begin{cases}f\left(\sigma[x \mapsto \sigma(x) + 1]\right), & \text{se } \sigma(x) < 10 \\ \sigma, & \text{caso contrário}\end{cases}$$

Em Unicode (`-f txt`), para onde não há LaTeX:

```
⟦x := 0; while x < 10 do x := x + 1⟧(σ) = fix(F₁)(σ[x ↦ 0])
F₁(f)(σ) = f(σ[x ↦ σ(x) + 1]) se σ(x) < 10; senão σ
```

## Arquitetura

```
main.py / codemath/cli.py    interface de linha de comando
codemath/api.py              analisar(): tudo em dados (tex + text por fórmula) — para o EduMotion
codemath/parser.py           código-fonte -> ast.Module (stdlib)
codemath/formula.py          IR sintático (Expr, Cmd) + percurso genérico (children, map_expr)
codemath/translator.py       ast.* -> IR sintático (um método por nó; açúcar de for/break/continue)
codemath/analysis.py         variáveis livres (atribuição definitiva) e escritas
codemath/rules.py            IR sintático -> IR semântico (StateExpr) + Kleene
codemath/hoare.py            IR sintático -> precondição mais fraca, invariantes dados
codemath/evaluator.py        executa o IR semântico, compara com o Python, rastro, obrigações
codemath/latex_render.py     IR semântico -> LaTeX / Markdown / HTML (+ regra de parênteses)
codemath/unicode_render.py   IR semântico -> texto Unicode (mesma regra de parênteses)
find_code.py                 busca de código por descrição, definição, árvore de chamadas, mapa
```

Nenhuma string de LaTeX é produzida fora de `latex_render.py`, nem de
Unicode fora de `unicode_render.py`. Os dois usam a mesma
`needs_parens`, para nunca discordarem sobre o que uma fórmula quer dizer.

## Regras implementadas

| construção | denotação |
|---|---|
| `pass` | ⟦skip⟧(σ) = σ |
| `x = e` | ⟦x := e⟧(σ) = σ[x ↦ ⟦e⟧(σ)] |
| `x, y = e1, e2` | ⟦…⟧(σ) = σ[x ↦ ⟦e1⟧(σ)][y ↦ ⟦e2⟧(σ)] — ambos lidos em σ |
| `x = y = e` | atribuição simultânea com o mesmo valor (`e` avaliada uma vez) |
| `c1; c2` | ⟦c1; c2⟧(σ) = ⟦c2⟧(⟦c1⟧(σ)) |
| `if b: c1 else: c2` | ⟦…⟧(σ) = ⟦c1⟧(σ) se ⟦b⟧(σ), senão ⟦c2⟧(σ) |
| `while b: c` | ⟦…⟧ = fix(F), F(f)(σ) = f(⟦c⟧(σ)) se ⟦b⟧(σ), senão σ |
| `for i in range(a,b,s)` | açúcar: k := a; while k<b do (i := k; c; k := k+s) — `>` se s < 0 |
| `break`, `continue` | bandeiras no estado (ver abaixo) |
| `while/for … else` | if ¬brk then c_else, logo depois do laço |
| `a if c else b` | ⟦…⟧(σ) = ⟦a⟧(σ) se ⟦c⟧(σ), senão ⟦b⟧(σ) |
| `abs`, `min`, `max` | \|x\|, min(a, b, …), max(a, b, …) |
| `def f(a): …` | expansão por substituição (funções puras, sem recursão) |

`fix` é o **menor** ponto fixo de F no CPO das funções parciais
State ⇀ State ordenadas por extensão.

### Funções puras

O corpo precisa virar **uma** expressão: atribuições locais são
substituídas adiante (`t = a*a; return t + 1` → `a*a + 1`) e `if` com
`return` nos ramos vira expressão condicional. Ler um local antes de
atribuí-lo é erro de tradução (no Python seria `UnboundLocalError`), e
não uma leitura silenciosa da global de mesmo nome. Argumentos nomeados
(`f(b=1, a=x)`) são aceitos.

### `break` e `continue`: bandeiras, não continuações

Tratados como saltos, `break` e `continue` quebram a assinatura
⟦c⟧ : State → State: um comando passaria a devolver um estado *mais um
modo de saída* (normal, break, continue), ou a receber continuações — e
isso muda o tipo de **toda** regra, não só a do laço.

O CodeMath usa a saída clássica que evita essa mudança: o salto vira
**estado**. `break` é `brk := true`; a guarda vira `¬brk ∧ b`; e o que
vem depois de um comando que *pode* ter saltado fica protegido por
`if ¬brk then … else skip`. O `continue` é igual, com a bandeira
reiniciada no começo de cada volta (e o incremento do `for` fora do
trecho protegido, para o contador andar).

O preço é real e vale saber: a fórmula ganha variáveis que o programa
não tem (`brk`, `cont`), e a denotação deixa de ser *composicional no
programa original* — ela é composicional no programa **desaçucarado**.
Para ensinar ponto fixo isso basta; para raciocinar sobre saltos como
saltos, o caminho continua sendo continuações.

### Variáveis internas

Nomes criados pela tradução começam com `#` (que não é identificador
Python, então nunca colidem): `#i` → î (contador do `for`), `#i.fim` →
î_fim (limite congelado), `#.brk`/`#.cont` → brk/cont. Laços aninhados
que usariam o mesmo nome ganham um apóstrofo (î′). Elas aparecem na
denotação, mas a verificação compara só as variáveis do programa.

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

`--rastro --sigma …` mostra o mesmo processo num estado concreto: σ,
F(σ), F(F(σ)), … até a guarda falhar.

## O verificador

Uma fórmula bonita não é garantia de fórmula correta. `evaluator.py`
interpreta o IR semântico — `fix` calculado por iteração, que é
exatamente percorrer a cadeia de Kleene — e compara com a execução
real do Python.

Coincidir em muitos estados é **evidência**, não prova. Divergir é
**prova** de que a tradução está errada. Três desfechos além do óbvio:

- **os dois lados falham igual** (os dois divergem, os dois dividem por
  zero): conta como acordo — a denotação é indefinida onde o programa é;
- **inconclusivo**: só um lado estourou o limite de passos. Os limites
  contam coisas diferentes (linhas no Python, voltas na denotação), então
  perto do limite isso não prova nada — aumente `--max-passos`;
- **tipo diferente** (`2` contra `2.0`) é divergência, embora `2 == 2.0`.

### Bugs que o verificador achou

Todos passavam em qualquer inspeção visual da fórmula:

- `for i in range(1, n)` deixava `i = n` no fim, quando o Python deixa
  o último valor iterado (`n-1`). Daí o contador fresco `î`.
- `x, y = y, x` traduzido como sequência dava `x = y = σ(y)`. Daí o nó
  `ParAssign` com avaliação simultânea.
- `x = y = x + 1` virava `x := x + 1; y := x + 1` — `y` saía uma unidade
  maior. O Python avalia o lado direito **uma** vez.
- `for i` dentro de `for i` usavam o mesmo contador: o interno zerava o
  externo, e o laço externo dava uma volta só.
- `for i in range(n): n = n - 1` relia `n` na guarda; o Python avalia
  `range(n)` uma vez. Agora o limite é congelado em `î_fim` quando o
  corpo mexe nele (e só então, para não poluir a fórmula).

Os três últimos foram achados pelo **teste diferencial**
(`tests/test_fuzz.py`): um gerador monta programas aleatórios com todas
as construções e o verificador confere cada um contra o Python.

## Lógica de Hoare

`--hoare "POS"` calcula a precondição mais fraca wp(c, Q) para trás.
Tudo é mecânico exceto o laço, que gera um invariante `I_k` opaco e
duas obrigações de prova:

```
(I ∧ b)  ⇒ wp(corpo, I)     preservação
(I ∧ ¬b) ⇒ Q                saída
```

`I_k` é o invariante do laço cujo funcional é `F_k` (numeração na ordem
do texto). `I` não é calculável — precisa ser inventado. É a mesma
dificuldade do `fix`, vista de outro ângulo, e é o motivo de verificação
de programas não ser automática.

`--inv "EXPR"` (um por laço, na ordem do texto) propõe o invariante: as
obrigações viram fórmulas concretas e são avaliadas em estados de
amostra. Um estado que as torna falsas **prova** que o invariante não
serve; nenhum contraexemplo é só evidência.

Isto é **correção parcial**: a tripla não diz nada sobre o laço
terminar. `while True: pass` satisfaz {true} c {false}.

## Uso como biblioteca (submódulo do EduMotion)

```python
from codemath import analisar
r = analisar(fonte, sigma={"a": 48, "b": 18}, unfold_n=2, pos="mdc > 0")
r["equacoes"]   # [{"papel", "nome", "tex", "text"}, ...]
r["passos"]     # [{"text": "a = 48, b = 18", "note": "entrada"}, ...]
r["verificacao"], r["rastro"], r["kleene"], r["hoare"], r["variaveis_livres"]
```

Cada fórmula vem em `tex` **e** `text` (Unicode): o EduMotion desenha o
`text` quando não há LaTeX instalado. `passos` tem o formato de
`expression_steps` (a sequência de estados do primeiro laço). Erros de
entrada sobem como `SyntaxError` ou `codemath.UnsupportedNode` (com
linha, coluna e motivo). `python3 main.py arquivo.py -f json` dá o mesmo
dicionário pela linha de comando; `pip install -e .` instala o comando
`codemath`.

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
não capturar o σ ligado; whiles aninhados usam σ, τ, ρ, υ (pela
profundidade: whiles em sequência reusam σ).

## Escopo

Dentro: atribuição (simples, simultânea, múltipla, anotada, `x += e`),
aritmética (`+ - * / // % **`), booleanos, comparações (inclusive
encadeadas), `if`/`elif`/`else`, expressão condicional, `while`,
`for … in range(…)` (passo literal, inclusive negativo), `break`,
`continue`, `else` de laço, sequência, `pass`, `print` (ignorado),
`abs`/`min`/`max`, funções puras (com locais, `if`/`return` e argumentos
nomeados).

Fora, com erro explícito (linha, coluna e motivo): recursão, listas e
outras coleções, strings, classes, exceções, atribuição a índice ou
atributo, laço dentro de função, chamada com possível efeito colateral
(`lista.append(x)` como statement), passo de `range` não literal.

Recursão é o próximo passo caro: precisa de ponto fixo no domínio das
*funções* (fix em (A → B) → (A → B)), não só no dos estados.

## Buscar no código

`find_code.py` acha código por descrição e navega pelo grafo de
chamadas, offline e só com a stdlib (é o mesmo script do CharyRick, com
extração de Python pela AST):

```bash
python3 find_code.py "onde o for vira while"         # busca por descrição
python3 find_code.py --def stmt_For                  # definição, doc, chama/chamada por
python3 find_code.py --arvore denote --profundidade 2  # o que denote chama, em árvore
python3 find_code.py --arvore lookup --subir         # quem chama lookup
python3 find_code.py --mapa codemath/rules.py        # sumário do arquivo
python3 find_code.py --vizinhos ponto                # como o projeto chama a coisa
```

O vocabulário do projeto fica em `find_code.sinonimos`.
