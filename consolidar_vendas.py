from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from case_bridge.produtos.normalizer import (
    AIOptions,
    ConsolidacaoError,
    ProdutoNormalizer,
    carregar_mapa_json,
    criar_mapa_base_slug,
)
from case_bridge.vendas.consolidar import PrecosRef, consolidar


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Etapa 2: normaliza e consolida vendas; calcula volume estimado via preço de referência."
    )
    parser.add_argument(
        "arquivos",
        nargs="+",
        help="Caminhos para os CSVs de vendas (ex: vendas_F001_marco2025.csv)",
    )
    parser.add_argument(
        "--precos",
        default="precos_referencia.csv",
        help="CSV de preços de referência (gerado pela Etapa 1)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Arquivo CSV unificado de saída. Se omitido, gera automaticamente "
            "vendas_consolidadas_YYYYMMDD_HHMMSS.csv."
        ),
    )

    parser.add_argument(
        "--map-file",
        default="mapeamento_produtos.json",
        help="JSON com mapeamentos aprendidos (slug -> canônico)",
    )
    parser.add_argument(
        "--ai-fallback",
        action="store_const",
        const=True,
        default=None,
        help=(
            "Se um produto for desconhecido, usa IA para classificar e salva no map-file. "
            "Se omitido, o script habilita automaticamente quando a variável --ai-api-key-env estiver definida."
        ),
    )
    parser.add_argument(
        "--ai-model",
        default="auto",
        help=(
            "Modelo do Google Gemini usado no fallback IA (ex.: gemini-1.5-flash, gemini-2.0-flash). "
            "Use 'auto' (padrão) para escolher automaticamente um modelo compatível."
        ),
    )
    parser.add_argument(
        "--ai-base-url",
        default="https://generativelanguage.googleapis.com/v1beta",
        help="Base URL da API do Google Gemini (GenerateContent).",
    )
    parser.add_argument(
        "--ai-api-key-env",
        default="GEMINI_API_KEY",
        help="Nome da variável de ambiente com a Gemini API key.",
    )

    args = parser.parse_args()

    arquivos = [Path(a) for a in args.arquivos]
    precos_ref = PrecosRef.carregar(Path(args.precos))

    mapa_path = Path(args.map_file)
    mapa_dinamico = carregar_mapa_json(mapa_path)

    ai_enabled = (
        bool(args.ai_fallback)
        if args.ai_fallback is not None
        else bool(os.getenv(str(args.ai_api_key_env)))
    )
    ai_opts = AIOptions(
        enabled=ai_enabled,
        base_url=str(args.ai_base_url),
        model=str(args.ai_model),
        api_key_env=str(args.ai_api_key_env),
    )
    normalizer = ProdutoNormalizer(
        mapa_base_slug=criar_mapa_base_slug(),
        mapa_dinamico_slug=mapa_dinamico,
        mapa_dinamico_path=mapa_path,
        ai=ai_opts,
    )

    df = consolidar(arquivos, precos_ref, normalizer=normalizer)

    if args.out:
        out_path = Path(args.out)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(f"vendas_consolidadas_{stamp}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    print(f"OK: {len(df)} linhas salvas em {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
