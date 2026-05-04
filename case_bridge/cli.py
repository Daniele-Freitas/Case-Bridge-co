from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from case_bridge.ai.gemini import GeminiOptions
from case_bridge.emails.parser import parse_email_txt
from case_bridge.emails.resumo import resumir_email_com_ia
from case_bridge.errors import CaseBridgeError, DataError
from case_bridge.paths import default_emails_dir, default_out_dir, default_vendas_dir, find_repo_root
from case_bridge.precos.rpa import DEFAULT_URL, extrair_precos_referencia, extrair_precos_referencia_de_arquivo
from case_bridge.produtos.normalizer import (
    AIOptions,
    ProdutoNormalizer,
    carregar_mapa_json,
    criar_mapa_base_slug,
)
from case_bridge.vendas.consolidar import PrecosRef, consolidar


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _collect_files(*, files: list[str] | None, dir_path: str, glob: str) -> list[Path]:
    if files:
        paths = [Path(p) for p in files]
        missing = [p for p in paths if not p.exists()]
        if missing:
            raise DataError("Arquivos não encontrados: " + ", ".join(str(p) for p in missing))
        return paths

    d = Path(dir_path)
    if not d.exists():
        raise DataError(f"Diretório não encontrado: {d}")

    out = sorted(d.glob(glob))
    if not out:
        raise DataError(f"Nenhum arquivo encontrado em {d} com glob={glob!r}")

    return out


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def cmd_precos(args: argparse.Namespace) -> int:
    if args.html:
        df = extrair_precos_referencia_de_arquivo(args.html, table_index=args.table_index)
    else:
        df = extrair_precos_referencia(args.url, table_index=args.table_index, timeout_s=args.timeout_s)

    out_path = Path(args.out)
    _ensure_parent(out_path)
    df.to_csv(out_path, index=False)
    print(f"OK: {len(df)} linhas salvas em {out_path}")
    return 0


def _build_produto_normalizer(args: argparse.Namespace, *, out_dir: Path) -> ProdutoNormalizer:
    mapa_path = Path(args.map_file) if args.map_file else (out_dir / "mapeamento_produtos.json")
    mapa_dinamico = carregar_mapa_json(mapa_path)

    if args.ai is None:
        ai_enabled = bool(os.getenv(str(args.ai_api_key_env)))
    else:
        ai_enabled = bool(args.ai)

    ai_opts = AIOptions(
        enabled=ai_enabled,
        base_url=str(args.ai_base_url),
        model=str(args.ai_model),
        api_key_env=str(args.ai_api_key_env),
        timeout_s=float(args.ai_timeout_s),
    )

    return ProdutoNormalizer(
        mapa_base_slug=criar_mapa_base_slug(),
        mapa_dinamico_slug=mapa_dinamico,
        mapa_dinamico_path=mapa_path,
        ai=ai_opts,
    )


def cmd_vendas(args: argparse.Namespace) -> int:
    root = find_repo_root()
    out_dir = default_out_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)

    arquivos = _collect_files(
        files=args.vendas,
        dir_path=args.vendas_dir,
        glob=args.vendas_glob,
    )

    precos_ref = PrecosRef.carregar(Path(args.precos))
    normalizer = _build_produto_normalizer(args, out_dir=out_dir)

    df = consolidar(arquivos, precos_ref, normalizer=normalizer)

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = out_dir / f"vendas_consolidadas_{_stamp()}.csv"

    _ensure_parent(out_path)
    df.to_csv(out_path, index=False)
    print(f"OK: {len(df)} linhas salvas em {out_path}")
    return 0


def cmd_emails(args: argparse.Namespace) -> int:
    root = find_repo_root()
    out_dir = default_out_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)

    arquivos = _collect_files(
        files=args.emails,
        dir_path=args.emails_dir,
        glob=args.emails_glob,
    )

    opts = GeminiOptions(
        base_url=str(args.ai_base_url),
        model=str(args.ai_model),
        api_key_env=str(args.ai_api_key_env),
        timeout_s=float(args.ai_timeout_s),
    )

    rows: list[dict] = []
    for path in arquivos:
        email = parse_email_txt(path)
        resumo = resumir_email_com_ia(email=email, opts=opts)
        rows.append(
            {
                "filial_id": email.filial_id,
                "filial_nome": email.filial_nome,
                "resumo": resumo.resumo,
                "destaques": "; ".join(resumo.destaques),
                "alertas": "; ".join(resumo.alertas),
                "sentimento_geral": resumo.sentimento_geral,
                "email_arquivo": str(path),
            }
        )

    if not rows:
        raise DataError("Nenhum e-mail processado.")

    df = pd.DataFrame(rows)
    df = df.sort_values(["filial_id"], kind="stable")

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = out_dir / f"resumo_gerentes_{_stamp()}.csv"

    _ensure_parent(out_path)
    df.to_csv(out_path, index=False)
    print(f"OK: {len(df)} linhas salvas em {out_path}")
    return 0


def cmd_entregaveis(args: argparse.Namespace) -> int:
    root = find_repo_root()
    out_dir = default_out_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Etapa 3.3/3.4 depende de IA para resumo dos e-mails.
    if not os.getenv(str(args.ai_api_key_env)):
        raise DataError(
            f"{args.ai_api_key_env} não está definida (necessária para resumir e-mails e gerar entregáveis)."
        )

    # 1) Garantir preços (gera apenas se não existir)
    precos_path = Path(args.precos)
    if not precos_path.exists():
        print(f"INFO: {precos_path} não existe; gerando via RPA...")
        df_precos = extrair_precos_referencia(
            args.url,
            table_index=args.table_index,
            timeout_s=args.timeout_s,
        )
        df_precos.to_csv(precos_path, index=False)
        print(f"OK: {len(df_precos)} linhas salvas em {precos_path}")

    # 2) Consolidar vendas (Etapa 2) -> nome fixo
    vendas_files = _collect_files(
        files=args.vendas,
        dir_path=args.vendas_dir,
        glob=args.vendas_glob,
    )
    precos_ref = PrecosRef.carregar(precos_path)
    normalizer = _build_produto_normalizer(args, out_dir=out_dir)
    df_vendas = consolidar(vendas_files, precos_ref, normalizer=normalizer)
    vendas_out = out_dir / "vendas_consolidadas_marco2025.csv"
    df_vendas.to_csv(vendas_out, index=False)
    print(f"OK: {len(df_vendas)} linhas salvas em {vendas_out}")

    # 3) Resumir e-mails (Etapa 3.3) -> nome fixo
    email_files = _collect_files(
        files=args.emails,
        dir_path=args.emails_dir,
        glob=args.emails_glob,
    )
    gemini_opts = GeminiOptions(
        base_url=str(args.ai_base_url),
        model=str(args.ai_model),
        api_key_env=str(args.ai_api_key_env),
        timeout_s=float(args.ai_timeout_s),
    )

    rows: list[dict] = []
    for p in email_files:
        email = parse_email_txt(p)
        resumo = resumir_email_com_ia(email=email, opts=gemini_opts)
        rows.append(
            {
                "filial_id": email.filial_id,
                "filial_nome": email.filial_nome,
                "resumo": resumo.resumo,
                "destaques": "; ".join(resumo.destaques),
                "alertas": "; ".join(resumo.alertas),
                "sentimento_geral": resumo.sentimento_geral,
                "email_arquivo": str(p),
            }
        )

    df_emails = pd.DataFrame(rows).sort_values(["filial_id"], kind="stable")
    emails_out = out_dir / "resumo_gerentes_marco2025.csv"
    df_emails.to_csv(emails_out, index=False)
    print(f"OK: {len(df_emails)} linhas salvas em {emails_out}")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    root = find_repo_root()

    parser = argparse.ArgumentParser(
        prog="case-bridge",
        description="CLI do Case Bridge (RPA + consolidação + resumo de e-mails).",
    )

    sub = parser.add_subparsers(dest="cmd")

    # precos
    p_precos = sub.add_parser("precos", help="Etapa 1: gerar precos_referencia.csv")
    src = p_precos.add_mutually_exclusive_group()
    src.add_argument("--url", default=DEFAULT_URL, help="URL da página com a tabela")
    src.add_argument("--html", help="Caminho para um .html local (modo offline)")
    p_precos.add_argument("--table-index", type=int, default=0)
    p_precos.add_argument("--timeout-s", type=float, default=20.0)
    p_precos.add_argument("--out", default=str(find_repo_root() / "precos_referencia.csv"))
    p_precos.set_defaults(func=cmd_precos)

    # vendas
    p_vendas = sub.add_parser("vendas", help="Etapa 2: consolidar CSVs de vendas")
    p_vendas.add_argument(
        "--vendas-dir",
        default=str(default_vendas_dir(root)),
        help="Diretório com CSVs de vendas (default: data/case/vendas)",
    )
    p_vendas.add_argument(
        "--vendas-glob",
        default="vendas_*.csv",
        help="Glob para arquivos dentro de vendas-dir (default: vendas_*.csv)",
    )
    p_vendas.add_argument(
        "--vendas",
        nargs="*",
        default=None,
        help="Lista explícita de arquivos de vendas (sobrepõe vendas-dir/glob)",
    )
    p_vendas.add_argument(
        "--precos",
        default=str(find_repo_root() / "precos_referencia.csv"),
        help="CSV de preços de referência",
    )
    p_vendas.add_argument("--out", default=None, help="Arquivo de saída (default: out/vendas_consolidadas_<timestamp>.csv)")

    # produto normalizer
    p_vendas.add_argument(
        "--map-file",
        default=None,
        help="JSON com mapeamentos aprendidos (default: out/mapeamento_produtos.json)",
    )
    p_vendas.add_argument(
        "--ai",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Habilita/desabilita IA para produtos desconhecidos (default: auto se GEMINI_API_KEY existir)",
    )
    p_vendas.add_argument("--ai-model", default="auto")
    p_vendas.add_argument("--ai-base-url", default="https://generativelanguage.googleapis.com/v1beta")
    p_vendas.add_argument("--ai-api-key-env", default="GEMINI_API_KEY")
    p_vendas.add_argument("--ai-timeout-s", type=float, default=20.0)
    p_vendas.set_defaults(func=cmd_vendas)

    # emails
    p_emails = sub.add_parser("emails", help="Etapa 3.3: resumir e-mails (requer Gemini)")
    p_emails.add_argument(
        "--emails-dir",
        default=str(default_emails_dir(root)),
        help="Diretório com e-mails .txt (default: data/case/emails)",
    )
    p_emails.add_argument(
        "--emails-glob",
        default="email_*.txt",
        help="Glob para arquivos dentro de emails-dir (default: email_*.txt)",
    )
    p_emails.add_argument(
        "--emails",
        nargs="*",
        default=None,
        help="Lista explícita de e-mails .txt (sobrepõe emails-dir/glob)",
    )
    p_emails.add_argument("--out", default=None, help="Arquivo de saída (default: out/resumo_gerentes_<timestamp>.csv)")
    p_emails.add_argument("--ai-model", default="auto")
    p_emails.add_argument("--ai-base-url", default="https://generativelanguage.googleapis.com/v1beta")
    p_emails.add_argument("--ai-api-key-env", default="GEMINI_API_KEY")
    p_emails.add_argument("--ai-timeout-s", type=float, default=20.0)
    p_emails.set_defaults(func=cmd_emails)

    # entregaveis
    p_ent = sub.add_parser(
        "entregaveis",
        help="Etapa 3.4: gera os 2 CSVs finais em out/ (vendas_consolidadas_marco2025.csv e resumo_gerentes_marco2025.csv)",
    )
    p_ent.add_argument(
        "--precos",
        default=str(find_repo_root() / "precos_referencia.csv"),
        help="Caminho do CSV de preços. Se não existir, será gerado via RPA.",
    )
    p_ent.add_argument("--url", default=DEFAULT_URL)
    p_ent.add_argument("--table-index", type=int, default=0)
    p_ent.add_argument("--timeout-s", type=float, default=20.0)

    p_ent.add_argument(
        "--vendas-dir",
        default=str(default_vendas_dir(root)),
    )
    p_ent.add_argument("--vendas-glob", default="vendas_*.csv")
    p_ent.add_argument("--vendas", nargs="*", default=None)

    p_ent.add_argument(
        "--emails-dir",
        default=str(default_emails_dir(root)),
    )
    p_ent.add_argument("--emails-glob", default="email_*.txt")
    p_ent.add_argument("--emails", nargs="*", default=None)

    p_ent.add_argument(
        "--map-file",
        default=None,
        help="JSON com mapeamentos aprendidos (default: out/mapeamento_produtos.json)",
    )
    p_ent.add_argument(
        "--ai",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Habilita/desabilita IA para produtos desconhecidos (default: auto se GEMINI_API_KEY existir)",
    )
    p_ent.add_argument("--ai-model", default="auto")
    p_ent.add_argument("--ai-base-url", default="https://generativelanguage.googleapis.com/v1beta")
    p_ent.add_argument("--ai-api-key-env", default="GEMINI_API_KEY")
    p_ent.add_argument("--ai-timeout-s", type=float, default=20.0)
    p_ent.set_defaults(func=cmd_entregaveis)

    return parser


def interactive_menu() -> int:
    root = find_repo_root()

    while True:
        print("\nCase Bridge")
        print("1) Etapa 1: gerar precos_referencia.csv")
        print("2) Etapa 2: consolidar vendas (out/) ")
        print("3) Etapa 3.3: resumir e-mails (out/) [requer GEMINI_API_KEY]")
        print("4) Etapa 3.4: gerar entregáveis finais (out/) [requer GEMINI_API_KEY]")
        print("0) Sair")

        choice = input("Escolha: ").strip()
        if choice == "0":
            return 0

        parser = _build_parser()
        if choice == "1":
            argv = ["precos", "--out", str(root / "precos_referencia.csv")]
        elif choice == "2":
            argv = ["vendas", "--vendas-dir", str(default_vendas_dir(root)), "--precos", str(root / "precos_referencia.csv")]
        elif choice == "3":
            argv = ["emails", "--emails-dir", str(default_emails_dir(root))]
        elif choice == "4":
            argv = ["entregaveis", "--precos", str(root / "precos_referencia.csv")]
        else:
            print("Opção inválida.")
            continue

        try:
            args = parser.parse_args(argv)
            return args.func(args)
        except CaseBridgeError as exc:
            print(f"ERRO: {exc}")
            if "GEMINI_API_KEY" in str(exc):
                print("Dica: defina a variável de ambiente GEMINI_API_KEY antes de rodar os e-mails.")
            return 2


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        return interactive_menu()

    parser = _build_parser()

    try:
        args = parser.parse_args(argv)
        if not hasattr(args, "func"):
            parser.print_help()
            return 2
        return args.func(args)
    except CaseBridgeError as exc:
        print(f"ERRO: {exc}")
        if "GEMINI_API_KEY" in str(exc):
            print("Dica: defina a variável de ambiente GEMINI_API_KEY antes de rodar os e-mails.")
        return 2
