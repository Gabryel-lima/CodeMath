#!/usr/bin/env python3
"""Acha onde algo está no código a partir de uma descrição, sem saber o nome.

Funciona offline, só com a biblioteca padrão do Python (sem IA, sem rede, sem
índice para manter). Em vez de procurar linhas soltas, quebra o projeto em
funções, macros, tipos e seções de documento, e ranqueia cada trecho:

  1. A pergunta vira "grupos de ideias": cada palavra e seus sinônimos
     (português e inglês, tabela abaixo mais o arquivo find_code.sinonimos).
  2. Cada trecho é pontuado por BM25 em campos: o nome da função vale mais que o
     comentário Doxygen, que vale mais que um texto entre aspas, que vale mais que
     o corpo. Trecho que casa vários grupos (ex.: "corrente" E "portal") sobe.
  3. Quem chama e quem é chamado reforça o vizinho: a rotina que "desenha a
     corrente" e a que "desenha o portal" se ajudam.
  4. A saída diz POR QUE cada trecho apareceu (qual palavra casou, em qual campo)
     e mostra as linhas que sustentam isso, com o que ele chama e quem o chama.

Além da busca, três modos para ENTENDER o código sem abrir arquivo por arquivo
(substituem o grep e o script improvisado para inspecionar):

  --def NOME        onde NOME é definido: assinatura, doc, quem chama, o que chama
  --arvore NOME     árvore de chamadas a partir de NOME (--subir: quem chama NOME)
  --mapa CAMINHO    sumário de um arquivo ou pasta: cada função/classe/constante,
                    com as linhas e a primeira frase da documentação

Exemplos:
    python3 find_code.py "onde são desenhadas as correntes do portal"
    python3 find_code.py -n 15 -C 1 "quanto dano a lava causa"
    python3 find_code.py --vizinhos corrente      # vocabulário do projeto
    python3 find_code.py --explicar "tela de game over"
    python3 find_code.py --def stmt_For           # ou Classe.metodo
    python3 find_code.py --arvore denote --profundidade 2
    python3 find_code.py --arvore lookup --subir
    python3 find_code.py --mapa codemath/rules.py

Quando não achar: veja a linha "Pergunta lida como" (as palavras entenderam o
que você quis?), rode com --vizinhos para ver como o projeto chama a coisa, ou
acrescente sinônimos em find_code.sinonimos. A pontuação é uma heurística:
confira sempre o trecho antes de concluir.
"""
from __future__ import annotations

import argparse
import ast
import bisect
import difflib
import hashlib
import json
import math
import os
import pickle
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

VERSAO_CACHE = 4


def _assinatura_do_script() -> str:
    """Versão do cache + hash deste arquivo: mudar a extração (ou um peso)
    invalida o cache sozinho. Antes, só a data dos arquivos indexados contava,
    e uma correção no extrator ficava escondida atrás do índice velho."""
    try:
        with open(__file__, "rb") as f:
            return f"{VERSAO_CACHE}-{hashlib.sha1(f.read()).hexdigest()[:12]}"
    except OSError:
        return str(VERSAO_CACHE)

# ---------------------------------------------------------------------------------
# Ajustes (mexa aqui para calibrar)
# ---------------------------------------------------------------------------------

EXT_CFAMILIA = {
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".inl", ".m", ".mm",
    ".cs", ".java", ".kt", ".kts", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".php", ".swift", ".glsl", ".vert", ".frag", ".hlsl", ".shader",
}
EXT_PYTHON = {".py", ".pyi", ".gd"}
EXT_MARKDOWN = {".md", ".markdown", ".rst"}
EXT_OUTROS = {
    ".txt", ".json", ".xml", ".svg", ".yaml", ".yml", ".toml", ".sh", ".bash",
    ".cmake", ".html", ".css", ".scss", ".lua", ".cfg", ".ini", ".properties",
    ".inc", ".ino", ".gml", ".rpy", ".cmd", ".bat", ".ps1", ".mk",
}
NOMES_TEXTO = {"makefile", "cmakelists.txt", "dockerfile", "readme", "license"}
PASTAS_IGNORADAS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "node_modules",
    "vendor", "dist", "out", "target", ".idea", ".vscode", "coverage", ".cache",
}
MAX_BYTES = 1_500_000

# Peso de cada campo na pontuação.
PESO_NOME = 6.0
PESO_DOC = 3.0
PESO_TEXTO = 2.5   # strings entre aspas ("SEALED", mensagens)
PESO_CAMINHO = 2.0
PESO_CODIGO = 1.0
K1 = 1.2
B = 0.55
PESO_TESTE = 0.65            # arquivo de teste vale menos: quem procura costuma querer a implementação
PESO_DOCUMENTO = 0.6        # seção de documento/trecho de texto vale menos que código
EXTRA_NOME = 0.9               # soma por termo que está no nome do trecho (vezes peso e idf)
EXTRA_DOC = 0.4                # idem, para o comentário de documentação
BONUS_NOME = 0.5               # por ideia a mais que o nome do trecho já carrega
BONUS_COBERTURA = 0.35      # por grupo de ideias casado além do primeiro
REFORCO_VIZINHO = 0.12      # fração da nota do vizinho (chama / é chamado)
PESO_SINONIMO = 0.85
PESO_PREFIXO = 0.55
JANELA_LINHAS = 40
JANELA_SOBRA = 8
MAX_LINHAS_TRECHO = 150     # trecho maior que isso é fatiado em janelas

PALAVRAS_VAZIAS = set("""
 a o as os um uma uns umas de do da dos das em no na nos nas por para pra com sem
 que qual quais onde como quando quem quanto quanta quantos quantas fica ficam
 esta estao esta sao ser foi encontro encontrar achar acho quero saber existe
 aqui isso isto esse essa isso causa causar faz fazer feito usa usar ele ela eles elas se ao aos e ou mas tem ter tudo
 the is are of in on at to for with where what how when which find is it its this that
 and or code codigo trecho parte funcao linha linhas arquivo arquivos projeto jogo
""".split())

PALAVRAS_RESERVADAS = set("""
 if else for while do switch case break continue return goto sizeof typedef static
 const volatile extern inline void int char short long float double unsigned signed
 bool true false null nullptr struct union enum class public private protected new
 delete this self def import from as with try except finally lambda pass none var
 let function fn pub use mod impl trait match loop mut ref
""".split())

# Grupos de sinônimos (português e inglês). Cada linha é uma ideia; qualquer
# palavra do grupo vale pelas outras. O arquivo find_code.sinonimos, na raiz
# do projeto, acrescenta os termos do seu jogo/projeto no mesmo formato.
SINONIMOS = """
desenhar, desenho, draw, drawing, paint, painter, render, renderer, rendering, blit, pintar
sprite, bitmap, image, imagem, texture, textura, picture
corrente, chain, link, rope, cable, tether, shackle, grilhao, elo, fetter
portal, gate, rift, doorway, archway, arch, arco, entrance, passagem
porta, door, hatch
tranca, lock, locked, seal, sealed, selar, trancar, latch
cor, color, colour, colors, palette, paleta, rgb, tint, hue
luz, light, lighting, glow, brilho, lamp, lamparina
tocha, torch
chama, flame, fogo, burn, queimar, queima
sombra, shadow, shade
animacao, animation, anim, tween, ease, easing, interpolate, interp, lerp
tremor, shake, rumble, jitter, tremer
particula, particle, spark, faisca, burst, efeito, effect, effects
fundo, background, backdrop, bg, cenario, scenery, parallax
ceu, sky, cloud, nuvem, star, estrela, meteor, meteoro, lightning, raio, relampago
tela, screen, display, monitor
janela, window
vista, view
quadro, frame
camera, scroll, viewport
texto, text, font, fonte, glyph, caption, legenda
menu
titulo, title
configuracao, settings, config, preferences, opcoes, options
hud, painel, panel, interface, ui
barra, bar
vida, hp, health, life, heart, coracao, vitality
dano, damage, dmg, hurt, wound, ferir, golpe
acerto, hit, impact, impacto
morte, death, die, dead, kill, killed, matar, morrer
caido, downed, knocked
reviver, revive, rez, resurrect, levantar
cura, heal, healing, regen, curar, restore
inimigo, enemy, foe, monster, monstro, mob, hostile, adversario
chefe, boss
heroi, hero, player, jogador, personagem, character
grupo, party, coop, team, equipe
vaga, seat, slot
classe, class
tiro, bullet, shot, projectile, projetil, shoot, atirar, fire, missile
ataque, attack, strike, melee, atacar
escudo, shield, ward, parry, defesa, defense, bloquear
rolar, roll, dodge, dash, evade, esquiva, esquivar
mover, move, movement, movimento, walk, andar, correr
velocidade, speed, velocity, vel
passo, step
colisao, collision, collide, hitbox, overlap, solid, solido, bump
parede, wall
mapa, map
mundo, world
andar, floor, level, depth, profundidade
sala, room
masmorra, dungeon
tile, tiles, ladrilho, grade, grid, celula, cell
item, pickup, loot, coletar, collect
reliquia, relic, artifact, artefato
moeda, coin, coins, gold, ouro, money, dinheiro
preco, price, cost, custo, buy, comprar
loja, shop, vendor, merchant, mercador, bancada, bench, sell, vender
bau, chest, crate, caixa
tempo, time, timer, temporizador
atraso, delay, wait, esperar
recarga, cooldown
duracao, duration
chance, probability, probabilidade, odds, random, aleatorio, rng, sorteio
som, sound, sfx, audio, noise, ruido, beep, voice, voz
musica, music
salvar, save, persist, gravar, savegame
carregar, load
ler, read, parse
teclado, keyboard, key, tecla, keycode, bind, bindings
controle, controller, gamepad, pad, joystick, stick, analogico, analog, trigger, gatilho
botao, button
mouse, cursor, pointer, click, clique
entrada, input, controls, controles
pausa, pause, paused
gameover, derrota, defeat, lose, perder
vitoria, victory, win
resultados, results, summary, resumo
fim, end, finish
inicio, start, begin, init, setup
spawn, nascer, nascimento, create, criar
reset, reiniciar, restart
sair, quit, exit
fechar, close, shutdown
destruir, destroy, free, liberar, cleanup
dificuldade, difficulty, hard, easy, balance, equilibrio, escala, curve, curva
pontos, score, points, pontuacao, record, recorde, highscore
conversa, talk, dialog, dialogue, dialogo, fala, speech, balloon, balao, bubble
aviso, banner, notice, warning, alert, popup, toast
mensagem, message
minimapa, minimap, radar
erro, error, fail, failure, falha, panic
teste, test
verificar, check, verify, assert
""".strip().splitlines()

# ---------------------------------------------------------------------------------
# Texto: palavras, radicais, sinônimos
# ---------------------------------------------------------------------------------


def sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


_RE_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_RE_PALAVRA = re.compile(r"[A-Za-zÀ-ÿ]+")


def palavras(texto: str) -> list[str]:
    """Quebra identificadores e frases em palavras minúsculas e sem acento.

    `scenePaintChain`, `scene_paint_chain` e "Scene Paint Chain" dão as mesmas três.
    """
    saida = []
    for pedaco in _RE_PALAVRA.findall(texto):
        for p in _RE_CAMEL.sub(" ", pedaco).split():
            p = sem_acento(p).lower()
            if len(p) >= 2:
                saida.append(p)
    return saida


_SUFIXOS = ("amentos", "amento", "imento", "mente", "adores", "ador", "acao", "ando",
            "endo", "indo", "ados", "adas", "ado", "ada", "idos", "idas", "ido",
            "ida", "ing", "ed", "er", "ar", "ir")


def radical(p: str) -> str:
    """Radical grosseiro, igual para singular/plural e para as formas comuns do
    verbo, em português e em inglês (draw/drawing, desenho/desenhar, tile/tiles)."""
    if len(p) <= 3:
        return p
    if p.endswith("ies") and len(p) > 4:
        p = p[:-3] + "y"
    elif p.endswith("ais") and len(p) > 4:
        p = p[:-3] + "al"
    elif p.endswith("es") and len(p) > 4 and p[-3] in "sxz":
        p = p[:-2]
    elif p.endswith("ches") or p.endswith("shes"):
        p = p[:-2]
    elif p.endswith("s") and not p.endswith("ss") and len(p) > 3:
        p = p[:-1]
    for suf in _SUFIXOS:
        if p.endswith(suf) and len(p) - len(suf) >= 3:
            p = p[: -len(suf)]
            break
    if len(p) > 4 and p[-1] in "aeo":
        p = p[:-1]
    return p


def radicais(texto: str) -> list[str]:
    return [radical(p) for p in palavras(texto)]


def carregar_sinonimos(extra: list[str]) -> dict[str, set[str]]:
    """radical -> radicais equivalentes (todo o grupo, sem ele mesmo)."""
    rel: dict[str, set[str]] = defaultdict(set)
    for linha in list(SINONIMOS) + extra:
        linha = linha.split("#", 1)[0].strip()
        if not linha:
            continue
        linha = linha.replace("=", ",")
        grupo = {radical(w) for t in linha.split(",") for w in palavras(t)}
        for r in grupo:
            rel[r] |= grupo - {r}
    return rel


# ---------------------------------------------------------------------------------
# Trechos
# ---------------------------------------------------------------------------------


class Trecho:
    """Um pedaço pesquisável: função, macro, tipo, seção de documento ou janela."""

    __slots__ = ("arquivo", "inicio", "fim", "tipo", "nome", "assinatura", "doc",
                 "t_nome", "t_doc", "t_texto", "t_codigo", "compr", "idents",
                 "t_caminho", "doc_inicio", "escopo", "chamadas")

    def __init__(self, arquivo, inicio, fim, tipo, nome, assinatura, doc=""):
        self.arquivo = arquivo
        self.inicio = inicio
        self.fim = fim
        self.tipo = tipo
        self.nome = nome
        self.assinatura = assinatura
        self.doc = doc
        self.doc_inicio = None
        self.t_nome = Counter()
        self.t_doc = Counter()
        self.t_texto = Counter()
        self.t_codigo = Counter()
        self.t_caminho: Counter = Counter()
        self.compr = 0
        self.idents: set[str] = set()
        self.escopo: tuple[str, ...] = ()   # classes/funções que envolvem (Python)
        # Nomes realmente CHAMADOS, quando a linguagem permite saber (Python,
        # pela AST). None: o grafo cai na heurística de identificadores.
        self.chamadas: set[str] | None = None

    @property
    def qualificado(self) -> str:
        """`Classe.metodo` em Python; o próprio nome nas outras linguagens."""
        return ".".join(self.escopo + (self.nome,))


_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RE_DEFINE = re.compile(r"#\s*define\s+([A-Za-z_]\w*)")


def separar_cfamilia(texto: str):
    """Máquina de estados: separa, por linha, o código, os comentários e as
    strings (sem as aspas). Mantém o número de linhas do original."""
    codigo, comentario, textos, marcas = [], [], [], []
    cod, com, txt = [], [], []
    tocou = False  # a linha atual tem (parte de) um comentário
    estado = "n"  # n normal, l comentário de linha, b bloco, s string, c char
    i, n = 0, len(texto)
    while i < n:
        ch = texto[i]
        nxt = texto[i + 1] if i + 1 < n else ""
        if ch == "\n":
            codigo.append("".join(cod))
            comentario.append("".join(com))
            textos.append("".join(txt))
            marcas.append(tocou or estado == "b")
            cod, com, txt = [], [], []
            tocou = estado == "b"
            if estado == "l":
                estado = "n"
            elif estado in ("s", "c"):
                estado = "n"  # string sem fechar: não deixa o erro se espalhar
            i += 1
            continue
        if estado == "n":
            if ch == "/" and nxt == "/":
                estado = "l"
                tocou = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                estado = "b"
                tocou = True
                i += 2
                continue
            if ch == '"':
                estado = "s"
                cod.append(" ")
            elif ch == "'":
                estado = "c"
                cod.append(" ")
            else:
                cod.append(ch)
        elif estado == "l":
            com.append(ch)
        elif estado == "b":
            if ch == "*" and nxt == "/":
                estado = "n"
                i += 2
                continue
            com.append(ch)
        elif estado == "s":
            if ch == "\\" and nxt and nxt != "\n":
                txt.append(nxt)
                i += 2
                continue
            if ch == '"':
                estado = "n"
            else:
                txt.append(ch)
        elif estado == "c":
            if ch == "\\" and nxt and nxt != "\n":
                i += 2
                continue
            if ch == "'":
                estado = "n"
        i += 1
    codigo.append("".join(cod))
    comentario.append("".join(com))
    textos.append("".join(txt))
    marcas.append(tocou or estado == "b")
    return codigo, comentario, textos, marcas


def _nome_do_cabecalho(cab: str) -> tuple[str, str]:
    """(tipo, nome) a partir do texto antes da chave ou do ponto e vírgula."""
    plano = " ".join(cab.split())
    if "(" in plano.split("=")[0]:
        antes = plano.split("(", 1)[0]
        ids = _RE_IDENT.findall(antes)
        ids = [x for x in ids if x not in PALAVRAS_RESERVADAS and not x.startswith("__")]
        return "função", ids[-1] if ids else "?"
    m = re.search(r"\b(struct|enum|union|class|interface|namespace)\s+([A-Za-z_]\w*)", plano)
    if m:
        return "tipo", m.group(2)
    antes = re.split(r"[=\[{]", plano)[0]
    ids = [x for x in _RE_IDENT.findall(antes) if x not in PALAVRAS_RESERVADAS]
    return "dado", ids[-1] if ids else "?"


def trechos_cfamilia(rel: str, texto: str) -> list[Trecho]:
    codigo, coment, textos, marcas = separar_cfamilia(texto)
    total = len(codigo)
    saida: list[Trecho] = []
    prof = 0
    ignoradas = 0          # chaves de `extern "C"`/namespace, que não abrem trecho
    cab_ini = None         # linha onde o item atual começou
    cab_corpo = None       # linha da primeira chave do item
    cab_texto: list[str] = []
    doc_linhas: list[int] = []   # comentário colado em cima do item
    doc_atual: list[int] = []

    def doc_para_item() -> tuple[str, int | None]:
        if not doc_atual:
            return "", None
        return " ".join(coment[k].strip(" *\t") for k in doc_atual if coment[k].strip(" *\t")), doc_atual[0] + 1

    def fechar(fim_linha: int, tipo_forcado: str | None = None):
        nonlocal cab_ini, cab_corpo, cab_texto, doc_atual
        if cab_ini is None:
            return
        cab = " ".join(cab_texto)
        tipo, nome = _nome_do_cabecalho(cab)
        if cab_corpo is None and tipo == "função":
            tipo = "protótipo"
        if tipo == "tipo" and cab.lstrip().startswith("typedef") or (tipo == "dado" and "typedef" in cab):
            # typedef struct X { ... } Nome;  -> o nome é o depois da chave
            m = re.search(r"\}\s*([A-Za-z_]\w*)\s*;", codigo[fim_linha])
            if m:
                nome = m.group(1)
                tipo = "tipo"
        if tipo_forcado:
            tipo = tipo_forcado
        t = Trecho(rel, cab_ini + 1, fim_linha + 1, tipo, nome, " ".join(cab.split())[:200])
        t.doc, t.doc_inicio = doc_para_item()
        saida.append(t)
        cab_ini = cab_corpo = None
        cab_texto = []
        doc_atual = []

    i = 0
    while i < total:
        linha = codigo[i]
        solto = linha.strip()
        eh_prep = solto.startswith("#")
        if prof - ignoradas == 0 and cab_ini is None:
            if solto == "":
                if marcas[i]:
                    doc_atual.append(i)   # linha só de comentário (inclui o fecha-bloco)
                else:
                    doc_atual = []        # linha em branco solta o comentário
                i += 1
                continue
            if eh_prep:
                m = _RE_DEFINE.match(solto)
                fim = i
                while codigo[fim].rstrip().endswith("\\") and fim + 1 < total:
                    fim += 1
                if m:
                    t = Trecho(rel, i + 1, fim + 1, "macro", m.group(1),
                               " ".join(solto.split())[:200])
                    t.doc, t.doc_inicio = doc_para_item()
                    # comentário na mesma linha: /**< ... */ ou // ...
                    extra = " ".join(coment[k].strip(" *<\t") for k in range(i, fim + 1))
                    t.doc = (t.doc + " " + extra).strip()
                    saida.append(t)
                doc_atual = []
                i = fim + 1
                continue
        # item de nível superior
        if cab_ini is None:
            cab_ini = i
        # O cabeçalho (assinatura) é o que vem antes da primeira chave do item.
        if cab_corpo is None:
            corte = linha.find("{")
            cab_texto.append((linha if corte < 0 else linha[:corte]).strip())
        for pos, ch in enumerate(linha):
            if ch == "{":
                if prof - ignoradas == 0 and re.search(r'(\bextern\s*$|\bnamespace\b[^;]*$)', " ".join(cab_texto)):
                    ignoradas += 1       # extern "C" { ... } e namespace não são um trecho
                    cab_ini = None
                    cab_texto = []
                    prof += 1
                    continue
                if prof - ignoradas == 0 and cab_corpo is None:
                    cab_corpo = i
                prof += 1
            elif ch == "}":
                prof -= 1
                if prof < ignoradas:
                    ignoradas = max(0, prof)
                elif prof - ignoradas == 0 and cab_ini is not None and cab_corpo is not None:
                    fechar(i)
        if cab_ini is not None and cab_corpo is None and not "".join(cab_texto).strip(" };"):
            cab_ini, cab_texto = None, []      # sobrou só o `}` que fechou um extern/namespace
        if cab_ini is not None and prof - ignoradas == 0 and ";" in linha and cab_corpo is None:
            fechar(i)
        i += 1
    if cab_ini is not None:
        fechar(total - 1)

    for t in saida:
        preencher_cfamilia(t, codigo, coment, textos)
    return fatiar_grandes(saida)


def preencher_cfamilia(t: Trecho, codigo, coment, textos) -> None:
    a, b = t.inicio - 1, t.fim
    t.t_nome = Counter(radicais(t.nome))
    comentarios = [coment[k] for k in range(a, b)]
    t.t_doc = Counter(radicais(t.doc + " " + " ".join(comentarios)))
    t.t_texto = Counter(r for k in range(a, b) for r in radicais(textos[k]))
    ident_total = Counter()
    for k in range(a, b):
        for ident in _RE_IDENT.findall(codigo[k]):
            if ident in PALAVRAS_RESERVADAS:
                continue
            if len(ident) >= 3:
                t.idents.add(ident)
            for r in radicais(ident):
                ident_total[r] += 1
    t.t_codigo = ident_total
    t.compr = max(1, sum(ident_total.values()))


def fatiar_grandes(trechos: list[Trecho]) -> list[Trecho]:
    """Função gigante vira janelas, para a linha achada não se perder no meio."""
    return trechos  # a nota é por trecho; o destaque de linhas já aponta o ponto dentro dele


def _comentarios_acima(linhas: list[str], inicio: int) -> str:
    """Bloco de `# ...` colado logo acima da linha `inicio` (1-based).

    Para numa linha separadora (`# -----`): o que vem acima dela é o título da
    seção, não a documentação deste item.
    """
    k = inicio - 2
    bloco = []
    while k >= 0 and linhas[k].lstrip().startswith("#"):
        texto = linhas[k].strip().lstrip("#").strip()
        if texto and not texto.strip("-=*~_ "):
            break
        bloco.append(texto)
        k -= 1
    return " ".join(reversed(bloco))


def _preencher_python(t: Trecho, linhas: list[str], nos: list, doc: str) -> None:
    """Campos de busca de um trecho Python a partir dos nós da AST.

    `nos` são os nós que pertencem ao trecho (o corpo da função, ou só a parte
    da classe que não é método): o código de um método não conta para a classe.
    """
    corpo = "\n".join(linhas[t.inicio - 1:t.fim])
    comentarios = " ".join(re.findall(r"#\s*(.*)", corpo))
    # `doc` é o que se mostra (docstring ou comentário colado em cima); os
    # comentários de dentro do corpo só contam para a busca.
    t.doc = " ".join(x for x in (doc, _comentarios_acima(linhas, t.inicio)) if x)
    t.t_nome = Counter(radicais(t.nome))
    t.t_doc = Counter(radicais(t.doc + " " + comentarios))
    textos: Counter = Counter()
    idents: Counter = Counter()
    chamadas: set[str] = set()
    for raiz_no in nos:
        for n in ast.walk(raiz_no):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if n.value != doc:
                    textos.update(radicais(n.value[:2000]))
                continue
            nome = (n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute)
                    else n.arg if isinstance(n, ast.arg) else None)
            if nome:
                if nome not in PALAVRAS_RESERVADAS and len(nome) >= 3:
                    t.idents.add(nome)
                idents.update(radicais(nome))
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    chamadas.add(f.id)
                elif isinstance(f, ast.Attribute):
                    chamadas.add(f.attr)
    t.t_texto = textos
    t.t_codigo = idents
    t.chamadas = chamadas
    t.compr = max(1, sum(idents.values()))


def _trechos_python_ast(rel: str, texto: str) -> list[Trecho] | None:
    """Funções, métodos, classes e constantes de módulo, pela AST do Python.

    Melhor que expressão regular em três pontos: o fim de cada trecho é o real
    (docstring com texto sem recuo não corta mais a função), constantes e
    tabelas de módulo viram trechos (`_PREC = {...}` era invisível), e o grafo
    sabe quem é CHAMADO, em vez de ligar por qualquer identificador igual.
    Devolve None se o arquivo não for Python válido (cai na regex).
    """
    try:
        arvore = ast.parse(texto)
    except (SyntaxError, ValueError):
        return None
    linhas = texto.splitlines()
    saida: list[Trecho] = []

    doc_mod = ast.get_docstring(arvore)
    if doc_mod and arvore.body:
        t = Trecho(rel, 1, arvore.body[0].end_lineno, "módulo", Path(rel).stem,
                   linhas[0].strip()[:200] if linhas else "")
        _preencher_python(t, linhas, [arvore.body[0]], doc_mod)
        t.chamadas = set()
        saida.append(t)

    def visitar(corpo: list, escopo: tuple[str, ...], em_funcao: bool = False) -> None:
        for no in corpo:
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ini = min([d.lineno for d in no.decorator_list] + [no.lineno])
                ret = f" -> {ast.unparse(no.returns)}" if no.returns else ""
                assin = f"def {no.name}({ast.unparse(no.args)}){ret}"
                t = Trecho(rel, ini, no.end_lineno, "função", no.name, assin[:200])
                t.escopo = escopo
                _preencher_python(t, linhas, no.body + no.decorator_list, ast.get_docstring(no) or "")
                saida.append(t)
                visitar(no.body, escopo + (no.name,), em_funcao=True)
            elif isinstance(no, ast.ClassDef):
                ini = min([d.lineno for d in no.decorator_list] + [no.lineno])
                filhos = [c for c in no.body
                          if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                proprio = [c for c in no.body if c not in filhos]
                fim = no.end_lineno
                if filhos:
                    primeiro = min([filhos[0].lineno] + [d.lineno for d in filhos[0].decorator_list])
                    fim = max(no.lineno, primeiro - 1)
                    while fim > no.lineno and not linhas[fim - 1].strip():
                        fim -= 1
                bases = ", ".join(ast.unparse(b) for b in no.bases)
                t = Trecho(rel, ini, fim, "classe", no.name,
                           f"class {no.name}" + (f"({bases})" if bases else ""))
                t.escopo = escopo
                _preencher_python(t, linhas, proprio + no.bases, ast.get_docstring(no) or "")
                saida.append(t)
                visitar(no.body, escopo + (no.name,), em_funcao)
            elif isinstance(no, (ast.Assign, ast.AnnAssign)) and not em_funcao:
                # constante/tabela de módulo ou atributo de classe; variável
                # local de função é só ruído na busca
                alvos = no.targets if isinstance(no, ast.Assign) else [no.target]
                nomes = [a.id for a in alvos if isinstance(a, ast.Name)]
                if not nomes or no.value is None:
                    continue
                t = Trecho(rel, no.lineno, no.end_lineno, "dado", nomes[0],
                           linhas[no.lineno - 1].strip()[:200])
                t.escopo = escopo
                _preencher_python(t, linhas, [no.value], "")
                saida.append(t)

    visitar(arvore.body, ())
    return saida


def trechos_python(rel: str, texto: str) -> list[Trecho]:
    if rel.endswith((".py", ".pyi")):
        trechos = _trechos_python_ast(rel, texto)
        if trechos is not None:
            return trechos or trechos_janelas(rel, texto)
    return _trechos_python_regex(rel, texto)


def _trechos_python_regex(rel: str, texto: str) -> list[Trecho]:
    """Plano B (arquivo com erro de sintaxe, .gd): por indentação."""
    linhas = texto.splitlines()
    saida: list[Trecho] = []
    re_def = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
    marcas = [(i, m) for i, l in enumerate(linhas) if (m := re_def.match(l))]
    for pos, (i, m) in enumerate(marcas):
        indent = len(m.group(1))
        fim = len(linhas) - 1
        for j in range(i + 1, len(linhas)):
            l = linhas[j]
            if l.strip() and (len(l) - len(l.lstrip())) <= indent and not l.lstrip().startswith(("#", ")", "]", "}")):
                fim = j - 1
                break
        if m.group(2) == "class":
            # a classe termina onde o primeiro método começa
            prox = [k for k, mm in marcas[pos + 1:] if k <= fim]
            if prox:
                fim = prox[0] - 1
        while fim > i and not linhas[fim].strip():
            fim -= 1
        t = Trecho(rel, i + 1, fim + 1, "classe" if m.group(2) == "class" else "função",
                   m.group(3), linhas[i].strip()[:200])
        corpo = "\n".join(linhas[i:fim + 1])
        comentarios = " ".join(re.findall(r"#\s*(.*)", corpo))
        achados = re.findall(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', corpo, re.S)
        docstrings = " ".join(x or y for x, y in achados)
        t.doc = (docstrings + " " + comentarios).strip()
        t.t_nome = Counter(radicais(t.nome))
        t.t_doc = Counter(radicais(t.doc))
        t.t_texto = Counter(r for s in re.findall(r"\"([^\"\n]*)\"|'([^'\n]*)'", corpo) for part in s for r in radicais(part))
        ident_total = Counter()
        for ident in _RE_IDENT.findall(corpo):
            if ident in PALAVRAS_RESERVADAS:
                continue
            if len(ident) >= 3:
                t.idents.add(ident)
            for r in radicais(ident):
                ident_total[r] += 1
        t.t_codigo = ident_total
        t.compr = max(1, sum(ident_total.values()))
        saida.append(t)
    if not saida:
        return trechos_janelas(rel, texto)
    return saida


def trechos_markdown(rel: str, texto: str) -> list[Trecho]:
    linhas = texto.splitlines()
    # Um `# comentário` dentro de bloco ``` de código não é título de seção.
    cortes, cerca = [], None
    for i, l in enumerate(linhas):
        m = re.match(r"\s*(```+|~~~+)", l)
        if m:
            marca = m.group(1)[0] * 3
            if cerca is None:
                cerca = marca
            elif marca == cerca:
                cerca = None
            continue
        if cerca is None and re.match(r"#{1,6}\s+\S", l):
            cortes.append(i)
    if not cortes:
        return trechos_janelas(rel, texto)
    cortes.append(len(linhas))
    saida = []
    if cortes[0] > 0:
        cortes.insert(0, 0)
    for a, b in zip(cortes, cortes[1:]):
        corpo = linhas[a:b]
        titulo = re.sub(r"^#+\s*", "", corpo[0]).strip() if corpo and corpo[0].startswith("#") else "(início)"
        t = Trecho(rel, a + 1, b, "seção", titulo, corpo[0].strip()[:200] if corpo else "")
        prosa = " ".join(corpo)
        t.doc = prosa
        t.t_nome = Counter(radicais(titulo))
        t.t_doc = Counter(radicais(prosa))
        t.t_codigo = Counter()
        t.compr = max(1, sum(t.t_doc.values()))
        saida.append(t)
    return saida


def trechos_janelas(rel: str, texto: str) -> list[Trecho]:
    linhas = texto.splitlines()
    saida = []
    passo = JANELA_LINHAS - JANELA_SOBRA
    for a in range(0, max(1, len(linhas)), passo):
        b = min(len(linhas), a + JANELA_LINHAS)
        corpo = linhas[a:b]
        if not any(l.strip() for l in corpo):
            continue
        primeira = next((l.strip() for l in corpo if l.strip()), "")
        t = Trecho(rel, a + 1, b, "trecho", primeira[:60], primeira[:200])
        comentarios = " ".join(re.findall(r"#+\s*(.*)", "\n".join(corpo)))
        t.doc = comentarios
        t.t_nome = Counter(radicais(t.nome))
        t.t_doc = Counter(radicais(comentarios))
        ident_total = Counter()
        for ident in _RE_IDENT.findall("\n".join(corpo)):
            for r in radicais(ident):
                ident_total[r] += 1
        t.t_codigo = ident_total
        t.compr = max(1, sum(ident_total.values()))
        saida.append(t)
        if b >= len(linhas):
            break
    return saida


def extrair_trechos(rel: str, texto: str) -> list[Trecho]:
    ext = Path(rel).suffix.lower()
    if ext in EXT_CFAMILIA:
        trechos = trechos_cfamilia(rel, texto)
    elif ext in EXT_PYTHON:
        trechos = trechos_python(rel, texto)
    elif ext in EXT_MARKDOWN:
        trechos = trechos_markdown(rel, texto)
    else:
        trechos = trechos_janelas(rel, texto)
    caminho = Counter(radicais(rel.replace("/", " ").replace(".", " ")))
    for t in trechos:
        t.t_caminho = caminho
    return trechos


# ---------------------------------------------------------------------------------
# Índice
# ---------------------------------------------------------------------------------


def listar_arquivos(raiz: Path, usar_git: bool) -> list[str]:
    if usar_git and (raiz / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(raiz), "ls-files", "-co", "--exclude-standard", "-z"],
                capture_output=True, check=True, timeout=30,
            ).stdout.decode("utf-8", "replace")
            return sorted(p for p in out.split("\0") if p and (raiz / p).is_file())
        except (OSError, subprocess.SubprocessError):
            pass
    achados = []
    for atual, pastas, arquivos in os.walk(raiz):
        pastas[:] = sorted(d for d in pastas if d not in PASTAS_IGNORADAS
                           and not d.startswith(("build", "cmake-build")))
        for nome in arquivos:
            achados.append(str((Path(atual) / nome).relative_to(raiz)))
    return sorted(achados)


def eh_texto(rel: str) -> bool:
    p = Path(rel)
    return (p.suffix.lower() in (EXT_CFAMILIA | EXT_PYTHON | EXT_MARKDOWN | EXT_OUTROS)
            or p.name.lower() in NOMES_TEXTO)


def caminho_cache(raiz: Path) -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    h = hashlib.sha1(str(raiz).encode()).hexdigest()[:12]
    return Path(base) / "find_code" / f"{raiz.name}-{h}.pickle"


class Indice:
    def __init__(self, raiz: Path, usar_cache=True, usar_git=True, extensoes=None, so_pasta=None,
                 ignorar=frozenset()):
        self.raiz = raiz
        self.trechos: list[Trecho] = []
        self.arquivos_lidos = 0
        self.reaproveitados = 0
        cache_arq = caminho_cache(raiz)
        cache = {}
        if usar_cache and cache_arq.exists():
            try:
                with cache_arq.open("rb") as f:
                    dados = pickle.load(f)
                if dados.get("versao") == _assinatura_do_script():
                    cache = dados["arquivos"]
            except Exception:
                cache = {}
        novo = {}
        for rel in listar_arquivos(raiz, usar_git):
            if not eh_texto(rel) or rel in ignorar:
                continue
            if extensoes and Path(rel).suffix.lower() not in extensoes:
                continue
            if so_pasta and not rel.startswith(so_pasta):
                continue
            caminho = raiz / rel
            try:
                st = caminho.stat()
            except OSError:
                continue
            if st.st_size > MAX_BYTES:
                continue
            assinatura = (st.st_mtime_ns, st.st_size)
            if rel in cache and cache[rel][0] == assinatura:
                trechos = cache[rel][1]
                self.reaproveitados += 1
            else:
                try:
                    texto = caminho.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                trechos = extrair_trechos(rel, texto)
            novo[rel] = (assinatura, trechos)
            self.trechos.extend(trechos)
            self.arquivos_lidos += 1
        if usar_cache and not extensoes and not so_pasta:
            try:
                cache_arq.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("wb", dir=cache_arq.parent, delete=False) as f:
                    pickle.dump({"versao": _assinatura_do_script(), "arquivos": novo}, f)
                os.replace(f.name, cache_arq)
            except OSError:
                pass
        self._ligar()

    def _ligar(self) -> None:
        # Protótipo no .h e definição no .c são a mesma coisa: a definição herda o
        # comentário do protótipo (onde mora a documentação) e o protótipo some.
        por_nome: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(self.trechos):
            por_nome[t.nome].append(i)
        descartar = set()
        for nome, idxs in por_nome.items():
            defs = [i for i in idxs if self.trechos[i].tipo == "função"]
            protos = [i for i in idxs if self.trechos[i].tipo == "protótipo"]
            if defs and protos:
                alvo = self.trechos[defs[0]]
                for p in protos:
                    pt = self.trechos[p]
                    if pt.doc and not alvo.doc:
                        alvo.doc, alvo.doc_inicio = pt.doc, pt.doc_inicio
                    alvo.t_doc = alvo.t_doc + pt.t_doc
                    descartar.add(p)
        self.trechos = [t for i, t in enumerate(self.trechos) if i not in descartar]

        self.n = len(self.trechos)
        self.df: Counter = Counter()
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(self.trechos):
            termos = set(t.t_nome) | set(t.t_doc) | set(t.t_texto) | set(t.t_codigo) | set(t.t_caminho)
            for r in termos:
                self.df[r] += 1
                self.postings[r].append(i)
        self.compr_medio = sum(t.compr for t in self.trechos) / max(1, self.n)
        self.radicais_de_nome: set[str] = set()
        for t in self.trechos:
            self.radicais_de_nome.update(t.t_nome)
        self.vocabulario = sorted(self.df)
        formas: dict[str, Counter] = defaultdict(Counter)
        for t in self.trechos:
            for w in palavras(t.nome + " " + t.doc[:300]):
                formas[radical(w)][w] += 1
        self.rotulos = {r: c.most_common(1)[0][0] for r, c in formas.items()}

        self.definidos: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(self.trechos):
            if t.tipo in ("função", "macro", "tipo", "dado", "classe"):
                self.definidos[t.nome].append(i)
        self.chama: list[list[int]] = []
        self.chamado_por: list[list[int]] = [[] for _ in self.trechos]
        for i, t in enumerate(self.trechos):
            # Python: só o que é chamado de fato (a AST sabe), e chamar uma classe
            # é instanciá-la. Outras linguagens: qualquer identificador igual ao
            # nome de uma função (heurística; pode ligar uma variável homônima).
            nomes = t.chamadas if t.chamadas is not None else t.idents
            tipos_alvo = ("função", "classe") if t.chamadas is not None else ("função",)
            alvos = []
            for ident in nomes:
                if ident == t.nome:
                    continue
                cands = [j for j in self.definidos.get(ident, ())
                         if j != i and self.trechos[j].tipo in tipos_alvo]
                # Homônimos em arquivos diferentes (dois `ident`, dois `static
                # draw`): se há um no mesmo arquivo, é ele que está sendo chamado.
                mesmos = [j for j in cands if self.trechos[j].arquivo == t.arquivo]
                alvos.extend(mesmos or cands)
            alvos = sorted(set(alvos))
            self.chama.append(alvos)
            for j in alvos:
                self.chamado_por[j].append(i)

    def rotulo(self, r: str) -> str:
        """Palavra legível para um radical (o radical cortado é feio: 'pres', 'depoi')."""
        return self.rotulos.get(r, r)

    def idf(self, r: str) -> float:
        d = self.df.get(r, 0)
        return math.log(1 + (self.n - d + 0.5) / (d + 0.5))

    def com_prefixo(self, prefixo: str, limite=6) -> list[str]:
        if len(prefixo) < 6:     # "over" -> "overlay" era só ruído
            return []
        i = bisect.bisect_left(self.vocabulario, prefixo)
        achados = []
        while i < len(self.vocabulario) and self.vocabulario[i].startswith(prefixo):
            if self.vocabulario[i] != prefixo:
                achados.append(self.vocabulario[i])
            i += 1
            if len(achados) >= limite:
                break
        return achados


# ---------------------------------------------------------------------------------
# Pergunta, pontuação e saída
# ---------------------------------------------------------------------------------


class Grupo:
    """Uma ideia da pergunta: a palavra original e o que vale por ela."""

    def __init__(self, original: str):
        self.original = original
        self.termos: dict[str, float] = {}     # radical -> peso
        self.origem: dict[str, str] = {}       # radical -> "palavra", "sinônimo", "projeto"

    def somar(self, radical_: str, peso: float, origem: str) -> None:
        if peso > self.termos.get(radical_, 0):
            self.termos[radical_] = peso
            self.origem[radical_] = origem


def palavra_vazia(p: str, indice: Indice) -> bool:
    """Palavra que não ajuda a achar nada ("onde", "the", "de").

    Exceção: palavra-chave da linguagem que é nome de algo no projeto. Em
    "onde o for vira while", o `for` é a construção (`stmt_For`), não a
    preposição inglesa.
    """
    if p not in PALAVRAS_VAZIAS:
        return False
    return not (p in PALAVRAS_RESERVADAS and radical(p) in indice.radicais_de_nome)


def montar_grupos(pergunta: str, indice: Indice, rel: dict[str, set[str]],
                  usar_projeto=True) -> tuple[list[Grupo], list[str]]:
    avisos = []
    grupos: list[Grupo] = []
    vistos = set()
    for p in palavras(pergunta):
        if palavra_vazia(p, indice) or p in vistos:
            continue
        vistos.add(p)
        g = Grupo(p)
        r = radical(p)
        g.somar(r, 1.0, "palavra")
        for s in rel.get(r, ()):
            g.somar(s, PESO_SINONIMO, "sinônimo")
        if r not in rel:
            # verbo conjugado em português (rola, rolou, rolando, salvo): tenta o infinitivo
            for cand in (p + "r", p + "ar", p[:-1] + "ar", p[:-1] + "er", p[:-1] + "ir",
                         p[:-2] + "ar", p[:-3] + "ar", p[:-4] + "ar"):
                rc = radical(cand)
                if rc in rel and len(cand) >= 4:
                    g.somar(rc, 0.9, "palavra")
                    for s in rel[rc]:
                        g.somar(s, PESO_SINONIMO, "sinônimo")
                    break
        if usar_projeto:
            for base in list(g.termos):
                for pref in indice.com_prefixo(base):
                    g.somar(pref, PESO_PREFIXO, "projeto")
        presentes = [t for t in g.termos if indice.df.get(t, 0) > 0]
        if not presentes:
            parecidas = difflib.get_close_matches(r, indice.vocabulario, n=4, cutoff=0.72)
            dica = f" (parecidas no projeto: {', '.join(parecidas)})" if parecidas else ""
            avisos.append(f"'{p}' não aparece em lugar nenhum do projeto{dica}")
        grupos.append(g)
    ordem = [p for p in palavras(pergunta) if not palavra_vazia(p, indice)]
    for a, b in zip(ordem, ordem[1:]):
        junto = radical(a + b)
        if indice.df.get(junto, 0) > 0 or junto in rel:
            for g in grupos:
                if g.original in (a, b):
                    g.somar(junto, 1.0, "palavra")
    return grupos, avisos


def eh_identificador(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token)) and (
        "_" in token or bool(re.search(r"[a-z][A-Z]", token)))


_RE_TESTE = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$", re.I)


def pontuar(indice: Indice, grupos: list[Grupo], pergunta: str, peso_teste: float = PESO_TESTE):
    """Devolve {índice do trecho: (nota, grupos casados)}."""
    candidatos: set[int] = set()
    for g in grupos:
        for t in g.termos:
            candidatos.update(indice.postings.get(t, ()))
    exatos = {tok.lower() for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", pergunta) if eh_identificador(tok)}
    notas = {}
    for i in candidatos:
        t = indice.trechos[i]
        norma = 1 - B + B * (t.compr / indice.compr_medio)
        eh_texto_corrido = t.tipo in ("seção", "trecho")
        if eh_texto_corrido:
            norma = max(norma, 1.0) + 0.5 * math.log(1 + t.compr / indice.compr_medio)
        total = 0.0
        casados = 0
        no_nome = 0      # ideias que aparecem no próprio nome (ex.: HERO_LAVA_DAMAGE: lava + dano)
        for g in grupos:
            melhor = 0.0
            if any(r in t.t_nome for r in g.termos):
                no_nome += 1
            for r, peso in g.termos.items():
                doc = PESO_DOC * t.t_doc.get(r, 0)
                if eh_texto_corrido:
                    doc /= norma
                tf = (PESO_NOME * t.t_nome.get(r, 0) + doc
                      + PESO_TEXTO * t.t_texto.get(r, 0) / norma + PESO_CAMINHO * t.t_caminho.get(r, 0)
                      + PESO_CODIGO * t.t_codigo.get(r, 0) / norma)
                if tf <= 0:
                    continue
                valor = peso * indice.idf(r) * tf * (K1 + 1) / (tf + K1)
                # A saturação do BM25 apaga a diferença entre "está no nome" e "aparece
                # no corpo 12 vezes"; estes extras devolvem a vantagem ao nome e ao comentário.
                if r in t.t_nome:
                    valor += EXTRA_NOME * peso * indice.idf(r)
                elif r in t.t_doc and not eh_texto_corrido:
                    valor += EXTRA_DOC * peso * indice.idf(r)
                melhor = max(melhor, valor)
            if melhor > 0:
                casados += 1
                total += melhor
        if total <= 0:
            continue
        if casados > 1:
            total *= 1 + BONUS_COBERTURA * (casados - 1)
        if no_nome > 1:
            total *= 1 + BONUS_NOME * (no_nome - 1)
        if eh_texto_corrido:
            total *= PESO_DOCUMENTO
        if peso_teste != 1.0 and _RE_TESTE.search(t.arquivo):
            total *= peso_teste
        if t.nome.lower() in exatos:
            total += 25.0
        notas[i] = [total, casados]
    return notas


def reforcar_vizinhos(indice: Indice, notas: dict) -> None:
    base = {i: v[0] for i, v in notas.items()}
    for i in base:
        vizinhos = sorted((base[j] for j in set(indice.chama[i]) | set(indice.chamado_por[i]) if j in base),
                          reverse=True)[:3]
        notas[i][0] += REFORCO_VIZINHO * sum(vizinhos)


def linhas_do_trecho(indice: Indice, t: Trecho) -> list[str]:
    try:
        texto = (indice.raiz / t.arquivo).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return texto.splitlines()[t.inicio - 1:t.fim]


def melhores_linhas(t: Trecho, linhas: list[str], grupos: list[Grupo], quantas: int):
    pontos = []
    for k, linha in enumerate(linhas):
        rs = set(radicais(linha))
        nota = 0.0
        for g in grupos:
            m = max((peso for r, peso in g.termos.items() if r in rs), default=0.0)
            nota += m
        if nota > 0 and linha.strip():
            pontos.append((nota, k))
    pontos.sort(key=lambda x: (-x[0], x[1]))
    return sorted(k for _, k in pontos[:quantas])


def por_que(indice: Indice, t: Trecho, grupos: list[Grupo]) -> list[str]:
    razoes = []
    for g in grupos:
        melhor = None
        for r, peso in g.termos.items():
            for campo, contagem in (("nome", t.t_nome), ("comentário", t.t_doc),
                                    ("texto", t.t_texto), ("caminho", t.t_caminho),
                                    ("código", t.t_codigo)):
                c = contagem.get(r, 0)
                if c:
                    valor = peso * indice.idf(r) * (1 + math.log(c))
                    if campo in ("nome", "comentário"):
                        valor *= 2
                    if melhor is None or valor > melhor[0]:
                        melhor = (valor, campo, r, g.origem[r])
        if melhor:
            _, campo, r, origem = melhor
            palavra = indice.rotulo(r)
            via = "" if palavra == g.original or origem == "palavra" else f" ≈ {g.original}"
            razoes.append(f"{campo}: {palavra}{via}")
    return razoes


class Cores:
    def __init__(self, ligar: bool):
        self.ligar = ligar

    def p(self, codigo: str, texto: str) -> str:
        return f"\033[{codigo}m{texto}\033[0m" if self.ligar else texto


def destacar(linha: str, grupos: list[Grupo], cores: Cores) -> str:
    if not cores.ligar:
        return linha
    alvo = {r for g in grupos for r in g.termos}

    def troca(m):
        return cores.p("1;33", m.group(0)) if radical(sem_acento(m.group(0)).lower()) in alvo else m.group(0)
    return re.sub(r"[A-Za-zÀ-ÿ]+", troca, linha)


def curto(texto: str, limite=150) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[:limite - 3] + "..."


def nome_curto(indice: Indice, i: int) -> str:
    t = indice.trechos[i]
    return f"{t.qualificado} ({Path(t.arquivo).name}:{t.inicio})"


def resumo_doc(t: Trecho) -> str:
    doc = re.sub(r"@\w+\s*", "", t.doc)
    doc = re.sub(r"\s+", " ", doc).strip(" */<")
    return curto(doc.split(". ")[0], 170) if doc else ""


def mostrar(indice, resultados, grupos, args, cores: Cores) -> None:
    for pos, (nota, i, casados) in enumerate(resultados, start=1):
        t = indice.trechos[i]
        local = f"{t.arquivo}:{t.inicio}-{t.fim}" if t.fim > t.inicio else f"{t.arquivo}:{t.inicio}"
        print(f"{cores.p('1', f'#{pos}')}  {cores.p('36', local)}  {t.tipo} {cores.p('1;32', t.qualificado)}"
              f"   [nota {nota:.1f}; {casados}/{len(grupos)} ideias]")
        doc = resumo_doc(t)
        if doc:
            print(f"    doc: {doc}")
        razoes = por_que(indice, t, grupos)
        if razoes:
            print(f"    por quê: {' · '.join(razoes)}")
        linhas = linhas_do_trecho(indice, t)
        mostrar_k = set()
        for k in melhores_linhas(t, linhas, grupos, args.linhas):
            for c in range(max(0, k - args.contexto), min(len(linhas), k + args.contexto + 1)):
                mostrar_k.add(c)
        ultimo = None
        for k in sorted(mostrar_k):
            if ultimo is not None and k > ultimo + 1:
                print("            ...")
            print(f"    {t.inicio + k:>6}: {destacar(curto(linhas[k], 160), grupos, cores)}")
            ultimo = k
        if indice.chama[i]:
            print("    chama:      " + ", ".join(nome_curto(indice, j) for j in indice.chama[i][:args.vizinhos_max])
                  + (" ..." if len(indice.chama[i]) > args.vizinhos_max else ""))
        if indice.chamado_por[i]:
            print("    chamada por: " + ", ".join(nome_curto(indice, j) for j in indice.chamado_por[i][:args.vizinhos_max])
                  + (" ..." if len(indice.chamado_por[i]) > args.vizinhos_max else ""))
        print()


def escolher(indice: Indice, notas: dict, args) -> list[tuple[float, int, int]]:
    ordem = sorted(((v[0], i, v[1]) for i, v in notas.items()), key=lambda x: -x[0])
    saida, por_arquivo = [], Counter()
    for nota, i, casados in ordem:
        t = indice.trechos[i]
        if por_arquivo[t.arquivo] >= args.por_arquivo:
            continue
        por_arquivo[t.arquivo] += 1
        saida.append((nota, i, casados))
        if len(saida) >= args.n:
            break
    return saida


def vizinhos_do_termo(indice: Indice, rel, termo: str, quantos=24) -> None:
    """Mostra o vocabulário do projeto perto de TERMO: palavras que aparecem no
    nome ou no comentário dos trechos em que o termo (ou um sinônimo) está no nome
    ou no comentário. Serve para descobrir COMO o projeto chama a coisa."""
    grupos, avisos = montar_grupos(termo, indice, rel, usar_projeto=False)
    for a in avisos:
        print("aviso:", a)
    if not grupos:
        print("Dê uma palavra para eu olhar a vizinhança dela.")
        return
    proprios = {r for g in grupos for r in g.termos}
    ligados = [t for t in indice.trechos
               if t.tipo not in ("trecho", "seção")
               and any(r in t.t_nome or r in t.t_doc for r in proprios)]
    contagem: Counter = Counter()
    exemplos: dict[str, list[str]] = defaultdict(list)
    for t in ligados:
        for r in set(t.t_nome) | set(t.t_doc):
            if r in proprios or len(r) < 3 or indice.df[r] > 0.05 * indice.n:
                continue          # palavra comum demais (and, make, class) não ensina nada
            contagem[r] += 1
            if len(exemplos[r]) < 2 and t.nome not in exemplos[r]:
                exemplos[r].append(t.nome)
    ranque = sorted(contagem.items(), key=lambda kv: -(kv[1] * indice.idf(kv[0])))[:quantos]
    print(f"Em {len(ligados)} trechos o projeto fala de '{termo}' (nome ou comentário). "
          f"Palavras que andam junto:\n")
    for r, c in ranque:
        print(f"  {indice.rotulo(r):<18} {c:>3}x   ex.: {', '.join(exemplos[r])}")
    print("\nUse essas palavras na pergunta, ou acrescente-as a find_code.sinonimos.")


NOMES_SINONIMOS = ("find_code.sinonimos", "buscar_codigo.sinonimos")


def ler_sinonimos_extras(raiz: Path, arquivo: Path | None) -> list[str]:
    """Vocabulário do projeto: `find_code.sinonimos` na raiz (e o nome antigo,
    `buscar_codigo.sinonimos`, que o CharyRick usa e a versão anterior deste
    script nunca lia), mais o arquivo de --sinonimos."""
    caminhos = [raiz / n for n in NOMES_SINONIMOS]
    if arquivo:
        caminhos.append(arquivo)
    linhas = []
    for c in caminhos:
        if c.is_file():
            linhas += c.read_text(encoding="utf-8", errors="replace").splitlines()
    return linhas


# ---------------------------------------------------------------------------------
# Navegação: definição, árvore de chamadas, mapa
# ---------------------------------------------------------------------------------


def achar_definicoes(indice: Indice, nome: str) -> list[int]:
    """Trechos que DEFINEM `nome` (ou `Classe.metodo`). Sem diferença de
    maiúsculas se não houver casamento exato."""
    exatos = [i for i, t in enumerate(indice.trechos)
              if t.tipo not in ("seção", "trecho", "protótipo") and nome in (t.nome, t.qualificado)]
    if exatos:
        return exatos
    baixo = nome.lower()
    return [i for i, t in enumerate(indice.trechos)
            if t.tipo not in ("seção", "trecho", "protótipo")
            and baixo in (t.nome.lower(), t.qualificado.lower())]


def sugerir_nomes(indice: Indice, nome: str) -> list[str]:
    """Nomes definidos parecidos com `nome` (erro de digitação, nome curto ou
    qualificado, maiúsculas)."""
    por_chave: dict[str, set[str]] = defaultdict(set)
    for t in indice.trechos:
        if t.tipo not in ("seção", "trecho"):
            por_chave[t.nome.lower()].add(t.qualificado)
            por_chave[t.qualificado.lower()].add(t.qualificado)
    alvo = nome.lower()
    saida: list[str] = []
    for chave in difflib.get_close_matches(alvo, list(por_chave), n=8, cutoff=0.6):
        saida += sorted(por_chave[chave] - set(saida))
    for chave in sorted(por_chave):
        if (alvo in chave or chave in alvo) and len(chave) >= 3:
            saida += sorted(por_chave[chave] - set(saida))
    return saida[:8]


def _sem_definicao(indice: Indice, nome: str) -> int:
    print(f"'{nome}' não é definido em nenhum trecho indexado.")
    sug = sugerir_nomes(indice, nome)
    if sug:
        print("Parecidos: " + ", ".join(sug))
    print("Para procurar por descrição: find_code.py \"o que a coisa faz\"")
    return 1


def mostrar_definicoes(indice: Indice, nome: str, args, cores: Cores) -> int:
    """--def: onde está, assinatura, doc completa, o que chama e quem chama."""
    achados = achar_definicoes(indice, nome)
    if not achados:
        return _sem_definicao(indice, nome)
    if args.json:
        print(json.dumps([{
            "arquivo": t.arquivo, "inicio": t.inicio, "fim": t.fim, "tipo": t.tipo,
            "nome": t.qualificado, "assinatura": t.assinatura, "doc": t.doc,
            "chama": [indice.trechos[j].qualificado for j in indice.chama[i]],
            "chamada_por": [indice.trechos[j].qualificado for j in indice.chamado_por[i]],
        } for i in achados for t in [indice.trechos[i]]], ensure_ascii=False, indent=2))
        return 0
    for i in achados:
        t = indice.trechos[i]
        print(f"{cores.p('36', f'{t.arquivo}:{t.inicio}-{t.fim}')}  {t.tipo} "
              f"{cores.p('1;32', t.qualificado)}  ({t.fim - t.inicio + 1} linhas)")
        if t.assinatura:
            print(f"    {t.assinatura}")
        if t.doc:
            print("    doc: " + curto(re.sub(r"@\w+\s*", "", t.doc), 600))
        for rotulo, lista in (("chama:      ", indice.chama[i]), ("chamada por: ", indice.chamado_por[i])):
            if lista:
                print("    " + rotulo + ", ".join(nome_curto(indice, j) for j in lista))
        print()
    return 0


def arvore_de_chamadas(indice: Indice, nome: str, profundidade: int, subir: bool,
                       args, cores: Cores) -> int:
    """--arvore: desce pelo que NOME chama (ou sobe por quem chama, com --subir).

    `↺` marca recursão (o nó já está no caminho); `…` marca um nó já expandido
    em outro ramo, para a árvore não repetir subárvores inteiras.
    """
    raizes = achar_definicoes(indice, nome)
    if not raizes:
        return _sem_definicao(indice, nome)
    vizinhos = indice.chamado_por if subir else indice.chama

    def como_dado(i: int, caminho: tuple, n: int) -> dict:
        t = indice.trechos[i]
        no = {"nome": t.qualificado, "arquivo": t.arquivo, "linha": t.inicio, "filhos": []}
        if n < profundidade:
            for j in vizinhos[i]:
                if j in caminho:
                    no["filhos"].append({"nome": indice.trechos[j].qualificado, "recursao": True})
                else:
                    no["filhos"].append(como_dado(j, caminho + (j,), n + 1))
        return no

    if args.json:
        print(json.dumps([como_dado(i, (i,), 0) for i in raizes], ensure_ascii=False, indent=2))
        return 0

    expandidos: set[int] = set()

    def rotulo(i: int) -> str:
        t = indice.trechos[i]
        return f"{cores.p('1;32', t.qualificado)}  {cores.p('2', f'{t.arquivo}:{t.inicio}')}"

    def desenhar(i: int, prefixo: str, caminho: tuple, n: int) -> None:
        filhos = vizinhos[i] if n < profundidade else []
        if n >= profundidade and vizinhos[i]:
            print(f"{prefixo}└─ … ({len(vizinhos[i])} mais fundo; aumente --profundidade)")
            return
        for k, j in enumerate(filhos):
            ultimo = k == len(filhos) - 1
            ramo, cont = ("└─ ", "   ") if ultimo else ("├─ ", "│  ")
            if j in caminho:
                print(f"{prefixo}{ramo}{indice.trechos[j].qualificado} ↺ recursão")
            elif j in expandidos and vizinhos[j]:
                print(f"{prefixo}{ramo}{rotulo(j)} … (já mostrado acima)")
            else:
                print(f"{prefixo}{ramo}{rotulo(j)}")
                expandidos.add(j)
                desenhar(j, prefixo + cont, caminho + (j,), n + 1)

    sentido = "quem chama" if subir else "o que chama"
    for i in raizes:
        print(f"{rotulo(i)}   [{sentido}, até {profundidade} níveis]")
        expandidos.add(i)
        if not vizinhos[i]:
            print("   (nada: " + ("ninguém chama este trecho no projeto)" if subir
                               else "não chama nenhuma função do projeto)"))
        desenhar(i, "", (i,), 0)
        print()
    if any(indice.trechos[i].chamadas is None for i in raizes):
        print("Nota: fora de Python o grafo liga por nome igual, não por chamada real; "
              "confira o trecho.")
    return 0


def mapa(indice: Indice, caminho: str, args, cores: Cores) -> int:
    """--mapa: o sumário de um arquivo ou pasta, na ordem do arquivo."""
    alvo = caminho.rstrip("/")
    por_arquivo: dict[str, list[Trecho]] = defaultdict(list)
    for t in indice.trechos:
        if t.arquivo == alvo or t.arquivo.startswith(alvo + "/") or alvo in ("", "."):
            por_arquivo[t.arquivo].append(t)
    if not por_arquivo:
        print(f"Nenhum trecho indexado em '{caminho}' (o caminho é relativo à raiz do projeto).")
        return 1
    if args.json:
        print(json.dumps({a: [{"inicio": t.inicio, "fim": t.fim, "tipo": t.tipo,
                               "nome": t.qualificado, "doc": resumo_doc(t)}
                              for t in sorted(ts, key=lambda t: t.inicio)]
                          for a, ts in sorted(por_arquivo.items())}, ensure_ascii=False, indent=2))
        return 0
    for arquivo, ts in sorted(por_arquivo.items()):
        print(cores.p("1;36", arquivo))
        for t in sorted(ts, key=lambda t: (t.inicio, -t.fim)):
            recuo = "  " * len(t.escopo)
            doc = resumo_doc(t)
            linhas = f"{t.inicio}-{t.fim}" if t.fim > t.inicio else f"{t.inicio}"
            print(f"  {linhas:>9}  {t.tipo:<7} {recuo}{cores.p('1;32', t.nome)}"
                  + (f"  — {curto(doc, 90)}" if doc else ""))
        print()
    return 0


# ---------------------------------------------------------------------------------
# Autoteste
# ---------------------------------------------------------------------------------


def autoteste() -> int:
    erros = []

    def confere(cond, msg):
        if not cond:
            erros.append(msg)

    confere(radical("correntes") == radical("corrente"), "radical: corrente/correntes")
    confere(radical("desenhar") == radical("desenho") == radical("desenhada"), "radical: desenh*")
    confere(radical("tiles") == radical("tile"), "radical: tile/tiles")
    confere(radical("images") == radical("image"), "radical: image/images")
    confere(radical("portais") == radical("portal"), "radical: portais/portal")
    confere(radical("drawing") == radical("draw"), "radical: draw/drawing")
    confere(palavras("scenePaintChain") == ["scene", "paint", "chain"], "palavras: camelCase")
    confere(palavras("scene_paint_chain") == ["scene", "paint", "chain"], "palavras: snake_case")

    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        (raiz / "src").mkdir()
        (raiz / "src" / "scene.h").write_text(
            "/**\n * @brief Desenha uma corrente de elos entre dois pontos.\n * @param x0 Origem.\n */\n"
            "void scene_paint_chain(int x0, int y0, int x1, int y1);\n"
            "/** @brief Tempo da esquiva, em segundos. */\n#define SIM_ROLL_TIME 0.24f\n"
            "/** @brief Estado de um tiro. */\ntypedef struct Bullet {\n    float x; /**< Posição. */\n} Bullet;\n")
        (raiz / "src" / "scene.c").write_text(
            '#include "scene.h"\n'
            "void scene_paint_chain(int x0, int y0, int x1, int y1) {\n"
            "    for (int k = 0; k < 8; ++k) {\n        plot(x0 + k, y0);\n    }\n}\n"
            "static void scene_paint_arch(int x) {\n"
            "    // Tranca do portal: as correntes caem no desbloqueio.\n"
            "    scene_paint_chain(x, 0, x, 9);\n    if (x > 1) {\n        plot(x, 1);\n    }\n}\n"
            "void audio_beep(void) {\n    play(\"beep\");\n}\n"
            'extern "C" {\nvoid dentro_do_extern(void) { plot(1, 1); }\n}\n')
        (raiz / "src" / "chain_tools.py").write_text(
            "def carregar_fundo(caminho):\n    \"\"\"Lê o fundo do menu.\"\"\"\n    return open(caminho)\n")
        (raiz / "docs.md").write_text("# Portal\nO portal tem correntes.\n# Som\nbeeps e músicas\n")
        idx = Indice(raiz, usar_cache=False, usar_git=False)
        rel = carregar_sinonimos([])

        def topo(pergunta, n=3):
            grupos, _ = montar_grupos(pergunta, idx, rel)
            notas = pontuar(idx, grupos, pergunta)
            reforcar_vizinhos(idx, notas)
            return [idx.trechos[i].nome for _, i, _ in sorted(((v[0], i, v[1]) for i, v in notas.items()), reverse=True)[:n]]

        nomes = {t.nome: t for t in idx.trechos}
        confere("scene_paint_chain" in nomes and nomes["scene_paint_chain"].tipo == "função",
                "trechos: scene_paint_chain deveria ser uma função")
        confere("Desenha uma corrente" in nomes["scene_paint_chain"].doc,
                "ligação: a definição deve herdar o comentário do protótipo do .h")
        confere(sum(1 for t in idx.trechos if t.nome == "scene_paint_chain") == 1,
                "ligação: o protótipo deve sumir quando há definição")
        confere("SIM_ROLL_TIME" in nomes and nomes["SIM_ROLL_TIME"].tipo == "macro", "trechos: macro")
        confere("Bullet" in nomes and nomes["Bullet"].tipo == "tipo", "trechos: typedef struct")
        confere("dentro_do_extern" in nomes, "trechos: função dentro de extern \"C\"")
        confere(topo("onde desenha a corrente")[0] == "scene_paint_chain",
                f"ranking: 'desenha a corrente' -> {topo('onde desenha a corrente')}")
        confere(topo("chain")[0] == "scene_paint_chain", "sinônimo: chain deve achar a função de corrente")
        confere("scene_paint_arch" in topo("correntes do portal", 4), "ideias combinadas: portal + corrente")
        confere(topo("scene_paint_chain")[0] == "scene_paint_chain", "identificador exato")
        confere(topo("esquiva tempo")[0] == "SIM_ROLL_TIME", f"macro por comentário: {topo('esquiva tempo')}")
        confere(topo("beep")[0] == "audio_beep", "texto entre aspas")
        confere("carregar_fundo" in topo("fundo do menu"), "python: docstring")
        confere(idx.chamado_por[idx.trechos.index(nomes["scene_paint_chain"])] != [], "grafo: chamada por")

        # --- Python pela AST ---
        (raiz / "src" / "regras.py").write_text(
            '"""Regras de tradução."""\n'
            "# Tabela de precedência dos operadores.\n"
            "PRECEDENCIA = {'+': 6, '*': 7}\n\n\n"
            "class Tradutor:\n"
            '    """Traduz nós da árvore."""\n'
            "    LIMITE = 3\n\n"
            "    @staticmethod\n"
            "    def expr(no):\n"
            '        """Traduz uma expressão.\n\nTexto sem recuo dentro da docstring.\n"""\n'
            "        return no\n\n"
            "    def stmt_for(self, no):\n"
            "        local = self.expr(no)\n"
            "        return ajuda(local)\n\n\n"
            "def ajuda(x):\n"
            "    expr = 1   # variável homônima de um método: não é chamada\n"
            "    return Tradutor()\n")
        (raiz / "src" / "outro.py").write_text("def ajuda(x):\n    return x\n")
        (raiz / "leia.md").write_text("# Uso\n```python\n# isto não é título\nx = 1\n```\nfim\n")
        idx = Indice(raiz, usar_cache=False, usar_git=False)
        nomes = {t.qualificado: t for t in idx.trechos}
        confere("Tradutor.expr" in nomes and nomes["Tradutor.expr"].fim == 16,
                "python: método com docstring sem recuo deve ir até o fim real")
        confere(nomes.get("Tradutor.expr") is not None and nomes["Tradutor.expr"].inicio == 10,
                "python: o trecho começa no decorador")
        confere("PRECEDENCIA" in nomes and "precedência" in nomes["PRECEDENCIA"].doc,
                "python: constante de módulo com o comentário de cima")
        confere("Tradutor.LIMITE" in nomes, "python: atributo de classe")
        confere(not any(t.nome == "local" for t in idx.trechos), "python: variável local não vira trecho")
        confere("regras" in nomes and nomes["regras"].tipo == "módulo", "python: docstring do módulo")
        i_for = idx.trechos.index(nomes["Tradutor.stmt_for"]) if "Tradutor.stmt_for" in nomes else -1
        chamados = {idx.trechos[j].qualificado for j in idx.chama[i_for]} if i_for >= 0 else set()
        confere(chamados == {"Tradutor.expr", "ajuda"},
                f"grafo: chamadas reais e homônimo do mesmo arquivo -> {chamados}")
        i_ajuda = idx.trechos.index(nomes["ajuda"]) if "ajuda" in nomes else -1
        confere(i_ajuda < 0 or {idx.trechos[j].qualificado for j in idx.chama[i_ajuda]} == {"Tradutor"},
                "grafo: instanciar classe é chamada; variável homônima não")
        confere(not any(t.nome.startswith("isto") for t in idx.trechos),
                "markdown: comentário dentro de bloco de código não é seção")
        confere([idx.trechos[i].qualificado for i in achar_definicoes(idx, "stmt_for")]
                == ["Tradutor.stmt_for"], "--def acha método pelo nome curto")
        (raiz / "buscar_codigo.sinonimos").write_text("tradutor, interprete\n")
        confere(any("interprete" in l for l in ler_sinonimos_extras(raiz, None)),
                "sinônimos: lê também o nome antigo buscar_codigo.sinonimos")

        # cache: segunda leitura reaproveita tudo
        os.environ["XDG_CACHE_HOME"] = str(raiz / ".cache")
        Indice(raiz, usar_cache=True, usar_git=False)
        idx2 = Indice(raiz, usar_cache=True, usar_git=False)
        confere(idx2.reaproveitados == idx2.arquivos_lidos > 0, "cache: não reaproveitou os arquivos")
    if erros:
        print("autoteste falhou:")
        for e in erros:
            print("  -", e)
        return 1
    print("autoteste: ok")
    return 0


# ---------------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------------


def main(argv=None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:     # a saída tem acentos e símbolos; no Windows o padrão não é UTF-8
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="Acha onde algo está no código a partir de uma descrição. Offline, só biblioteca padrão.",
        epilog="Veja o topo do arquivo para como a nota é calculada e como calibrar.")
    ap.add_argument("pergunta", nargs="*", help="o que você procura, em linguagem natural (pt ou en)")
    ap.add_argument("--raiz", type=Path, default=Path(__file__).resolve().parent,
                    help="pasta do projeto (padrão: a pasta deste script)")
    ap.add_argument("-n", type=int, default=8, help="quantos trechos mostrar (padrão 8)")
    ap.add_argument("-C", "--contexto", type=int, default=0, help="linhas de contexto em volta de cada linha achada")
    ap.add_argument("--linhas", type=int, default=4, help="linhas casadas mostradas por trecho (padrão 4)")
    ap.add_argument("--por-arquivo", type=int, default=6,
                    help="máximo de trechos por arquivo (padrão 6; arquivo grande esconde o que você quer se for baixo)")
    ap.add_argument("--ext", help="só estas extensões, separadas por vírgula (ex.: .c,.h)")
    ap.add_argument("--pasta", help="só arquivos cujo caminho começa assim (ex.: src/)")
    ap.add_argument("--sinonimos", type=Path, help="arquivo extra de sinônimos (uma ideia por linha)")
    ap.add_argument("--vizinhos", metavar="TERMO", help="lista as palavras que o projeto usa perto de TERMO")
    ap.add_argument("--def", dest="definicao", metavar="NOME",
                    help="onde NOME (ou Classe.metodo) é definido: assinatura, doc, chama e chamada por")
    ap.add_argument("--arvore", metavar="NOME", help="árvore do que NOME chama (com --subir: de quem chama NOME)")
    ap.add_argument("--subir", action="store_true", help="com --arvore: sobe pelos chamadores em vez de descer")
    ap.add_argument("--profundidade", type=int, default=3, help="níveis da --arvore (padrão 3)")
    ap.add_argument("--mapa", metavar="CAMINHO",
                    help="sumário de um arquivo ou pasta (relativo à raiz; '.' = tudo)")
    ap.add_argument("--vizinhos-max", type=int, default=6, help="quantos chama/chamada-por mostrar (padrão 6)")
    ap.add_argument("--refinar", action="store_true",
                    help="uma rodada extra: usa as palavras dos melhores trechos para ampliar a pergunta")
    ap.add_argument("--explicar", action="store_true", help="mostra como a pergunta foi expandida")
    ap.add_argument("--sem-projeto", action="store_true", help="não expande por prefixos do vocabulário do projeto")
    ap.add_argument("--sem-cache", action="store_true", help="não lê nem grava o cache de índice")
    ap.add_argument("--sem-git", action="store_true", help="percorre as pastas em vez de usar git ls-files")
    ap.add_argument("--incluir-script", action="store_true", help="indexa também este script (padrão: não)")
    ap.add_argument("--so-codigo", action="store_true", help="ignora documentos (.md/.rst)")
    ap.add_argument("--testes", action="store_true", help="arquivos de teste valem tanto quanto o resto (padrão: valem menos)")
    ap.add_argument("--json", action="store_true", help="saída em JSON")
    ap.add_argument("--esperar", metavar="NOME",
                    help="sai com 1 se NOME não estiver entre os resultados (para testes/CI)")
    ap.add_argument("--cores", choices=("auto", "sim", "nao"), default="auto")
    ap.add_argument("--autoteste", action="store_true", help="roda os testes internos e sai")
    args = ap.parse_args(argv)

    if args.autoteste:
        return autoteste()

    # Compatibilidade com a forma antiga: find_code.py <pasta> "pergunta".
    palavras_pergunta = list(args.pergunta)
    if len(palavras_pergunta) > 1 and Path(palavras_pergunta[0]).is_dir():
        args.raiz = Path(palavras_pergunta.pop(0))
    pergunta = " ".join(palavras_pergunta).strip()
    navegar = args.definicao or args.arvore or args.mapa is not None
    if not pergunta and not args.vizinhos and not navegar:
        ap.print_usage()
        print("Dê uma pergunta, por exemplo: find_code.py \"onde são desenhadas as correntes do portal\"")
        return 2
    raiz = args.raiz.expanduser().resolve()
    if not raiz.is_dir():
        print(f"Erro: '{raiz}' não é uma pasta.")
        return 2

    ext = {e if e.startswith(".") else "." + e for e in args.ext.lower().split(",")} if args.ext else None
    if args.so_codigo and not ext:
        ext = EXT_CFAMILIA | EXT_PYTHON | EXT_OUTROS
    proprio = set()
    try:
        proprio.add(str(Path(__file__).resolve().relative_to(raiz)))   # o script não acha a si mesmo
    except ValueError:
        pass
    indice = Indice(raiz, usar_cache=not args.sem_cache, usar_git=not args.sem_git,
                    extensoes=ext, so_pasta=args.pasta,
                    ignorar=frozenset() if args.incluir_script else frozenset(proprio))
    if indice.n == 0:
        print("Nenhum arquivo de texto encontrado com esses filtros.")
        return 1
    rel = carregar_sinonimos(ler_sinonimos_extras(raiz, args.sinonimos))
    cores = Cores(args.cores == "sim" or (args.cores == "auto" and sys.stdout.isatty()
                                          and not os.environ.get("NO_COLOR")))

    if args.vizinhos:
        vizinhos_do_termo(indice, rel, args.vizinhos)
        return 0
    if args.definicao:
        return mostrar_definicoes(indice, args.definicao, args, cores)
    if args.arvore:
        return arvore_de_chamadas(indice, args.arvore, max(1, args.profundidade), args.subir, args, cores)
    if args.mapa is not None:
        return mapa(indice, args.mapa, args, cores)

    grupos, avisos = montar_grupos(pergunta, indice, rel, usar_projeto=not args.sem_projeto)
    if not grupos:
        print("Só achei palavras vazias na pergunta; descreva o objeto ou a ação (ex.: 'desenha corrente').")
        return 1
    peso_teste = 1.0 if args.testes else PESO_TESTE
    notas = pontuar(indice, grupos, pergunta, peso_teste)
    reforcar_vizinhos(indice, notas)

    if args.refinar and notas:
        # Realimentação: palavras do nome/comentário dos 5 melhores viram um grupo extra, de peso baixo.
        top = sorted(notas.items(), key=lambda kv: -kv[1][0])[:5]
        ja = {r for g in grupos for r in g.termos}
        c: Counter = Counter()
        for i, _ in top:
            t = indice.trechos[i]
            for r in set(t.t_nome) | set(t.t_doc):
                if r not in ja and len(r) >= 4:
                    c[r] += indice.idf(r)
        extra = Grupo("(refino)")
        for r, _ in c.most_common(6):
            extra.somar(r, 0.35, "projeto")
        if extra.termos:
            grupos.append(extra)
            notas = pontuar(indice, grupos, pergunta, peso_teste)
            reforcar_vizinhos(indice, notas)

    resultados = escolher(indice, notas, args)

    if args.json:
        print(json.dumps([{
            "arquivo": indice.trechos[i].arquivo, "inicio": indice.trechos[i].inicio,
            "fim": indice.trechos[i].fim, "tipo": indice.trechos[i].tipo,
            "nome": indice.trechos[i].qualificado, "nota": round(nota, 2), "ideias": casados,
            "por_que": por_que(indice, indice.trechos[i], grupos),
            "chama": [indice.trechos[j].qualificado for j in indice.chama[i]],
            "chamada_por": [indice.trechos[j].qualificado for j in indice.chamado_por[i]],
        } for nota, i, casados in resultados], ensure_ascii=False, indent=2))
    else:
        partes = []
        for g in grupos:
            extras = [r for r, o in g.origem.items() if o != "palavra"]
            partes.append(g.original + (f" (+{len(extras)})" if extras else ""))
        print(f"Pergunta lida como: {' · '.join(partes)}   "
              f"[{indice.n} trechos em {indice.arquivos_lidos} arquivos]")
        for a in avisos:
            print("aviso:", a)
        if args.explicar:
            for g in grupos:
                itens = ", ".join(f"{r}{'' if o == 'palavra' else '~' + o[:3]}" for r, o in g.origem.items())
                print(f"  {g.original}: {itens}")
        print()
        if not resultados:
            print("Nada provável encontrado. Tente outras palavras, --vizinhos <termo> para ver o")
            print("vocabulário do projeto, ou acrescente sinônimos em find_code.sinonimos.")
            return 1
        else:
            mostrar(indice, resultados, grupos, args, cores)
            soma: Counter = Counter()
            for nota, i, _ in resultados:
                soma[indice.trechos[i].arquivo] += nota
            print("Arquivos mais prováveis: " + ", ".join(a for a, _ in soma.most_common(4)))
            print("Dica: peça `--vizinhos <palavra>` para ver como o projeto nomeia a coisa, e siga `chama:` /"
                  " `chamada por:` para ligar as pontas.")

    if args.esperar:
        if not any(args.esperar in (indice.trechos[i].nome, indice.trechos[i].qualificado)
                   for _, i, _ in resultados):
            print(f"ESPERADO NÃO ENCONTRADO: {args.esperar}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
        sys.stdout.flush()
    except BrokenPipeError:      # `| head` fechou a saída: não é erro
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        codigo = 0
    raise SystemExit(codigo)
