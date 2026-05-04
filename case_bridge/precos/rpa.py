from __future__ import annotations

import argparse
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from case_bridge.errors import DataError


DEFAULT_URL = "https://bridgenoc.github.io/case-postos/precos_marco2025.html"


class PrecosReferenciaError(DataError):
    pass


@dataclass(frozen=True)
class FetchOptions:
    url: str = DEFAULT_URL
    timeout_s: float = 20.0
    user_agent: str = "case-bridge-rpa/1.0 (+python requests)"


def _baixar_html(opts: FetchOptions) -> str:
    try:
        resp = requests.get(
            opts.url,
            timeout=opts.timeout_s,
            headers={"User-Agent": opts.user_agent},
        )
    except requests.RequestException as exc:
        raise PrecosReferenciaError(f"Falha ao requisitar URL: {opts.url}") from exc

    if resp.status_code != 200:
        raise PrecosReferenciaError(
            f"Resposta HTTP inesperada ({resp.status_code}) ao acessar {opts.url}"
        )

    return resp.text


def extrair_precos_referencia(
    url: str = DEFAULT_URL,
    *,
    table_index: int = 0,
    timeout_s: float = 20.0,
) -> pd.DataFrame:
    html = _baixar_html(FetchOptions(url=url, timeout_s=timeout_s))
    soup = BeautifulSoup(html, "html.parser")

    tabelas = soup.find_all("table")
    if not tabelas:
        raise PrecosReferenciaError("Nenhuma tag <table> encontrada no HTML.")

    if table_index < 0 or table_index >= len(tabelas):
        raise PrecosReferenciaError(
            f"table_index inválido: {table_index}. Encontradas {len(tabelas)} tabelas."
        )

    try:
        df = pd.read_html(io.StringIO(str(tabelas[table_index])))[0]
    except ValueError as exc:
        raise PrecosReferenciaError("Falha ao converter a tabela HTML em DataFrame.") from exc

    df.columns = [str(c).strip() for c in df.columns]
    return df


def extrair_precos_referencia_de_arquivo(
    html_path: str | Path,
    *,
    table_index: int = 0,
) -> pd.DataFrame:
    path = Path(html_path)
    if not path.exists():
        raise PrecosReferenciaError(f"Arquivo não encontrado: {path}")

    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    tabelas = soup.find_all("table")
    if not tabelas:
        raise PrecosReferenciaError("Nenhuma tag <table> encontrada no HTML.")

    if table_index < 0 or table_index >= len(tabelas):
        raise PrecosReferenciaError(
            f"table_index inválido: {table_index}. Encontradas {len(tabelas)} tabelas."
        )

    df = pd.read_html(io.StringIO(str(tabelas[table_index])))[0]
    df.columns = [str(c).strip() for c in df.columns]
    return df


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Etapa 1 (RPA): extrai preços de referência de uma página HTML e salva em CSV."
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--url", default=DEFAULT_URL, help="URL da página com a tabela")
    src.add_argument("--html", help="Caminho para um .html local (modo offline)")

    parser.add_argument(
        "--table-index",
        type=int,
        default=0,
        help="Qual tabela usar, caso existam múltiplas (0 = primeira)",
    )
    parser.add_argument(
        "--out",
        default="precos_referencia.csv",
        help="Arquivo CSV de saída",
    )

    args = parser.parse_args(argv)

    if args.html:
        df = extrair_precos_referencia_de_arquivo(args.html, table_index=args.table_index)
    else:
        df = extrair_precos_referencia(args.url, table_index=args.table_index, timeout_s=20.0)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    print(f"OK: {len(df)} linhas salvas em {out_path}")
    return 0
