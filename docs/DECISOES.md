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

## O que ainda não foi resolvido

- **`cli.py` sem teste** (329 linhas, 0%). Testá-lo exige encenar Discord, LLM e
  vault ao mesmo tempo. Boa parte do risco está coberta pelas peças embaixo, mas
  a orquestração em si não está.
- **Cloudflare e X/Twitter** continuam bloqueando a leitura. O link entra com
  resumo baseado só no seu comentário.
- **Site renderizado por JavaScript** devolve texto vazio; os metadados `og:`
  assumem sozinhos.
