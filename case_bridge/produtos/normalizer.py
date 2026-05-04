from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from case_bridge.ai.gemini import GeminiOptions, generate_json
from case_bridge.errors import DataError


class ConsolidacaoError(DataError):
    pass


CANONICOS = ("Gasolina Comum", "Etanol", "Diesel S10")


def _slug(texto: str) -> str:
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def carregar_mapa_json(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConsolidacaoError(f"JSON inválido em: {path}") from exc

    if not isinstance(data, dict):
        raise ConsolidacaoError(f"Formato inválido no JSON (esperado objeto/dict): {path}")

    mapa: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        mapa[k] = v
    return mapa


def salvar_mapa_json(path: Path, mapa: dict[str, str]) -> None:
    path.write_text(
        json.dumps(mapa, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass
class AIOptions:
    enabled: bool = False
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    model: str = "auto"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_s: float = 20.0


def criar_mapa_base_slug() -> dict[str, str]:
    mapa = {
        # Gasolina
        "gasolina comum": "Gasolina Comum",
        "gasolina comun": "Gasolina Comum",
        "gasolina c": "Gasolina Comum",
        "gas comum": "Gasolina Comum",
        "gas. comum": "Gasolina Comum",
        "gc": "Gasolina Comum",
        # Etanol
        "etanol": "Etanol",
        "etanol comum": "Etanol",
        "etanol hid": "Etanol",
        "etanol hid.": "Etanol",
        "etanol hidratado": "Etanol",
        # Diesel S10
        "diesel s10": "Diesel S10",
        "diesel s 10": "Diesel S10",
        "diesel s-10": "Diesel S10",
        "dsl s10": "Diesel S10",
        "diesel s10 aditivado": "Diesel S10",
    }
    return {_slug(k): v for k, v in mapa.items()}


def _classificar_por_heuristica(produto_raw: str) -> str:
    s = _slug(produto_raw)
    s_compact = s.replace(" ", "")

    if "etanol" in s:
        return "Etanol"

    if "diesel" in s or "dsl" in s or "s10" in s_compact:
        return "Diesel S10"

    return "Gasolina Comum"


def classificar_produto_com_ia(*, produto_raw: str, canonicos: list[str], ai: AIOptions) -> str:
    api_key = os.getenv(ai.api_key_env)
    if not api_key:
        heur = _classificar_por_heuristica(produto_raw)
        print(
            f"WARN: {ai.api_key_env} não está definida; usando heurística local (último fallback). "
            f"produto={produto_raw!r} -> {heur}"
        )
        return heur

    prompt = (
        "Você classifica nomes de combustíveis em um de três rótulos canônicos.\n"
        "Responda via function calling quando disponível ou, alternativamente, com JSON VÁLIDO "
        "no formato exato: {\"canonical\": \"...\"}.\n"
        f"Canônicos permitidos: {canonicos}.\n"
        f"Produto: {produto_raw!r}.\n"
        "Escolha exatamente UM canônico."
    )

    tools = [
        {
            "functionDeclarations": [
                {
                    "name": "set_canonical",
                    "description": "Retorna o rótulo canônico para o combustível informado.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "canonical": {
                                "type": "STRING",
                                "enum": canonicos,
                            }
                        },
                        "required": ["canonical"],
                    },
                }
            ]
        }
    ]
    tool_config = {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["set_canonical"],
        }
    }

    opts = GeminiOptions(
        base_url=ai.base_url,
        model=ai.model,
        api_key_env=ai.api_key_env,
        timeout_s=ai.timeout_s,
    )

    try:
        data = generate_json(
            prompt=prompt,
            opts=opts,
            max_output_tokens=128,
            temperature=0.0,
            force_json=True,
            tools=tools,
            tool_config=tool_config,
        )
    except Exception as exc:
        heur = _classificar_por_heuristica(produto_raw)
        print(
            "WARN: Falha ao chamar Gemini; usando heurística local (último fallback). "
            f"produto={produto_raw!r} -> {heur} err={type(exc).__name__}"
        )
        return heur

    canonical = data.get("canonical") if isinstance(data, dict) else None
    if canonical in canonicos:
        return str(canonical)

    heur = _classificar_por_heuristica(produto_raw)
    print(
        "WARN: Gemini respondeu mas não retornou 'canonical' válido; usando heurística local (último fallback). "
        f"produto={produto_raw!r} -> {heur}"
    )
    return heur


@dataclass
class ProdutoNormalizer:
    mapa_base_slug: dict[str, str]
    mapa_dinamico_slug: dict[str, str]
    mapa_dinamico_path: Path
    ai: AIOptions

    def normalize(self, produto_raw: str) -> str:
        chave = _slug(str(produto_raw))

        if chave in self.mapa_base_slug:
            return self.mapa_base_slug[chave]

        if chave in self.mapa_dinamico_slug:
            return self.mapa_dinamico_slug[chave]

        if not self.ai.enabled:
            canonico = _classificar_por_heuristica(str(produto_raw))
            self.mapa_dinamico_slug[chave] = canonico
            salvar_mapa_json(self.mapa_dinamico_path, self.mapa_dinamico_slug)
            print(
                f"INFO: IA desabilitada; mapeamento heurístico salvo em {self.mapa_dinamico_path}: "
                f"{produto_raw!r} -> {canonico}"
            )
            return canonico

        canonico = classificar_produto_com_ia(
            produto_raw=str(produto_raw),
            canonicos=list(CANONICOS),
            ai=self.ai,
        )

        self.mapa_dinamico_slug[chave] = canonico
        salvar_mapa_json(self.mapa_dinamico_path, self.mapa_dinamico_slug)
        print(
            f"INFO: mapeamento aprendido e salvo em {self.mapa_dinamico_path}: {produto_raw!r} -> {canonico}"
        )
        return canonico
