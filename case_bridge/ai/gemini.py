from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

from case_bridge.errors import ConfigError, GeminiError


_FIXED_MODEL = "models/gemini-2.5-flash"
_LAST_REQUEST_AT_S = 0.0


@dataclass
class GeminiOptions:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # O projeto usa um único modelo fixo para reduzir variabilidade.
    model: str = "gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_s: float = 20.0
    min_interval_s: float = 0.25


def _get_api_key(opts: GeminiOptions) -> str:
    api_key = os.getenv(opts.api_key_env)
    if not api_key:
        raise ConfigError(f"{opts.api_key_env} não está definida (necessária para usar Gemini).")
    return api_key


def _resolve_model_name() -> str:
    # Modelo fixo por decisão de projeto.
    return _FIXED_MODEL


def _build_payload(
    *,
    prompt: str,
    max_output_tokens: int,
    temperature: float,
    force_json: bool,
    tools: list[dict] | None,
    tool_config: dict | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }

    if force_json:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    if tools:
        payload["tools"] = tools

    if tool_config:
        payload["toolConfig"] = tool_config

    return payload


def _post_generate_content(
    url: str,
    *,
    api_key: str,
    payload: dict,
    timeout_s: float,
    min_interval_s: float,
) -> requests.Response:
    global _LAST_REQUEST_AT_S

    # Intervalo mínimo entre chamadas para evitar rajadas (especialmente ao processar vários e-mails).
    # Não resolve quota=0, mas ajuda com limites por segundo.
    if min_interval_s and min_interval_s > 0:
        now = time.monotonic()
        elapsed = now - _LAST_REQUEST_AT_S
        wait_s = float(min_interval_s) - float(elapsed)
        if wait_s > 0:
            time.sleep(wait_s)

    # Marca o início da chamada (controla o espaçamento entre requisições)
    _LAST_REQUEST_AT_S = time.monotonic()

    try:
        return requests.post(
            url,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        raise GeminiError("Falha ao chamar API do Gemini (rede/timeout).") from exc


def _extract_api_error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or "").strip()

    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err.get("message", "").strip()

    return (resp.text or "").strip()


def _extract_parts(data: dict) -> list[dict]:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except Exception:
        return []
    if not isinstance(parts, list):
        return []
    return [p for p in parts if isinstance(p, dict)]


def _extract_function_call_args(data: dict) -> dict[str, Any] | None:
    for p in _extract_parts(data):
        fc = p.get("functionCall")
        if not isinstance(fc, dict):
            continue
        args = fc.get("args")
        if isinstance(args, dict):
            return args
    return None


def _extract_text(data: dict) -> str | None:
    texts: list[str] = []
    for p in _extract_parts(data):
        text = p.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
    if not texts:
        return None
    return "\n".join(texts)


def _extract_finish_reason(data: dict) -> str | None:
    try:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None
        c0 = candidates[0]
        if not isinstance(c0, dict):
            return None
        fr = c0.get("finishReason")
        if isinstance(fr, str) and fr.strip():
            return fr.strip()
    except Exception:
        return None
    return None


def _extract_usage_counts(data: dict) -> tuple[int | None, int | None, int | None]:
    """Return (promptTokenCount, candidatesTokenCount, thoughtsTokenCount) when available."""
    try:
        usage = data.get("usageMetadata")
        if not isinstance(usage, dict):
            return (None, None, None)
        pt = usage.get("promptTokenCount")
        ct = usage.get("candidatesTokenCount")
        tt = usage.get("thoughtsTokenCount")
        return (
            int(pt) if isinstance(pt, int) else None,
            int(ct) if isinstance(ct, int) else None,
            int(tt) if isinstance(tt, int) else None,
        )
    except Exception:
        return (None, None, None)


def _raise_for_non_200(*, resp: requests.Response, model: str) -> None:
    if resp.status_code == 200:
        return

    if resp.status_code == 429:
        msg = _extract_api_error_message(resp)
        if "limit: 0" in msg or "limit: 0" in (resp.text or ""):
            raise GeminiError(
                "Falha ao chamar Gemini (HTTP 429): sua quota para este modelo/projeto parece ser 0 (limit: 0). "
                "Isso normalmente é configuração/plano/billing da conta (não é bug do código). "
                "Verifique rate limits/quota no console do Gemini e se sua chave tem acesso ao modelo."
            )
        raise GeminiError(
            "Falha ao chamar Gemini (HTTP 429): rate limit/quota excedida. "
            "Tente novamente mais tarde ou reduza volume de chamadas (menos e-mails por execução)."
        )

    raise GeminiError(
        f"Falha ao chamar Gemini (modelo {model}, HTTP {resp.status_code}): {resp.text[:500]}"
    )


def _parse_json_text(
    *,
    text: str,
    model: str,
    finish_reason: str | None,
    max_output_tokens: int,
    prompt_preview: str,
    prompt_tokens: int | None,
    candidate_tokens: int | None,
    thoughts_tokens: int | None,
) -> dict[str, Any]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        if finish_reason == "MAX_TOKENS":
            raise GeminiError(
                "Gemini retornou uma resposta truncada (finishReason=MAX_TOKENS), então o JSON ficou incompleto. "
                f"Aumente max_output_tokens (atual={max_output_tokens}) ou reduza o tamanho do prompt/entrada. "
                f"tokens: prompt={prompt_tokens}, output={candidate_tokens}, thoughts={thoughts_tokens}. "
                f"resposta_inicial={text[:200]!r}"
            ) from exc
        raise GeminiError(
            "Gemini respondeu, mas o JSON é inválido "
            f"(modelo {model}; finishReason={finish_reason}; max_output_tokens={max_output_tokens}; "
            f"tokens: prompt={prompt_tokens}, output={candidate_tokens}, thoughts={thoughts_tokens}). "
            f"prompt_inicial={prompt_preview!r} resposta_inicial={text[:200]!r}. erro={str(exc)}"
        ) from exc

    if not isinstance(obj, dict):
        raise GeminiError(f"Gemini respondeu JSON, mas não é um objeto (modelo {model}).")
    return obj


def _debug_candidate(data: dict) -> str:
    try:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            pf = data.get("promptFeedback")
            if isinstance(pf, dict) and isinstance(pf.get("blockReason"), str):
                return f"promptFeedback.blockReason={pf.get('blockReason')}"
            return "sem candidates"

        c0 = candidates[0] if isinstance(candidates[0], dict) else None
        if not isinstance(c0, dict):
            return "candidate[0] inválido"

        fr = c0.get("finishReason")
        if isinstance(fr, str) and fr:
            return f"finishReason={fr}"

        return "candidate[0] sem finishReason"
    except Exception:
        return "(debug indisponível)"


def generate_json(
    *,
    prompt: str,
    opts: GeminiOptions,
    max_output_tokens: int = 4096,
    temperature: float = 0.0,
    force_json: bool = True,
    tools: list[dict] | None = None,
    tool_config: dict | None = None,
) -> dict[str, Any]:
    api_key = _get_api_key(opts)
    base_url = opts.base_url.rstrip("/")

    model = _resolve_model_name()
    payload = _build_payload(
        prompt=prompt,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        force_json=force_json,
        tools=tools,
        tool_config=tool_config,
    )
    url = base_url + f"/{model}:generateContent"
    resp = _post_generate_content(
        url,
        api_key=api_key,
        payload=payload,
        timeout_s=opts.timeout_s,
        min_interval_s=float(getattr(opts, "min_interval_s", 0.0) or 0.0),
    )
    _raise_for_non_200(resp=resp, model=model)

    data = resp.json()

    # Se function-calling estiver habilitado, preferimos os args estruturados.
    args = _extract_function_call_args(data) if tools else None
    if isinstance(args, dict):
        return args

    text = _extract_text(data)
    if not isinstance(text, str):
        dbg = _debug_candidate(data)
        raise GeminiError(f"Gemini não retornou texto/args parseáveis (modelo {model}; {dbg}).")

    finish_reason = _extract_finish_reason(data)
    prompt_tokens, candidate_tokens, thoughts_tokens = _extract_usage_counts(data)

    return _parse_json_text(
        text=text,
        model=model,
        finish_reason=finish_reason,
        max_output_tokens=max_output_tokens,
        prompt_preview=prompt[:200],
        prompt_tokens=prompt_tokens,
        candidate_tokens=candidate_tokens,
        thoughts_tokens=thoughts_tokens,
    )
