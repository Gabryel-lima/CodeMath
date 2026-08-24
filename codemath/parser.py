"""Camada de parsing: codigo-fonte Python -> AST nativa."""
from __future__ import annotations

import ast


def parse_source(code: str, filename: str = "<codemath>") -> ast.Module:
    """Converte texto em uma ast.Module.

    Nao faz nenhuma traducao; erros de sintaxe sobem como SyntaxError.
    """
    return ast.parse(code, filename=filename, mode="exec")


def parse_file(path: str) -> ast.Module:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_source(fh.read(), filename=path)


def dump(module: ast.Module) -> str:
    """Util de depuracao da etapa 2: visualizar a arvore."""
    return ast.dump(module, indent=4)
