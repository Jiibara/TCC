"""Calcula a pontuacao SUS (Brooke, 1996) a partir da planilha do Google Forms.

Itens impares: resposta - 1; itens pares: 5 - resposta; soma x 2,5 (0 a 100).
Ignora quem nao concordou em participar. IC 95% da media pela distribuicao t de Student.

Uso: python resultados\\calcula_sus.py [arquivo.xlsx]
"""
import math
import statistics
import sys
from pathlib import Path

import openpyxl

ARQ = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("sus_respostas.xlsx")
# valores criticos t (bicaudal, 95%) para gl = 1..30
T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160,
       2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056,
       2.052, 2.048, 2.045, 2.042]

ws = openpyxl.load_workbook(ARQ, read_only=True).worksheets[0]
linhas = list(ws.iter_rows(values_only=True))
notas = []
for n, l in enumerate(linhas[1:], start=1):
    if not l or str(l[1]).strip().lower().startswith("não") or l[2] is None:
        print(f"resposta {n}: sem consentimento ou incompleta, ignorada")
        continue
    r = [int(float(v)) for v in l[2:12]]
    soma = sum((x - 1) if i % 2 == 0 else (5 - x) for i, x in enumerate(r))
    nota = soma * 2.5
    notas.append(nota)
    print(f"participante {len(notas)} ({l[0]}): respostas {r} -> SUS {nota:.1f}")

k = len(notas)
media = statistics.mean(notas)
dp = statistics.stdev(notas) if k > 1 else 0.0
t = T95[k - 2] if 1 < k <= 31 else 1.96
margem = t * dp / math.sqrt(k)
print(f"\nn = {k} | média = {media:.1f} | mediana = {statistics.median(notas):.1f} | "
      f"desvio-padrão = {dp:.1f} | mín. = {min(notas):.1f} | máx. = {max(notas):.1f}")
print(f"IC 95% da média (t, gl = {k - 1}, t = {t}): {media - margem:.1f} a {min(media + margem, 100):.1f}"
      f"  (limite superior bruto {media + margem:.1f})")
print(f"Participantes com SUS >= 68: {sum(x >= 68 for x in notas)} de {k}")
# media por item (respostas brutas 1 a 5)
print("\nMédia das respostas por item:")
for i in range(10):
    vals = [int(float(l[2 + i])) for l in linhas[1:] if l and l[2] is not None and not str(l[1]).lower().startswith("não")]
    print(f"  item {i + 1:2d}: {statistics.mean(vals):.1f}")
