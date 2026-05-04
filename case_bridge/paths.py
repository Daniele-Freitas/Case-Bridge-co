from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """Encontra a raiz do projeto (procura por .git subindo diretórios)."""

    cur = (start or Path.cwd()).resolve()
    for p in (cur, *cur.parents):
        if (p / ".git").exists():
            return p
    return cur


def case_data_dir(root: Path | None = None) -> Path:
    root = find_repo_root(root)
    return root / "data" / "case"


def default_emails_dir(root: Path | None = None) -> Path:
    return case_data_dir(root) / "emails"


def default_vendas_dir(root: Path | None = None) -> Path:
    return case_data_dir(root) / "vendas"


def default_out_dir(root: Path | None = None) -> Path:
    root = find_repo_root(root)
    return root / "out"
