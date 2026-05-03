# Case Bridge — Etapa 1 (RPA) + Etapa 2 (Consolidação)

Este repositório implementa um fluxo em duas etapas:

1. **Etapa 1 (RPA):** extrai uma tabela de preços de referência e gera `precos_referencia.csv`.
2. **Etapa 2 (Consolidação):** consolida CSVs de vendas, normaliza produtos (3 canônicos) e calcula `volume_estimado_litros`.

## Requisitos

- Windows + PowerShell
- Python 3.11+
- (Opcional) Gemini API Key para melhorar a normalização de produtos desconhecidos

## Instalação

Crie e ative um ambiente virtual (recomendado):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Instale dependências:

```powershell
python -m pip install -r requirements.txt
```

## Etapa 1 — Gerar preços de referência

Roda o script de extração e gera `precos_referencia.csv`.

```powershell
.\.venv\Scripts\python.exe .\rpa_precos.py
```

Se preferir salvar em outro caminho, use o argumento `--out` (se existir no seu script).

## Etapa 2 — Consolidar vendas e calcular litros

### Entrada

- Um ou mais arquivos `vendas_*.csv` com colunas obrigatórias:
  - `data`
  - `produto`
  - `valor_total_brl`

### Saída

- Um CSV com nome único por execução:
  - `vendas_consolidadas_YYYYMMDD_HHMMSS.csv`

### Rodar consolidação

Exemplo com 1 arquivo (funciona com vários também):

```powershell
.\.venv\Scripts\python.exe .\consolidar_vendas.py .\vendas_F001_marco2025.csv --precos .\precos_referencia.csv --map-file .\mapeamento_produtos.json
```

### Smoke test (do zero, com um CSV mínimo)

Cria um arquivo de vendas pequeno, roda a Etapa 1 e a Etapa 2.

```powershell
# 1) Gerar preços de referência
.\.venv\Scripts\python.exe .\rpa_precos.py

# 2) Criar um CSV mínimo de vendas
@"
data,produto,valor_total_brl
2025-03-01,Gasolina Especial,100.00
"@ | Out-File -Encoding utf8 .\vendas_F001_marco2025.csv

# 3) (Opcional) setar a API key para habilitar IA automaticamente quando necessário
# $env:GEMINI_API_KEY="SUA_KEY_AQUI"

# 4) Consolidar (gera vendas_consolidadas_YYYYMMDD_HHMMSS.csv)
.\.venv\Scripts\python.exe .\consolidar_vendas.py .\vendas_F001_marco2025.csv --precos .\precos_referencia.csv --map-file .\mapeamento_produtos.json

# 5) Rodar de novo para validar reuso do cache no JSON
.\.venv\Scripts\python.exe .\consolidar_vendas.py .\vendas_F001_marco2025.csv --precos .\precos_referencia.csv --map-file .\mapeamento_produtos.json
```

## Normalização de produtos (dicionário + JSON persistente + IA + heurística)

O sistema tenta normalizar o `produto` nesta ordem:

1. **Dicionário base** (regras locais)
2. **Arquivo JSON de mapeamento aprendido** (ex.: `mapeamento_produtos.json`)
3. **Fallback IA (Gemini)**, se uma API key estiver configurada
4. **Heurística local (último recurso)** para evitar quebrar a execução

O “aprendizado” **não é só cache em memória**: o script salva no arquivo JSON (`--map-file`).
Se você reutilizar o mesmo `--map-file` nas próximas execuções, ele reaproveita esses mapeamentos.

### Canônicos

- `Gasolina Comum`
- `Etanol`
- `Diesel S10`

## Configurar Gemini API Key (opcional)

O projeto lê a key pela variável de ambiente `GEMINI_API_KEY`.

### Definir só para o terminal atual

```powershell
$env:GEMINI_API_KEY="SUA_KEY_AQUI"
```

Conferir:

```powershell
echo $env:GEMINI_API_KEY
```

### Definir de forma persistente (novos terminais)

```powershell
setx GEMINI_API_KEY "SUA_KEY_AQUI"
```

Depois, feche e reabra o terminal.

### Escolher modelo

Por padrão, o projeto usa `--ai-model auto` e tenta escolher um modelo compatível automaticamente.

Você pode fixar explicitamente um modelo, por exemplo:

```powershell
.\.venv\Scripts\python.exe .\consolidar_vendas.py .\vendas_F001_marco2025.csv --precos .\precos_referencia.csv --map-file .\mapeamento_produtos.json --ai-model gemini-flash-latest
```

## Troubleshooting

### Produto desconhecido

O script é 100% automático:

- Se houver `GEMINI_API_KEY`, ele tenta normalizar via IA e salva no JSON.
- Se não houver (ou se a IA falhar), ele usa uma heurística simples como **último recurso** e também salva no JSON.