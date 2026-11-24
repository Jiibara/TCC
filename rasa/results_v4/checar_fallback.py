import json

with open("intent_errors.json", encoding="utf-8") as f:
    erros = json.load(f)

print("Total de erros no arquivo:", len(erros))
print()
for x in erros:
    if x["intent"] == "fora_do_escopo":
        pred = x["intent_prediction"]
        print(f'{x["text"][:60]!r:65} -> {pred["name"]:25} conf={pred["confidence"]:.2f}')

print()
print("Erros previstos como nlu_fallback:",
      sum(1 for x in erros if x["intent_prediction"]["name"] == "nlu_fallback"))