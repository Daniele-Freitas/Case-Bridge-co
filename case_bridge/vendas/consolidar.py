from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from case_bridge.produtos.normalizer import ConsolidacaoError, ProdutoNormalizer


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

    cols = [
        "filial",
        "data",
        "produto",
        "valor_total_brl",
        "preco_medio_litro_brl",
        "volume_estimado_litros",
    ]
    return df[cols]
