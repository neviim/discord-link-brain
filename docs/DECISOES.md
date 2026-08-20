# Decisões

As escolhas não óbvias e o raciocínio por trás. Registradas porque daqui a seis
meses "por que isso é assim?" custa mais caro que escrever agora.

Cada uma diz o que **custaria** desfazer — é isso que separa decisão de opinião.

---

## Marcar no Discord, nunca apagar

**Alternativa considerada:** apagar a mensagem do canal depois de arquivar.

Apagar é irreversível e destrói a captura crua. Se uma classificação sair ruim,
se o `ESQUEMA` do vault mudar, ou se você quiser reprocessar com outro modelo, a
fonte precisa existir. É o mesmo motivo pelo qual `10_FONTES/` é imutável: o
material bruto é o que permite refazer o trabalho interpretado.

A reação dá o mesmo sinal visual — você bate o olho no canal e sabe o que já
está no Obsidian — sem custo de volta.

**Dois emojis, dois estados:** ✅ "entrou no vault agora" e 📚 "você já tinha
isto". Para o pipeline as duas valem igual; a distinção é para o olho humano.

**Desfazer custa:** nada. É uma linha de configuração (`MARK_PROCESSED=0`).

---

## Por que não um índice em SQLite

**A pergunta é justa:** consultar um banco não seria mais rápido que ler
arquivos? É. Medido, lendo todas as URLs arquivadas:

| Capturas | Links | Tamanho | Ler arquivos | SQLite |
|---|---|---|---|---|
| 41 | 82 | 14 KB | **1,7 ms** | 0,3 ms |
| 365 | 1.825 | 296 KB | **12,8 ms** | 1,6 ms |
| 3.650 | 73.000 | 11 MB | **225 ms** | 41 ms |

O SQLite ganha por ~5×. E não importa: uma chamada de LLM leva **30 a 60
segundos**, e cada página aberta, centenas de milissegundos. Na linha de baixo —
que no ritmo real (~85 links/ano) levaria um século — os 225 ms são 0,5% de
*uma* classificação.

**O custo do banco não é performance, é correção.** Ele vira uma segunda fonte
de verdade que pode divergir do vault: apagar uma nota no Obsidian deixaria o
banco afirmando que o link está arquivado, e ele nunca mais voltaria. Lendo
`10_FONTES/`, o vault **é** a verdade.

Se um dia pesar, o passo certo antes do banco é um cache por `mtime` — reler só
as capturas que mudaram. Mantém o vault como autoridade com muito menos peça
móvel.

**Desfazer custa:** implementar o índice, mais uma rotina de reconciliação com o
vault que hoje não existe.

---

## Agendador próprio em vez de cron

Um laço em `sh` é o PID 1 do container, e não `cron`. Três motivos concretos:

1. **cron não herda o ambiente do container** — a dor clássica; as variáveis do
   compose simplesmente não chegam ao job;
2. **os logs não vão para o stdout do PID 1** — `docker logs` ficaria vazio;
3. **o SIGTERM do `docker stop` não chega ao job** — a parada esperaria o
   timeout de 10s. Com o laço próprio e trap, a parada leva ~0,2s.

**Desfazer custa:** trocar 40 linhas de shell por um crontab e resolver os três
problemas acima de outro jeito.

---

## Uma chamada de LLM por dia, não por link

Mais barato, e o contexto do dia inteiro melhora a classificação de cada item —
o modelo percebe que dois links são o mesmo vídeo, que três são da mesma
newsletter, que o comentário de um explica o outro.

**Limite conhecido:** um dia com centenas de links pode estourar o contexto. Na
prática não chega perto — o pico observado foi 14.

**Desfazer custa:** mais chamadas, mais dinheiro, classificação pior.

---

## O pipeline não cria página em `20_WIKI/`

Compilar conceito é trabalho curado, não automatizável sem perder qualidade. Os
conceitos que o LLM identifica saem de duas formas:

- conceito que **já tem página** → wikilink de verdade (`[[Obsidian]]`)
- conceito que **ainda não tem** → texto `Nome *(candidato)*` numa lista marcada
  com `#acao/expandir`

Criar stub vazio derrubaria dois invariantes do `ESQUEMA` de uma vez: o 4
(nota-fantasma) e o 2 (link não-resolvido não conta como aresta do grafo).

**Desfazer custa:** um vault cheio de página vazia, que é pior que vault sem
página.

---

## Canonizar URL em vez de deduplicar por conteúdo

O botão de compartilhar do YouTube posta duas formas do mesmo vídeo
(`/shorts/ID` **e** `/watch?v=ID`); o X manteve `twitter.com` vivo ao lado de
`x.com`. Ambos entravam duas vezes no vault.

A alternativa seria comparar o conteúdo das páginas — caro, frágil, e falha
justamente onde a leitura falha. Canonizar a URL resolve na origem, é
determinístico e testável.

**Limite:** só cobre os padrões conhecidos. Outro site com duas URLs para o
mesmo conteúdo passa batido até alguém notar.

**Desfazer custa:** nada; é uma função pura com teste.

---

## oEmbed para YouTube, e não raspagem

A página `/watch` responde **429 para requisição de servidor** de forma
consistente — redireciona para `google.com/sorry`. Não é limitação de código, é
anti-bot.

O oEmbed (`/oembed?url=…&format=json`) devolve título e canal, sem API key e sem
bloqueio. A tentativa de pegar a descrição na página segue no código como bônus:
quando passa, é lucro.

**Consequência aceita:** vídeo sem descrição vira nota baseada em título e
canal, e o prompt manda o modelo **dizer que não viu o conteúdo** em vez de
inventar.

---

## Vault somente-leitura no modo dev

`--dry-run` funciona inteiro com o vault montado `:ro`, porque a escrita
já é curto-circuitada. O que o `:ro` protege é o comando errado — o `sync` sem
`--dry-run` digitado por engano no terminal de desenvolvimento.

**Desfazer custa:** nada; `--rw` libera quando você quer.

---

## O container roda com o UID do host

Ele escreve num vault que vive no host. Rodando como root, todo arquivo escrito
no seu Obsidian sairia com dono `root` — e você descobriria isso ao tentar
editar a nota.

Por isso o UID/GID são argumentos de build **e** parâmetro de run, resolvidos
pelo `./dlb.sh` a partir do seu `id`.

---

## Montar o `~/.claude` no container

Com `LLM_BACKEND=claude-cli` o container recebe o binário do `claude` (somente
leitura) e a pasta `~/.claude`, onde está o `.credentials.json`. É o que permite
classificar **sem API key**.

**Consequência aceita:** o container enxerga seu `~/.claude` inteiro — sessões,
histórico, plugins. Mesma máquina e mesmo usuário, mas é uma superfície maior
que o necessário.

**Alternativa disponível:** `LLM_BACKEND=anthropic` (+ `ANTHROPIC_API_KEY`) ou
`none`. Nesses casos o wrapper não monta nada do seu login, e o lugar do binário
fica com um script placeholder que explica o que faltou.

---

## Uma nota atômica por link, em `50_LINKS/`

**Alternativa considerada:** continuar só com o bullet na nota diária.

Era o desenho original, e resolvia a captura. O que ele não resolve é a
recuperação: 70 links espalhados em 26 notas de `40_DIARIO/` não têm backlink
próprio, não aparecem no grafo, não são filtráveis e não têm estado. Memória
write-only — o link entra e nunca sai.

Com nota atômica cada link vira nó de primeira classe, e é o que torna possível a
triagem, as views do Bases e (depois) a busca.

Precisou de emenda no `90_META/ESQUEMA.md` **antes** do código: camada nova na
tabela, `tipo: link`, e as propriedades `triagem`, `dominio`, `categoria` e
`nota`. A regra é do ESQUEMA, não desta ferramenta.

**Desfazer custa:** apagar as notas e ligar `LINK_NOTES=0`. O dado não se perde,
porque `10_FONTES/` continua imutável e é dele que tudo se refaz. O que fica
perdido é a triagem que você tiver feito à mão.

---

## `triagem` é propriedade própria, não `status` nem `#acao/`

**Alternativas consideradas:** `status: inbox` (o que o `plan/plan.md` propunha) e
`#acao/ler`.

`status` é maturidade da nota; `triagem` é o que você fez com o material. São
eixos ortogonais: uma nota de link `maduro` (cartão completo, nada a acrescentar)
pode estar `descartado`, e uma `semente` pode estar `aplicado`. Empilhar os dois
em `status` faria o vocabulário de maturidade mentir para toda nota que não é
link — e o vault tem 14 páginas de wiki que dependem dele.

`#acao/ler` seria idiomático, mas deixaria dezenas de `#acao/` pendurados por
meses, contra a regra do próprio ESQUEMA, e não teria casa para `aplicado` e
`descartado`, que são estados terminais e não trabalho a fazer.

**Desfazer custa:** um passe de substituição em N notas, mais a emenda inversa do
ESQUEMA — e mais do que parece, porque a propriedade é o filtro de quatro das
cinco views do Bases.

---

## `10_FONTES/` continua a única autoridade do dedup

**Alternativa considerada:** `archived_urls()` passar a ler `50_LINKS/` também.

Seria natural: a nota de link já grava a URL como `<url>` em linha própria, na
forma exata que o regex reconhece. Mas mudaria o significado de apagar uma nota:
o link voltaria a ser capturado. E apagar é exatamente o que você faz com o que
não quer — o estado certo para isso é `triagem: descartado`, que mantém o arquivo.

Pior, duas autoridades divergem no primeiro `--force`, e passa a existir um
"arquivado" que depende de qual das duas você leu. É o mesmo argumento que
rejeitou o índice em SQLite: o custo não é performance, é correção. `50_LINKS/` é
**derivado**; `10_FONTES/` é **fonte**.

**Desfazer custa:** quase nada em código — uma linha de `glob`. A decisão é
semântica, não técnica.

---

## O diário aponta para a nota, não repete o conteúdo

**Alternativa considerada:** manter o bullet rico com resumo, por-que-importa e
conceitos, e só acrescentar o wikilink.

Custo zero de código, e teria preservado dois testes. Mas seriam duas cópias da
mesma prosa, que divergem no primeiro `--force` — e o ESQUEMA proíbe manter a
mesma verdade em dois lugares. O `40_DIARIO/` é, pela definição do próprio
ESQUEMA, "registro cronológico, não conhecimento estruturado".

**A URL continua na linha do bullet**, dentro de `[abrir](url)`, e isso não é
decoração: `_append_diario` detecta o que já está na seção com
`re.findall(r"\]\((https?://[^)\s]+)\)", secao)`. Trocar por `<url>` ou por
wikilink puro faz o segundo lote do dia ser escrito duas vezes ou sumir em
silêncio, com a mensagem já marcada como arquivada no Discord — o bug que
`TestLotePosteriorNoMesmoDia` existe para impedir. Há um teste dedicado a essa
regressão na forma nova.

Quando não há nota atômica (`LINK_NOTES=0`), o bullet continua rico — senão esse
conteúdo não existiria em lugar nenhum.

**Desfazer custa:** reescrever a seção de N dias.

---

## `migrar` não chama LLM

**Alternativa considerada:** reclassificar os 26 dias de histórico.

Tudo que o modelo produziu já está escrito nos bullets do diário, e tudo que é
cru está na fonte. Reclassificar gastaria 26 chamadas para produzir um resultado
**diferente** do que você já leu — e nada garante que melhor.

Medido antes de escrever: 70 de 70 bullets casam com o parser, 70 de 70 cruzam
com a nota de fonte do dia, zero avisos. A falha mais provável era o resumo com
mais de um ` — ` (24 dos 70, 34%); a regex ancora no fechamento do link em vez de
dividir pelo separador.

**Desfazer custa:** nada. É rodar `sync --day X --force` num dia específico.

---

## A migração insere referência no bullet antigo

**Alternativa considerada:** deixar os 26 diários intactos.

Era o plano, e estava errado. `parte-de: "[[MOC - Captura Discord]]"` é aresta
**saindo**, e o invariante 3 do ESQUEMA exige aresta **entrando** ou constar de um
MOC. Sem tocar o diário, as 70 notas migradas nasceriam órfãs de uma vez.

A inserção é uma linha `Nota: [[...]]` sob o bullet — puramente aditiva, então
nada humano é perdido e não existe caminho de código que apague seção. Reescrever
o bullet rico em magro também resolveria, mas é a única operação capaz de
destruir conteúdo, e fica fora desta fase.

**Desfazer custa:** apagar as linhas `Nota: [[` — um `sed`. `--no-diario`
desliga a inserção, ao preço de notas órfãs.

---

## A nota de link não é reescrita na segunda execução

**Alternativa considerada:** sobrescrever sempre, como `write_fonte` faz sem
`--force`.

A nota de link é o único artefato do vault que o humano **edita por desenho**:
`triagem`, `nota`, e o que você escrever abaixo de `## Minhas notas`.
Sobrescrever apagaria justamente o trabalho que a camada existe para viabilizar.

Com `--force` o miolo gerado é refeito e o que é seu sobrevive: triagem, avaliação,
maturidade, relações acrescentadas à mão, e tudo abaixo da fronteira. Nota escrita
à mão que não tem a fronteira não é tocada nem com `--force`, porque não há como
distinguir o gerado do seu.

**Desfazer custa:** nada (`--force`). O inverso custa o seu trabalho de triagem.

---

## A correção do link da mensagem mora na camada derivada

**Alternativa considerada:** corrigir os `jump_url` errados em `10_FONTES/`.

Por um tempo o pipeline gravou todo link de mensagem como
`https://discord.com/channels/@me/...`, porque `GET /channels/{id}/messages` não
devolve `guild_id` por mensagem e o `cli.py` não passava o do canal. Num canal de
servidor esse link não abre. Medido: 70 de 70 links, em 30 notas de fonte.

Corrigido na origem (`DiscordClient.guild_id()` + fiação nos dois comandos), então
captura nova sai certa. Para o histórico, a correção vai para `50_LINKS/` via
`migrar --force` + `DISCORD_GUILD_IDS`, e **a fonte fica como capturou**.

Não é meio-conserto: é o padrão que o próprio ESQUEMA prescreve no invariante 9 —
*"corrigiu entendimento? Corrija a página derivada, nunca a fonte"*. O valor certo
é integralmente derivável de `(servidor, canal, mensagem)`, e a fonte guarda os
dois últimos. Reescrever `10_FONTES/` para consertar um campo derivável abriria a
imutabilidade por conveniência, e a próxima exceção sai mais barata que esta.

**Desfazer custa:** tirar `DISCORD_GUILD_IDS` do `.env` e rodar `migrar --force` —
os links voltam a ser o que a fonte diz.

---

## A síntese periódica roda em outro backend que a classificação

**Alternativa considerada:** usar `claude-cli` para tudo, como já era.

O `docker/scheduler.sh` documenta no próprio arquivo a tensão entre a grade de 6h
e a janela de uso de 5h da assinatura Claude. A classificação diária é o gasto que
não pode esperar — ela é o caminho crítico da captura. A síntese é uma chamada por
semana, e não competir por aquela janela é de graça.

`SINTESE_BACKEND=codex` por padrão. O `codex exec --output-schema` tem uma
vantagem técnica de brinde: o modelo devolve JSON válido sem cerca e sem
tagarelice, então o parsing defensivo quase não trabalha. Quase — a validação
continua, porque `id` fora da faixa e URL inventada não são problema de formato.

Os runners ficaram em `sintese.py` em vez de compartilhados com `classify.py`:
lá o `SYSTEM_PROMPT` está embutido na função, e parametrizá-lo quebraria os 27
testes que trocam `_run_claude_cli` como global do módulo. Duplicação consciente
de ~40 linhas de `subprocess`; quando aparecer o terceiro consumidor, é a hora de
extrair um `llm.py`.

**Desfazer custa:** uma linha de configuração (`SINTESE_BACKEND=claude-cli`).

---

## A síntese cita por `id`; o link quem monta somos nós

**Alternativa considerada:** deixar o modelo escrever o markdown com as URLs.

O payload que vai para o modelo **não tem a URL** — só `id`, título, resumo,
por-que-importa e conceitos. Assim ele não tem como emitir um endereço, e todo
link do texto final é montado a partir dos nossos dados. `id` fora da faixa é
descartado em silêncio.

Isso não é desconfiança de estilo: é a diferença entre "o prompt pede para não
inventar link" e "inventar link é estruturalmente impossível". A primeira falha em
silêncio e vira nota com URL que não existe.

Na prática o modelo ainda escreveu "Links: 6, 7" no fim de uma frase, apesar da
instrução — número interno não diz nada a quem lê a nota. Há uma limpeza para
isso, e um teste.

**Desfazer custa:** nada, mas não há por que: o caminho custa 5 linhas e fecha uma
classe inteira de erro.

---

## Só o miolo entre as cercas é regenerado

**Alternativa considerada:** reescrever a nota inteira a cada execução.

A nota de síntese tem `## Minhas notas` e a cerca
`<!-- dlb:sintese hash=… -->` … `<!-- /dlb:sintese -->`. Regenerar troca só o que
está entre as cercas; o que você escrever fora sobrevive, mesmo princípio do
`SECTION_MARK` no diário. Nota que alguém escreveu à mão, sem a cerca, **recebe** o
bloco em vez de ser sobrescrita.

O `hash` na cerca cobre URL e resumo do período. Sem ele, o agendador gastaria uma
chamada de 30–60s por execução para regenerar uma semana que não mudou.

**Desfazer custa:** nada; `--force` ignora o hash.

---

## Pseudo-comando por polling, não slash command

**Alternativas consideradas:** gateway e endpoint de Interactions.

**Gateway** exigiria uma biblioteca de websocket — `discord.py` traz `aiohttp`,
`multidict`, `yarl`, `frozenlist`, `aiosignal`, `attrs` para um projeto cujo
runtime inteiro é `requests` + `PyYAML`. E exigiria um processo que mantém conexão
com estado: heartbeat, token de resume, reconexão. É o oposto do "roda um programa
curto, confere o código de saída, sai" que o `scheduler.sh` assume — e torna o
healthcheck por heartbeat sem sentido, porque o processo pode estar de pé com a
conexão morta em silêncio.

**Interactions por HTTP** exigiria URL pública com TLS, verificação ed25519
(PyNaCl, extensão em C), um servidor ouvindo, e registro de comando. O custo
decisivo não é a dependência: **hoje esta máquina só faz conexão de saída, e isso
abriria a primeira porta de entrada na máquina que guarda o seu `~/.claude`.**

Polling reaproveita `iter_messages()` inteiro — paginação, 429, 5xx já resolvidos e
testados — e o mesmo idioma de idempotência que o projeto já usa: a reação do
próprio bot como segunda barreira, com `me: true` para reação de humano não contar.
Zero dependência nova, zero superfície de entrada.

O custo é honesto e pequeno: latência de um ciclo, e não é slash command na UI. A
60s, e com a chamada de modelo levando 30-60s, **o polling não é o gargalo** — o que
elimina a principal vantagem das outras duas.

**Consequência aceita, e a que mais confunde na prática:** sem gateway, o Discord
marca o bot como **Offline** na lista de membros — para sempre, mesmo com o
responder rodando e respondendo. "Online" ali significa "tem websocket aberto", e
não "está funcionando". O sinal de vida é a reação ⏳ aparecendo na pergunta.

**Desfazer custa:** uma dependência de websocket e um segundo processo sempre de
pé; para Interactions, também uma entrada pública que hoje não existe.

---

## Falar é opt-in, e as marcas são dois vocabulários

`DISCORD_POST_ENABLED=0` por padrão. A assimetria justifica: escrita no vault é
idempotente e corrigível reexecutando; mensagem que outro humano leu, não é. A
permissão vai de 66624 para 68672 (soma *Enviar mensagens*).

*Enviar links incorporados* ficou de fora de propósito: toda URL sai como `<url>`,
que suprime a prévia. Três links viriam como parede de cartões, e a permissão a
menos é de graça.

**E as marcas do responder são um conjunto separado** de `cfg.marcas`:

    cfg.marcas            = (✅, 📚)      "o sync arquivou"
    cfg.marcas_responder  = (💬, ⏳, ❌)   "o responder resolveu"

Se se misturassem, uma pergunta que contivesse um link seria pulada pelo `sync`
para sempre, e uma mensagem legitimamente arquivada não poderia mais ser
perguntada. A colisão é silenciosa e está a um typo de distância, então
`validar_marcas()` levanta erro na configuração e o `doctor` mostra.

`allowed_mentions: {"parse": []}` em todo POST: o bot **não consegue** mencionar
ninguém, nem `@everyone`, mesmo que a pergunta de alguém contenha o texto. É mais
barato desligar a capacidade do que filtrar conteúdo.

**Desfazer custa:** uma linha de configuração.

---

## Busca sem embeddings, e o gatilho medido para mudar

**Alternativa considerada:** Ollama + `nomic-embed-text` + cosseno.

Os números: 70 notas somam ~33 mil caracteres, uns 12 mil tokens. O corpus
**inteiro** cabe num prompt com folga de 10×, e mandar tudo tem recall
estritamente melhor que top-k por cosseno — não existe `k` para errar, nem limiar
de similaridade, nem chunk.

Embeddings custariam: instalar o Ollama (não está na máquina), baixar 275 MB, abrir
a rede do container, mais um cache com chave de invalidação, um reindexar e um GC —
para **descartar** 62 de 70 documentos que já caberiam. É um sistema pior, não
menor.

O ranqueamento léxico existe mesmo assim, e ordena para o corte por orçamento.
Hoje o corte nunca dispara; com 300+ notas a degradação é gradual e o caminho de
código é o mesmo.

**O gatilho é um número na tela, não julgamento:** `dlb doctor` imprime o corpus
contra `BUSCA_MAX_CHARS` (hoje 28%). Quando passar de 100%, ou quando a resposta
começar a não achar coisa que você sabe que está lá, escreva
`_ranquear_embeddings` com a mesma assinatura e acrescente um `elif`.

**Desfazer custa:** nada hoje — o ponto de troca já existe, e um ranqueador novo é
uma função mais um `elif`.

---

## O ressurgimento guarda estado em `.state/`, e isso não contradiz a decisão do SQLite

**Alternativas consideradas:** um campo no frontmatter da nota, e deduzir do
histórico do canal.

A decisão contra o índice em SQLite recusou uma **segunda autoridade para um fato
que o vault já possui** ("esta URL está arquivada?"): podia divergir, e a
divergência dava resposta *errada*. "Que links eu já ressurgi" é um fato sobre o
qual o vault não tem opinião nenhuma — não há de que divergir. É a mesma categoria
dos dois cursores, que a ARQUITETURA já lista como estado do pipeline. E o pior
caso de perder o arquivo é **um** link repetido, que não é nem erro.

Campo no frontmatter foi rejeitado por escrever no vault para registrar um não-fato,
e por sujar o Obsidian Sync a cada execução. Deduzir do canal seria reparsear as
próprias mensagens antigas — histórico de Discord não é índice consultável.

A semente do sorteio inclui o dia, então rodar duas vezes no mesmo dia escolhe os
mesmos links: idempotência de graça. Mas o histórico continua, porque semente
determinística sozinha não dá carência.

**Dois filtros que não são arbitrários:** `triagem: descartado` sai (você já disse
que não serviu), e link sem `porque_importa` fica para o fim — medido, esse campo é
vazio exatamente nos 16 links que o modelo não conseguiu descrever, e ressurgir
"conteúdo não identificado" é ruído, não memória. É preferência e não corte: se
sobrar só isso, melhor lembrar de algo do que de nada.

**Desfazer custa:** apagar `.state/ressurgir.json`.

---

## `temas` relata; não cria MOC

**Alternativa considerada:** criar o MOC quando o conceito passa do limiar.

Reafirma a decisão de não criar página em `20_WIKI/`, por um motivo novo: uma pasta
cheia de MOC vazio é **pior** que uma pasta sem MOC nenhum. O `MOC - Índice` do
vault diz isso com outras palavras — "MOC não nasce, emerge". O comando emite um
bloco pronto para colar; a prosa é a parte que não dá para automatizar sem perder o
ponto.

Três reinterpretações do `plan/plan.md`, que enquadrava isto por contagem de tag:

- **Tag de assunto é proibida** pelo vault. O sinal real são os `conceitos`, que o
  pipeline já emite como `[[X]]` ou `X *(candidato)*`.
- **O limiar é 8, não 15.** É o número do próprio `MOC - Índice`. Onde os dois
  discordam, vale o do vault.
- **Os líderes brutos são plataformas, não temas.** Medido: Instagram 7, YouTube 6,
  contra n8n 4 — onde o link mora, não do que trata. Sem stoplist o relatório é
  ruído. O descartado aparece no relatório, nunca sai em silêncio.

Plural é colapsado **observando, não adivinhando**: `X` junta com `Xs` só quando as
duas formas aparecem no acervo. Cortar `s` por tamanho de palavra fazia `SaaS` virar
`saa` — foi um teste que pegou.

**Desfazer custa:** nada. O caminho contrário custaria limpar MOCs vazios à mão.

---

## Dois containers: um lento e um rápido

**Alternativa considerada:** um laço só, despachando o prazo mais próximo.

O agendador roda de 6 em 6 horas e contém chamadas de LLM de 30-60s mais buscas de
página de até 10s cada. Num laço único, a latência de uma resposta a `?buscar`
ficaria **ilimitada** — uma pergunta às 12:00:05 poderia esperar um backfill
inteiro. E latência limitada é a feature.

É seguro porque os dois processos escrevem arquivos **disjuntos** em `.state/`:

| Arquivo | Escrito por |
|---|---|
| `<canal>.json` | `sync`, `backfill` |
| `responder-<canal>.json` | `responder` |
| `ressurgir.json` | `ressurgir` |
| `heartbeat` | o agendador |
| `heartbeat-responder` | o responder |

Nenhum arquivo tem dois escritores — é esse o invariante. Por isso o cursor do
responder é **arquivo separado** e não um campo a mais em `ChannelState`: um campo
criaria read-modify-write concorrente com perda silenciosa. E o `Store.save` passou
a ser atômico (`tmp` + `os.replace`), porque `write_text` deixa uma janela de
arquivo truncado que o `load` interpretaria como "recomece do zero".

O ressurgimento fica no laço lento justamente porque **pode** esperar.

O healthcheck do responder não precisou de código: o `HEALTHCHECK` embutido já lê
`DLB_HEARTBEAT` e `DLB_HEALTH_TOLERANCE`, e o compose só aponta para outro arquivo
com outra tolerância.

**Desfazer custa:** juntar os laços e aceitar latência ilimitada.

---

## Histórico de execução: um diretório por run, não um arquivo de log

**O problema:** o que o sistema fez ontem só existia no log do Docker. Ele
rotaciona, não é consultável e some quando o container é recriado. Qualquer
pergunta do tipo "rodou hoje?", "quantos links entraram na semana?" ou "por que
não entrou nada de terça?" não tinha onde ser respondida.

**Alternativa considerada:** um `runs.jsonl` com uma linha por execução. É o
desenho óbvio e o errado aqui. O laço do container, um `sync` disparado à mão no
host e (adiante) um run disparado pelo painel são **três processos anexando ao
mesmo arquivo**. Escrita concorrente sem lock intercala linha, e o defeito
aparece como "o histórico está estranho", meses depois, sem como reproduzir.

    .state/runs/20260820T001503Z-sync-4127/summary.json

Diretório por run torna a corrida **impossível por construção** em vez de
administrada: um escritor por diretório, e o nome carrega o pid de quem escreve.
É a mesma regra que `state.py` já aplica ao separar o cursor do `responder` do
cursor do `sync`.

O nome começa com o instante em ISO compacto, então ordenação lexicográfica é
ordenação cronológica: listar as vinte últimas execuções não abre arquivo nenhum
para decidir a ordem.

Um diretório para um arquivo só parece exagero hoje. É o ponto: quando o passo a
passo estruturado (`events.jsonl`) entrar, ele entra ao lado do `summary.json`
sem migração e sem mudar quem lê o resumo.

**Poda:** 500 runs, fixo no código. Quatro comandos por ciclo, quatro ciclos por
dia passam de 5 mil diretórios por ano. Não é configurável de propósito — é
detalhe do histórico, não política que alguém precise ajustar.

**Desfazer custa:** nada externo depende do formato ainda. Trocar por banco
custaria a reconciliação que a decisão do SQLite acima já descreve.

---

## O lock recusa em vez de esperar

**O problema:** duas execuções de `sync` (ou `sync` e `backfill`) sobre o mesmo
canal fazem read-modify-write no mesmo `.state/<canal>.json`. A atualização de
uma se perde, e o efeito não é perder trabalho — é **um trecho do canal deixar de
ser lido**, que a deduplicação por URL nunca recria, porque ela só sabe do que
foi lido.

Isto nunca aconteceu enquanto o único disparador era o laço do container, que é
sequencial. Passa a poder acontecer no minuto em que existir um botão.

`flock` em `.state/locks/canais.lock`, tomado dentro do `main()` — **não** no
código que dispara job. Fosse no disparador, o `sync` do container não o tomaria,
e a colisão que o lock existe para impedir seria exatamente a que passaria. É
também o motivo de o painel disparar `python3 -m dlb sync` em subprocess em vez
de chamar `cmd_sync()`: um só caminho, uma só proteção.

**Recusa, não espera.** Um `sync` pode levar minutos; uma requisição de painel
pendurada nisso parece travamento, e enfileirar dois syncs seguidos não vale nada
— o segundo não teria o que ler. A recusa sai com código **3** (distinto de 1,
que é falha de verdade) e a mensagem diz quem está com o lock e desde quando.

O metadado em `canais.json` é conveniência, não autoridade: processo morto de
`kill -9` deixa o arquivo para trás. A verdade é o `flock` — se ele está livre, o
metadado é lixo. É assim que "rodando" se distingue de "morreu no meio" sem
inventar timeout.

`responder` fica fora do lock: cursor próprio, escritor próprio, e roda de 60 em
60 segundos. Travá-lo junto do sync deixaria o bot mudo durante um sync longo, de
graça.

**Vale em bind mount local; não vale em NFS.** Se `.state` migrar para rede, este
desenho quebra — o aviso está no topo de `dlb/journal.py`.

**Desfazer custa:** nada. Remover o lock devolve o comportamento de hoje.

---

## O painel roda no host, e não dentro do container

**Alternativa considerada:** um serviço a mais no `docker-compose.yml`, ao lado do
`prod` e do `responder`. É o desenho que parece consistente com o resto — e o
errado.

O painel precisa poder disparar `docker compose up` (é o único jeito de aplicar
`.env` novo — `restart` não relê, ver a decisão acima). De dentro de um container,
isso exige montar `/var/run/docker.sock`, o que é **root no host**. Por um botão
de conveniência, num processo que também vai ter chat de LLM lendo texto vindo da
internet. O container é isolamento; furá-lo para ganhar comodidade troca a única
garantia que ele dava.

No host o painel é um processo comum, com o usuário do Dotcom, que já pode rodar
`docker compose` porque é a máquina dele.

**Desfazer custa:** o socket do Docker montado, ou um agente de execução separado
com sua própria fronteira — mais peça do que o painel inteiro tem hoje.

---

## Índice derivado: permitido, com gatilho medido

O painel lê `50_LINKS/` do disco a cada requisição, com memo por
`(mtime_ns, size)` por arquivo. Sem índice. É o "passo certo antes do banco" que a
decisão do SQLite acima já apontava.

Números do acervo real (77 notas, 320 KB), medidos:

| Operação | Tempo |
|---|---|
| `corpus.ler_links()` — parse de 77 notas | **105 ms** |
| a mesma leitura pelo memo | **2,2 ms** |
| uma nota editada no Obsidian | relê 1 de 77 |
| página do painel inteira | ~3 ms |

**O que a medição corrigiu:** a tabela do SQLite acima mede leitura de URLs de
`10_FONTES/`, que é `1,7 ms` para 82 links. Ler o **acervo estruturado** é outra
operação — parse de YAML por nota — e custa 105 ms para 77 notas, ~60× mais. O
número da decisão antiga não vale para esta pergunta, e é o tipo de confusão que
faria alguém concluir que sobra folga onde não sobra.

Um índice em SQLite/FTS5 fica **permitido** — derivado, descartável,
reconstruível a partir de `50_LINKS/`, nunca autoritativo, nunca guardando
julgamento humano — quando **qualquer um** destes acontecer:

1. `corpus.ler_links()` passar de **150 ms** no acervo real, medido; ou
2. uma tela precisar de faceta ou paginação que o memo não sustente em um request; ou
3. `doctor` mostrar o prompt de busca encostando em `BUSCA_MAX_CHARS`.

Estamos a **65% do gatilho 1** com 77 notas. No ritmo de ~85 links/ano ele chega
em uns dois anos — mas chega, e quando chegar a porta está aberta e documentada em
vez de fechada por gosto.

**Desfazer custa:** enquanto não existir, nada. Depois de existir, a rotina de
reconciliação com o vault que a decisão do SQLite descreve.

---

## O que ainda não foi resolvido

- **`cli.py` testado pela metade** (1041 linhas, 68%). `sync` e `backfill` têm
  teste de ponta a ponta com dublês — inclusive a ordem escreve-depois-marca, que
  é onde link some em silêncio. Faltam `doctor` e o laço do `responder`.
  O `backfill` entrou depois de custar um `NameError` em produção: ele lia
  `guilds`, atribuída só dentro de `cmd_sync`, e nenhum teste chegava ao corpo do
  laço para descobrir. Cobertura de despacho não é cobertura de miolo.
- **Os runners de subprocess** (`classify`, `sintese`) ficam em ~70%: o que falta
  é encanamento de `subprocess`, e dublá-lo testaria o dublê.
- **Cloudflare e X/Twitter** continuam bloqueando a leitura. O link entra com
  resumo baseado só no seu comentário.
- **Site renderizado por JavaScript** devolve texto vazio; os metadados `og:`
  assumem sozinhos.
