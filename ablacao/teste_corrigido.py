"""Estatisticas da validacao cruzada repetida (ablacao do BERTimbau).

Le ablacao/resultados.csv (linhas 'cv': 3 repeticoes x 5 particoes, as mesmas
particoes para as duas configuracoes) e imprime:
  - media e desvio-padrao (amostral) de acuracia e F1 macro por configuracao;
  - teste de Wilcoxon pareado (o mesmo do texto, para conferencia);
  - teste t corrigido de Nadeau e Bengio (2003) para validacao cruzada repetida:
        t = media(d) / sqrt( (1/(k*r) + n_teste/n_treino) * var(d) ),  gl = k*r - 1
    A correcao n_teste/n_treino compensa o fato de as particoes compartilharem
    dados de treino (as diferencas nao sao independentes).

Uso (venv ativo, na raiz do projeto):  python ablacao\\teste_corrigido.py
"""
import csv
import math
import statistics
from pathlib import Path

from scipy import stats

ARQ = Path(__file__).resolve().parent / "resultados.csv"
TOTAL_FRASES = 703

linhas = [l for l in csv.DictReader(ARQ.open(encoding="utf-8")) if l["avaliacao"] == "cv"]
chave = lambda l: (int(l["repeticao"]), int(l["fold"]))
com = {chave(l): l for l in linhas if l["config"] == "com_bertimbau"}
sem = {chave(l): l for l in linhas if l["config"] == "sem_bertimbau"}
pares = sorted(com.keys() & sem.keys())
k = len({f for _, f in pares})
r = len({rep for rep, _ in pares})
n_teste = statistics.mean(int(com[p]["n_teste"]) for p in pares)
n_treino = TOTAL_FRASES - n_teste
print(f"{len(pares)} particoes pareadas (k={k}, r={r}); n_teste medio={n_teste:.1f}, n_treino={n_treino:.1f}\n")

for met in ("acuracia", "f1_macro"):
    a = [float(com[p][met]) for p in pares]
    b = [float(sem[p][met]) for p in pares]
    d = [x - y for x, y in zip(a, b)]
    print(f"== {met}")
    print(f"  com BERTimbau: media {statistics.mean(a):.4f}  DP {statistics.stdev(a):.4f}  "
          f"min {min(a):.4f}  max {max(a):.4f}")
    print(f"  sem BERTimbau: media {statistics.mean(b):.4f}  DP {statistics.stdev(b):.4f}")
    # IC 95% da media com a mesma correcao de variancia (Nadeau e Bengio)
    fator_ic = 1 / (k * r) + n_teste / n_treino
    meia = stats.t.ppf(0.975, k * r - 1) * math.sqrt(fator_ic * statistics.variance(a))
    print(f"  IC 95% corrigido da media (com BERTimbau): {statistics.mean(a) - meia:.4f} a "
          f"{statistics.mean(a) + meia:.4f}")
    md, vd = statistics.mean(d), statistics.variance(d)
    print(f"  diferenca:     media {md:.4f}  DP {math.sqrt(vd):.4f}  "
          f"(com > sem em {sum(x > 0 for x in d)} de {len(d)}; empates {sum(x == 0 for x in d)})")
    w = stats.wilcoxon(a, b)
    print(f"  Wilcoxon pareado: W={w.statistic:.1f}  p={w.pvalue:.4f}")
    t_ing = md / math.sqrt(vd / len(d))
    p_ing = 2 * stats.t.sf(abs(t_ing), len(d) - 1)
    print(f"  t pareado sem correcao: t={t_ing:.3f}  p={p_ing:.4f}")
    fator = 1 / (k * r) + n_teste / n_treino
    t_cor = md / math.sqrt(fator * vd)
    p_cor = 2 * stats.t.sf(abs(t_cor), k * r - 1)
    print(f"  t corrigido (Nadeau e Bengio): t={t_cor:.3f}  gl={k * r - 1}  p={p_cor:.4f}\n")
