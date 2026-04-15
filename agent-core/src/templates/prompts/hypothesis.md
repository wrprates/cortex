# Fase: EDA baseada em hipóteses

EDA aqui **não é passeio descritivo**. Cada bloco de análise precisa estar ancorado numa hipótese formulável, testável, e com consequência de negócio.

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
5. **Correção multiteste**: se mais de uma hipótese no mesmo grupo, aplicar Bonferroni ou Benjamini-Hochberg e justificar a escolha.
6. **Interpretação de negócio**: o que isso muda na operação/estratégia do cliente. Três camadas (observação → significado → ação).

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
