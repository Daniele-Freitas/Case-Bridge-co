from __future__ import annotations

"""Compat: reexporta a normalização de produtos do pacote case_bridge.

Este arquivo existe para manter compatibilidade com imports antigos.
"""

from case_bridge.produtos.normalizer import (  # noqa: F401
    AIOptions,
    CANONICOS,
    ConsolidacaoError,
    ProdutoNormalizer,
    carregar_mapa_json,
    criar_mapa_base_slug,
    salvar_mapa_json,
)

__all__ = [
    "AIOptions",
    "CANONICOS",
    "ConsolidacaoError",
    "ProdutoNormalizer",
    "carregar_mapa_json",
    "criar_mapa_base_slug",
    "salvar_mapa_json",
]
