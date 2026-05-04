# Case Bridge — CLI modular (Etapas 1, 2, 3.3 e 3.4)

Este repositório implementa o fluxo completo do case:

- **Etapa 1 (RPA):** extrai preços de referência e gera `precos_referencia.csv`.
- **Etapa 2 (Vendas):** consolida CSVs, normaliza produtos e calcula `volume_estimado_litros`.
- **Etapa 3.3 (E-mails):** resume e-mails com IA (Gemini) em JSON estruturado.
- **Etapa 3.4 (Entregáveis):** gera automaticamente os dois CSVs finais dentro de `out/`.

## Estrutura de dados

- Entradas do case ficam em:
  - `data/case/vendas/` (CSV)
  - `data/case/emails/` (TXT)
- Saídas geradas pelo projeto ficam em:
  - `out/` (ignorado pelo git)

## Requisitos

- Windows + PowerShell
- Python 3.11+
- Dependências do Python: ver `requirements.txt`
- Para resumir e-mails (Etapa 3.3/3.4): **Gemini API Key** via `GEMINI_API_KEY`

## Instalação

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Como rodar (modo menu interativo)

```powershell
\.venv\Scripts\python.exe -m case_bridge
```

## Como rodar (modo por argumentos)

### Etapa 1 — preços de referência

```powershell
\.venv\Scripts\python.exe -m case_bridge precos --out precos_referencia.csv
```

### Etapa 2 — consolidar vendas

Usa por padrão os arquivos em `data/case/vendas/`.

```powershell
\.venv\Scripts\python.exe -m case_bridge vendas --precos precos_referencia.csv
```

### Etapa 3.3 — resumir e-mails (requer Gemini)

Defina a variável de ambiente (somente no terminal atual):

```powershell
$env:GEMINI_API_KEY="SUA_KEY_AQUI"
```

E rode:

```powershell
\.venv\Scripts\python.exe -m case_bridge emails
```

### Etapa 3.4 — gerar entregáveis finais (requer Gemini)

Gera:

- `out/vendas_consolidadas_marco2025.csv`
- `out/resumo_gerentes_marco2025.csv`

```powershell
\.venv\Scripts\python.exe -m case_bridge entregaveis
```

Obs.: se `precos_referencia.csv` não existir, o comando `entregaveis` gera automaticamente via RPA.

## Usar arquivos externos (fora do projeto)

Você pode apontar para outros diretórios sem mudar nada no código:

```powershell
\.venv\Scripts\python.exe -m case_bridge vendas --vendas-dir "C:\caminho\para\vendas" --precos precos_referencia.csv
\.venv\Scripts\python.exe -m case_bridge emails --emails-dir "C:\caminho\para\emails"
```

## Normalização de produtos (dicionário + JSON persistente + IA + heurística)

O sistema normaliza o `produto` nesta ordem:

1. **Dicionário base** (regras locais)
2. **JSON persistente de mapeamento aprendido** (default: `out/mapeamento_produtos.json`)
3. **(Opcional) Gemini**, se houver `GEMINI_API_KEY`
4. **Heurística local (último fallback)** para manter execução automática

Canônicos:

- `Gasolina Comum`
- `Etanol`
- `Diesel S10`