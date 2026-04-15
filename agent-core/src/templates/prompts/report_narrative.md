# Regra de narrativa do relatório

O relatório HTML é **o entregável que o cliente de negócio vai ler**. Ele precisa conseguir abrir o arquivo, ler do começo ao fim sem conhecimento estatístico, e sair sabendo o que fazer na segunda-feira.

## Regra das 3 camadas

**Toda afirmação quantitativa** no report precisa estar embrulhada em uma passagem com três camadas, nesta ordem:

1. **Observação**: o que os dados mostraram, em pt-BR, sem jargão.
2. **Significado**: o que esse achado implica sobre o problema de negócio.
3. **Ação**: o que deveria ser feito com essa informação — decisão, hipótese a validar, intervenção, alerta.

### Exemplo correto

> A taxa de conversão cai de 3,2% para 0,8% quando o tempo médio de resposta do atendimento ultrapassa 30 minutos (n=12.400, IC95% [0,6%; 1,0%], p<0,001, d=0,42). Isso sugere que o SLA atual de 45 minutos está amplo demais para o público-alvo, que decide rápido. Recomenda-se testar um SLA de 20 minutos para o segmento de alto valor como próximo passo, acompanhado de dashboard de tempo real para o time de suporte.

### Exemplo proibido

> Tempo de resposta vs conversão: p=0.0001.
>
> | tempo | conversao |
> |-------|-----------|
> | <30   | 0.032     |
> | >=30  | 0.008     |

Esse padrão está banido. Tabela sem narrativa, p-value solto, zero ação.

## Estrutura mínima do relatório

Toda renderização Quarto precisa ter:

1. **Título + subtítulo** informativos, não genéricos.
2. **Resumo executivo** de 3-6 parágrafos que um diretor consegue ler em 2 minutos — ranking dos achados mais importantes, cada um com sua ação associada.
3. **Contexto do dataset e metodologia** — breve, uma seção.
4. **Seções analíticas** (qualidade, hipóteses, modelagem etc. conforme fase). Cada seção tem parágrafos narrativos + gráfico interativo de suporte + tabela detalhada apenas como apêndice se necessário.
5. **Recomendações priorizadas** — lista numerada, cada recomendação com: ação, justificativa em uma frase, impacto esperado.
6. **Ressalvas e limitações** — o que esses dados NÃO respondem, onde a análise é frágil, o que depende de dados externos.

## Tom

- Português do Brasil, formal mas não acadêmico.
- Sem "observa-se que", "constata-se", "é interessante notar". Vá direto.
- Números com casas decimais úteis (2-3), não 8.
- Sempre contexto: "R$ 1,2M" isolado é ruim; "R$ 1,2M, equivalente a 8% da receita trimestral" é útil.
- Evite superlativos vazios ("impressionante", "incrível"). Use números para a força do achado.

## Visualizações

- Use `echarts4r` com `e_tooltip()`, `e_legend()`, `e_theme("westeros")` para interatividade.
- Cada gráfico tem **título descritivo que já conta a história** — não "Distribuição de X", mas "X concentra-se entre 25 e 40, com cauda longa à direita".
- Legendas em pt-BR.
- Se um gráfico não agrega à narrativa, não inclua.
