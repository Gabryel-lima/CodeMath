# CodeMath — guia para quem trabalha no código

Tradutor de programas Python para **semântica denotacional** (LaTeX, Unicode
e JSON), com um verificador que **executa a fórmula** e a compara com o
Python real. É usado como **submódulo do EduMotion**: a API (`codemath.api`)
é um contrato com outro projeto.

Leia o `README.md` antes de mudar regra de tradução: ele explica as decisões
(sem sympy, substituição em vez de composição literal, bandeiras para
`break`/`continue`) e a lista de bugs que o verificador já achou.

## Achar código: use o `find_code.py` antes do grep

Não abra arquivo por arquivo nem escreva script Python improvisado para
inspecionar o código. O `find_code.py` (raiz, só stdlib, offline) indexa
funções, métodos, classes e constantes **pela AST** e conhece o grafo de
chamadas real:

```bash
python3 find_code.py "onde o for vira while"          # por descrição (pt ou en)
python3 find_code.py --def stmt_For                   # definição: assinatura, doc, chama, chamada por
python3 find_code.py --def Translator.block           # nome qualificado também vale
python3 find_code.py --arvore denote --profundidade 2 # o que `denote` chama, em árvore
python3 find_code.py --arvore lookup --subir          # quem chama `lookup` (impacto de uma mudança)
python3 find_code.py --mapa codemath/translator.py    # sumário do arquivo, na ordem
python3 find_code.py --mapa .                         # sumário do projeto inteiro
python3 find_code.py --vizinhos ponto                 # como o projeto chama a coisa
python3 find_code.py --json --def denote              # saída estruturada
```

- **Antes de mudar uma função**, rode `--arvore NOME --subir`: mostra quem
  depende dela. Antes de entender um módulo, `--mapa`.
- **O resultado é pista, não prova.** Leia o trecho e siga `chama:` /
  `chamada por:` antes de concluir. Fora de Python o grafo liga por nome
  igual (heurística); em Python, por chamada real.
- **Não achou?** Veja "Pergunta lida como", rode `--vizinhos <palavra>` e
  acrescente o par em `find_code.sinonimos` (uma ideia por linha). Termo novo
  do projeto entra lá no mesmo commit que o introduz.
- **Calibrar** (pesos `PESO_*`, `EXTRA_*`, `BONUS_*` no topo do script):
  `python3 -m unittest tests.test_find_code`. Pergunta nova que vale proteger
  vira uma linha em `PERGUNTAS` desse teste.
- O cache fica em `~/.cache/find_code/` e se invalida sozinho quando um
  arquivo indexado ou o próprio script muda; `--sem-cache` ignora.
- O script é o mesmo do CharyRick (que ainda usa a versão antiga) e é
  genérico: não ponha nada específico do CodeMath nele — vocabulário vai no
  `find_code.sinonimos`.

## Comandos

```bash
python3 -m unittest discover -s tests       # tudo (inclui fuzz curto e find_code)
python3 verificar_exemplos.py               # cada exemplo: denotação × Python
CODEMATH_FUZZ=20000 python3 -m unittest tests.test_fuzz   # fuzz diferencial longo
python3 main.py ARQ.py -f txt               # denotação em Unicode
python3 main.py ARQ.py --ir                 # IR depois do açúcar (for, break, continue)
python3 main.py ARQ.py --verificar --amostras 30
python3 main.py ARQ.py --rastro --sigma "n=5"
python3 main.py ARQ.py -f json --sigma "n=5"   # o que o EduMotion consome
python3 galeria.py                          # galeria.html com todos os exemplos
```

## Regras de trabalho

- **Só biblioteca padrão**, Python ≥ 3.9. Nada de sympy (o README diz por quê).
- **Fronteiras dos módulos**: LaTeX só em `latex_render.py`, Unicode só em
  `unicode_render.py`; os dois usam `latex_render.needs_parens` (uma regra de
  parênteses, duas saídas). `rules.py` não conhece Python (`ast`); o
  `translator.py` não conhece estados.
- **Percurso de expressões**: substituição, leitura no estado e afins tratam
  só as folhas que lhes interessam e delegam o resto a `formula.map_expr`.
  Não volte ao `if isinstance` por tipo de nó repetido em cada módulo.
- **Construção nova** (nó de expressão, comando, açúcar) passa, no mesmo
  commit, por: `translator.py` (com `UnsupportedNode` com motivo para o que
  ficar de fora), `rules.py`/`evaluator.py` se mudar a semântica, os **dois**
  renderizadores, um teste em `tests/test_codemath.py`, um exemplo em
  `exemplos/` com `# sigma:` (e `# esperado: diverge` se for o caso), o
  gerador de `tests/test_fuzz.py` e as tabelas "Regras implementadas" e
  "Escopo" do README.
- **Fidelidade antes de beleza**: a tradução tem de concordar com o Python
  em *todo* estado, inclusive em falhar (divisão por zero, divergência).
  Atalho que simplifica a fórmula e muda o resultado é bug, mesmo que passe
  nos exemplos — o fuzz existe para isso. Ao corrigir um bug de tradução,
  acrescente-o à lista "Bugs que o verificador achou" do README e um teste
  que falha sem a correção.
- **Teste que falha sem a mudança**: desligue a mudança e confira que o
  teste cai (o fuzz pega mutações como reverter a atribuição múltipla).
- **Variáveis internas** começam com `#` (ver o topo de `translator.py`);
  só são únicas entre laços *abertos*. Nunca compare nem mostre essas
  variáveis como se fossem do programa (`analysis.is_internal`).
- **Contrato com o EduMotion**: mudou o formato do dicionário de
  `api.analisar` (chave renomeada ou removida)? Suba `VERSAO_API` e diga no
  commit. Chave nova não quebra ninguém.
- **Erros**: entrada inválida sobe como `SyntaxError` ou `UnsupportedNode`
  (linha, coluna, motivo); a CLI devolve 2 ou 3. Nunca engula silenciosamente
  uma construção desconhecida (`stmt_Expr` recusa chamada com efeito colateral
  em vez de tratá-la como `skip`).

## Vídeos com o EduMotion (voz do dono, roteiro e transcrição)

O CodeMath entra nos vídeos do EduMotion só como explicação de código (`edu-video code semantics`, contrato `codemath.api`). O fluxo completo de produção está no `CLAUDE.md` e em `docs/VOZ.md` do **EduMotion**; o resumo do que vale aqui:
- **Roteiro feito junto com o dono**, frases curtas numeradas, aprovado antes de gravar. Demonstração antes de ensino técnico.
- O dono grava **só o áudio** e o prepara com `scripts/preparar_audio.sh` do EduMotion (FFmpeg: mono, `loudnorm` −16 LUFS, MP3 ≤ 30 MB).
- Um Whisper pequeno (`scripts/transcrever_sherpa.py` na nuvem, `transcrever_audio.py` na máquina do dono) diz **onde cada frase foi dita**; `edu-video voice import --transcript` fica com a última tentativa. É aproximado: o reconhecimento erra nomes e números, então o dono ouve as frases marcadas.
- Para explicar um programa no vídeo, use um **modelo em Python** (subconjunto aceito) e confira-o contra o código real antes de dizer que "bate".

## Quem usa o CodeMath (e como manter o contrato)

Além do EduMotion (vídeos: `edu-video code semantics|trace`, via `codemath.api`), o CodeMath é uma biblioteca/CLI genérica e pode ser usado em **qualquer projeto** que precise explicar ou verificar um programa Python (o CharyRick o usa indiretamente, nos vídeos que explicam regras do jogo com um modelo em Python). Por isso: mantenha `codemath.api` estável e documentada, sem nada específico de um consumidor; ao mudar o formato de `api.analisar`, suba `VERSAO_API` e **avise no commit** para o EduMotion subir `SUPPORTED_API`. O fluxo de vídeo está no `CLAUDE.md` do EduMotion.

## Idioma

Código, comentários, mensagens de commit e documentação em português. Os
comentários existentes evitam acentos dentro do código (ASCII); textos para
o usuário (CLI, README) usam acentos normalmente.
