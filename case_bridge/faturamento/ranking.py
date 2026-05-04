from __future__ import annotations

import pandas as pd

from case_bridge.errors import DataError


_REQUIRED_COLS = {
    "filial",
    "produto",
    "valor_total_brl",
    "volume_estimado_litros",
}


def _ensure_required(df: pd.DataFrame) -> None:
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise DataError("Dados de vendas sem colunas obrigatórias: " + ", ".join(sorted(missing)))


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["valor_total_brl"] = pd.to_numeric(df["valor_total_brl"], errors="coerce")
    df["volume_estimado_litros"] = pd.to_numeric(df["volume_estimado_litros"], errors="coerce")

    if df["valor_total_brl"].isna().any() or df["volume_estimado_litros"].isna().any():
        raise DataError("Dados de vendas têm valores inválidos em valor_total_brl/volume_estimado_litros.")

    return df


def ranking_faturamento_por_filial(df_vendas: pd.DataFrame) -> pd.DataFrame:
    _ensure_required(df_vendas)
    df = _coerce_numeric(df_vendas)

    total = float(df["valor_total_brl"].sum())

    out = (
        df.groupby("filial", as_index=False)
        .agg(
            faturamento_brl=("valor_total_brl", "sum"),
            volume_estimado_litros=("volume_estimado_litros", "sum"),
            itens=("valor_total_brl", "size"),
        )
        .sort_values(["faturamento_brl", "volume_estimado_litros"], ascending=False, kind="stable")
        .reset_index(drop=True)
    )

    out.insert(0, "rank", out.index + 1)
    out["faturamento_pct"] = (out["faturamento_brl"] / total) if total else 0.0

    return out


def ranking_faturamento_por_produto(df_vendas: pd.DataFrame) -> pd.DataFrame:
    _ensure_required(df_vendas)
    df = _coerce_numeric(df_vendas)

    total = float(df["valor_total_brl"].sum())

    out = (
        df.groupby("produto", as_index=False)
        .agg(
            faturamento_brl=("valor_total_brl", "sum"),
            volume_estimado_litros=("volume_estimado_litros", "sum"),
            itens=("valor_total_brl", "size"),
        )
        .sort_values(["faturamento_brl", "volume_estimado_litros"], ascending=False, kind="stable")
        .reset_index(drop=True)
    )

    out.insert(0, "rank", out.index + 1)
    out["faturamento_pct"] = (out["faturamento_brl"] / total) if total else 0.0

    return out
