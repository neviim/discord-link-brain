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

## O que ainda não foi resolvido

- **`cli.py` sem teste** (615 linhas, 0%). Testá-lo exige encenar Discord, LLM e
  vault ao mesmo tempo. Boa parte do risco está coberta pelas peças embaixo, mas
  a orquestração em si não está.
- **Cloudflare e X/Twitter** continuam bloqueando a leitura. O link entra com
  resumo baseado só no seu comentário.
- **Site renderizado por JavaScript** devolve texto vazio; os metadados `og:`
  assumem sozinhos.
