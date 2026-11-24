#!/usr/bin/env python3
"""E4 - ablacao do BERTimbau: compara o pipeline de NLU com e sem o
LanguageModelFeaturizer nos MESMOS arquivos de treino e teste.

Duas avaliacoes:
  cv    - validacao cruzada estratificada (5 folds) repetida com sementes
          fixas (scikit-learn StratifiedKFold(shuffle=True, random_state=s)),
          gerada a partir de rasa/data/nlu.yml. Cada fold e usada pelas duas
          configuracoes, o que permite comparar fold a fold (pareado).
  split - o conjunto 80/20 ja usado no Cap. 5 (rasa/train_test_split),
          treinado e testado algumas vezes por configuracao, porque o
          DIETClassifier nao tem semente fixa e varia entre treinos.

Cada rodada e um `rasa train nlu` + `rasa test nlu` (o mesmo avaliador das
metricas do Cap. 5). O cache de treino do Rasa e desligado
(RASA_MAX_CACHE_SIZE=0) para cada treino ser independente. Os modelos vao
para ablacao/modelos e sao apagados apos o teste; rasa/models nao e tocado.
Os resultados sao gravados em ablacao/resultados.csv; se o script for
interrompido, roda de novo e continua de onde parou.

Uso (venv ativo, servidores do bot parados, a partir de qualquer pasta):
  python ablacao\\ablacao.py                  # tudo: 3 sementes x 5 folds x 2 configs + 3 x 2 no split
  python ablacao\\ablacao.py --limite 1       # so a primeira rodada pendente (teste rapido)
  python ablacao\\ablacao.py --resumo         # so imprime o resumo do que ja rodou
"""
import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
RASA_EXE = RAIZ / ".venv" / "Scripts" / "rasa.exe"
NLU = RAIZ / "rasa" / "data" / "nlu.yml"
SPLIT_TREINO = RAIZ / "rasa" / "train_test_split" / "training_data.yml"
SPLIT_TESTE = RAIZ / "rasa" / "train_test_split" / "test_data.yml"
CONFIGS = {
    "com_bertimbau": RAIZ / "rasa" / "config.yml",
    "sem_bertimbau": RAIZ / "pacote" / "pkg" / "scripts" / "config_sem_bertimbau.yml",
}
PASTA_FOLDS = AQUI / "folds"
PASTA_MODELOS = AQUI / "modelos"
PASTA_RELATORIOS = AQUI / "relatorios"
PASTA_LOGS = AQUI / "logs"
CSV_RESULTADOS = AQUI / "resultados.csv"
CAMPOS = ["avaliacao", "repeticao", "fold", "config", "acuracia", "f1_macro",
          "f1_ponderado", "n_teste", "seg_treino", "seg_teste"]


def gerar_folds(sementes, k):
    """Gera (uma vez) os arquivos de treino/teste de cada fold, estratificados
    por intencao, preservando entidades, sinonimos, regex e lookup tables."""
    from sklearn.model_selection import StratifiedKFold
    from rasa.shared.nlu.training_data.loading import load_data
    from rasa.shared.nlu.training_data.training_data import TrainingData

    td = None
    for s in sementes:
        pasta = PASTA_FOLDS / f"semente{s}"
        if all((pasta / f"fold{i}_{p}.yml").exists() for i in range(1, k + 1) for p in ("treino", "teste")):
            continue
        if td is None:
            td = load_data(str(NLU))
            exemplos = td.intent_examples
            rotulos = [m.get("intent") for m in exemplos]
        pasta.mkdir(parents=True, exist_ok=True)
        skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=s)
        for i, (idx_tr, idx_te) in enumerate(skf.split(exemplos, rotulos), start=1):
            for nome, idx in (("treino", idx_tr), ("teste", idx_te)):
                parte = TrainingData(
                    training_examples=[exemplos[j] for j in idx],
                    entity_synonyms=td.entity_synonyms,
                    regex_features=td.regex_features,
                    lookup_tables=td.lookup_tables,
                )
                (pasta / f"fold{i}_{nome}.yml").write_text(parte.nlu_as_yaml(), encoding="utf-8")
        print(f"folds da semente {s} gerados em {pasta}")


def listar_rodadas(sementes, k, reps_split):
    rodadas = []
    for s in sementes:
        for i in range(1, k + 1):
            for cfg in CONFIGS:
                rodadas.append(dict(
                    avaliacao="cv", repeticao=s, fold=i, config=cfg,
                    treino=PASTA_FOLDS / f"semente{s}" / f"fold{i}_treino.yml",
                    teste=PASTA_FOLDS / f"semente{s}" / f"fold{i}_teste.yml"))
    for r in range(1, reps_split + 1):
        for cfg in CONFIGS:
            rodadas.append(dict(avaliacao="split", repeticao=r, fold=0, config=cfg,
                                treino=SPLIT_TREINO, teste=SPLIT_TESTE))
    return rodadas


def chave(d):
    return (d["avaliacao"], int(d["repeticao"]), int(d["fold"]), d["config"])


def ler_resultados():
    if not CSV_RESULTADOS.exists():
        return []
    with open(CSV_RESULTADOS, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def rodar(cmd, log, env, produto):
    """Roda o comando e confere se ele gerou `produto` (arquivo esperado).
    No Windows, o TensorFlow as vezes quebra ao ENCERRAR o processo
    (codigo 3221225477 = 0xC0000005) depois de ter terminado o trabalho;
    por isso o criterio de sucesso e o arquivo gerado, nao so o codigo."""
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n$ " + " ".join(map(str, cmd)) + "\n")
        f.flush()
        p = subprocess.run([str(c) for c in cmd], stdout=f, stderr=subprocess.STDOUT, env=env, cwd=str(AQUI))
        if p.returncode != 0 and Path(produto).exists():
            f.write(f"\n[ablacao] codigo de saida {p.returncode}, mas {produto} foi gerado; resultado aceito.\n")
    if not Path(produto).exists():
        raise RuntimeError(f"comando falhou (codigo {p.returncode}); veja {log}")
    return p.returncode


def executar(rodada, env):
    ident = f"{rodada['avaliacao']}_r{rodada['repeticao']}_f{rodada['fold']}_{rodada['config']}"
    pasta_modelo = PASTA_MODELOS / ident
    pasta_rel = PASTA_RELATORIOS / ident
    log = PASTA_LOGS / f"{ident}.log"
    for p in (pasta_modelo, pasta_rel):
        shutil.rmtree(p, ignore_errors=True)
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.unlink()

    t0 = time.perf_counter()
    modelo = pasta_modelo / "modelo.tar.gz"
    rodar([RASA_EXE, "train", "nlu", "-c", CONFIGS[rodada["config"]], "-u", rodada["treino"],
           "--out", pasta_modelo, "--fixed-model-name", "modelo"], log, env, modelo)
    t1 = time.perf_counter()
    rodar([RASA_EXE, "test", "nlu", "-u", rodada["teste"], "-m", modelo, "--out", pasta_rel],
          log, env, pasta_rel / "intent_report.json")
    t2 = time.perf_counter()

    rel = json.loads((pasta_rel / "intent_report.json").read_text(encoding="utf-8"))
    shutil.rmtree(pasta_modelo, ignore_errors=True)
    return dict(
        avaliacao=rodada["avaliacao"], repeticao=rodada["repeticao"], fold=rodada["fold"],
        config=rodada["config"], acuracia=f"{rel['accuracy']:.4f}",
        f1_macro=f"{rel['macro avg']['f1-score']:.4f}",
        f1_ponderado=f"{rel['weighted avg']['f1-score']:.4f}",
        n_teste=rel["macro avg"]["support"], seg_treino=f"{t1 - t0:.0f}", seg_teste=f"{t2 - t1:.0f}")


def resumo():
    linhas = ler_resultados()
    if not linhas:
        print("Nenhum resultado ainda.")
        return

    def ms(v):
        return f"{statistics.mean(v):.4f} ± {statistics.stdev(v):.4f}" if len(v) > 1 else f"{v[0]:.4f}"

    print("\n=== RESUMO ===")
    for av in ("cv", "split"):
        sel = [l for l in linhas if l["avaliacao"] == av]
        if not sel:
            continue
        print(f"\n[{av}]")
        for cfg in CONFIGS:
            ls = [l for l in sel if l["config"] == cfg]
            if ls:
                print(f"  {cfg:<14} n={len(ls):>2} | F1 macro {ms([float(l['f1_macro']) for l in ls])}"
                      f" | acurácia {ms([float(l['acuracia']) for l in ls])}")
        # comparacao pareada (mesmo arquivo de teste)
        por = {}
        for l in sel:
            por.setdefault((l["repeticao"], l["fold"]), {})[l["config"]] = l
        pares = [v for v in por.values() if len(v) == 2]
        if len(pares) >= 2:
            for met in ("f1_macro", "acuracia"):
                dif = [float(p["com_bertimbau"][met]) - float(p["sem_bertimbau"][met]) for p in pares]
                ganha = sum(d > 0 for d in dif)
                perde = sum(d < 0 for d in dif)
                txt = (f"  {met}: diferença (com - sem) média {statistics.mean(dif):+.4f} "
                       f"(dp {statistics.stdev(dif):.4f}); com BERTimbau melhor em {ganha}, "
                       f"pior em {perde}, empate em {len(dif) - ganha - perde} de {len(dif)} pares")
                try:
                    from scipy.stats import wilcoxon
                    if any(dif):
                        txt += f"; Wilcoxon p = {wilcoxon(dif).pvalue:.4f}"
                except Exception as e:  # scipy ausente ou amostra inadequada
                    txt += f"; Wilcoxon indisponível ({e.__class__.__name__})"
                print(txt)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sementes", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--reps-split", type=int, default=3)
    ap.add_argument("--limite", type=int, help="executa no maximo N rodadas pendentes")
    ap.add_argument("--resumo", action="store_true", help="so imprime o resumo")
    args = ap.parse_args()

    if args.resumo:
        resumo()
        return

    gerar_folds(args.sementes, args.folds)
    feitas = {chave(l) for l in ler_resultados()}
    pendentes = [r for r in listar_rodadas(args.sementes, args.folds, args.reps_split) if chave(r) not in feitas]
    if args.limite:
        pendentes = pendentes[:args.limite]
    print(f"{len(feitas)} rodadas já feitas; {len(pendentes)} a executar agora.")

    env = dict(os.environ, RASA_MAX_CACHE_SIZE="0", PYTHONIOENCODING="utf-8",
               TF_CPP_MIN_LOG_LEVEL="2")
    novo = not CSV_RESULTADOS.exists()
    for n, r in enumerate(pendentes, start=1):
        t = time.perf_counter()
        res = executar(r, env)
        with open(CSV_RESULTADOS, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            if novo:
                w.writeheader()
                novo = False
            w.writerow(res)
        print(f"[{n}/{len(pendentes)}] {r['avaliacao']} rep{r['repeticao']} fold{r['fold']} {r['config']}: "
              f"F1 macro {res['f1_macro']} | acurácia {res['acuracia']} | {time.perf_counter() - t:.0f} s")
    resumo()


if __name__ == "__main__":
    main()
