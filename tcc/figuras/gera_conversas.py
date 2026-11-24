"""Gera as figuras de tela de conversa (secao "Prototipacao da interface") a partir das
conversas reais salvas em cenarios/conversas_interface.json (gera_conversas_interface.py).
As mensagens sao copiadas sem alteracao; so se escapam os caracteres especiais do LaTeX.
Cria conversa_*.tex nesta pasta; depois rode:  python gera_figuras.py conversa_cadastro conversa_agendamento conversa_remarcacao
"""
import json
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DADOS = json.loads((AQUI.parent.parent / "cenarios" / "conversas_interface.json").read_text(encoding="utf-8"))
F = DADOS["fluxos"]

PREAMBULO = r"""\documentclass[border=6pt]{standalone}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{varwidth}
\usepackage{tikz}
\usetikzlibrary{calc,backgrounds}
\renewcommand{\familydefault}{\sfdefault}
\tikzset{
  bot/.style={rectangle, rounded corners=6pt, fill=white, draw=black!25, inner sep=5pt, align=left},
  pac/.style={rectangle, rounded corners=6pt, fill=green!12, draw=green!45!black!40, inner sep=5pt, align=left},
}
\begin{document}
\small\hyphenpenalty=10000\exhyphenpenalty=10000
\begin{tikzpicture}
\def\W{12}
\node[anchor=north west, fill=blue!55!black, text=white, minimum width=\W cm, minimum height=0.9cm,
      inner sep=0pt, font=\bfseries] (topo) at (0,0) {\hspace{8pt}Clínica — assistente virtual (Telegram)};
\coordinate (dir) at ({\W-0.3},0);
\coordinate (esq) at (0.3,0);
"""
FIM = r"""\begin{scope}[on background layer]
  \fill[blue!4] (topo.north west) rectangle (topo.east |- fim);
\end{scope}
\end{tikzpicture}
\end{document}
"""


def esc(t: str) -> str:
    t = t.replace("\\", r"\textbackslash{}")
    for a, b in (("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"), ("_", r"\_"),
                 ("{", r"\{"), ("}", r"\}")):
        t = t.replace(a, b)
    # aspas retas -> aspas tipograficas alternadas
    partes = t.split('"')
    t = "".join(p if i == 0 else (("``" if i % 2 else "''") + p) for i, p in enumerate(partes))
    return t.replace("\n", r"\\")


def figura(nome: str, trocas):
    linhas = [PREAMBULO]
    prev = "topo"
    k = 0
    for troca in trocas:
        mensagens = [("pac", troca["paciente"])] + [("bot", b) for b in troca["bot"]]
        for quem, texto in mensagens:
            k += 1
            linhas.append(rf"\path ({prev}.south) ++(0,-3.5mm) coordinate (c{k});")
            if quem == "pac":
                pos = f"(dir |- c{k})"
                linhas.append(rf"\node[pac, anchor=north east] (m{k}) at {pos} "
                              r"{\begin{varwidth}{7.5cm}" + esc(texto) + r"\end{varwidth}};")
            else:
                pos = f"(esq |- c{k})"
                linhas.append(rf"\node[bot, anchor=north west] (m{k}) at {pos} "
                              r"{\begin{varwidth}{9cm}" + esc(texto) + r"\end{varwidth}};")
            prev = f"m{k}"
    linhas.append(rf"\path ({prev}.south) ++(0,-4mm) coordinate (fim);")
    linhas.append(FIM)
    (AQUI / f"{nome}.tex").write_text("\n".join(linhas), encoding="utf-8")
    print(f"{nome}.tex: {k} mensagens")


ca = F["cadastro_agendamento"]
figura("conversa_cadastro", ca[:8])                                     # saudacao, consentimento e cadastro
figura("conversa_agendamento", ca[8:] + F["convenio"] + F["consulta"])  # escolha, confirmacao, convenio, consulta
figura("conversa_remarcacao", F["remarcacao"] + F["cancelamento"])      # remarcacao e cancelamento
