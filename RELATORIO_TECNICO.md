# Case Bridge — Relatório técnico (decisões, limitações e próximos passos)

## 1) Como pensei a solução

A solução foi desenhada como um **pipeline de dados determinístico**, com etapas pequenas e verificáveis, priorizando:

- **Reprodutibilidade**: o mesmo input gera o mesmo output (principalmente nas etapas sem IA).
- **Separação de responsabilidades**: cada módulo faz uma coisa bem definida.
- **Execução simples**: um único ponto de entrada (CLI) para rodar as etapas com dados do case ou dados externos.
- **Evolução incremental**: cada feature nova entra como um comando/etapa isolada (evitando “scripts soltos”).

A partir do problema do case, que mistura coleta (RPA), transformação (normalização/consolidação) e sumarização (IA), o desenho escolhido foi:

1. **Entrada de dados versionada** em `data/case/` (vendas e e-mails), para termos um “contrato” estável.
2. **Saídas geradas** em `out/` (ignorado pelo git), para não poluir o repositório com artefatos.
3. **CLI** como orquestrador das etapas, garantindo uma experiência consistente (argumentos e modo interativo).

### Por que CLI (e não notebooks/scripts)
A decisão por CLI (ao invés de scripts isolados ou notebook) vem do objetivo de entrega:

- Facilita rodar o projeto em outra máquina com o mínimo de atrito.
- Ajuda a demonstrar “produto” e não apenas “código”.
- Permite tanto automação (por argumentos) quanto uso guiado (menu interativo).

### Dados do case vs. dados externos
O projeto assume por padrão os dados do case em `data/case`, mas também permite o usuário informar caminhos de arquivos/diretórios no modo interativo. A motivação foi:

- Manter o case **fácil de avaliar** (rodar direto sem configurar nada).
- Permitir **reuso** da solução em dados novos (sem alterar código).

## 2) Decisões técnicas relevantes

### 2.1 Arquitetura modular por feature
A base do projeto foi dividida por domínios (por exemplo: preços/RPA, vendas, e-mails, IA, ranking de faturamento). Isso evita acoplamento e reduz o risco de mudanças em uma parte quebrarem outras.

A CLI (`python -m case_bridge`) funciona como **camada fina**: ela faz parsing de argumentos, valida caminhos/inputs e chama funções de domínio.

### 2.2 Consolidação de vendas como “fonte única”
A consolidação transforma múltiplos CSVs em uma tabela única com colunas úteis para relatórios:

- Normaliza o nome do produto.
- Garante tipagem numérica.
- Calcula `volume_estimado_litros` com base no preço de referência.

Isso cria uma base consistente para relatórios posteriores (ranking por filial/produto).

### 2.3 Normalização de produtos com camadas (e controle de custo)
O normalizador foi pensado para ser **robusto** e **barato**:

- Regras locais e um mapa persistente resolvem a maior parte dos casos.
- IA só é usada quando habilitada e quando necessário.

Essa estratégia evita gastar tokens em situações previsíveis e diminui instabilidade do pipeline.

### 2.4 IA (Gemini) como componente opcional e “fail-fast”
O maior risco do projeto é a variabilidade de retorno da IA. Por isso, o caminho escolhido foi:

- **JSON estrito**: o pipeline de e-mails exige resposta em JSON puro e valida chaves/formatos.
- **Sem heurísticas de extração**: o sistema não tenta “consertar” respostas que vêm com texto extra ou JSON parcial.
- **Falha explícita**: se a IA não cumprir o contrato (JSON válido), a etapa falha com erro claro.

Essa decisão prioriza previsibilidade e evita que a entrega gere outputs “meio certos” que mascaram problemas.

### 2.5 Nomes de arquivos finais inferidos do período
Os entregáveis finais foram padronizados para conter o período no nome (ex.: `marco2025`), inferindo mês/ano a partir da coluna `data` das vendas consolidadas. Isso elimina hardcode e garante:

- Menos erro manual.
- Saídas com contexto (“de que mês é esse arquivo?”).

### 2.6 Ranking de faturamento em CSV
A escolha de CSV para ranking por filial e por produto é proposital:

- É fácil validar em Excel/Google Sheets.
- É simples de automatizar e versionar a regra de cálculo.
- Evita dependência de BI/ferramentas externas.

## 3) Limitações conhecidas

- **Dependência externa da IA**: instabilidade, limites de cota e respostas que fogem do esperado podem interromper a etapa de e-mails.
- **Dados com múltiplos períodos**: a inferência do nome do mês assume que o dataset representa um único mês/ano. Se vier mais de um mês, hoje a etapa falha (por segurança).
- **Ausência de suíte de testes formal**: há smoke tests manuais/por comando, mas não há ainda testes automatizados (unitários/integration) rodando em CI.
- **UX do modo interativo**: o sub-menu de inputs cobre o principal (dados do case vs caminhos), mas pode ser expandido (ex.: validação mais amigável, re-seleção sem reiniciar).

## 4) Próximos passos (recomendados para evoluir a entrega)

1. **Testes automatizados**
   - Testes unitários para consolidação, inferência de período e ranking.
   - Testes de contrato para validar o schema do JSON de e-mails.

2. **Observabilidade e diagnóstico**
   - Logs com nível (INFO/WARN/ERROR) e contexto (arquivo/filial/produto).
   - Captura opcional de “payload/response” da IA em modo debug (sem vazar chave).

3. **Resiliência controlada na IA (sem perder o fail-fast)**
   - Retentativas apenas para falhas transitórias (HTTP 503/timeouts), sem fallback para parsing heurístico.
   - Estratégia de seleção de modelo configurável (e explícita para o usuário).

4. **Configuração por arquivo (opcional)**
   - Suportar um `config.json`/`config.yaml` para evitar reconfigurar caminhos em toda execução.

5. **Relatórios adicionais**
   - Ranking por filial **e produto** (matriz/pivot) ou Top-N por filial.
   - Métricas complementares: ticket médio estimado, participação (%) e evolução ao longo do mês.

---

**Resumo**: o Case Bridge foi estruturado para ser simples de rodar e fácil de auditar. A parte determinística (RPA + consolidação + ranking) é estável; a parte de IA é deliberadamente estrita e falha quando o contrato não é cumprido, para preservar a qualidade dos entregáveis.
