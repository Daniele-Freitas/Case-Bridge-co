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
    # Mantido apenas por compatibilidade com a CLI (argumento --ai-model).
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
    max_output_tokens: int = 512,
    temperature: float = 0.0,
    force_json: bool = True,
    strict_json: bool = True,
    tools: list[dict] | None = None,
    tool_config: dict | None = None,
) -> dict[str, Any]:
    api_key = _get_api_key(opts)
    base_url = opts.base_url.rstrip("/")

    model_name = _resolve_model_name()

    def call(model: str, *, allow_force_json: bool, allow_tools: bool) -> dict[str, Any]:
        payload = _build_payload(
            prompt=prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            force_json=force_json and allow_force_json,
            tools=tools if allow_tools else None,
            tool_config=tool_config if allow_tools else None,
        )
        url = base_url + f"/{model}:generateContent"
        resp = _post_generate_content(
            url,
            api_key=api_key,
            payload=payload,
            timeout_s=opts.timeout_s,
            min_interval_s=float(getattr(opts, "min_interval_s", 0.0) or 0.0),
        )

        if resp.status_code != 200:
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

        data = resp.json()

        if allow_tools:
            args = _extract_function_call_args(data)
            if isinstance(args, dict):
                return args

        text = _extract_text(data)
        if not isinstance(text, str):
            dbg = _debug_candidate(data)
            raise GeminiError(f"Gemini não retornou texto/args parseáveis (modelo {model}; {dbg}).")

        if strict_json:
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GeminiError(
                    f"Gemini respondeu, mas o JSON é inválido (modelo {model}): {str(exc)}"
                ) from exc
            if not isinstance(obj, dict):
                raise GeminiError(f"Gemini respondeu JSON, mas não é um objeto (modelo {model}).")
            return obj

        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GeminiError(
                f"Gemini respondeu, mas não retornou JSON parseável (modelo {model}): {str(exc)}"
            ) from exc
        if not isinstance(obj, dict):
            raise GeminiError(f"Gemini respondeu JSON, mas não é um objeto (modelo {model}).")
        return obj

    return call(model_name, allow_force_json=True, allow_tools=True)
