#!/usr/bin/env python3
"""Regenera grafico1_mercado_chatbots.png: valor(ano) = 9,30 x 1,2315^(ano-2025); 2031 = valor da fonte (32,45)."""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
anos = list(range(2025, 2032))
v = [round(9.30 * 1.2315 ** (a - 2025), 2) for a in anos]; v[-1] = 32.45
fig, ax = plt.subplots(figsize=(8, 4.2), dpi=200)
b = ax.bar([str(a) for a in anos], v, color="#4472c4")
for r, x in zip(b, v): ax.text(r.get_x() + r.get_width()/2, x + 0.5, f"{x:.2f}".replace(".", ","), ha="center", fontsize=9)
ax.set_ylabel("US$ bilhões"); ax.set_ylim(0, 38); ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout(); fig.savefig("grafico1_mercado_chatbots.png"); print(dict(zip(anos, v)))
