# Documentação do discord-link-brain

## Em uma frase

Lê os links que você jogou num canal do Discord, descobre do que cada um trata,
classifica com um LLM, e arquiva no vault Obsidian de forma consultável — por
dia, por categoria e por conceito.

```
Discord  →  extrai links  →  lê a página   →  LLM classifica  →  vault Obsidian  →  marca no Discord
            (dedup, limpa)   (oEmbed, og:,    (resumo, cat.)     (fonte + nota       (✅ ou 📚)
                              texto central)                      atômica + diário
                                                                  + MOC)
```

---

## Leia nesta ordem

Nove passos, do primeiro contato até mexer no código. A ordem não é alfabética nem
por tamanho: **cada passo assume o anterior**, e o motivo de cada posição está
escrito. São ~3.400 linhas no total, e ninguém precisa ler tudo de uma vez — os
[atalhos](#atalhos-se-você-não-quer-a-trilha-inteira) dizem onde parar.

### 0 · [`../README.md`](../README.md) — a porta de entrada

**Responde:** o que isto faz, o que precisa estar instalado, como sai o primeiro
`sync`.
**Por que primeiro:** é o único documento que não pressupõe nada.
**Depois dele você:** tem o projeto instalado e sabe o que esperar.
`228 linhas · leitura curta`

### 1 · [CONFIGURACAO.md](CONFIGURACAO.md) — as ~50 variáveis

**Responde:** o que cada variável do `.env` faz, e qual vence qual.
**Por que aqui:** nada roda sem configurar, e este documento ensina o **vocabulário**
(`DISCORD_CHANNEL_IDS`, `LLM_BACKEND`, `DLB_SYNC_EVERY_HOURS`) que todos os outros
usam sem reexplicar.
**Depois dele você:** tem um `.env` que o `dlb doctor` aprova.
`307 linhas · consulta — leia as seções do que você usa e volte quando precisar`

### 2 · [OPERACAO.md](OPERACAO.md) — rodar de verdade

**Responde:** primeiro sync, agendamento, recuperar histórico antigo, e o que fazer
quando algo falha.
**Por que aqui:** com a configuração de pé, o passo natural é ver o pipeline rodar.
**Depois dele você:** capturou links de verdade e sabe reler o histórico do canal.
`298 linhas · leitura média`

### 3 · [LINKS.md](LINKS.md) — o que ele produz

**Responde:** como é a nota atômica de cada link, o ciclo de triagem
(`inbox → lido → aplicado | descartado`), a síntese do período, o `migrar`.
**Por que aqui:** é **o produto**, e faz muito mais sentido depois de você ter rodado
um sync e visto as notas aparecerem no Obsidian. Lido antes, é abstrato.
**Depois dele você:** sabe usar o acervo, não só alimentá-lo.
`525 linhas · o mais longo, e o que mais paga`

### 4 · [PAINEL.md](PAINEL.md) — acompanhar pelo navegador

**Responde:** as seis telas, o gestor de oito regras, o chat sobre o acervo, a
configuração pela interface, autenticação e o serviço systemd.
**Por que aqui:** o painel é uma **segunda face** do que você já aprendeu a fazer
pela linha de comando. Antes dos passos 1–3, os números dele não significariam nada.
**Depois dele você:** vê estado, agenda e acervo sem abrir terminal.
`457 linhas · abre com um "Como usar" numerado, se quiser só o prático`

### 5 · [ARQUITETURA.md](ARQUITETURA.md) — como funciona por dentro

**Responde:** o caminho que um link percorre, quem faz o quê, onde o estado mora.
**Por que aqui:** agora você já **viu** o comportamento. Ler a arquitetura antes é
decorar nome de módulo; ler depois é reconhecer o que você observou.
**Depois dele você:** sabe em que arquivo mexer para mudar cada coisa.
`203 linhas · leitura curta e densa`

### 6 · [DECISOES.md](DECISOES.md) — por que é assim

**Responde:** as escolhas não óbvias e o que custaria desfazer cada uma — sem banco
de dados, agendador próprio em vez de cron, marcar em vez de apagar, painel no host,
gestor por regras e não por LLM.
**Por que aqui:** decisão só é compreensível depois de você conhecer a alternativa
que ela recusou — e a alternativa aparece no passo 5.
**Depois dele você:** para de propor mudanças que já foram medidas e rejeitadas.
`764 linhas · o maior; feito para consulta por título, não leitura corrida`

### 7 · [DESENVOLVIMENTO.md](DESENVOLVIMENTO.md) — mexer no código

**Responde:** as sete formas de rodar os testes, o que cada arquivo de teste cobre,
cobertura, e as convenções (teste em português, nomeado pelo comportamento que
garante).
**Por que aqui:** é o primeiro documento que pressupõe **todos** os anteriores.
**Depois dele você:** consegue estender o projeto sem quebrar invariante.
`286 linhas · leitura média`

### 8 · [PADRAO_SAIDA_DE_TESTES.md](PADRAO_SAIDA_DE_TESTES.md) — o padrão portável

**Responde:** estilo, formato e método da saída do `./testar.py`, para refazer em
**outro** projeto.
**Por que por último:** é o único documento que não é sobre este projeto. Pular não
deixa buraco.
`315 linhas · independente do resto`

---

## Atalhos, se você não quer a trilha inteira

| Seu caso | Leia só |
|---|---|
| **só quero usar** o que já está rodando | 0 → 3 → 4 |
| **vou instalar e operar** | 0 → 1 → 2, e o 4 quando quiser painel |
| **vou mexer no código** | 5 → 6 → 7, assumindo que já sabe usar |
| **estou depurando agora** | 2 (a seção de problemas) e o painel em `/execucoes` |
| **quero entender uma decisão** | 6, por título — não precisa do resto |
| **vou copiar o runner de teste** | 8 |

## Ou por tarefa, se você já conhece o projeto

| Quero… | Leia |
|---|---|
| **usar a nota atômica, triar, migrar, gerar a síntese semanal** | **[LINKS.md](LINKS.md)** |
| entender como funciona por dentro | [ARQUITETURA.md](ARQUITETURA.md) |
| configurar o Discord, o vault, o LLM | [CONFIGURACAO.md](CONFIGURACAO.md) |
| rodar, agendar, recuperar histórico, resolver problema | [OPERACAO.md](OPERACAO.md) |
| saber **por que** algo foi feito daquele jeito | [DECISOES.md](DECISOES.md) |
| mexer no código, rodar teste, estender | [DESENVOLVIMENTO.md](DESENVOLVIMENTO.md) |
| acompanhar pelo navegador o que rodou e o que está no acervo | [PAINEL.md](PAINEL.md) |
| refazer em outro projeto a apresentação dos testes | [PADRAO_SAIDA_DE_TESTES.md](PADRAO_SAIDA_DE_TESTES.md) |

---

## Os onze comandos

| Comando | O que faz | Gasta LLM? | Fala no Discord? |
|---|---|---|---|
| `dlb sync` | captura links novos, classifica e escreve no vault | sim, 1× por dia de links | só reação |
| `dlb backfill` | mesma coisa, mastigando o passado em fatias | sim | só reação |
| `dlb migrar` | cria a nota atômica do que já está no vault | **não** | não |
| `dlb sintese` | o que a semana foi: temas, conexões, perguntas | sim, 1× por período | não |
| `dlb buscar` | pergunta ao acervo, pela linha de comando | sim, 1× por pergunta | não |
| `dlb responder` | vigia `?buscar` no canal e responde | sim, por pergunta | **sim** |
| `dlb ressurgir` | 3 links antigos com o resumo | **não** | **sim** |
| `dlb temas` | que conceitos viraram tema — relatório | não | não |
| `dlb semana` | apanhado bruto dos últimos dias | não | não |
| `dlb doctor` | confere configuração, vault e acesso | não | só com `--testar-envio` |
| `dlb painel` | painel web de acompanhamento em `127.0.0.1` | não | não |

Detalhe de uso: são executados como `python3 -m dlb <comando>` **a partir da raiz
do projeto** (não há pacote instalado), ou `./dlb.sh dev <comando>` pelo container,
que resolve o diretório sozinho.

## O problema que ele resolve

O canal do Discord é ótimo para **capturar** — link entra sem fricção, do celular,
no meio de outra coisa. E é péssimo para **consultar**: uma semana depois é uma
pilha de URLs sem contexto, e você não lembra por que salvou nenhuma.

Este pipeline transforma a pilha em trilha. O link vira uma nota que diz o que
aquilo era e por que importava, no lugar onde você já procura as coisas.

## Três invariantes que valem saber de cara

1. **A camada `10_FONTES/` do vault é imutável.** O pipeline escreve a captura
   crua uma vez e nunca reescreve (exceto com `--force`, e é decisão sua).
2. **Ele não cria página em `20_WIKI/`.** Compilar conceito é trabalho curado;
   o pipeline só aponta candidatos. Ver [DECISOES.md](DECISOES.md).
3. **Ele nunca apaga nada** — nem no vault, nem no Discord. Marca com reação.

E uma quarta, da camada mais nova: **a nota de link é sua.** O pipeline a cria e
nunca a reescreve sem `--force`; `triagem`, `nota` e tudo abaixo de
`## Minhas notas` sobrevivem até com `--force`. Ver [LINKS.md](LINKS.md).
