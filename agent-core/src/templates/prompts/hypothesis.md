# Fase: EDA baseada em hipóteses

EDA aqui **não é passeio descritivo**. Cada bloco de análise precisa estar ancorado numa hipótese formulável, testável, e com consequência de negócio.

## Formulação das hipóteses (liberdade caso-a-caso)

Você **propõe as hipóteses** com base no contexto do projeto e no que a fase de qualidade revelou. Não há template: olhe as colunas, os tipos, a pergunta de negócio original, o que chamou atenção no relatório de qualidade (outliers, segmentos desbalanceados, gaps temporais) e **formule 4-8 hipóteses que movam o negócio**.

Priorize hipóteses que:
- **Conectam variável preditora com resultado de negócio** (receita, conversão, churn, ticket médio, LTV, satisfação).
- **Comparam segmentos** que o negócio consegue acionar (canal de aquisição, região, faixa etária, tipo de produto).
- **Expõem riscos ou oportunidades operacionais** (ex: tempo de resposta × abandono, sazonalidade × estoque).

Evite hipóteses triviais ("será que preço alto vende menos?") ou sem alavanca ("será que existe correlação entre X e Y?" sem dizer pra quê).

## Estrutura obrigatória por hipótese

Cada hipótese segue este ciclo — documente tudo no `report.html`:

1. **Hipótese de negócio** em linguagem clara: *"Clientes que receberam cupom na primeira compra têm LTV maior que os que não receberam."*
2. **Tradução estatística**: H0, H1, estatística usada, pressupostos verificados.
3. **Teste apropriado**:
   - Médias: `t.test()` (paramétrico, se pressupostos válidos) ou `wilcox.test()` (não paramétrico).
   - Proporções: `prop.test()` ou `chisq.test()`.
   - Associação entre categóricas: `chisq.test()` com Cramér's V.
   - Correlação: `cor.test()` (Pearson/Spearman conforme distribuição).
   - ANOVA/Kruskal para comparação múltipla.
4. **Resultado com rigor**: p-value, intervalo de confiança 95%, **effect size** (Cohen's d, r, Cramér's V, η²). P-value sozinho não presta — sempre reporte magnitude.
   - **Rank-biserial r** (para Wilcoxon): `r = 1 - (2 * W) / (n1 * n2)` onde W é a estatística do teste e n1/n2 os tamanhos dos grupos. O valor DEVE estar entre -1 e +1. Se o cálculo der fora desse intervalo, a fórmula está errada — revise antes de prosseguir. Alternativamente use o pacote `broom` ou calcule via `2 * W / (n1 * n2) - 1`.
   - **Cramér's V**: use `sqrt(chisq / (n * (min(nrow, ncol) - 1)))`. Deve estar entre 0 e 1.
5. **Correção multiteste**: se mais de uma hipótese no mesmo grupo, aplicar Bonferroni ou Benjamini-Hochberg e justificar a escolha.
6. **Interpretação de negócio**: o que isso muda na operação/estratégia do cliente. Três camadas (observação → significado → ação). **REGRA CRÍTICA**: se `p_adj > α` (tipicamente 0.05), a conclusão DEVE ser "Sem evidência de associação/diferença" — nunca afirme associação quando o teste não rejeita H0. `business_implication` e `recommended_action` devem refletir a incerteza ("exploratório", "não confirmado", "necessita mais dados"), nunca apresentar como achado confirmado.

## Visualização obrigatória por tipo de hipótese

Toda hipótese precisa de **pelo menos um gráfico interativo** (`echarts4r`) que deixe o achado visível mesmo sem ler o texto. Use o gráfico certo pro tipo de teste:

- **Comparação de médias** (t-test, Wilcoxon): **boxplot** lado-a-lado por grupo (`e_boxplot`) ou density plot (`e_density`) sobrepostos. Mostre a diferença, não só o p-valor.
- **Comparação de proporções** (prop.test, chi-quadrado 2×2): **barras empilhadas com %** (`e_bar` + `stack`) ou mosaic. Anote N absoluto em cada barra.
- **Associação de categóricas** (chi-quadrado N×M): **heatmap** de resíduos padronizados ou mosaic plot — destaque as células com maior contribuição ao chi².
- **Correlação** (cor.test): **scatter** com linha de tendência (`e_scatter` + `e_lm`) e coeficiente no subtítulo. Se n > 5000, use `e_effect_scatter` ou amostra + faixa de densidade pra não virar mancha.
- **ANOVA/Kruskal**: boxplot por grupo + linha da média geral de referência.
- **Séries temporais**: `e_line` com marcadores de evento/breakpoint quando relevante.

Cada gráfico precisa de:
- **Título que conta a história** (ver `report_narrative.md`) — nunca "Distribuição de X".
- Legenda e eixos em pt-BR.
- N reportado em algum lugar (subtítulo, anotação, tooltip).

## Verificação de pressupostos

Antes de rodar teste paramétrico, verificar e declarar no report:
- Normalidade: `shapiro.test()` ou inspeção gráfica (Q-Q plot); se N > 5000, Shapiro não é confiável — usar visualização.
- Homocedasticidade: `car::leveneTest()` ou `bartlett.test()`.
- Independência: justificar via design amostral.

Se pressupostos violados → usar não-paramétrico. **Não simule normalidade. Não esconda violação.**

## O que NÃO fazer

- Rodar 30 correlações e reportar "as significativas" — pescaria estatística (p-hacking). Correção multiteste obrigatória.
- Testar hipóteses sem justificativa de negócio. Se você não consegue explicar *por que* alguém quereria saber isso, não teste.
- Reportar apenas p-value. Sempre: p, IC, effect size, N.
- Concluir causalidade a partir de associação. Diga "associado a", não "causa".

## Entregáveis

1. **`./outputs/summary.json`** com:
   ```json
   {
     "hypotheses": [{
       "id": str,
       "business_question": str,
       "H0": str, "H1": str,
       "test": str, "n": int,
       "statistic": float, "p_value": float,
       "ci_low": float, "ci_high": float,
       "effect_size": {"name": str, "value": float, "magnitude": "small|medium|large"},
       "assumptions_ok": bool, "assumption_notes": str,
       "conclusion": str, "business_implication": str, "recommended_action": str
     }],
     "multitest_correction": {"method": str, "n_tests": int}
   }
   ```
2. **`./outputs/report.html`** estruturado por hipótese — uma seção por hipótese, cada uma com narrativa completa + visualização de suporte (`echarts4r`).

## Decisão final obrigatória

No fim do report, uma seção **"Recomendações priorizadas"** com as 3-5 ações mais importantes derivadas das hipóteses confirmadas, ordenadas por impacto esperado e viabilidade.
