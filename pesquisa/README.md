# Pesquisa — o que funciona de verdade (e o que não)

Scripts usados para decidir a estratégia do robô com **18 anos de dados** (2008–2026), custos incluídos e
**treino (2008-2019) separado de teste cego (2020-2026)**. Nenhum parâmetro foi escolhido olhando o teste cego.

```bash
export TDK=<sua chave Twelve Data>      # plano grátis serve (8 req/min — o script respeita)
export REPO=..                           # raiz do projeto
python baixar_historico.py '[["XAU/USD","1day",1],["BTC/USD","1day",1],["EUR/USD","1day",1]]'
python pesq_diaria.py                    # Didi, Donchian, SMA, TSMOM em 14 ativos (treino x teste cego)
python pesq_tendencia_discreta.py        # a regra da Estrategia T (discreta, stop movel)
python bt_tendencia_producao.py 1.0      # backtest com o CODIGO DE PRODUCAO (argumento = multiplicador de custo)
python replay_caminho_ao_vivo.py XAU/USD 600   # repete 600 dias pelo caminho do robo ao vivo e compara com o backtest
python didi_1h.py                        # Didi de producao em 1h (2 anos)
```

## Conclusões (out/2026)
* **Didi (agulhada) em barras diárias, 14 ativos, 18 anos:** Sharpe negativo no treino (−0,17 a −0,32), ~0 no teste cego.
  Sem vantagem estatística. Em FX nenhum sistema técnico testado paga o custo (Sharpe < 0 nos dois períodos).
* **Tendência diária em ouro + BTC + ETH:** positiva no treino **e** no teste cego, nos 3 ativos, estável a qualquer
  variação de parâmetro (stop, limiar de entrada). Vira a **Estratégia T** do robô.
* Custo 4x maior mal muda o resultado (horizonte diário). **Não modelado: financiamento overnight (swap/funding)** —
  pode comer parte relevante do ganho do ouro.
* Cripto só tem dados desde 2017/18 (período de alta forte): o teste cego é menos "independente" do que parece.
