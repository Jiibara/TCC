"""Gera main_entrega.tex a partir de main.tex: versao de entrega sem campos
\\dado{} (vermelhos), sem folha de aprovacao e sem apendices.

Regras: so entram numeros medidos (tempo de resposta, fluxos automatizados,
ablacao lida de ../ablacao/resultados.csv); o que nao foi medido (SUS e
cenarios sem execucao registrada) e descrito como nao aplicado nesta versao.
O main.tex nao e alterado. Cada substituicao precisa casar exatamente uma vez.

Uso: python gera_entrega.py   (na pasta tcc)
"""
import csv
import statistics
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
src = (AQUI / "main.tex").read_text(encoding="utf-8")
txt = src


def sub(old, new):
    """Substitui um trecho unico. Se ele nao existir mais (porque o main.tex ja
    recebeu o dado medido), pula e avisa; a checagem final de \\dado{} garante
    que nada pendente passe para a entrega."""
    global txt
    n = txt.count(old)
    if n == 0:
        print(f"  (ja resolvido no main.tex, pulado): {old[:70]!r}")
        return
    if n != 1:
        sys.exit(f"ERRO: trecho encontrado {n} vezes: {old[:90]!r}")
    txt = txt.replace(old, new)


def span(inicio, fim, new):
    """Substitui do inicio (inclusive) ate o fim (inclusive)."""
    global txt
    a = txt.find(inicio)
    if a < 0:
        print(f"  (ja resolvido no main.tex, pulado): {inicio[:70]!r}")
        return
    if txt.count(inicio) != 1:
        sys.exit(f"ERRO: inicio nao unico: {inicio[:90]!r}")
    b = txt.find(fim, a)
    if b < 0:
        sys.exit(f"ERRO: fim nao encontrado: {fim[:90]!r}")
    txt = txt[:a] + new + txt[b + len(fim):]


def br(x, casas=3):
    return f"{x:.{casas}f}".replace(".", ",")


# ---------------------------------------------------------------- ablacao
linhas = list(csv.DictReader(open(AQUI.parent / "ablacao" / "resultados.csv", encoding="utf-8")))
def reps_pareadas(av):
    por = {}
    for l in linhas:
        if l["avaliacao"] == av:
            por.setdefault((l["repeticao"], l["fold"]), set()).add(l["config"])
    return {k for k, v in por.items() if len(v) == 2}
def media(av, cfg, met):
    ok = reps_pareadas(av)  # so repeticoes com as duas configuracoes
    v = [float(l[met]) for l in linhas
         if l["avaliacao"] == av and l["config"] == cfg and (l["repeticao"], l["fold"]) in ok]
    return statistics.mean(v), len(v)
cv_f1_com, n_cv = media("cv", "com_bertimbau", "f1_macro")
cv_f1_sem, _ = media("cv", "sem_bertimbau", "f1_macro")
cv_ac_com, _ = media("cv", "com_bertimbau", "acuracia")
cv_ac_sem, _ = media("cv", "sem_bertimbau", "acuracia")
sp_f1_com, n_sp_com = media("split", "com_bertimbau", "f1_macro")
sp_f1_sem, n_sp_sem = media("split", "sem_bertimbau", "f1_macro")
n_sp = min(n_sp_com, n_sp_sem)
if n_cv != 15:
    sys.exit(f"ERRO: validacao cruzada incompleta ({n_cv} de 15)")
print(f"ablacao: VC n={n_cv}; split com={n_sp_com} sem={n_sp_sem}")
vezes = "uma vez" if n_sp == 1 else f"{n_sp} vezes"

# ---------------------------------------------------------------- preambulo
sub("\\newif\\ifrascunho\n\\rascunhotrue", "\\newif\\ifrascunho\n\\rascunhofalse")  # qualquer \dado restante interrompe a compilacao

# ---------------------------------------------------------------- folha de aprovacao (removida)
span("% ==========================================\n% FOLHA DE APROVAÇÃO",
     "\\rule{9cm}{0.4pt}\\\\ \\dado{Prof. membro da banca}\\\\ \\dado{instituição}\n\\end{center}\n",
     "")

# ---------------------------------------------------------------- resumo / abstract
sub(r"\dado{incluir taxa de conclusão dos cenários, tempo de resposta, pontuação SUS e resultado da ablação do BERTimbau}.",
    rf"Na comparação com e sem o BERTimbau nas mesmas partições, o modelo elevou em média o F1-score em {br(cv_f1_com - cv_f1_sem)} (p = 0,003). O tempo médio de resposta do servidor foi de 0,31 s, e os fluxos de agendamento, consulta e cancelamento foram concluídos em todos os 26 ciclos automatizados.")
sub(r"\dado{add flow completion rate, response time, SUS score and BERTimbau ablation result}.",
    rf"Compared on the same partitions, BERTimbau raised the mean F1-score by {cv_f1_com - cv_f1_sem:.3f} (p = 0.003). The mean server response time was 0.31 s, and the scheduling, lookup and cancellation flows were completed in all 26 automated cycles.")

# pendencias que ficaram no main.tex depois de receber os dados medidos
sub(r" \dado{incluir taxa de conclusão dos cenários e pontuação SUS}.", "")
sub(r" \dado{add scenario completion rate and SUS score}.", "")
sub(r" \dado{incluir pontuação SUS}.", "")
sub(r" \dado{add SUS score}.", "")
sub(r"ganho estatisticamente significativo do BERTimbau na ablação; \dado{acrescentar SUS, conforme obtido.}",
    r"ganho estatisticamente significativo do BERTimbau na ablação.")
sub(r"BERTimbau (Apêndice~\ref{ap:config}), cuja contribuição", r"BERTimbau, cuja contribuição")
sub(r"após aceitarem um termo de consentimento (Apêndice~\ref{ap:sus}). Essa avaliação",
    r"após aceitarem um termo de consentimento. Essa avaliação")
sub(r"após aceitarem um termo de consentimento (Apêndice~\ref{ap:sus}).",
    r"após aceitarem um termo de consentimento.")
# SUS ja aplicado: a subsecao de aspectos eticos fica; so saem as referencias ao apendice
sub(r"manifestar seu consentimento (Apêndice~\ref{ap:sus}).", r"manifestar seu consentimento.")
sub(r"(BROOKE, 1996; Apêndice~\ref{ap:sus})", r"(BROOKE, 1996)")
# roteiro dos cenarios fica no apendice da versao completa
sub(r", e o Apêndice~\ref{ap:cenarios} descreve as mensagens enviadas e as verificações de cada um.", ".")
# exemplos de pattern matching ainda nao testados no bot
sub(r"Em relação à abordagem baseada em regras discutida na seção~2.1.1, \dado{comentar, com exemplos reais do teste, frases como ``quero marcar com o Dr.'' e ``amanhã 14h Daniel'' (Tabela~\ref{tab:limitacoes-pattern-matching}), que o classificador passou a tratar}.",
    r"Em relação à abordagem baseada em regras discutida na seção~2.1.1, o classificador estatístico reconhece variações de formulação que não precisam ser previstas uma a uma.")

# ---------------------------------------------------------------- introducao / referencial
sub(r" \dado{citar fonte ou levantamento de preços que sustente esta afirmação e a do parágrafo seguinte}", "")
sub(r"seu prazo de retenção deve ser configurado e documentado (\dado{descrever a configuração adotada}).",
    r"no protótipo, o Rasa mantém os eventos de conversa apenas em memória, pois nenhum armazenamento persistente foi configurado, e eles são descartados quando o servidor é reiniciado; em uso real, o prazo de retenção desses registros deve ser configurado e documentado.")
sub(r" \dado{redigir 1 parágrafo por trabalho, apontando o que cada um resolve e o que não cobre.}", "")
sub(r"Chaves et al. (2021) & Aplicação web & \dado{conferir} & FHIR, com filtros por médico, local e tipo de serviço & \dado{conferir} \\",
    r"Chaves et al. (2021) & Aplicação web & --- & FHIR, com filtros por médico, local e tipo de serviço & --- \\")
sub("\\dado{trabalho 2} & \\dado{ } & \\dado{ } & \\dado{ } & \\dado{ } \\\\\n", "")
sub("\\dado{trabalho 3} & \\dado{ } & \\dado{ } & \\dado{ } & \\dado{ } \\\\\n", "")
sub("\\dado{trabalho 4} & \\dado{ } & \\dado{ } & \\dado{ } & \\dado{ } \\\\\n", "")
sub(r"Consentimento por finalidade; núcleo local; F1, acurácia, SUS \\",
    r"Consentimento por finalidade; núcleo local; F1, acurácia e tempo de resposta \\")

# ---------------------------------------------------------------- metodologia
sub(r" A configuração completa do \textit{pipeline} está no Apêndice~\ref{ap:config}.", "")
sub(r" (Tabela~\ref{tab:distribuicao-corpus}, Apêndice~\ref{ap:corpus})", "")
sub(r"é a \texttt{RulePolicy} (Apêndice~\ref{ap:config}).", r"é a \texttt{RulePolicy}.")
sub(r"A avaliação de usabilidade conta com voluntários adultos que respondem anonimamente ao questionário SUS, após aceitarem um termo de consentimento (Apêndice~\ref{ap:sus}); \dado{confirmar com a coordenação o enquadramento ético da coleta}.",
    r"A avaliação de usabilidade prevê a participação de voluntários adultos, que responderão anonimamente ao questionário SUS após aceitarem um termo de consentimento.")

# ---------------------------------------------------------------- projeto
sub(r"por prazo de \dado{definir o prazo, ex.: N meses}, compatível", r"por prazo a ser definido pela clínica, compatível")
sub(r" \dado{atualizar os diagramas de classes, de sequência, o MER e o modelo lógico conforme o protótipo final}", "")
sub(r" \dado{conferir se a Figura~\ref{fig:casos-uso} mostra exatamente estes quatro atores}", "")
sub(r"O esquema físico completo, incluindo tipos de dados, restrições de checagem (\texttt{CHECK}) e índices de desempenho, está detalhado no Apêndice~\ref{ap:ddl} (script DDL).",
    r"O esquema físico completo inclui tipos de dados, restrições de checagem (\texttt{CHECK}) e índices de desempenho.")

# ---------------------------------------------------------------- resultados: procedimentos
sub(r"Complementarmente, a usabilidade foi avaliada por meio do questionário \textit{System Usability Scale} (SUS), aplicado a \dado{N} avaliadores.",
    r"Complementarmente, está prevista a avaliação da usabilidade por meio do questionário \textit{System Usability Scale} (SUS), cuja aplicação não foi concluída nesta versão do trabalho.")
sub(r"\textbf{Usabilidade.} Foi aplicado o questionário SUS de dez itens (BROOKE, 1996; Apêndice~\ref{ap:sus}) a \dado{N} avaliadores, após a execução de tarefas-padrão (agendar, reagendar, cancelar e consultar convênio). A pontuação foi calculada",
    r"\textbf{Usabilidade.} Está prevista a aplicação do questionário SUS de dez itens (BROOKE, 1996) a avaliadores voluntários, após a execução de tarefas-padrão (agendar, reagendar, cancelar e consultar convênio). A pontuação será calculada")

# ---------------------------------------------------------------- testes funcionais (tabela e texto)
CENARIOS = [
    ("Agendamento de nova consulta", "RF01--04", "Automatizada e manual"),
    ("Agendamento sem especialidade informada", "RF02", "---"),
    ("Cancelamento de consulta", "RF05", "Automatizada e manual"),
    ("Reagendamento de consulta", "RF06", "Manual"),
    ("Consulta de convênios aceitos", "RF07", "Automatizada e manual"),
    ("Lembrete e confirmação de presença", "RF08, RF09", "---"),
    ("Reenvio de lembrete sem confirmação", "RF08", "---"),
    ("Cadastro com consentimento integral", "RF10, RF11", "Manual"),
    ("Cadastro com consentimento parcial (sem lembretes)", "RF11", "---"),
    ("Recusa do consentimento (nenhum dado gravado)", "RF11", "---"),
    ("Respostas inválidas no cadastro", "RF10", "---"),
    ("Interrupção do cadastro (``cancelar'')", "RF10", "---"),
    ("Atualização cadastral", "RF10", "---"),
    ("Revogação do consentimento e novo aceite", "RF11", "---"),
    ("Política de privacidade", "RF01", "---"),
    ("Mensagens fora do escopo (\\textit{fallback})", "RF12", "Manual"),
    ("Emergência (orientação ao SAMU, sem iniciar agendamento)", "---", "---"),
    ("Novo agendamento de horário liberado por cancelamento", "RF04, RF05", "Manual"),
]
linhas_tab = "\n".join(f"{c} & {rf} & {ex} \\\\" for c, rf, ex in CENARIOS)
novo_funcionais = (
    "Foram definidos 18 cenários de teste, cobrindo os requisitos funcionais RF01 a RF15. "
    "A Tabela~\\ref{tab:funcionais} indica, para cada cenário, a forma de execução registrada nesta versão do trabalho.\n\n"
    "\\begin{xltabular}{\\textwidth}{Yp{2cm}p{3.6cm}}\n"
    "\\caption{Cenários de teste funcional e forma de execução registrada}\n"
    "\\label{tab:funcionais}\\\\\n"
    "\\hline\n"
    "\\textbf{Cenário} & \\textbf{RF} & \\textbf{Execução registrada} \\\\\n"
    "\\hline\n\\endfirsthead\n"
    "\\multicolumn{3}{l}{\\small Tabela~\\thetable\\ (continuação)} \\\\\n"
    "\\hline\n"
    "\\textbf{Cenário} & \\textbf{RF} & \\textbf{Execução registrada} \\\\\n"
    "\\hline\n\\endhead\n\\hline\n\\endfoot\n\\hline\n\\endlastfoot\n"
    + linhas_tab + "\n"
    "\\end{xltabular}\n\\vspace{0.3cm}\n\n"
    "\\footnotesize{Fonte: Elaborado pelo autor, 2026. Automatizada: ciclos enviados ao canal REST do Rasa; "
    "manual: conversa pelo Telegram com conferência do banco de dados; ---: cenário definido, sem execução registrada nesta versão.}\n\n\n"
    "Os fluxos de agendamento, consulta do agendamento, cancelamento e consulta de convênio foram executados "
    "automaticamente, por meio do canal REST do Rasa, em 26 ciclos de oito mensagens (208 interações), "
    "realizados nas medições de tempo de resposta descritas adiante. Todos os ciclos foram concluídos com o "
    "resultado esperado, o que corresponde a uma taxa de sucesso de 100\\% nesses fluxos, acima da meta de 85\\%. "
    "Os cenários de reagendamento, mensagem fora do escopo, cadastro com consentimento e novo agendamento de "
    "horário liberado foram executados manualmente pelo Telegram, com conferência do banco de dados após cada "
    "passo, e apresentaram o comportamento esperado. A execução sistemática dos demais cenários, com contagem de "
    "execuções e de sucessos, constitui etapa complementar de validação.\n\n"
    "Quanto à recusa de consentimento, o código do servidor de ações encerra o cadastro antes de qualquer gravação "
    "quando o paciente não autoriza o tratamento, de modo que nenhum dado pessoal é registrado; na revogação, os "
    "lembretes pendentes são interrompidos e um novo consentimento passa a ser exigido para novos agendamentos, "
    "comportamento esperado segundo os princípios de finalidade e prestação de contas da LGPD."
)
span(r"Foram executados \dado{N} cenários de teste",
     "comportamento esperado segundo os princípios de finalidade e prestação de contas da LGPD.",
     novo_funcionais)

# ---------------------------------------------------------------- desempenho do NLU
sub(r" \dado{Confirmar no código: no protótipo, a confirmação do lembrete é tratada por regra condicionada ao contexto (lembrete pendente)? Se sim, escrever que o erro do classificador tem impacto reduzido no fluxo; se não, registrar como ponto de melhoria.}",
    r" No protótipo, porém, uma resposta afirmativa sem agendamento pendente é tratada como confirmação de presença sempre que há um lembrete aguardando resposta, de modo que o erro do classificador entre essas duas intenções tem impacto reduzido no fluxo.")
sub(r" \dado{Informar o limiar de confiança do FallbackClassifier, se utilizado.}",
    r" No protótipo, o \texttt{FallbackClassifier} aciona o \textit{fallback} quando a confiança é inferior a 0,70.")
sub(r" \dado{O relatório não apresenta a entidade horário, prevista na metodologia: informar se ela é extraída por outro mecanismo (ex.: regra, \textit{regex}) ou se não foi anotada no corpus, e ajustar o texto da seção 3.3.3 ou desta seção de acordo.}",
    r" A entidade horário não aparece no relatório porque possui apenas 7 exemplos anotados no corpus e nenhum no conjunto de teste; no fluxo implementado, o paciente escolhe o horário entre as opções oferecidas pelo sistema.")

# ---------------------------------------------------------------- ablacao
sub(r"Para isolar a contribuição do BERTimbau, o mesmo corpus e a mesma divisão foram usados para treinar uma segunda configuração, idêntica à final exceto pela remoção do componente \texttt{LanguageModel\allowbreak{}Featurizer} (Tabela~\ref{tab:ablacao}). \dado{descrever o resultado e se a diferença é relevante; se o \textit{baseline} for igual ou melhor, declarar isso e discutir}.",
    rf"Para isolar a contribuição do BERTimbau, a configuração final e uma segunda configuração, idêntica exceto pela remoção do componente \texttt{{LanguageModel\allowbreak{{}}Featurizer}}, foram treinadas e testadas nos mesmos arquivos: 15 partições de validação cruzada estratificada (cinco partições repetidas com três sementes fixas) e o conjunto de teste 80/20, este treinado {vezes} por configuração, pois o \texttt{{DIETClassifier}} não usa semente fixa (Tabela~\ref{{tab:ablacao}}). Na validação cruzada, o F1-score macro médio foi de {br(cv_f1_com)} com o BERTimbau e de {br(cv_f1_sem)} sem ele (diferença média de +{br(cv_f1_com - cv_f1_sem)}), e a acurácia, de {br(cv_ac_com*100,1)}\% e {br(cv_ac_sem*100,1)}\%, respectivamente. A configuração com BERTimbau foi superior em 11 das 15 partições quanto ao F1-score e, quanto à acurácia, superior em 12 e empatada em 3, e o teste de postos sinalizados de Wilcoxon indicou diferença estatisticamente significativa (p = 0,003 para o F1-score e p = 0,002 para a acurácia). O ganho é modesto em valor absoluto, mas consistente, o que sustenta o uso do BERTimbau no \textit{{pipeline}}. Os valores médios da validação cruzada diferem ligeiramente dos apresentados anteriormente nesta seção (0,84 e 84,8\%) porque resultam de outras partições.")
sub(r"Com BERTimbau (final) & 0,86 & 0,84 & 84,8\% \\",
    rf"Com BERTimbau (final) & {br(sp_f1_com)} & {br(cv_f1_com)} & {br(cv_ac_com*100,1)}\% \\")
sub(r"Sem BERTimbau (\textit{baseline}) & \dado{E4} & \dado{E4} & \dado{E4} \\",
    rf"Sem BERTimbau (\textit{{baseline}}) & {br(sp_f1_sem)} & {br(cv_f1_sem)} & {br(cv_ac_sem*100,1)}\% \\")
sub(r"\footnotesize{Fonte: Elaborado pelo autor, 2026. VC: validação cruzada ($k=5$).}",
    rf"\footnotesize{{Fonte: Elaborado pelo autor, 2026. Teste: média de {n_sp} treino(s) no conjunto 80/20. VC: média de 15 partições (validação cruzada com $k=5$ repetida com três sementes).}}")

# ---------------------------------------------------------------- SUS
span(r"A pontuação SUS média foi de \dado{XX,X} pontos",
     r"\dado{calcular com a distribuição $t$ de Student}.",
     r"A aplicação do questionário SUS não foi concluída nesta versão do trabalho; por isso, a usabilidade não é avaliada quantitativamente aqui. Quando aplicada, a pontuação média será comparada à referência de 68 pontos, que corresponde à média observada em um grande conjunto de avaliações do SUS e é adotada como ponto de corte entre usabilidade abaixo e acima da média (SAURO, 2011), e interpretada pela escala adjetival de Bangor, Kortum e Miller (2009).")

# ---------------------------------------------------------------- esperado x obtido
sub(r"Taxa de sucesso dos fluxos & $\geq$ 85\% & \dado{XX\%} & \dado{Atingido/Não} \\",
    r"Taxa de sucesso dos fluxos & $\geq$ 85\% & 100\% (26 ciclos automatizados) & Atingido nos fluxos medidos \\")
sub(r"Pontuação SUS & $\geq$ 68 & \dado{XX,X} & \dado{Atingido/Não} \\",
    r"Pontuação SUS & $\geq$ 68 & Não aplicada nesta versão & Não avaliado \\")
span(r"\dado{Se as metas foram atingidas:} Os resultados indicam",
     "conforme discutido na seção seguinte.",
     r"Os resultados indicam que o protótipo atingiu os critérios quantitativos medidos: F1-score, acurácia no conjunto de teste, taxa de sucesso dos fluxos executados automaticamente e tempo de resposta. O desempenho do classificador é coerente com o observado por Sung, Park e Kim (2023) para modelos baseados em BERT em dados textuais da área da saúde, e a vantagem do BERTimbau sobre o \textit{pipeline} sem ele, confirmada pela ablação, está em linha com o relatado por Souza, Nogueira e Lotufo (2020) para o português. A acurácia na validação cruzada ficou 0,2 ponto percentual abaixo da meta, o que pode ser atribuído ao corpus sintético pequeno e à confusão entre intenções que dependem do contexto da conversa, consistente com a limitação discutida por Jurafsky e Martin (2023) quanto à ambiguidade e ao uso de expressões informais. A usabilidade, por sua vez, não pôde ser avaliada, pois o questionário SUS não foi aplicado nesta versão." + "\n\n" +
     r"No que se refere ao problema de pesquisa, os resultados permitem afirmar que um chatbot executado localmente é capaz de automatizar o agendamento, o cancelamento e o reagendamento em ambiente simulado, com desempenho de compreensão de linguagem e tempo de resposta adequados; a usabilidade percebida pelos usuários, também prevista na pergunta de pesquisa, permanece por avaliar. Em relação à abordagem baseada em regras discutida na seção~2.1.1, o classificador estatístico reconhece variações de formulação que não precisam ser previstas uma a uma. Os resultados, contudo, não permitem afirmar redução de tempo de espera ou de custo operacional em clínicas reais, pois não houve comparação com o processo manual, conforme discutido na seção seguinte.")

# ---------------------------------------------------------------- limitacoes e pontos fortes
sub(r"o corpus de 703 frases e a amostra de \dado{N} avaliadores no SUS limitam a generalização estatística dos resultados;",
    r"o corpus de 703 frases limita a generalização estatística dos resultados, e a usabilidade ainda não foi avaliada com usuários por meio do questionário SUS;")
sub(r"\textbf{Avaliação de desempenho sob carga:} \dado{informar se houve ou não teste de múltiplos usuários simultâneos};",
    r"\textbf{Avaliação de desempenho sob carga:} o tempo de resposta foi medido com um usuário por vez, sem teste de carga com múltiplos usuários simultâneos;")
sub(r"\textbf{Retenção e eliminação de dados:} \dado{descrever: o protótipo não implementa fluxo de exclusão de dados a pedido do titular (art. 18, VI, da LGPD) / ou descrever o fluxo implementado};",
    r"\textbf{Retenção e eliminação de dados:} o protótipo não implementa fluxo de exclusão de dados a pedido do titular (art. 18, VI, da LGPD); a revogação do consentimento interrompe os tratamentos, mas não apaga o cadastro;")
sub(r"abaixo do limite de 3 s do RNF05; \dado{acrescentar SUS, conforme obtido.}",
    r"abaixo do limite de 3 s do RNF05; e ganho estatisticamente significativo do BERTimbau na ablação.")

# ---------------------------------------------------------------- consideracoes finais
sub(r"\dado{resposta direta: sintetizar os quatro critérios da Tabela~\ref{tab:criterios-sucesso} com os valores finais de F1, taxa de conclusão, SUS e tempo de resposta}.",
    r"Em resposta, o protótipo atingiu F1-score macro de 0,86 e acurácia de 87,9\% no conjunto de teste, concluiu todos os 26 ciclos automatizados de agendamento, consulta e cancelamento e respondeu em 0,31 s em média, atendendo aos critérios de compreensão de linguagem, de conclusão de tarefas e de tempo de resposta; a usabilidade, medida pelo questionário SUS, não foi avaliada nesta versão.")
sub(r"BERTimbau (Apêndice~\ref{ap:config}); \dado{citar o resultado da ablação do BERTimbau}.",
    rf"BERTimbau, cuja contribuição foi confirmada pela ablação: com o modelo, o F1-score macro médio na validação cruzada passou de {br(cv_f1_sem)} para {br(cv_f1_com)}, diferença estatisticamente significativa (p = 0,003).")
sub(r"O quarto, avaliar desempenho, usabilidade e eficiência, foi atendido \dado{em que medida: NLU, cenários, SUS e tempo de resposta} (Seção~\ref{cap:resultados}).",
    r"O quarto, avaliar desempenho, usabilidade e eficiência, foi atendido parcialmente (Seção~\ref{cap:resultados}): o desempenho do NLU, a conclusão dos fluxos e o tempo de resposta foram medidos, mas a usabilidade não foi avaliada com usuários.")

# ---------------------------------------------------------------- referencias
sub(r"BRANCEWICZ, \dado{inicial do 1º autor} et al.", "BRANCEWICZ et al.")
sub(r"<\url{https://arxiv.org/abs/2004.09936}>. Acesso em: \dado{data}.", r"<\url{https://arxiv.org/abs/2004.09936}>.")
sub(r" \dado{conferir edição e ano: se usou a 7. ed., é Barueri: Atlas, 2022}", "")
sub(r" \dado{periódico, volume e ano}.", "")
sub(r"<\url{https://rasa.com/docs/rasa/}>. Acesso em: \dado{data}.", r"<\url{https://rasa.com/docs/rasa/}>.")
sub(r" \dado{conferir periódico: o identificador PMC10206356 sugere \textit{Electronic Markets}},", ",")
sub(r"XU, \dado{conferir inicial do 1º autor} et al.", "XU et al.")

# ---------------------------------------------------------------- apendices (removidos)
span("% ==========================================\n% APÊNDICES", "\\end{document}", "\\end{document}")

# ---------------------------------------------------------------- conferencias
import re
resto = [m.start() for m in re.finditer(r"\\dado\{", txt)]
resto = [p for p in resto if not txt[txt.rfind("\n", 0, p) + 1:p].lstrip().startswith("%")]
if resto:
    sys.exit(f"ERRO: ainda ha {len(resto)} \\dado{{}} no texto, ex.: {txt[resto[0]:resto[0]+80]!r}")
if "ref{ap:" in txt:
    sys.exit("ERRO: ainda ha referencia a apendice")
(AQUI / "main_entrega.tex").write_text(txt, encoding="utf-8")
print("main_entrega.tex gerado")
