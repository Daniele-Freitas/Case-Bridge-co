from __future__ import annotations

import json
import re
from dataclasses import dataclass

from case_bridge.ai.gemini import GeminiOptions, generate_json
from case_bridge.emails.parser import Email
from case_bridge.errors import DataError


@dataclass(frozen=True)
class EmailResumo:
    resumo: str
    destaques: list[str]
    alertas: list[str]
    sentimento_geral: str


def _coerce_list_str(value: object) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        out: list[str] = []
        for x in value:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []

        # Tenta converter lista em múltiplas linhas/bullets para lista.
        lines = [ln.strip() for ln in re.split(r"\r?\n", text) if ln.strip()]
        items: list[str] = []
        for ln in lines:
            ln = re.sub(r"^[\s\-•]+", "", ln).strip()
            if ln:
                items.append(ln)

        return items or [text]

    return []


def _validar_email_resumo(data: dict) -> EmailResumo:
    resumo = data.get("resumo")
    if not isinstance(resumo, str) or not resumo.strip():
        raise DataError("JSON inválido: campo 'resumo' ausente ou vazio.")

    destaques = _coerce_list_str(data.get("destaques"))
    if not destaques:
        raise DataError("JSON inválido: campo 'destaques' ausente ou vazio.")

    alertas = _coerce_list_str(data.get("alertas"))

    sentimento = data.get("sentimento_geral")
    if not isinstance(sentimento, str):
        raise DataError("JSON inválido: campo 'sentimento_geral' ausente.")

    sentimento = sentimento.strip().lower()
    allowed = {"positivo", "neutro", "negativo"}
    if sentimento not in allowed:
        raise DataError(
            "JSON inválido: 'sentimento_geral' deve ser um de: " + ", ".join(sorted(allowed))
        )

    return EmailResumo(
        resumo=resumo.strip(),
        destaques=destaques,
        alertas=alertas,
        sentimento_geral=sentimento,
    )


def resumir_email_com_ia(*, email: Email, opts: GeminiOptions) -> EmailResumo:
    # Envia o conteúdo mais relevante (assunto + corpo). Mantém o prompt determinístico.
    prompt = (
        "Você é um assistente que resume e-mails de gerentes de postos de combustível.\n"
        "Use a chamada de função para retornar os campos estruturados.\n\n"
        "Regras:\n"
        "- resumo: 1 a 3 frases, em pt-BR.\n"
        "- destaques: 2 a 4 itens.\n"
        "- alertas: 0 a 3 itens (use [] se não houver).\n"
        "- sentimento_geral: escolha exatamente um valor do enum.\n\n"
        f"Filial: {email.filial_id} ({email.filial_nome}).\n"
        f"Assunto: {email.assunto or ''}\n\n"
        f"Corpo:\n{email.corpo}\n"
    )

    tools = [
        {
            "functionDeclarations": [
                {
                    "name": "emitir_resumo_email",
                    "description": "Retorna o resumo estruturado do e-mail em campos.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "resumo": {"type": "STRING"},
                            "destaques": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "alertas": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "sentimento_geral": {
                                "type": "STRING",
                                "enum": ["positivo", "neutro", "negativo"],
                            },
                        },
                        "required": ["resumo", "destaques", "alertas", "sentimento_geral"],
                    },
                }
            ]
        }
    ]
    tool_config = {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["emitir_resumo_email"],
        }
    }

    data = generate_json(
        prompt=prompt,
        opts=opts,
        max_output_tokens=512,
        temperature=0.0,
        force_json=True,
        strict_json=True,
        tools=tools,
        tool_config=tool_config,
    )

    if not isinstance(data, dict):
        raise DataError("Gemini retornou um JSON inválido (não-objeto).")

    # Algumas vezes o modelo devolve JSON como string dentro de um campo.
    if len(data) == 1 and "json" in data and isinstance(data.get("json"), str):
        try:
            parsed = json.loads(data["json"])
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            pass

    return _validar_email_resumo(data)
