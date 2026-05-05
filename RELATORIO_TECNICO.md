# Relatório Técnico — Case Bridge

## 1. Visão Geral e Objetivos

O projeto foi desenvolvido para solucionar um problema crítico de fragmentação de dados em uma rede de postos de combustível. O desafio central não era apenas técnico, mas de negócio: como transformar relatos informais e tabelas inconsistentes em uma visão gerencial unificada para tomada de decisão.

A solução proposta utiliza um **pipeline de dados automatizado** que integra:
- Web Scraping (RPA)
- Engenharia de Dados
- Inteligência Artificial Generativa

---

## 2. Decisões de Arquitetura e Engenharia

### 2.1 Escolha da Interface CLI (Command Line Interface)

Optou-se por construir uma ferramenta de linha de comando (CLI) modular em Python, em vez de scripts isolados ou notebooks. As justificativas para essa escolha incluem:

- **Reprodutibilidade**: Garante que o pipeline possa ser executado em diferentes ambientes com os mesmos resultados.
- **Escalabilidade**: A estrutura modular permite que novas etapas (como exportação para BI) sejam adicionadas sem afetar os módulos de coleta ou IA.
- **Orquestração**: Um único ponto de entrada (`python -m case_bridge`) permite rodar o processo de ponta a ponta, simulando um ambiente real de produção.

### 2.2 Coleta de Dados via RPA (Etapa 1)

Para a extração dos preços de referência, foi utilizada a biblioteca `BeautifulSoup` em conjunto com `requests`.

- **Decisão Técnica**: Priorizou-se o acesso direto ao HTML via requisições HTTP, evitando o uso de ferramentas de automação de navegador (como Selenium), que são mais pesadas e lentas para tabelas estáticas.
- **Confiabilidade**: O script extrai de forma determinística os valores de Gasolina Comum, Etanol e Diesel S10, criando a base para o cálculo de volume de vendas.

### 2.3 Normalização Inteligente de Produtos (Etapa 2)

A normalização foi desenhada em camadas para otimizar custo e performance:

- **Mapeamento Base**: Um dicionário estático resolve 90% das variações comuns (ex: "GC" para "Gasolina Comum").
- **Aprendizado Persistente**: Mapeamentos já identificados são salvos em um JSON local, evitando reprocessamento.
- **Fallback de IA**: Em casos de novos nomes desconhecidos, o sistema consulta o Gemini para inferir o produto canônico, garantindo que o pipeline nunca trave.

---

## 3. Estratégia de Inteligência Artificial (Etapa 3.3)

O uso do modelo **Gemini 1.5 Flash** foi central para a sumarização dos e-mails dos gerentes. Durante o desenvolvimento, enfrentamos desafios técnicos que moldaram a solução final:

### 3.1 Superação de Limitações de API

- **Gestão de Erros 429 (Too Many Requests)**: Implementou-se uma lógica de seleção de modelo e intervalos de segurança para respeitar os limites da camada gratuita do Google AI Studio.
- **Ajuste de Tokens e JSON**: Identificou-se que respostas complexas poderiam ser cortadas prematuramente (`finishReason: MAX_TOKENS`). A solução foi ampliar o `max_output_tokens` para 1024+ e refinar o prompt para exigir estritamente o formato JSON RFC 8259, garantindo que a saída fosse sempre parseável pelo sistema.

### 3.2 Engenharia de Prompt e Estruturação

O prompt foi configurado para atuar como um analista de operações, extraindo:

- **Fatos**: Resumo e destaques operacionais.
- **Insights**: Sentimento geral e alertas (como falta de suprimentos ou manutenções).

Isso transforma um texto subjetivo em uma linha de dados quantitativa, permitindo filtrar postos com problemas em segundos.

---

## 4. Senso de Produto e Valor de Negócio

A solução não entrega apenas arquivos; ela entrega **inteligência competitiva**.

### 4.1 Ranking de Desempenho

O ranking gerado por filial e produto (exibindo faturamento e volume estimado) permite à sede identificar imediatamente:

- **Oportunidades**: Quais filiais estão performando acima da média de mercado.
- **Anomalias**: Por que o Posto São João (F003) teve queda em gasolina enquanto o Posto Litoral Norte (F001) teve recorde? O cruzamento com os e-mails revela que fatores externos (turismo vs. feriado local) foram os causadores.

### 4.2 Alertas Proativos

Ao consolidar os alertas gerados pela IA no arquivo `resumo_gerentes_<mes><ano>.csv`, a gestão central ganha uma "torre de controle". Exemplos reais capturados pelo sistema:

- **Logística**: Atraso de fornecedor no Ipiranga Express (F002).
- **Manutenção**: Bomba fora de operação no Posto Bandeirantes (F005).

---

## 5. Limitações e Futuro

### 5.1 Limitações Atuais

- **Dependência de Internet**: O RPA e a IA exigem conexão estável.
- **Single-Month Context**: O sistema atual foca em um período isolado, não realizando comparações históricas automáticas.
- **Ausência de suíte de testes formal**: Há smoke tests manuais, mas não há testes automatizados em CI.

### 5.2 Próximos Passos (Roadmap)

- **Dashboard Visual**: Integração da tabela consolidada com ferramentas como Streamlit ou Power BI para visualização geográfica do desempenho.
- **Monitoramento de Estoque**: Cruzar os e-mails de "abastecimento reduzido" com dados reais de tanques para automatizar pedidos de compra.
- **Suíte de Testes**: Implementação de testes unitários para garantir que a lógica de cálculo de volume permaneça correta após atualizações de código.

---

## Conclusão

O **Case Bridge** demonstrou que a automação, quando aliada a uma estratégia clara de tratamento de dados e uso consciente de IA, elimina tarefas manuais exaustivas e fornece clareza estratégica para a gestão da rede de combustíveis.

A solução é simples de rodar, fácil de auditar e pronta para evoluir conforme as necessidades do negócio crescerem.