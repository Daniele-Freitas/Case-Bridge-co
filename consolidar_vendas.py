from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from produto_normalizer import (
    AIOptions,
    CANONICOS,
    ConsolidacaoError,
    ProdutoNormalizer,
    carregar_mapa_json,
    criar_mapa_base_slug,
)


def _inferir_filial(file_path: Path) -> str:
    m = re.search(r"vendas_(F\d{3})_", file_path.name, flags=re.IGNORECASE)
    return m.group(1).upper() if m else file_path.stem


@dataclass(frozen=True)
class PrecosRef:
    df: pd.DataFrame

    @staticmethod
    def carregar(path: Path) -> "PrecosRef":
        if not path.exists():
            raise ConsolidacaoError(f"Arquivo de preços de referência não encontrado: {path}")

        df = pd.read_csv(path)
        required = {"produto", "preco_medio_litro_brl"}
        missing = required - set(df.columns)
        if missing:
            raise ConsolidacaoError(
                f"CSV de preços sem colunas obrigatórias {sorted(missing)}: {path}"
            )

        df = df.copy()
        df["produto"] = df["produto"].astype(str).map(lambda x: x.strip())
        df["preco_medio_litro_brl"] = pd.to_numeric(df["preco_medio_litro_brl"], errors="coerce")
        if df["preco_medio_litro_brl"].isna().any():
            raise ConsolidacaoError(
                "Há preço inválido/NaN em preco_medio_litro_brl no CSV de referência."
            )

        return PrecosRef(df=df)


def ler_vendas_csv(path: Path, *, normalizer: ProdutoNormalizer) -> pd.DataFrame:
    if not path.exists():
        raise ConsolidacaoError(f"Arquivo não encontrado: {path}")

    df = pd.read_csv(path)

    required = {"data", "produto", "valor_total_brl"}
    missing = required - set(df.columns)
    if missing:
        raise ConsolidacaoError(f"CSV {path} sem colunas obrigatórias: {sorted(missing)}")

    df = df.copy()
    df["filial"] = _inferir_filial(path)

    df["produto"] = df["produto"].astype(str).map(normalizer.normalize)
    df["valor_total_brl"] = pd.to_numeric(df["valor_total_brl"], errors="coerce")

    if df["valor_total_brl"].isna().any():
        raise ConsolidacaoError(
            f"Há valores inválidos/NaN em valor_total_brl no arquivo: {path}"
        )

    return df


def consolidar(
    arquivos: Iterable[Path],
    precos_ref: PrecosRef,
    *,
    normalizer: ProdutoNormalizer,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for a in arquivos:
        frames.append(ler_vendas_csv(a, normalizer=normalizer))

    if not frames:
        raise ConsolidacaoError("Nenhum arquivo de vendas informado.")

    df = pd.concat(frames, ignore_index=True)

    # Join com preços de referência pelos nomes canônicos
    df = df.merge(
        precos_ref.df[["produto", "preco_medio_litro_brl"]],
        on="produto",
        how="left",
        validate="many_to_one",
    )

    if df["preco_medio_litro_brl"].isna().any():
        desconhecidos = sorted(df.loc[df["preco_medio_litro_brl"].isna(), "produto"].unique())
        raise ConsolidacaoError(
            "Produtos sem preço de referência (verifique precos_referencia.csv): "
            + ", ".join(desconhecidos)
        )

    df["volume_estimado_litros"] = df["valor_total_brl"] / df["preco_medio_litro_brl"]

    # Ordem de colunas mais útil para análise
    cols = [
        "filial",
        "data",
        "produto",
        "valor_total_brl",
        "preco_medio_litro_brl",
        "volume_estimado_litros",
    ]
    df = df[cols]

    return df


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
