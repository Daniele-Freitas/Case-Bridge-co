from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

from case_bridge.errors import ConfigError, GeminiError


_MODELS_PREFIX = "models/"
_BANNED_MODEL_KEYWORDS = ("tts", "image", "robotics", "deep-research", "lyria")
_PREFERRED_TEXT_MODELS = (
    "models/gemini-2.0-flash",
    "models/gemini-flash-latest",
    "models/gemini-2.5-flash",
    "models/gemini-2.0-flash-lite",
    "models/gemini-flash-lite-latest",
    "models/gemini-pro-latest",
    "models/gemini-2.5-pro",
)


@dataclass
class GeminiOptions:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    model: str = "auto"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_s: float = 20.0


def _get_api_key(opts: GeminiOptions) -> str:
    api_key = os.getenv(opts.api_key_env)
    if not api_key:
        raise ConfigError(f"{opts.api_key_env} não está definida (necessária para usar Gemini).")
    return api_key


def _supports_generate_content(model: dict) -> bool:
    methods = model.get("supportedGenerationMethods")
    return isinstance(methods, list) and "generateContent" in methods


def _is_text_model(name: str) -> bool:
    low = name.lower()
    return not any(bad in low for bad in _BANNED_MODEL_KEYWORDS)


def _gemini_list_models(*, base_url: str, api_key: str, timeout_s: float) -> list[dict]:
    url = base_url.rstrip("/") + "/models"
    try:
        resp = requests.get(url, params={"key": api_key}, timeout=timeout_s)
    except requests.RequestException as exc:
        raise GeminiError("Falha ao listar modelos do Gemini (rede/timeout).") from exc

    if resp.status_code != 200:
        raise GeminiError(f"Falha ao listar modelos do Gemini (HTTP {resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    models = data.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def _pick_best_text_model(models: list[dict]) -> str | None:
    supported: list[str] = []
    for m in models:
        name = m.get("name")
        if _supports_generate_content(m) and isinstance(name, str) and _is_text_model(name):
            supported.append(name)

    if not supported:
        for m in models:
            name = m.get("name")
            if _supports_generate_content(m) and isinstance(name, str):
                supported.append(name)

    if not supported:
        return None

    for p in _PREFERRED_TEXT_MODELS:
        if p in supported:
            return p

    for kw in ("flash", "pro"):
        for n in supported:
            if kw in n.lower():
                return n

    return supported[0]


def _select_model(*, base_url: str, api_key: str, requested_model: str, timeout_s: float) -> str:
    requested_model = requested_model.strip()

    if requested_model.lower() == "auto":
        models = _gemini_list_models(base_url=base_url, api_key=api_key, timeout_s=timeout_s)
        picked = _pick_best_text_model(models)
        if not picked:
            raise GeminiError("Não foi possível selecionar um modelo automaticamente.")
        return picked

    requested_model = requested_model.removeprefix(_MODELS_PREFIX)
    return f"{_MODELS_PREFIX}{requested_model}"


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


def _post_generate_content(url: str, *, api_key: str, payload: dict, timeout_s: float) -> requests.Response:
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

    model_name = _select_model(
        base_url=base_url,
        api_key=api_key,
        requested_model=opts.model,
        timeout_s=opts.timeout_s,
    )

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
        resp = _post_generate_content(url, api_key=api_key, payload=payload, timeout_s=opts.timeout_s)

        if resp.status_code != 200:
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
