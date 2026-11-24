#!/usr/bin/env python3
"""E2 - tempo de resposta do SERVIDOR (NLU + dialogo + acoes + banco), SEM o Telegram e o ngrok.

Mede o tempo entre enviar uma mensagem ao canal REST do Rasa e receber a
resposta completa, para um paciente de teste JA CADASTRADO e com
consentimento (sender "latencia"), repetindo um ciclo realista de conversa:
saudacao, convenio, agendamento (busca, escolha, confirmacao), consulta do
agendamento e cancelamento.

Pre-requisitos:
  - rasa\\credentials.yml com a linha `rest:`
  - banco de teste: copia do clinica.db com o paciente "latencia" cadastrado
  - rasa run actions  com  set CLINICA_DB=<caminho do banco de teste>
  - rasa run  SEM --debug
  - ninguem usando o bot pelo Telegram durante a medicao

Uso (com o venv ativo):
  python mede_latencia.py                 # 1 ciclo de aquecimento + 13 medidos (104 mensagens)
  python mede_latencia.py --ciclos 20 --csv latencia.csv
"""
import argparse
import csv
import math
import statistics
import sys
import time

import requests

# mostra o progresso na hora, mesmo com a saida redirecionada
sys.stdout.reconfigure(line_buffering=True)

# 127.0.0.1, e nao localhost: no Windows, "localhost" tenta antes o IPv6
# e acrescenta ~2 s a cada conexao (o Rasa so escuta em IPv4).
URL_PADRAO = "http://127.0.0.1:5005/webhooks/rest/webhook"

# (mensagem, trecho esperado na resposta ou None). O trecho confere que o
# fluxo andou como previsto; a comparacao ignora maiusculas.
CICLO = [
    ("oi", None),
    ("vocês aceitam Unimed?", None),
    ("quero marcar uma consulta com cardiologista", None),
    ("1", None),
    ("sim", "agendada com sucesso"),
    ("qual data eu agendei?", None),
    ("quero cancelar minha consulta", "cancelar a consulta"),
    ("sim", "cancelada"),
]


def enviar(url, sender, texto, timeout):
    """Envia uma mensagem e devolve (segundos, status_http, texto_resposta)."""
    t0 = time.perf_counter()
    r = requests.post(url, json={"sender": sender, "message": texto}, timeout=timeout)
    dt = time.perf_counter() - t0
    try:
        corpo = r.json()
    except ValueError:
        corpo = []
    resposta = " | ".join(m.get("text", "") for m in corpo if isinstance(m, dict))
    return dt, r.status_code, resposta


def percentil(valores_ordenados, p):
    """Percentil pelo metodo do posto mais proximo (nearest-rank)."""
    k = max(1, math.ceil(p / 100 * len(valores_ordenados)))
    return valores_ordenados[k - 1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=URL_PADRAO)
    ap.add_argument("--sender", default="latencia")
    ap.add_argument("--ciclos", type=int, default=13, help="ciclos medidos (8 mensagens cada)")
    ap.add_argument("--aquecimento", type=int, default=1, help="ciclos descartados no inicio")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--csv", help="grava cada medida neste arquivo CSV")
    args = ap.parse_args()

    amostras = []          # (ciclo, passo, mensagem, segundos, status, ok)
    falhas_http = 0
    ciclos_completos = 0
    total = args.aquecimento + args.ciclos

    for ciclo in range(total):
        medido = ciclo >= args.aquecimento
        # zera o estado da conversa (nao medido); o cadastro vem do banco
        enviar(args.url, args.sender, "/restart", args.timeout)
        ciclo_ok = True
        for passo, (msg, esperado) in enumerate(CICLO, start=1):
            dt, status, resposta = enviar(args.url, args.sender, msg, args.timeout)
            ok = status == 200 and bool(resposta)
            if esperado and esperado.lower() not in resposta.lower():
                ok = False
            ciclo_ok = ciclo_ok and ok
            if ciclo == 0:
                print(f"[ciclo 1] {msg!r} -> {resposta[:110]!r}  ({dt:.2f} s, HTTP {status})")
            if medido:
                amostras.append((ciclo + 1 - args.aquecimento, passo, msg, dt, status, ok))
                if status != 200:
                    falhas_http += 1
        if medido and ciclo_ok:
            ciclos_completos += 1

    if not amostras:
        sys.exit("Nenhuma amostra medida.")

    tempos = sorted(a[3] for a in amostras)
    n = len(tempos)
    print()
    print(f"Mensagens medidas: n = {n} ({args.ciclos} ciclos x {len(CICLO)}; {args.aquecimento} ciclo(s) de aquecimento descartado(s))")
    print(f"Falhas HTTP: {falhas_http} | ciclos com fluxo completo e esperado: {ciclos_completos} de {args.ciclos}")
    print(f"Média: {statistics.mean(tempos):.3f} s | mediana: {statistics.median(tempos):.3f} s | "
          f"desvio-padrão: {statistics.stdev(tempos):.3f} s")
    print(f"Mínimo: {tempos[0]:.3f} s | p95: {percentil(tempos, 95):.3f} s | máximo: {tempos[-1]:.3f} s")
    print(f"Acima de 3 s (RNF05): {sum(t > 3 for t in tempos)} de {n}")
    print()
    print("Média por mensagem do ciclo:")
    for passo, (msg, _) in enumerate(CICLO, start=1):
        ts = [a[3] for a in amostras if a[1] == passo]
        print(f"  {passo}. {msg:<45} média {statistics.mean(ts):.3f} s | máx {max(ts):.3f} s")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ciclo", "passo", "mensagem", "segundos", "status_http", "resposta_ok"])
            for a in amostras:
                w.writerow([a[0], a[1], a[2], f"{a[3]:.4f}", a[4], int(a[5])])
        print(f"\nMedidas gravadas em {args.csv}")


if __name__ == "__main__":
    main()
