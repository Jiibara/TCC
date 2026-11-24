"""Classifica frases com o modelo atual do Rasa (NLU), sem precisar dos servidores.

Uso (venv ativo, na raiz do projeto):  python cenarios\\testa_frases.py
"""
import asyncio
import re
import sys
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
RAIZ = Path(__file__).resolve().parent.parent
FRASES = [
    "Agende a consulta",
    "Quero marcar com o Dr.",
    "Amanhã 14h Daniel",
    "quero marcar",
    "preciso de uma consulta",
    "tem horário disponível",
]


def norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", "", t).strip()


def no_corpus():
    texto = (RAIZ / "rasa" / "data" / "nlu.yml").read_text(encoding="utf-8")
    exemplos = set()
    for linha in texto.splitlines():
        m = re.match(r"\s*-\s+(.*)", linha)
        if m and not m.group(1).startswith(("intent:", "synonym:", "regex:", "lookup:")):
            exemplos.add(norm(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1))))
    return exemplos


async def main():
    from rasa.core.agent import Agent
    modelos = sorted((RAIZ / "rasa" / "models").glob("*.tar.gz"))
    modelo = modelos[-1]
    print(f"modelo: {modelo.name}\n")
    agent = Agent.load(str(modelo))
    corpus = no_corpus()
    for f in FRASES:
        r = await agent.parse_message(f)
        ranking = r.get("intent_ranking", [])
        # a intencao do DIET antes do FallbackClassifier
        diet = next((x for x in ranking if x["name"] != "nlu_fallback"), r["intent"])
        ents = ", ".join(f"{e['entity']}={e['value']}" for e in r.get("entities", [])) or "nenhuma"
        print(f"{f!r}")
        print(f"   final: {r['intent']['name']} ({r['intent']['confidence']:.2f}) | DIET: {diet['name']} "
              f"({diet['confidence']:.2f}) | entidades: {ents} | igual a exemplo do corpus: "
              f"{'sim' if norm(f) in corpus else 'não'}")


asyncio.run(main())
