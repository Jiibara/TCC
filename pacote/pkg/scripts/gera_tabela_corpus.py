#!/usr/bin/env python3
"""Gera tabela_corpus.tex (frases por intenção) a partir de data/nlu.yml do Rasa e avisa
sobre frases repetidas entre intenções.   Uso:  python gera_tabela_corpus.py data/nlu.yml > tabela_corpus.tex"""
import sys, re, unicodedata, yaml
def norm(s):
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", "", s).strip()
def tex(s): return s.replace("_", "\\_\\allowbreak{}")
dados = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
por, vistos, dup = {}, {}, []
for it in dados.get("nlu", []):
    if "intent" not in it: continue
    fr = [l.strip()[2:].strip() for l in it["examples"].splitlines() if l.strip().startswith("- ")]
    por.setdefault(it["intent"], []).extend(fr)
    for f in fr:
        k = norm(f)
        if k in vistos and vistos[k] != it["intent"]: dup.append((f, vistos[k], it["intent"]))
        vistos[k] = it["intent"]
total = sum(map(len, por.values()))
print("% Gerado por gera_tabela_corpus.py")
print("\\begin{table}[H]\n\\centering\n\\caption{Distribuição das frases do corpus por intenção}\n\\label{tab:distribuicao-corpus}\n\\small")
print("\\begin{tabular}{p{6.2cm}p{2.5cm}p{2.5cm}}\n\\hline\n\\textbf{Intenção} & \\textbf{Frases} & \\textbf{\\%} \\\\\n\\hline")
for n, fr in sorted(por.items()):
    print(f"\\texttt{{{tex(n)}}} & {len(fr)} & {str(round(100*len(fr)/total,1)).replace('.',',')}\\% \\\\")
print(f"\\hline\n\\textbf{{Total}} & \\textbf{{{total}}} & 100\\% \\\\\n\\hline\n\\end{{tabular}}\n\\vspace{{0.3cm}}\n\n\\footnotesize{{Fonte: Elaborado pelo autor, 2026.}}\n\\end{{table}}")
print(f"% {len(por)} intenções, {total} frases (menor: {min(map(len,por.values()))}, maior: {max(map(len,por.values()))}); repetidas entre intenções: {len(dup)}", file=sys.stderr)
for f, a, b in dup: print(f"% repetida: '{f}' ({b}) ja existe em '{a}'", file=sys.stderr)
