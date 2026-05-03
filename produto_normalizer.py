from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import requests


class ConsolidacaoError(RuntimeError):
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

    # Esperado: {"variacao_slug": "Canonico"}
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
    # Pode ser um nome de modelo (ex.: gemini-1.5-flash, gemini-2.0-flash)
    # ou "auto" (recomendado) para escolher um modelo compatível via ListModels.
    model: str = "auto"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_s: float = 20.0


def _gemini_list_models(*, base_url: str, api_key: str, timeout_s: float) -> list[dict]:
    url = base_url.rstrip("/") + "/models"
    try:
        resp = requests.get(url, params={"key": api_key}, timeout=timeout_s)
    except requests.RequestException as exc:
        raise ConsolidacaoError("Falha ao listar modelos do Gemini (rede/timeout).") from exc

    if resp.status_code != 200:
        raise ConsolidacaoError(
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
    # models vêm como {"name": "models/...", "supportedGenerationMethods": [...]}
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

    # Preferir "-latest" quando existir
    for c in candidates:
        if c.endswith("-latest"):
            return c

    # Caso contrário, pega o primeiro
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
        # Evitar modelos de TTS/imagem e prévias específicas (não precisamos disso aqui)
        banned = (
            "tts",
            "image",
            "robotics",
            "deep-research",
            "lyria",
        )
        return not any(b in low for b in banned)

    supported_text = [n for n in supported if is_text_model(n)]
    if not supported_text:
        supported_text = supported

    # Preferência explícita (bons defaults para texto e custo/latência)
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

    # Fallback: flash > pro > qualquer
    for kw in ("flash", "pro"):
        for name in supported_text:
            if kw in name.lower():
                return name

    return supported_text[0]


def _gemini_pick_retry_model(*, models: list[dict], current: str) -> str | None:
    """Escolhe UM modelo alternativo para nova tentativa.

    Mantém o número de chamadas baixo para não estourar quota.
    """

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


def criar_mapa_base_slug() -> dict[str, str]:
    # slug -> canônico
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


def _extrair_json_da_resposta(text: str) -> str | None:
    # 1) Bloco Markdown ```json ...```
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()

    # 2) Primeiro '{' até o último '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1].strip()

    return None


def _extrair_canonical_de_resposta(data: dict, canonicos: list[str]) -> str | None:
    """Extrai o canônico de uma resposta Gemini.

    Prioridade:
    1) Function calling (parts[].functionCall.args.canonical)
    2) Texto JSON puro (parts[].text)
    3) Texto com JSON embutido (markdown/bloco)
    """

    try:
        parts = data["candidates"][0]["content"]["parts"]
    except Exception:
        return None

    if not isinstance(parts, list):
        return None

    # 1) Function calling
    for p in parts:
        if not isinstance(p, dict):
            continue
        fc = p.get("functionCall")
        if not isinstance(fc, dict):
            continue
        args = fc.get("args")
        if isinstance(args, dict):
            canonical = args.get("canonical")
            if canonical in canonicos:
                return canonical

    # 2) Texto -> JSON
    text = None
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            text = p.get("text")
            break

    if not isinstance(text, str):
        return None

    # JSON puro
    try:
        parsed = json.loads(text)
        canonical = parsed.get("canonical") if isinstance(parsed, dict) else None
        return canonical if canonical in canonicos else None
    except json.JSONDecodeError:
        pass

    extracted = _extrair_json_da_resposta(text)
    if extracted:
        try:
            parsed = json.loads(extracted)
            canonical = parsed.get("canonical") if isinstance(parsed, dict) else None
            return canonical if canonical in canonicos else None
        except json.JSONDecodeError:
            return None

    return None


def _classificar_por_heuristica(produto_raw: str) -> str:
    """Heurística simples e determinística (último fallback).

    Usada somente quando a IA está indisponível ou não retorna saída parseável.
    """

    s = _slug(produto_raw)
    s_compact = s.replace(" ", "")

    if "etanol" in s:
        return "Etanol"

    # Diesel (inclui S10)
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
        "Responda apenas com o canônico (via function calling quando disponível) ou, alternativamente, com JSON VÁLIDO "
        "no formato exato: {\"canonical\": \"...\"}.\n"
        f"Canônicos permitidos: {canonicos}.\n"
        f"Produto: {produto_raw!r}.\n"
        "Escolha exatamente UM canônico."
    )

    base_url = ai.base_url.rstrip("/")
    requested_model = ai.model.strip()

    if requested_model.lower() == "auto":
        models = _gemini_list_models(base_url=base_url, api_key=api_key, timeout_s=ai.timeout_s)
        picked = _gemini_pick_best_model(models=models)
        if not picked:
            raise ConsolidacaoError(
                "Não foi possível selecionar um modelo automaticamente (nenhum modelo com generateContent)."
            )
        model_name = picked
    else:
        if requested_model.startswith("models/"):
            requested_model = requested_model.removeprefix("models/")
        model_name = f"models/{requested_model}"

    url = base_url + f"/{model_name}:generateContent"

    def build_payload(*, use_tools: bool, force_json: bool) -> dict:
        payload: dict = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 128,
            },
        }

        if force_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        if use_tools:
            payload["tools"] = [
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
            payload["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": ["set_canonical"],
                }
            }

        return payload

    def do_post(post_url: str, payload: dict) -> requests.Response:
        try:
            return requests.post(
                post_url,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=ai.timeout_s,
            )
        except requests.RequestException as exc:
            raise ConsolidacaoError("Falha ao chamar API do Gemini (rede/timeout).") from exc

    # 1ª tentativa: tools + JSON (mais determinístico)
    payload = build_payload(use_tools=True, force_json=True)
    resp = do_post(url, payload)

    # Alguns modelos/versões podem não aceitar responseMimeType; se der 400 por isso, tenta sem.
    if resp.status_code == 400 and "responseMimeType" in resp.text:
        payload = build_payload(use_tools=True, force_json=False)
        resp = do_post(url, payload)

    # Alguns modelos/versões podem não aceitar tools/toolConfig; se der 400, tenta sem tools.
    if resp.status_code == 400 and any(k in resp.text for k in ("toolConfig", "functionDeclarations", "tools")):
        payload = build_payload(use_tools=False, force_json=True)
        resp = do_post(url, payload)
        if resp.status_code == 400 and "responseMimeType" in resp.text:
            payload = build_payload(use_tools=False, force_json=False)
            resp = do_post(url, payload)

    # Se o modelo não existir/suportar o método, tentamos resolver automaticamente via ListModels.
    if resp.status_code == 404:
        models = _gemini_list_models(base_url=base_url, api_key=api_key, timeout_s=ai.timeout_s)
        resolved = None
        if requested_model.lower() != "auto":
            resolved = _gemini_resolver_model_name(requested=requested_model, models=models)

        # Se não resolveu pelo nome solicitado, escolhe automaticamente um modelo suportado
        if not resolved:
            resolved = _gemini_pick_best_model(models=models)

        if resolved and resolved != model_name:
            print(f"INFO: Gemini model resolvido automaticamente: {model_name} -> {resolved}")
            url2 = base_url + f"/{resolved}:generateContent"
            resp = do_post(url2, payload)

    if resp.status_code != 200:
        heur = _classificar_por_heuristica(produto_raw)
        print(
            "WARN: Falha ao chamar Gemini; usando heurística local (último fallback). "
            f"http={resp.status_code} produto={produto_raw!r} -> {heur}"
        )
        return heur

    data = resp.json()
    canonical = _extrair_canonical_de_resposta(data, canonicos)
    if canonical in canonicos:
        return canonical

    # Resposta 200 mas não foi parseável/estruturada no formato esperado.
    # Tenta UMA nova chamada com outro modelo antes de falhar (sem heurística local).
    retry_model = None
    try:
        models = _gemini_list_models(base_url=base_url, api_key=api_key, timeout_s=ai.timeout_s)
        retry_model = _gemini_pick_retry_model(models=models, current=model_name)
    except Exception:
        retry_model = None

    if retry_model:
        print(f"INFO: Resposta inválida; tentando novamente com outro modelo: {model_name} -> {retry_model}")
        url_retry = base_url + f"/{retry_model}:generateContent"
        resp2 = do_post(url_retry, payload)
        if resp2.status_code == 200:
            canonical2 = _extrair_canonical_de_resposta(resp2.json(), canonicos)
            if canonical2 in canonicos:
                return canonical2

    heur = _classificar_por_heuristica(produto_raw)
    print(
        "WARN: Gemini respondeu mas não retornou saída parseável; usando heurística local (último fallback). "
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
