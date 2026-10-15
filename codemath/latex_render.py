"""Renderizacao: IR (formula/rules) -> LaTeX.

Toda producao de string vive aqui. As funcoes recebem estrutura, nunca
texto pronto, para que os testes possam comparar arvores antes de
comparar strings.

Nota de projeto: nao usamos sympy. Sympy canoniza e simplifica
(reordena somas, reescreve comparacoes, avalia constantes), o que
destroi a fidelidade ao codigo-fonte -- e fidelidade e' exatamente o
que um tradutor de semantica precisa preservar. O custo e' escrever um
impressor com precedencia; o beneficio e' que `x + 1 < 10` sai como
`x + 1 < 10` e nao como `x < 9`.
"""
from __future__ import annotations

from typing import List

from .formula import (
    Assign, Atom, BinOp, BoolOp, Cmd, Compare, Const, Expr, Func, If, IfExp,
    ParAssign, Seq, Skip, UnOp, Var, While,
)
from .rules import (
    Bottom, CondState, Denotation, FixApply, FunApply, Lookup, Named,
    StateExpr, Update,
)

LB = r"[\![\,"
RB = r"\,]\!]"

# precedencia: maior liga mais forte (compartilhada com unicode_render)
_PREC = {
    "if": 0,
    "->": 1, "or": 2, "and": 3, "not": 4,
    "<": 5, "<=": 5, ">": 5, ">=": 5, "==": 5, "!=": 5,
    "+": 6, "-": 6,
    "*": 7, "/": 7, "//": 7, "%": 7,
    "neg": 8,
    "**": 9,
}
_ATOM = 100

_BIN_TEX = {"+": "+", "-": "-", "*": r"\cdot"}
_CMP_TEX = {
    "<": "<", "<=": r"\leq", ">": ">", ">=": r"\geq",
    "==": "=", "!=": r"\neq",
}
_BOOL_TEX = {"and": r"\land", "or": r"\lor", "->": r"\Rightarrow"}


def split_internal(name: str):
    """Decompoe um nome interno do translator.

    '#i' -> ('ctr', 'i', 0)    '#i.fim\'' -> ('fim', 'i', 1)
    '#.brk' -> ('flag', 'brk', 0)
    Devolve None para nomes do usuario.
    """
    if not name.startswith("#"):
        return None
    core = name[1:]
    primes = len(core) - len(core.rstrip("'"))
    core = core.rstrip("'")
    if core.startswith("."):
        return ("flag", core[1:], primes)
    if "." in core:
        var, _, suf = core.partition(".")
        return (suf, var, primes)
    return ("ctr", core, primes)


def ident(name: str) -> str:
    """Nome de variavel em LaTeX.

    Nomes internos (ver translator): `#i` -> \\hat{i} (contador do for),
    `#i.fim` -> \\hat{i}_{\\mathrm{fim}}, `#.brk` -> \\mathsf{brk}; cada
    apostrofo final vira uma linha (laco aninhado).
    """
    parts = split_internal(name)
    if parts is not None:
        kind, base, primes = parts
        if kind == "flag":
            out = r"\mathsf{" + base + "}"
        elif kind == "ctr":
            out = r"\hat{" + ident(base) + "}"
        else:
            out = r"\hat{" + ident(base) + r"}_{\mathrm{" + kind + "}}"
        return out + "'" * primes
    if len(name) == 1:
        return name
    return r"\mathit{" + name.replace("_", r"\_") + "}"


def prec(e: Expr) -> int:
    """Precedencia do no mais externo de `e` (maior liga mais forte)."""
    if isinstance(e, (BinOp, Compare, BoolOp)):
        return _PREC[e.op]
    if isinstance(e, UnOp):
        return _PREC["neg"] if e.op == "-" else _PREC["not"]
    if isinstance(e, IfExp):
        return _PREC["if"]
    return _ATOM


def needs_parens(e: Expr, outer: int, strict: bool = False,
                 parent_op: str = "", side: str = "") -> bool:
    """Decide parenteses em volta de um operando.

    Alem da precedencia, tres casos que a tabela sozinha erra (a regra
    vale para os dois renderizadores):
      - menos unario como operando DIREITO de operador binario:
        `a - -b` e `a \\cdot -b` leem mal; sai `a - (-b)`;
      - `mod` misturado com produto: `a \\bmod b \\cdot c` e' ambiguo
        para quem le, embora a tabela diga que e' (a mod b)*c;
      - implicacao dentro de implicacao: `a => b => c` nao tem
        associatividade combinada.
    """
    inner = prec(e)
    if inner < outer or (strict and inner == outer):
        return True
    if side == "right" and isinstance(e, UnOp) and e.op == "-" and parent_op in _PREC:
        return True
    mult = ("*", "%")
    if isinstance(e, BinOp) and parent_op in mult and e.op in mult and e.op != parent_op:
        return True
    if isinstance(e, BoolOp) and e.op == "->" and parent_op == "->":
        return True
    return False


def _prec(e: Expr) -> int:
    return prec(e)


def _wrap(e: Expr, outer: int, *, strict: bool = False, parent_op: str = "",
          side: str = "") -> str:
    s = expr_to_latex(e)
    if needs_parens(e, outer, strict, parent_op, side):
        return r"\left(" + s + r"\right)"
    return s


# --------------------------------------------------------------------------
# Expressoes
# --------------------------------------------------------------------------
def expr_to_latex(e: Expr) -> str:
    if isinstance(e, Const):
        if e.value is True:
            return r"\mathbf{true}"
        if e.value is False:
            return r"\mathbf{false}"
        if e.value is None:
            # None e' um VALOR do Python, nao o indefinido: renderizar
            # como \\bot confundia com a funcao que diverge
            return r"\mathbf{None}"
        return str(e.value)

    if isinstance(e, Var):
        return ident(e.name)

    if isinstance(e, Atom):
        out = e.name
        for block in e.subs:
            out += ("[" + ", ".join(ident(v) for v, _ in block) + r" \mapsto "
                    + ", ".join(expr_to_latex(x) for _, x in block) + "]")
        return out

    if isinstance(e, Lookup):
        return f"{state_to_latex(e.state)}({ident(e.var)})"

    if isinstance(e, UnOp):
        if e.op == "-":
            return "-" + _wrap(e.operand, _PREC["neg"])
        # ¬ sempre com parenteses fora de atomos: `\lnot i \leq n` le mal
        if prec(e.operand) == _ATOM:
            return r"\lnot " + expr_to_latex(e.operand)
        return r"\lnot\left(" + expr_to_latex(e.operand) + r"\right)"

    if isinstance(e, BinOp):
        p = _PREC[e.op]
        if e.op == "/":
            return (r"\frac{" + expr_to_latex(e.left) + "}{"
                    + expr_to_latex(e.right) + "}")
        if e.op == "//":
            return (r"\left\lfloor \frac{" + expr_to_latex(e.left) + "}{"
                    + expr_to_latex(e.right) + r"} \right\rfloor")
        if e.op == "%":
            return (_wrap(e.left, p, parent_op="%") + r" \bmod "
                    + _wrap(e.right, p, strict=True, parent_op="%", side="right"))
        if e.op == "**":
            # exponenciacao associa a direita
            return _wrap(e.left, p, strict=True) + "^{" + expr_to_latex(e.right) + "}"
        # + - * : associativos a esquerda
        return (_wrap(e.left, p, parent_op=e.op) + " " + _BIN_TEX[e.op] + " "
                + _wrap(e.right, p, strict=(e.op == "-"), parent_op=e.op, side="right"))

    if isinstance(e, Compare):
        p = _PREC[e.op]
        return (_wrap(e.left, p, strict=True) + " " + _CMP_TEX[e.op] + " "
                + _wrap(e.right, p, strict=True))

    if isinstance(e, BoolOp):
        p = _PREC[e.op]
        sep = " " + _BOOL_TEX[e.op] + " "
        return sep.join(_wrap(o, p, parent_op=e.op) for o in e.operands)

    if isinstance(e, IfExp):
        return (r"\begin{cases}" + expr_to_latex(e.then_)
                + r", & \text{se } " + expr_to_latex(e.cond) + r" \\ "
                + expr_to_latex(e.else_) + r", & \text{caso contrário}\end{cases}")

    if isinstance(e, Func):
        if e.name == "abs":
            return r"\left\lvert " + expr_to_latex(e.args[0]) + r" \right\rvert"
        return ("\\" + e.name + r"\left(" + ", ".join(expr_to_latex(a) for a in e.args)
                + r"\right)")

    raise TypeError(f"expressao desconhecida: {e!r}")


# --------------------------------------------------------------------------
# Estados
# --------------------------------------------------------------------------
def state_to_latex(s: StateExpr) -> str:
    if isinstance(s, Bottom):
        return r"\bot"
    if isinstance(s, Named):
        return s.name
    if isinstance(s, Update):
        return (state_to_latex(s.base) + "[" + ident(s.var) + r" \mapsto "
                + expr_to_latex(s.value) + "]")
    if isinstance(s, FixApply):
        return r"\mathrm{fix}(" + s.functional + ")(" + state_to_latex(s.arg) + ")"
    if isinstance(s, FunApply):
        return s.fun + r"\left(" + state_to_latex(s.arg) + r"\right)"
    if isinstance(s, CondState):
        return (r"\begin{cases}" + state_to_latex(s.then_)
                + r", & \text{se } " + expr_to_latex(s.cond) + r" \\ "
                + state_to_latex(s.else_)
                + r", & \text{caso contrário}\end{cases}")
    raise TypeError(f"estado desconhecido: {s!r}")


# --------------------------------------------------------------------------
# Sintaxe abstrata (para aparecer dentro de [[ ]])
# --------------------------------------------------------------------------
def _cmd_block(c: Cmd) -> str:
    """Corpo de laco ou ramo de if: sequencia vai entre parenteses.

    Sem isso, `while b do c1; c2; c3` nao diz onde o laco acaba. Segue
    a convencao dos livros-texto: `;` liga mais fraco que if/while, e
    um corpo composto se escreve `while b do (c1; c2)`.
    """
    s = cmd_to_latex(c)
    return r"\left(" + s + r"\right)" if isinstance(c, Seq) else s


def cmd_to_latex(c: Cmd) -> str:
    if isinstance(c, Skip):
        return r"\mathbf{skip}"
    if isinstance(c, Assign):
        return ident(c.target) + " := " + expr_to_latex(c.value)
    if isinstance(c, ParAssign):
        return (", ".join(ident(t) for t in c.targets) + " := "
                + ", ".join(expr_to_latex(v) for v in c.values))
    if isinstance(c, Seq):
        return r";\; ".join(cmd_to_latex(x) for x in c.commands)
    if isinstance(c, If):
        return (r"\mathbf{if}\; " + expr_to_latex(c.cond)
                + r" \;\mathbf{then}\; " + _cmd_block(c.then_branch)
                + r" \;\mathbf{else}\; " + _cmd_block(c.else_branch))
    if isinstance(c, While):
        return (r"\mathbf{while}\; " + expr_to_latex(c.cond)
                + r" \;\mathbf{do}\; " + _cmd_block(c.body))
    raise TypeError(f"comando desconhecido: {c!r}")


def denote_brackets(c: Cmd, state: str = r"\sigma") -> str:
    return LB + cmd_to_latex(c) + RB + "(" + state + ")"


# --------------------------------------------------------------------------
# Documento
# --------------------------------------------------------------------------
def render_equations(cmd: Cmd, den: Denotation) -> List[str]:
    """Lista de equacoes LaTeX (sem delimitadores de math mode)."""
    eqs = [denote_brackets(cmd, den.initial_state) + " = "
           + state_to_latex(den.result)]
    for sd in den.state_defs:
        eqs.append(sd.name + " = " + state_to_latex(sd.value))
    for fd in den.functionals:
        eqs.append(
            fd.name + "(" + fd.param + ")(" + fd.state + ") = "
            + r"\begin{cases}" + state_to_latex(fd.then_)
            + r", & \text{se } " + expr_to_latex(fd.cond) + r" \\ "
            + state_to_latex(fd.else_) + r", & \text{caso contrário}\end{cases}"
        )
        for sd in fd.local_defs:
            eqs.append(sd.name + " = " + state_to_latex(sd.value))
    return eqs


def render_markdown(source: str, cmd: Cmd, den: Denotation, sections=()) -> str:
    """Documento Markdown com LaTeX entre $$ (renderiza no VSCode/GitHub)."""
    out = ["# Semântica denotacional", "", "## Programa", "",
           "```python", source.strip(), "```", "", "## Denotação", ""]
    eqs = render_equations(cmd, den)
    out.append("$$" + eqs[0] + "$$")
    if len(eqs) > 1:
        out += ["", "onde", ""]
        for e in eqs[1:]:
            out += ["$$" + e + "$$", ""]
    for title, note, items in sections:
        out += ["", "## " + title, ""]
        if note:
            out += [note, ""]
        for label, body in items:
            if label:
                out.append("**" + label + "**")
                out.append("")
            out += ["$$" + body + "$$", ""] if body else []
    return "\n".join(out).rstrip() + "\n"


def render_tex(source: str, cmd: Cmd, den: Denotation, sections=()) -> str:
    eqs = list(render_equations(cmd, den))
    for _, _, items in sections:
        eqs += [b for _, b in items if b]
    body = "\n".join(r"\begin{equation*}" + "\n" + e + "\n" + r"\end{equation*}"
                     for e in eqs)
    return (r"\documentclass{article}" "\n"
            r"\usepackage{amsmath,amssymb}" "\n"
            r"\begin{document}" "\n" + body + "\n" r"\end{document}" "\n")


_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CodeMath &mdash; {titulo}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"></script>
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{{delimiters:[{{left:'$$',right:'$$',display:true}}]}})"></script>
<style>
:root {{ color-scheme: light dark; --bg:#fbfbfa; --fg:#1c1c1a; --mut:#6b6b66;
        --line:#e3e3df; --card:#fff; --accent:#8a5a2b; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#161615; --fg:#eeeeec; --mut:#a0a09a; --line:#2e2e2b;
           --card:#1e1e1c; --accent:#d9a066; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:2.5rem 1.25rem 5rem; background:var(--bg); color:var(--fg);
       font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:60rem; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin:0 0 .25rem; letter-spacing:-.01em; }}
h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.09em;
      color:var(--mut); margin:2.5rem 0 .75rem; font-weight:600; }}
.sub {{ color:var(--mut); margin:0 0 2rem; }}
pre {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
      padding:1rem 1.1rem; overflow-x:auto; font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; }}
.eq {{ background:var(--card); border:1px solid var(--line); border-left:3px solid var(--accent);
      border-radius:8px; padding:.9rem 1.1rem; margin:.6rem 0; overflow-x:auto; }}
.lbl {{ font-size:.78rem; color:var(--mut); margin:1.2rem 0 .3rem; font-weight:600; }}
.note {{ color:var(--mut); font-size:.92rem; margin:.2rem 0 1rem; }}
footer {{ color:var(--mut); font-size:.8rem; margin-top:4rem;
          border-top:1px solid var(--line); padding-top:1rem; }}
</style></head><body><main>
<h1>Semântica denotacional</h1>
<p class="sub">{titulo}</p>
<h2>Programa</h2>
<pre>{codigo}</pre>
{corpo}
<footer>Gerado por CodeMath.</footer>
</main></body></html>
"""


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(source: str, cmd: Cmd, den: Denotation, sections=(),
                titulo: str = "programa") -> str:
    parts = ["<h2>Denotação</h2>"]
    eqs = render_equations(cmd, den)
    parts.append('<div class="eq">$$' + eqs[0] + "$$</div>")
    if len(eqs) > 1:
        parts.append('<p class="note">onde</p>')
        for e in eqs[1:]:
            parts.append('<div class="eq">$$' + e + "$$</div>")
    for title, note, items in sections:
        parts.append("<h2>" + _esc(title) + "</h2>")
        if note:
            parts.append('<p class="note">' + _esc(note) + "</p>")
        for label, body in items:
            if label:
                parts.append('<div class="lbl">' + _esc(label) + "</div>")
            if body:
                parts.append('<div class="eq">$$' + body + "$$</div>")
    return _HTML.format(titulo=_esc(titulo), codigo=_esc(source.strip()),
                        corpo="\n".join(parts))
