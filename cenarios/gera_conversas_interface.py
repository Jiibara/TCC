"""Gera conversas reais com o chatbot (canal REST, banco de TESTE) para as figuras da
secao "Prototipacao da interface" do TCC. Usa um paciente ficticio do pesquisador.
Salva as mensagens trocadas em cenarios/conversas_interface.json.

Pre-requisitos: rasa run e rasa run actions no ar, com CLINICA_DB apontando para o banco de teste.
Uso (venv ativo, na raiz do projeto):  python cenarios\\gera_conversas_interface.py
"""
import json
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")
URL = "http://127.0.0.1:5005/webhooks/rest/webhook"
SAIDA = Path(__file__).resolve().parent / "conversas_interface.json"
SENDER = f"interface_{int(time.time())}"

FLUXOS = {
    "cadastro_agendamento": ["oi", "quero marcar uma consulta com cardiologista", "sim", "sim",
                             "Maria Exemplo da Silva", "10/05/1990", "(45) 99999-1234", "particular",
                             "1", "sim"],
    "convenio": ["vocês aceitam Unimed?"],
    "consulta": ["qual data eu agendei?"],
    "remarcacao": ["quero remarcar minha consulta", "2", "sim"],
    "cancelamento": ["quero cancelar minha consulta", "sim"],
}

resultado = {}
for nome, msgs in FLUXOS.items():
    conversa = []
    for m in msgs:
        r = requests.post(URL, json={"sender": SENDER, "message": m}, timeout=60)
        r.raise_for_status()
        respostas = [x.get("text", "") for x in r.json() if isinstance(x, dict) and x.get("text")]
        conversa.append({"paciente": m, "bot": respostas})
        print(f"> {m}")
        for t in respostas:
            print(f"< {t}")
    resultado[nome] = conversa
    print()

SAIDA.write_text(json.dumps({"sender": SENDER, "fluxos": resultado}, ensure_ascii=False, indent=1),
                 encoding="utf-8")
print(f"salvo em {SAIDA}")
