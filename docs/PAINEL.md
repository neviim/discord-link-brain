# Painel

Acompanhar o que o sistema fez, o que está pendente e o que está no acervo — sem
abrir o log do Docker nem o `.state/` na mão.

Roda **no host**, não dentro do container. Precisa do `.env` do projeto (o mesmo
que a CLI usa) e de acesso de leitura ao vault.

> **Num clone novo, comece pelo painel.** Ele sobe **sem** `.env`, sem
> `DISCORD_TOKEN` e sem vault: é o editor da configuração, e exigir configuração
> válida para abrir o editor de configuração deixava o clone novo sem primeiro
> passo. Suba, vá em `/config`, preencha e salve — o `.env` é criado no primeiro
> salvamento.
>
> O que ele faz em vez de recusar é **dizer**: enquanto falta chave obrigatória, uma
> faixa no topo de **toda** tela nomeia o que falta, aponta o arquivo e diz a
> consequência. A faixa lê o arquivo a cada requisição, então ela muda no reload
> depois de você salvar.
>
> A tolerância acaba no painel. `sync`, `backfill`, `sintese` e `ressurgir`
> continuam recusando sem configuração válida, com código 2 — falar com o Discord
> sem token não é degradação graciosa, é erro. `dlb doctor` também abre sem
> configuração, por ser o comando que existe para dizer o que está errado.
>
> Uma consequência que vale saber: depois de salvar, **comando disparado pela tela
> já usa o valor novo** (roda em subprocess e relê o arquivo), mas as telas deste
> painel seguem com a configuração de quando ele subiu, até reiniciar. A segunda
> faixa avisa exatamente isso quando é o caso.

## Como usar

### 1. Na sua máquina, e nada mais

```bash
cd discord-link-brain                 # a raiz do projeto: é onde o `.env` mora
python3 -m dlb painel                 # http://127.0.0.1:8765
python3 -m dlb painel --porta 9000    # se a 8765 estiver ocupada
```

É o caminho mais curto e não pede senha: sem `PAINEL_TOKEN` no `.env`, o painel só
aceita subir em `127.0.0.1`, e quem alcança a porta já está na máquina.

Para parar: `Ctrl-C`. Fechar o terminal também derruba — é o que o serviço abaixo
resolve.

### 2. De outra máquina, pela tailnet

Duas chaves no `.env`:

```bash
PAINEL_TOKEN=<token forte>            # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
PAINEL_HOSTS=maquina,maquina.SEU-TAILNET.ts.net
```

`PAINEL_TOKEN` liga a autenticação; `PAINEL_HOSTS` são os nomes pelos quais o
painel aceita ser chamado — sem eles, acessar por `http://maquina:8765` recebe
**421**, porque a checagem de `Host` é o que fecha DNS rebinding.

Depois:

```bash
python3 -m dlb painel --host "$(tailscale ip -4)"
```

Da outra máquina, `http://maquina:8765`. Ele pede o token uma vez e guarda cookie.
O tráfego é WireGuard, então o token não viaja em texto puro na rede mesmo sem TLS.
**Fora de uma rede já criptografada, ponha um proxy com TLS na frente.**

### 3. Subindo junto com a máquina (recomendado)

Serviço de **usuário**, sem sudo. O arquivo vive no repositório, então precisa ser
**copiado** — sem a cópia, `systemctl --user restart dlb-painel` responde
`Unit dlb-painel.service not found`:

```bash
mkdir -p ~/.config/systemd/user
cp docker/dlb-painel.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dlb-painel
```

O serviço liga no IP da tailnet quando ele existe, e cai para `127.0.0.1` quando
não. Depois:

```bash
systemctl --user restart dlb-painel   # depois de atualizar o código
systemctl --user status dlb-painel
journalctl --user -u dlb-painel -f    # o log
systemctl --user stop dlb-painel
```

Duas coisas que o serviço **não** faz sozinho:

- **sobreviver ao logout** — precisa de `sudo loginctl enable-linger $USER`;
- **se atualizar** — depois de um `git pull` que mexa na unit, repita a cópia e o
  `daemon-reload`.

### 4. O de todo dia

| Quero | Vou em |
|---|---|
| ver se está tudo de pé | **Painel** — dois heartbeats, alertas do gestor, o ciclo |
| saber o que rodou e por que falhou | **Execuções** — e os quatro botões de rodar agora |
| conferir quando é o próximo sync | **Agenda** — e `.env` × container |
| achar um link, triar o acervo | **Acervo** — busca literal e o seletor de triagem |
| perguntar em linguagem natural | **Chat** — mesma resposta do `dlb buscar` |
| mexer numa variável | **Configuração** — salvar, aplicar, desfazer |

### Quando algo não abre

| Sintoma | Causa quase certa |
|---|---|
| `Address already in use` | já tem painel na porta — `pgrep -fa "dlb painel"` |
| `401` em tudo | `PAINEL_TOKEN` definido e sem sessão: entre pelo formulário |
| `421 Host não atendido` | acesse por nome que esteja em `PAINEL_HOSTS` |
| `403 origem cruzada` | o JSON da resposta traz `origin_recebido` e `nomes_aceitos` |
| banner diz "PAINEL_TOKEN vazio" mas o `.env` tem | processo velho no ar — reinicie |
| mudei o `.env` e nada mudou | é a faixa *salvo ≠ aplicado*: clique em aplicar |

## As sete telas

| Tela | Responde |
|---|---|
| **Painel** (`/`) | O **dashboard**: quanto tem no acervo, o ritmo dos últimos 30 dias, a triagem, as categorias, de onde vem, o que está esperando trabalho — e o estado agora, atualizado sozinho. |
| **Estado** (`/estado`) | A mesma informação em tabela, número por número: heartbeats, cursor de cada canal, o ciclo, as últimas execuções. |
| **Execuções** (`/execucoes`) | Uma linha por run, do mais recente para o mais antigo, com status, duração, contadores e erro. Filtra por comando. |
| **Agenda** (`/agenda`) | Qual dos três modos está valendo, quando é o próximo disparo, e o que cada um dos quatro comandos do ciclo vai fazer. |
| **Acervo** (`/acervo`) | As notas de `50_LINKS/`, com busca literal por título, resumo, domínio e conceito, e faceta por triagem. |
| **Chat** (`/chat`) | Pergunta em linguagem natural sobre o acervo, respondida pelo mesmo código que `dlb buscar`. |
| **Configuração** (`/config`) | As 55 chaves do `.env` em formulário, com validação, backup e o estado *salvo ≠ aplicado*. |

Cada tela tem um JSON equivalente, para `curl` e para script:

```bash
curl -s localhost:8765/api/estado     | jq .heartbeats
curl -s localhost:8765/api/execucoes  | jq '.execucoes[0]'
curl -s localhost:8765/api/agenda     | jq '.modo, .slots[].proximo'
curl -s localhost:8765/api/acervo     | jq '.total, .resumo.por_triagem'
curl -s 'localhost:8765/api/acervo?q=nostr&triagem=inbox&limite=20' | jq '.no_filtro'
curl -s 'localhost:8765/api/chat?q=o+que+eu+sei+de+nostr'             | jq -r '.resposta.texto'
```

## O dashboard, e por que ele é leve

Seis gráficos, **nenhuma biblioteca**. O SVG é montado no servidor e o navegador só
desenha. Medido no acervo real (102 notas): **7,5 ms** por requisição quente, **18 KB**
de HTML, 74 dicas nativas de `<title>`.

Três camadas de economia:

| Camada | Custo medido |
|---|---|
| memo do acervo por `(mtime_ns, size)` | 138 ms na 1ª leitura, **2,2 ms** depois |
| uma passada para todos os agregados | 5,3 ms |
| cache por versão do acervo | **0,31 ms** quando nada mudou |

A versão é `(quantidade, maior mtime_ns)`: muda quando nota entra, sai ou é editada,
e em mais nada. É o que faz o tempo real custar quase zero.

### O tempo real que não consulta o servidor

Duas classes de número, dois tratamentos:

- **o que muda a cada segundo** — idade do heartbeat, contagem até o próximo disparo,
  tempo de pé do painel — é aritmética de relógio **no navegador**, a partir de um
  instante base. Zero requisição. Pedir ao servidor para ele responder "1 s mais que
  antes" seria gastar rede para fazer subtração;
- **o que muda quando o vault muda** vem por SSE a cada 5 s: cinco números e a
  **versão do acervo**. Versão diferente → a página recarrega, porque gráfico é SVG do
  servidor. Captura acontece algumas vezes ao dia, e recarregar nessa frequência é
  mais barato que qualquer atualização parcial.

Aba escondida fecha a conexão. Teto de **4 clientes** simultâneos, com 503 acima
disso: `ThreadingHTTPServer` gasta uma thread por conexão, e SSE não fecha.

### As formas, escolhidas pelo trabalho do dado

| O que | Forma | Por quê |
|---|---|---|
| total no acervo | figura herói | é o número que o dashboard lidera; um gráfico de uma barra seria pior |
| 7 dias, hoje, heartbeats | KPIs | manchete, não série |
| captura por dia (30) | colunas | tendência no tempo, série única |
| triagem | empilhada + medidor | parte-para-todo com quatro classes ordenadas |
| categoria, domínio | barras horizontais | nome longo; na vertical o rótulo precisaria girar |
| execuções | faixa de status | linha do tempo de estado, com legenda nomeando cada um |

### A cor tem função, e foi validada por script

**Um tom só** para toda barra nominal — categoria, domínio, dia. Colorir barra
nominal por valor gastaria o canal de identidade reencodando o que o comprimento da
barra já mostra. **Rampa ordinal de quatro passos** só na triagem, que é ordenada de
verdade (`inbox → lido → aplicado | descartado`): ali a ordem aparece na cor.

A paleta foi conferida com o validador do padrão de dataviz **contra as superfícies
deste painel** (`#fbfbfa` claro, `#16171a` escuro), não contra as de referência:
rampa monotônica, vãos de lightness ≥ 0,06, ponta clara em 2,04:1 (claro) e 2,21:1
(escuro), tom nominal acima de 3:1 nos dois modos. Os dois tons de status que ficam
abaixo de 3:1 no claro carregam **rótulo em texto** na legenda — a compensação que a
paleta exige, não um detalhe opcional.

O modo escuro tem passos próprios, revalidados contra a superfície escura. Não é o
claro invertido.

### O que ele não inventa

"Pendente de processar" **não** inclui mensagem do Discord ainda não capturada: saber
isso exigiria chamar a API a cada atualização, com limite de taxa e dependência de
rede num painel que precisa abrir justamente quando a rede está ruim.

Pendência aqui é o que se sabe de graça, do disco — fila de triagem, histórico
incompleto, conceito sem página, síntese nunca gerada — e a lista diz **de quem é
cada uma**: sua ou do pipeline. Misturar as duas produz uma lista que ninguém sabe
atacar.

## O gestor: as oito regras

O bloco **Gestor** na tela inicial não é um resumo do que está acima — é a
conclusão. Cada linha tem evidência com número e a ação que resolve.

| Regra | Dispara quando | Nível |
|---|---|---|
| `agendador_parado` | heartbeat do sync mais velho que a tolerância, ou inexistente | alerta |
| `responder_parado` | heartbeat do responder atrasado — **só se ele já rodou alguma vez** | alerta |
| `ultimo_ciclo_falhou` | o último `sync` ou `backfill` terminou em falha ou erro | alerta |
| `falhas_seguidas` | 3+ execuções consecutivas do mesmo comando em falha | alerta |
| `run_pendurado` | run marcado `rodando` há 2h+ e o lock está livre | atenção |
| `historico_parado` | backfill começado, não concluído, e sem execução há 48h | atenção |
| `fila_de_triagem` | 40+ notas em `triagem: inbox` | atenção |
| `conceitos_sem_pagina` | 3+ conceitos candidatos sem nota em `20_WIKI/` | info |

Três níveis: **alerta** (quebrado agora), **atenção** (acumulando ou vai
quebrar), **info** (trabalho disponível, nada errado). Quando nada pede ação a
tela diz isso em uma linha, em vez de mostrar oito verdinhos — painel que sempre
tem algo a mostrar ensina a não olhar.

**Sem LLM.** São comparações de número e de data em `dlb/gestor.py`, função pura
sobre o mesmo dicionário que a tela recebe. Custam zero token e funcionam quando o
modelo está sem quota — que é exatamente quando o painel importa mais, porque a
classificação diária acabou de falhar por isso. Cada regra tem teste que fabrica o
estado que a dispara e outro que confirma que ela fica calada.

Narração por LLM é opcional e entra junto do chat: o modelo **narra** alerta que a
regra já decidiu, nunca decide qual alerta existe. Um narrador não pode inventar um
problema; um decisor pode.

## O que os cartões significam

**Agendador** e **Responder** mostram idade de heartbeat, não "o container
existe". O agendador grava `date +%s` em `.state/heartbeat` **só depois de um sync
com código 0**; o responder grava em `.state/heartbeat-responder` a cada volta.
Heartbeat velho quer dizer *o último ciclo não deu certo*, que é diferente de o
processo estar no ar. A tolerância é a mesma do healthcheck do Docker
(`DLB_HEALTH_TOLERANCE`, `DLB_RESPONDER_HEALTH_TOLERANCE`) — o painel não tem
opinião própria sobre "está vivo".

**Fila de triagem** é o número de notas em `triagem: inbox`. É informação sobre
hábito, não sobre pipeline: se só cresce, a triagem não está acontecendo.

Se algum comando estiver rodando com o lock de canais tomado, aparece um aviso no
topo com o pid e desde quando — é o mesmo lock que faz um segundo `sync`
simultâneo ser recusado.

## A agenda mostra duas colunas, e diz qual manda

Esta é a tela que tem mais chance de mentir, porque a grade de verdade é
`docker/scheduler.sh` — shell, dentro do container — e `dlb/agenda.py` é uma
segunda implementação da mesma regra.

Três defesas:

1. **A fonte do número é nomeada.** Container no ar: os valores vêm dele, via
   `docker inspect` (leitura, no host, sem socket montado). Container parado ou
   docker indisponível: vêm do `.env`, e a tela diz que vieram do `.env`. Nunca os
   dois ao mesmo tempo.
2. **Divergência aparece em tabela.** `restart` não relê o `.env`; só `up` relê.
   Então arquivo e processo podem discordar por semanas sem erro nenhum, e a tela
   lista chave por chave onde discordam.
3. **A aritmética é conferida contra o script.** `tests/test_agenda.py` extrai o
   Python embutido no `scheduler.sh`, fixa o relógio e compara com a nossa função.
   Editar a grade no shell quebra o teste no dia da edição.

**Os quatro comandos aparecem sempre**, ativos ou não, com a variável que decide.
Uma tela que mostrasse só o `sync` esconderia três quartos do ciclo.

### Para usar `DLB_SYNC_AT` (horário fixo)

A precedência do scheduler é `DLB_SYNC_INTERVAL` > `DLB_SYNC_EVERY_HOURS` >
`DLB_SYNC_AT`. Então o horário fixo só vale se `DLB_SYNC_EVERY_HOURS` chegar
**vazio** ao container:

```bash
DLB_SYNC_EVERY_HOURS=       # vazio, e não ausente
DLB_SYNC_AT=03:30
```

Depois, `./dlb.sh prod up` — não `restart`. A tela de agenda avisa quando o `AT`
está definido e sendo ignorado.

## O chat, e por que ele é contido

A pergunta vai para `busca.buscar()` — **a mesma função** que `dlb buscar` chama,
com o mesmo `cfg` e o mesmo acervo. A resposta na tela e a no terminal saem do
mesmo código; não há um segundo mecanismo de busca para divergir.

O que o painel acrescenta é contenção, e ela existe por um motivo concreto: o
modelo roda por binário local (`codex`, `claude`) já logado, sem chave de API, e o
painel é o **terceiro** consumidor da mesma janela de uso — depois da classificação
diária e do responder. Se ele come a quota, a classificação da noite falha e o
painel reporta um erro que ele mesmo causou.

| Limite | Valor | Por quê |
|---|---|---|
| Fila de um | 1 rodando + 1 esperando | A terceira pergunta é recusada em vez de empilhar processos `codex` |
| Teto por hora | `PAINEL_CHAT_MAX_POR_HORA` (20) | Contado em `.state/painel.json`; **sobrevive a reinício**, porque quota não reinicia com o processo |
| Espera na fila | `PAINEL_CHAT_ESPERA_S` (120 s) | Quanto uma pergunta aguarda a anterior antes de desistir |
| Timeout do modelo | `SINTESE_TIMEOUT` (900 s) | É o do runner. Um segundo timeout no painel não mataria o subprocess — só mentiria sobre ter matado |

A tela mostra quantas chamadas foram feitas na última hora, e o histórico da
sessão fica **em memória**: reiniciar o painel perde as perguntas. Persistir
transcrição pede decidir onde ela mora e quem pode ler, e ainda não há
autenticação.

**Sem streaming de token.** `busca.buscar()` chama runners que usam
`subprocess.run(capture_output=True)`, então a saída só existe quando o processo
termina — a resposta chega inteira, depois de uns 20 segundos. Prometer streaming
aqui seria desenhar uma barra falsa.

`/chat` é a única rota que gasta modelo, e por isso é a única com guarda de
origem: requisição com `Sec-Fetch-Site` diferente de `same-origin`/`none` recebe
403. Sem isso, uma página hostil aberta na mesma máquina poderia queimar a janela
de uso com `<img src="/chat?q=...">` — pedido que não manda `Origin` nenhum, então
checar `Origin` não pegaria. Cliente sem o cabeçalho (curl, script) passa: a
guarda é contra navegador, que é quem tem o problema.

## Configuração pela interface

As 55 chaves configuráveis do `.env` em formulário, agrupadas como no arquivo.
Uma tabela só (`dlb/painel_schema.py`) decide três coisas: o que é valor válido, que
controle renderizar, e **onde a mudança vale**. Um teste de contrato afirma que toda
chave do `.env.example` está no schema e vice-versa — é o par que pega a classe de
defeito que já aconteceu aqui (chave duplicada com valores opostos) e que validação
de tipo não pegaria, porque os dois valores eram válidos.

### Salvar ≠ aplicar

**Salvar** escreve no `.env`: valida tudo antes de escrever nada, faz backup em
`.env.bak` (modo `0600`, porque o arquivo tem o token do Discord), escreve num
temporário e troca com `os.replace`. Comentário, ordem e linha em branco são
preservados; chave duplicada é removida.

**Aplicar** roda `./dlb.sh prod up`, que recria o container. Não existe botão de
`restart` aqui, e a ausência é a decisão: `restart` **não relê o `.env`** e não
avisa (commit `4ee85aa`). Enquanto houver diferença entre arquivo e container, a
tela mostra a faixa "salvo, ainda não aplicado" com as chaves e os dois valores, e
o botão de aplicar só fica ativo aí.

**Chave que só o painel lê não entra nessa conta.** `PAINEL_TOKEN`,
`PAINEL_HOSTS` e os dois tetos do chat são lidos pelo processo do painel, no host:
valem ao **reiniciar o painel**, e o container não tem nada a ver com isso. Mostrá-las
como pendentes mandaria recriar o container por uma configuração que ele não usa.

**Aplicar dispara um ciclo na hora.** Com `DLB_RUN_ON_START=1` — o padrão — o
container recém-criado roda `sync`, e junto o que estiver ligado: `backfill`,
`sintese`, `ressurgir`. Isso gasta chamada de modelo, e o `ressurgir` **posta no
Discord**. O botão avisa, listando o que vai rodar, lido do ambiente do container e
não do arquivo. Medido: três aplicares seguidos num teste mastigaram nove dias de
histórico.

O backup e o temporário estão no `.gitignore` — os dois carregam o `DISCORD_TOKEN`,
e `.env` sozinho não cobre `.env.bak`. Há teste chamando `git check-ignore`.

**Desfazer** volta o `.env` do backup, byte a byte. Um rollback que reformatasse o
arquivo não seria rollback, seria uma terceira versão.

### Segredo é write-only

`DISCORD_TOKEN` e `ANTHROPIC_API_KEY` nunca voltam para a tela nem para
`/api/config`: o campo aparece vazio, dizendo se há algo guardado. Deixar em branco
mantém o valor; preencher substitui.

### O `Host` é conferido em toda requisição

Requisição com `Host` que este painel não atende recebe **421**. Isso fecha o
caminho de **DNS rebinding**: uma página em `evil.com` aponta o domínio para
`127.0.0.1`, e o navegador manda `Host: evil.com` com `Origin: http://evil.com` —
os dois batem entre si, a checagem de origem passaria, e a página, sendo mesma
origem aos olhos do navegador, **leria a resposta** e com ela o token de CSRF.

Aceitos: `127.0.0.1`, `localhost`, `::1`, o endereço de bind, **o hostname da
máquina** e o que estiver em `PAINEL_HOSTS`. A porta é conferida.

O hostname entra sozinho porque é o que se digita numa tailnet com MagicDNS
(`http://maquina:8765`), e um atacante não controla esse nome. O nome completo
(`maquina.SEU-TAILNET.ts.net`) e domínio de proxy vão em `PAINEL_HOSTS`, declarados por
quem opera em vez de deduzidos.

O passo a passo para expor na tailnet está em **Como usar**, no topo.

### A fronteira de mutação

As rotas que escrevem passam, nesta ordem, por: teto de 256 KB no corpo, checagem
de `Origin`/`Referer` **contra a lista de nomes aceitos**, e token de CSRF comparado
com `secrets.compare_digest`.

A comparação é com a lista, e não com o `Host` da própria requisição, por um motivo
que apareceu no uso real: com proxy na frente — `tailscale serve`, nginx — o
navegador manda `Origin: https://maquina.SEU-TAILNET.ts.net` e o proxy reescreve o
`Host` para `127.0.0.1:8765`. Comparar um com o outro recusava login legítimo com
"origem cruzada recusada". Só o nome é comparado, sem porta: atrás de proxy a porta
pública não é a interna.

`Origin: null` — origem opaca, que aparece com `file://`, iframe com sandbox ou
política de referrer restritiva — conta como **ausência de informação**, não como
hostilidade: passa a checagem de origem e é barrada, se for o caso, pelo token de
CSRF, que uma página de outra origem não consegue ler.

A resposta manda `Referrer-Policy: same-origin`, e não `no-referrer`. A diferença
não é estética: com `no-referrer` o navegador serializa a origem como opaca e manda
`Origin: null` no POST do próprio formulário — o painel recusava a si mesmo por
causa de um cabeçalho posto por higiene.

Quando recusa, a resposta **diz o que viu e o que aceita**: `origin_recebido`,
`host_recebido` e `nomes_aceitos`. São cabeçalhos do próprio navegador de quem
pediu, e ter esses três valores na mão é a diferença entre consertar na hora e
adivinhar. O token é
gerado por processo e vive no formulário — uma página de outro site não consegue
lê-lo por causa da política de mesma origem. Depois do POST vem um 303, para
recarregar a página não reenviar o formulário.

`PUT`, `DELETE` e `PATCH` não existem em rota nenhuma. `POST` em rota de leitura é
405.

### O que acontece se o `.env` estragar

A tela abre. O leitor ignora linha que não parseia, a tela avisa o que encontrou de
estranho (chave repetida, por exemplo) e o resto continua editável. Um painel que
estourasse com `.env` inválido seria inútil exatamente quando é mais necessário.

## As duas ações

**Triar uma nota.** A tabela do acervo tem um seletor por linha, e o chat oferece as
notas que citou. É a única coisa que o painel escreve no vault — e escreve trocando
**uma linha** do frontmatter, não recompondo o arquivo. Tudo o mais sai byte a byte
igual: a ordem que você deixou, as relações que acrescentou à mão, e tudo abaixo de
`## Minhas notas`. Há teste comparando o arquivo inteiro antes e depois e exigindo
que as únicas linhas diferentes sejam `triagem:` e `atualizado:`.

Voltar para `inbox` funciona pela tabela, mas o chat não oferece: desfazer triagem é
decisão de quem triou, não sugestão de ferramenta.

**Rodar um comando.** Quatro botões em Execuções: `sync`, `backfill`, `sintese`,
`temas`. Dispara `python3 -m dlb <comando>` em subprocess — o **mesmo** caminho do
container e do terminal, então passa pelo mesmo `flock` e pelo mesmo journal.
Chamar `cmd_sync()` direto seria um segundo caminho de execução, e o segundo caminho
é sempre o que não tem proteção. Se outra execução estiver no ar, a sua sai com
código 3 e a tela diz "recusado — outra execução está com o lock", em laranja e não
em vermelho: é o sistema se protegendo.

Fora da lista: `ressurgir` **posta no Discord**, que é a única ação irreversível do
projeto; `responder` é laço infinito; `migrar` reescreve nota em massa. Nenhum tem
botão, e a lista branca é travada por teste.

O chat **oferece**; quem decide é o clique. A heurística das ofertas é
deliberadamente boba — esforço em adivinhar melhor produziria sugestão mais
convincente para uma decisão que não é da máquina. Um modelo marcando um link como
`descartado` apagaria julgamento humano, e `descartado` é estado terminal. Botão só entra junto da fronteira de mutação (checagem de
`Origin`, CSRF, token), e não antes — senão existiria uma janela com botão e sem
guarda.

## Autenticação

Sem `PAINEL_TOKEN` no `.env`, o painel **não pede nada** e só sobe em `127.0.0.1`:
quem alcança a porta já está na máquina, e pedir senha para isso só criaria
fricção. É o caso normal de `python3 -m dlb painel` no próprio terminal.

Com `PAINEL_TOKEN` preenchido, o painel pede o token uma vez e guarda a sessão num
cookie:

- comparação com `hmac.compare_digest` — `==` em segredo vaza o tamanho do prefixo
  comum pelo tempo de resposta;
- cookie `HttpOnly`, então um XSS não rouba a sessão;
- cookie `SameSite=Strict`, então o navegador não o manda em requisição vinda de
  outro site — isso fecha o CSRF **por baixo** do token de formulário;
- sessão em memória: reiniciar o painel derruba todas. São segundos para entrar de
  novo, e guardar sessão em disco pediria decidir expiração e rotação.

Sem sessão, tela responde **401** com a página de login e `/api/*` responde 401 em
JSON. `POST` de mutação sem sessão é 401 **antes** da checagem de CSRF: quem não
entrou não chega a ser avaliado. Token errado não diz se errou o token ou se ele
existe.

O CSS e o JS ficam abertos — a tela de login precisa deles, e não há neles nada que
não esteja no repositório.

## O serviço systemd, por dentro

Os comandos estão em **Como usar**, no topo. Aqui é o porquê das escolhas dentro de
`docker/dlb-painel.service`:

- **`KillSignal=SIGTERM` e o painel trata o sinal.** Sinal não é exceção, então sem
  o tratador `systemctl stop` deixaria o run do painel eternamente "rodando" no
  journal. Descoberto matando o painel à mão durante os testes.
- **`exec` dentro do `sh -c`.** Sem ele o shell fica como pai e o SIGTERM morre
  nele, o que anula o item de cima.
- **Bind no IP da tailnet com fallback para loopback.** No boot o `tailscaled` pode
  ainda não ter endereço, e um `--host ""` viraria bind em todas as interfaces —
  pior que loopback. Verificado que o systemd **não** estraga o `${IP:-127.0.0.1}`
  dentro do `sh -c`, o que não era óbvio: a expansão dele não suporta a forma `:-`.
- **Sem `EnvironmentFile`.** O painel lê o `.env` do projeto por conta própria.
  Duplicar aqui criaria uma segunda fonte de verdade que só atualiza com
  `daemon-reload` — exatamente o defeito do `restart` que não relê o `.env`, de novo.
- **`Restart=on-failure`, não `always`.** `always` reiniciaria em laço um painel que
  não consegue subir — porta ocupada, por exemplo — enchendo o journal.

## Por que só em `127.0.0.1`

`python3 -m dlb painel --host 0.0.0.0` **recusa subir sem `PAINEL_TOKEN`**: sem
token, quem alcança a porta entra.

Com token, sobe — e imprime um aviso, porque ainda falta TLS. `http.server` não tem
timeout de leitura nem limite de conexão, e o token viaja em texto puro. Isso só é
aceitável dentro de rede já criptografada, como uma tailnet. Fora dela, proxy de
verdade na frente, com TLS e timeouts. A decisão está em `docs/DECISOES.md`.

Túnel SSH resolve o caso comum sem proxy nenhum:

```bash
ssh -N -L 8765:127.0.0.1:8765 maquina
```

## O acervo é lido do disco, não de um índice

Não existe banco. As notas são lidas de `50_LINKS/` sob demanda, com memo por
`(mtime_ns, size)` por arquivo: editar uma nota no Obsidian e recarregar a página
mostra a mudança, relendo **só aquele arquivo**.

Medido no acervo real (77 notas, 320 KB): primeira leitura **105 ms**, releitura
pelo memo **2,2 ms**, e uma nota editada custa a releitura de uma nota só. A
página inteira responde em ~3 ms.

O rodapé tem uma caixa de "recarregar sozinho a cada 30 s", que fica lembrada no
navegador. Aba em segundo plano não recarrega.

## Onde ficam as coisas

| Caminho | O quê |
|---|---|
| `.state/runs/<instante>-<comando>-<pid>/summary.json` | histórico de execução (500 runs mais recentes) |
| `.state/locks/canais.{lock,json}` | lock de `sync`/`backfill` e quem está com ele |
| `.state/heartbeat`, `.state/heartbeat-responder` | idade do último ciclo bem-sucedido |
| `.state/<canal>.json` | cursor por canal, lido pelo painel via `Store` |
