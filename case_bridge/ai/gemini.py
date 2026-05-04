from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from case_bridge.errors import ConfigError, GeminiError

@dataclass
class GeminiOptions:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Pode ser um nome de modelo (ex.: gemini-2.0-flash, gemini-flash-latest)
    # ou "auto" (recomendado) para escolher um modelo compatível via ListModels.
    model: str = "auto"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_s: float = 20.0


def _get_api_key(opts: GeminiOptions) -> str:
    api_key = os.getenv(opts.api_key_env)
    if not api_key:
        raise ConfigError(f"{opts.api_key_env} não está definida (necessária para usar Gemini).")
    return api_key


def _gemini_list_models(*, base_url: str, api_key: str, timeout_s: float) -> list[dict]:
    url = base_url.rstrip("/") + "/models"
    try:
        resp = requests.get(url, params={"key": api_key}, timeout=timeout_s)
    except requests.RequestException as exc:
        raise GeminiError("Falha ao listar modelos do Gemini (rede/timeout).") from exc

    if resp.status_code != 200:
        raise GeminiError(
            f"Falha ao listar modelos do Gemini (HTTP {resp.status_code}): {resp.text[:500]}"
        )

    data = resp.json()
    models = data.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def _gemini_resolver_model_name(
    *, requested: str, models: list[dict], required_method: str = "generateContent"
) -> str | None:
    requested = requested.strip()
    requested_short = requested.removeprefix("models/")

    def supports(m: dict) -> bool:
        methods = m.get("supportedGenerationMethods")
        return isinstance(methods, list) and required_method in methods

    # 1) match exato
    for m in models:
        name = m.get("name")
        if supports(m) and isinstance(name, str) and name == f"models/{requested_short}":
            return name

    # 2) match por "contém" (ex.: gemini-1.5-flash -> gemini-1.5-flash-latest)
    candidates: list[str] = []
    for m in models:
        name = m.get("name")
        if not (supports(m) and isinstance(name, str)):
            continue
        short = name.removeprefix("models/")
        if requested_short in short:
            candidates.append(name)

    if not candidates:
        return None

    for c in candidates:
        if c.endswith("-latest"):
            return c

    return candidates[0]


def _gemini_pick_best_model(
    *, models: list[dict], required_method: str = "generateContent"
) -> str | None:
    def supports(m: dict) -> bool:
        methods = m.get("supportedGenerationMethods")
        return isinstance(methods, list) and required_method in methods

    supported: list[str] = []
    for m in models:
        name = m.get("name")
        if supports(m) and isinstance(name, str):
            supported.append(name)

    if not supported:
        return None

    def is_text_model(name: str) -> bool:
        low = name.lower()
        banned = ("tts", "image", "robotics", "deep-research", "lyria")
        return not any(b in low for b in banned)

    supported_text = [n for n in supported if is_text_model(n)]
    if not supported_text:
        supported_text = supported

    preferred = [
        "models/gemini-2.0-flash",
        "models/gemini-flash-latest",
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash-lite",
        "models/gemini-flash-lite-latest",
        "models/gemini-pro-latest",
        "models/gemini-2.5-pro",
    ]
    for p in preferred:
        if p in supported_text:
            return p

    for kw in ("flash", "pro"):
        for name in supported_text:
            if kw in name.lower():
                return name

    return supported_text[0]


def _gemini_rank_text_models(*, models: list[dict], required_method: str = "generateContent") -> list[str]:
    def supports(m: dict) -> bool:
        methods = m.get("supportedGenerationMethods")
        return isinstance(methods, list) and required_method in methods

    supported: list[str] = []
    for m in models:
        name = m.get("name")
        if supports(m) and isinstance(name, str):
            supported.append(name)

    if not supported:
        return []

    def is_text_model(name: str) -> bool:
        low = name.lower()
        banned = ("tts", "image", "robotics", "deep-research", "lyria")
        return not any(b in low for b in banned)

    supported_text = [n for n in supported if is_text_model(n)] or supported

    preferred = [
        "models/gemini-2.0-flash",
        "models/gemini-flash-latest",
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash-lite",
        "models/gemini-flash-lite-latest",
        "models/gemini-pro-latest",
        "models/gemini-2.5-pro",
    ]

    ordered: list[str] = []
    for p in preferred:
        if p in supported_text and p not in ordered:
            ordered.append(p)
    for n in supported_text:
        if n not in ordered:
            ordered.append(n)
    return ordered


def _gemini_pick_retry_model(*, models: list[dict], current: str) -> str | None:
    def supports(m: dict) -> bool:
        methods = m.get("supportedGenerationMethods")
        return isinstance(methods, list) and "generateContent" in methods

    supported: list[str] = []
    for m in models:
        name = m.get("name")
        if supports(m) and isinstance(name, str):
            supported.append(name)

    if not supported:
        return None

    def ok(name: str) -> bool:
        low = name.lower()
        banned = ("tts", "image", "robotics", "deep-research", "lyria")
        return name != current and not any(b in low for b in banned)

    supported = [n for n in supported if ok(n)]
    if not supported:
        return None

    preferred = [
        "models/gemini-2.0-flash",
        "models/gemini-flash-latest",
        "models/gemini-2.5-flash",
        "models/gemini-pro-latest",
        "models/gemini-2.5-pro",
    ]
    for p in preferred:
        if p in supported:
            return p

    for kw in ("flash", "pro"):
        for name in supported:
            if kw in name.lower():
                return name

    return supported[0]


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
    # Alguns modelos retornam o conteúdo em múltiplos parts.
    return "\n".join(texts)


def _extrair_json_da_resposta(text: str) -> str | None:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1].strip()

    return None


def _parse_json_obj(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        extracted = _extrair_json_da_resposta(text)
        if not extracted:
            return None
        try:
            obj = json.loads(extracted)
        except json.JSONDecodeError:
            return None

    return obj if isinstance(obj, dict) else None


def _post_generate_content(
    url: str, *, api_key: str, payload: dict, timeout_s: float
) -> requests.Response:
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


def _build_payload(
    *,
    prompt: str,
    max_output_tokens: int,
    temperature: float,
    force_json: bool,
    tools: list[dict] | None,
    tool_config: dict | None,
) -> dict:
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


def _select_model(*, base_url: str, api_key: str, requested_model: str, timeout_s: float) -> str:
    requested_model = requested_model.strip()

    if requested_model.lower() == "auto":
        models = _gemini_list_models(base_url=base_url, api_key=api_key, timeout_s=timeout_s)
        picked = _gemini_pick_best_model(models=models)
        if not picked:
            raise GeminiError(
                "Não foi possível selecionar um modelo automaticamente (nenhum modelo com generateContent)."
            )
        return picked

    if requested_model.startswith("models/"):
        requested_model = requested_model.removeprefix("models/")
    return f"models/{requested_model}"


def generate_json(
    *,
    prompt: str,
    opts: GeminiOptions,
    max_output_tokens: int = 512,
    temperature: float = 0.0,
    force_json: bool = True,
    strict_json: bool = False,
    tools: list[dict] | None = None,
    tool_config: dict | None = None,
) -> dict[str, Any]:
    """Chama o Gemini e retorna um objeto JSON (dict).

    - Tenta priorizar function calling (args) quando disponível.
    - Caso contrário, tenta parsear JSON a partir do texto retornado.
    - Faz no máximo 1 retry com outro modelo se a resposta vier não-parseável.

    strict_json=True:
    - Não tenta “extrair” JSON de texto (sem fences/regex/substring).
    - A resposta precisa ser JSON puro (parseável via json.loads).
    """

    api_key = _get_api_key(opts)
    base_url = opts.base_url.rstrip("/")

    models_cache: list[dict] | None = None

    def get_models() -> list[dict]:
        nonlocal models_cache
        if models_cache is None:
            models_cache = _gemini_list_models(base_url=base_url, api_key=api_key, timeout_s=opts.timeout_s)
        return models_cache

    model_name = _select_model(
        base_url=base_url,
        api_key=api_key,
        requested_model=opts.model,
        timeout_s=opts.timeout_s,
    )

    def try_call(model: str, *, allow_force_json: bool, allow_tools: bool) -> dict[str, Any]:
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

        # Alguns modelos rejeitam responseMimeType=application/json quando function calling é forçado (ANY).
        # Ex.: "Forced function calling (ANY mode) with a response mime type ... is unsupported".
        if (
            resp.status_code == 400
            and allow_tools
            and (force_json and allow_force_json)
            and "Forced function calling" in resp.text
            and "response mime type" in resp.text
        ):
            return try_call(model, allow_force_json=False, allow_tools=allow_tools)

        # Alguns modelos não aceitam responseMimeType
        if resp.status_code == 400 and (force_json and allow_force_json) and "responseMimeType" in resp.text:
            return try_call(model, allow_force_json=False, allow_tools=allow_tools)

        # Alguns modelos não aceitam tools/toolConfig
        if resp.status_code == 400 and allow_tools and any(
            k in resp.text for k in ("toolConfig", "functionDeclarations", "tools")
        ):
            return try_call(model, allow_force_json=allow_force_json, allow_tools=False)

        # Modelo não encontrado / método não suportado
        if resp.status_code == 404:
            models = _gemini_list_models(base_url=base_url, api_key=api_key, timeout_s=opts.timeout_s)

            resolved = None
            if opts.model.strip().lower() != "auto":
                resolved = _gemini_resolver_model_name(requested=opts.model, models=models)
            if not resolved:
                resolved = _gemini_pick_best_model(models=models)

            if resolved and resolved != model:
                return try_call(resolved, allow_force_json=allow_force_json, allow_tools=allow_tools)

        if resp.status_code != 200:
            raise GeminiError(
                f"Falha ao chamar Gemini (modelo {model}, HTTP {resp.status_code}): {resp.text[:500]}"
            )

        data = resp.json()

        args = _extract_function_call_args(data)
        if isinstance(args, dict):
            return args

        text = _extract_text(data)
        if not isinstance(text, str):
            raise GeminiError(f"Gemini não retornou texto/args parseáveis (modelo {model}).")

        if strict_json:
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                obj = None
            parsed = obj if isinstance(obj, dict) else None
        else:
            parsed = _parse_json_obj(text)

        if parsed is None:
            raise GeminiError(f"Gemini respondeu, mas não retornou JSON parseável (modelo {model}).")

        return parsed

    # Tentativas: modelo principal + até 2 alternativas (trocando modelo ajuda em 503/alta demanda).
    candidates: list[str] = [model_name]
    try:
        ranked = _gemini_rank_text_models(models=get_models())
        for m in ranked:
            if m not in candidates:
                candidates.append(m)
    except GeminiError:
        # Se falhar ao listar modelos, ainda tentamos o modelo principal.
        pass

    # Limite para evitar explosão de chamadas.
    candidates = candidates[:3]

    last_exc: Exception | None = None
    attempted: list[str] = []
    for m in candidates:
        try:
            attempted.append(m)
            return try_call(m, allow_force_json=True, allow_tools=True)
        except GeminiError as exc:
            last_exc = exc
            continue

    # Fallback final: se conseguimos listar, tenta escolher um único retry_model “clássico”.
    try:
        retry_model = _gemini_pick_retry_model(models=get_models(), current=model_name)
    except GeminiError:
        retry_model = None
    if retry_model and retry_model not in candidates:
        try:
            attempted.append(retry_model)
            return try_call(retry_model, allow_force_json=True, allow_tools=True)
        except GeminiError as exc:
            last_exc = exc

    assert last_exc is not None
    # Erro final com contexto de tentativas/modelos.
    raise GeminiError(
        "Falha ao chamar Gemini após tentativas com modelos: "
        + ", ".join(attempted)
        + ". Último erro: "
        + str(last_exc)
    ) from last_exc
