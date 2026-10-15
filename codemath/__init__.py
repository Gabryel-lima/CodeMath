"""CodeMath -- tradutor de codigo-fonte para notacao matematica."""
from .parser import parse_source, parse_file, dump
from .translator import translate, Translator, UnsupportedNode
from .rules import denote, Denotation
from .latex_render import render_html, render_markdown, render_tex, render_equations
from .unicode_render import render_equations_text, render_text
from .api import analisar

__version__ = "0.2.0"


def compile_source(code: str, fmt: str = "md") -> str:
    """Atalho: codigo-fonte -> documento pronto (md, tex, html ou txt)."""
    tree = parse_source(code)
    cmd = translate(tree)
    den = denote(cmd)
    if fmt == "tex":
        return render_tex(code, cmd, den)
    if fmt == "html":
        return render_html(code, cmd, den)
    if fmt == "txt":
        return render_text(code, cmd, den)
    return render_markdown(code, cmd, den)


__all__ = [
    "parse_source", "parse_file", "dump", "translate", "Translator",
    "UnsupportedNode", "denote", "Denotation", "render_markdown",
    "render_tex", "render_equations", "render_html", "render_text",
    "render_equations_text", "compile_source", "analisar",
]
