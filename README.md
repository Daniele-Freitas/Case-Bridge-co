# Case Bridge — CLI modular (Etapas 1, 2, 3.3 e 3.4)

Este repositório implementa o fluxo completo do case:

- **Etapa 1 (RPA):** extrai preços de referência e gera `precos_referencia.csv`.
- **Etapa 2 (Vendas):** consolida CSVs, normaliza produtos e calcula `volume_estimado_litros`.
- **Etapa 3.3 (E-mails):** resume e-mails com IA (Gemini) em JSON estruturado.
- **Etapa 3.4 (Entregáveis):** gera automaticamente os dois CSVs finais dentro de `out/`.
- **Ranking de faturamento:** gera tabelas (CSV) por filial e por produto.

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
- **Para resumir e-mails (Etapa 3.3/3.4):** API Key do Google Gemini (defina via variável de ambiente `GEMINI_API_KEY`)

## Como obter a Gemini API Key

1. Acesse [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Clique em "Create API Key"
3. Copie a chave gerada

## Como definir GEMINI_API_KEY

### Opção 1: Terminal (válido só para aquele terminal)

```powershell
$env:GEMINI_API_KEY="SUA_KEY_AQUI"
```

Depois rode o comando da CLI normalmente. A chave será perdida ao fechar o terminal.

### Opção 2: Arquivo `.env` (persistente)

Crie um arquivo `.env` na raiz do projeto:

```
GEMINI_API_KEY=SUA_KEY_AQUI
```

Depois use este script em PowerShell para carregar automaticamente:

```powershell
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([^=#]+)\s*=\s*(.+)$') {
            [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }
}
```

Depois rode o comando da CLI. A chave será carregada do `.env` **apenas naquele terminal**.

### Opção 3: Variável de ambiente global (Windows permanente)

Abra **Propriedades do Sistema** → **Variáveis de Ambiente** e crie:

- **Nome:** `GEMINI_API_KEY`
- **Valor:** `SUA_KEY_AQUI`

A chave será disponível em todos os terminais novos.

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

Ao iniciar, o modo interativo pergunta se você quer:

- usar os dados padrão do case (`data/case/`), ou
- informar caminhos de arquivos/diretórios para vendas e e-mails.

**Nota:** A CLI não pede nenhuma entrada durante a execução. Se uma etapa precisar da `GEMINI_API_KEY`, ela deve estar **já definida no terminal antes** de rodar o comando.

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

A chave `GEMINI_API_KEY` já deve estar definida (ver seção acima). Depois rode:

```powershell
\.venv\Scripts\python.exe -m case_bridge emails
```

### Etapa 3.4 — gerar entregáveis finais (requer Gemini)

Gera:

- `out/vendas_consolidadas_<mes><ano>.csv` (ex.: `vendas_consolidadas_marco2025.csv`)
- `out/resumo_gerentes_<mes><ano>.csv` (ex.: `resumo_gerentes_marco2025.csv`)

```powershell
\.venv\Scripts\python.exe -m case_bridge entregaveis
```

Obs.: se `precos_referencia.csv` não existir, o comando `entregaveis` gera automaticamente via RPA.

### Ranking de faturamento — por filial e por produto

Gera:

- `out/ranking_faturamento_por_filial.csv`
- `out/ranking_faturamento_por_produto.csv`

```powershell
\.venv\Scripts\python.exe -m case_bridge faturamento --precos precos_referencia.csv
```

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