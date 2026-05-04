from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Email:
    path: Path
    filial_id: str
    de: str | None
    para: str | None
    assunto: str | None
    corpo: str
    raw: str
    filial_nome: str


def _inferir_filial_id(path: Path) -> str:
    m = re.search(r"(F\d{3})", path.stem, flags=re.IGNORECASE)
    return m.group(1).upper() if m else path.stem


def _extrair_nome_por_gerente(corpo: str) -> str | None:
    for line in reversed(corpo.splitlines()):
        line = line.strip()
        if not line:
            continue

        m = re.search(r"(?i)\bgerente\b.*?[–—-]\s*(.+)$", line)
        if m:
            nome = m.group(1).strip()
            if nome:
                return nome

    return None


def _extrair_nome_por_assunto(assunto: str) -> str | None:
    assunto = assunto.strip()
    if not assunto:
        return None

    parts = re.split(r"\s+[–—-]\s+", assunto)
    if len(parts) < 2:
        return None

    nome = parts[-1].strip()
    nome = re.sub(r"(?i)^(relat[óo]rio|fechamento)\s+", "", nome).strip()
    return nome or None


def _extrair_nome_por_corpo(corpo: str) -> str | None:
    # Exemplos: "... do Posto São João" / "... do Auto Posto Central"
    m = re.search(r"(?i)\bdo\s+((?:auto\s+)?posto\s+[^\n\.]+)", corpo)
    if m:
        return m.group(1).strip()
    return None


def inferir_filial_nome(*, assunto: str | None, corpo: str, filial_id: str) -> str:
    by_gerente = _extrair_nome_por_gerente(corpo)
    if by_gerente:
        return by_gerente

    if assunto:
        by_assunto = _extrair_nome_por_assunto(assunto)
        if by_assunto:
            return by_assunto

    by_corpo = _extrair_nome_por_corpo(corpo)
    if by_corpo:
        return by_corpo

    return filial_id


def parse_email_txt(path: Path) -> Email:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    headers: dict[str, str] = {}
    body_lines: list[str] = []

    in_headers = True
    for line in lines:
        if in_headers:
            if line.strip() == "":
                in_headers = False
                continue

            if ":" in line:
                k, v = line.split(":", 1)
                key = k.strip().lower()
                val = v.strip()
                if key in ("de", "para", "assunto"):
                    headers[key] = val
                    continue

            # Linha não reconhecida como header: considera início do corpo
            in_headers = False
            body_lines.append(line)
            continue

        body_lines.append(line)

    corpo = "\n".join(body_lines).strip()
    filial_id = _inferir_filial_id(path)
    filial_nome = inferir_filial_nome(
        assunto=headers.get("assunto"),
        corpo=corpo,
        filial_id=filial_id,
    )

    return Email(
        path=path,
        filial_id=filial_id,
        de=headers.get("de"),
        para=headers.get("para"),
        assunto=headers.get("assunto"),
        corpo=corpo,
        raw=raw,
        filial_nome=filial_nome,
    )
