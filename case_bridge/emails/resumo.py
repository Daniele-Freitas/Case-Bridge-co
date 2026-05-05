from __future__ import annotations

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
    # Prompt ultra-específico para forçar JSON puro (conforme exigência do case).
    prompt_sistema = (
        "Você é um analisador de dados.\n"
        "REGRA ABSOLUTA: responda SOMENTE com um JSON VÁLIDO (RFC 8259).\n"
        "- A resposta deve conter APENAS o JSON (nenhum texto antes/depois).\n"
        "- NÃO use markdown, crases, nem bloco ```json.\n"
        "- Responda em UMA ÚNICA LINHA (sem quebras de linha).\n"
        "- O primeiro caractere da resposta deve ser '{' e o último deve ser '}'.\n"
        "- Use aspas duplas em TODAS as chaves e strings.\n"
        "- Não use vírgulas finais (trailing commas).\n"
        "- Não inclua comentários.\n"
        "- Se precisar de texto com aspas, escape corretamente (\\\").\n"
        "- Antes de responder, verifique mentalmente que o JSON faz parse.\n\n"
        "Estrutura obrigatória (chaves exatas):\n"
        "{\n"
        '  \"resumo\": \"...\",\n'
        '  \"destaques\": [\"...\"],\n'
        '  \"alertas\": [\"...\"],\n'
        '  \"sentimento_geral\": \"positivo|neutro|negativo\"\n'
        "}\n\n"
        "Regras de conteúdo:\n"
        "- resumo: 1 a 3 frases, pt-BR.\n"
        "- destaques: 2 a 4 itens.\n"
        "- alertas: 0 a 3 itens (use [] se não houver).\n"
        "- sentimento_geral: exatamente um do enum.\n"
        "- Não use quebras de linha em nenhum campo; use espaço.\n"
    )

    prompt = (
        f"{prompt_sistema}\n\n"
        "Analise o seguinte e-mail e gere o JSON conforme a estrutura:\n\n"
        f"Filial: {email.filial_id} ({email.filial_nome}).\n"
        f"Assunto: {email.assunto or ''}\n\n"
        f"Corpo:\n{email.corpo}\n"
    )

    data = generate_json(
        prompt=prompt,
        opts=opts,
        max_output_tokens=4096,
        temperature=0.0,
        force_json=True,
        tools=None,
        tool_config=None,
    )

    if not isinstance(data, dict):
        raise DataError("Gemini retornou um JSON inválido (não-objeto).")

    return _validar_email_resumo(data)
