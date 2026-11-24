"""Compila os diagramas TikZ desta pasta e exporta PNG (300 dpi) para a pasta tcc,
com os nomes de arquivo usados pelo main.tex.

Uso (na pasta tcc\\figuras):  python gera_figuras.py [nome ...]
"""
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
TCC = AQUI.parent
SAIDA = {
    "modelo_logico": "fig_modelo_logico.png",
    "mer_conceitual": "MERConceitual.png",
    "sequencia_agendamento": "DiagramaAgendaCorrigida.png",
    "sequencia_cancelamento": "DiagragmaCancelaCorrigida.png",
    "atividade_agendamento": "AtividadeAgendamentoo.png",
    "atividade_cancelamento": "AtividadeCancelaa.png",
    "classes": "DiagramaClasses (2).png",
    "classificador_intencoes": "fig_classificador_intencoes.png",
    "ner_exemplo": "fig_ner_exemplo.png",
}

nomes = sys.argv[1:] or list(SAIDA)
for nome in nomes:
    tex = AQUI / f"{nome}.tex"
    r = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex.name],
                       cwd=AQUI, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        erro = [l for l in r.stdout.splitlines() if l.startswith("!") or l.startswith("l.")]
        print(f"ERRO em {nome}: {' | '.join(erro[:4])}")
        continue
    png = TCC / SAIDA[nome]
    subprocess.run(["rungs", "-q", "-dSAFER", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m",
                    "-r300", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                    f"-sOutputFile={png}", str(AQUI / f"{nome}.pdf")], check=True)
    print(f"ok: {nome} -> {png.name}")
