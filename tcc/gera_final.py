"""Gera main_final.tex a partir de main.tex: versao final de entrega COMPLETA
(com apendices), sem a folha de aprovacao (preenchida so depois da banca) e
sem nenhum campo \\dado{} (vermelho).

O main.tex nao e alterado. Se sobrar algum \\dado{} fora da folha de
aprovacao, o script para com erro em vez de gerar o arquivo.

Uso (na pasta tcc):  python gera_final.py   e depois   latexmk -pdf main_final.tex
"""
import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
txt = (AQUI / "main.tex").read_text(encoding="utf-8")

# rascunho desligado: qualquer \dado restante interrompe a compilacao
antes = "\\newif\\ifrascunho\n\\rascunhotrue"
if txt.count(antes) != 1:
    sys.exit("ERRO: chave de rascunho nao encontrada")
txt = txt.replace(antes, "\\newif\\ifrascunho\n\\rascunhofalse")

# folha de aprovacao removida
ini = txt.find("% ==========================================\n% FOLHA DE APROVAÇÃO")
fim_marca = "\\rule{9cm}{0.4pt}\\\\ \\dado{Prof. membro da banca}\\\\ \\dado{instituição}\n\\end{center}\n"
fim = txt.find(fim_marca, ini)
if ini < 0 or fim < 0:
    sys.exit("ERRO: folha de aprovacao nao encontrada")
txt = txt[:ini] + txt[fim + len(fim_marca):]

# conferencia: nenhum \dado fora de comentario
resto = [m.start() for m in re.finditer(r"\\dado\{", txt)]
resto = [p for p in resto if not txt[txt.rfind("\n", 0, p) + 1:p].lstrip().startswith("%")]
if resto:
    sys.exit(f"ERRO: ainda ha {len(resto)} \\dado{{}} no texto, ex.: {txt[resto[0]:resto[0]+80]!r}")
if "\\appendix" not in txt:
    sys.exit("ERRO: apendices ausentes")

(AQUI / "main_final.tex").write_text(txt, encoding="utf-8")
print("main_final.tex gerado (com apendices, sem folha de aprovacao)")
