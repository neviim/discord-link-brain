# Painel

Acompanhar o que o sistema fez, o que está pendente e o que está no acervo — sem
abrir o log do Docker nem o `.state/` na mão.

```bash
python3 -m dlb painel                 # http://127.0.0.1:8765
python3 -m dlb painel --porta 9000
```

Roda **no host**, não dentro do container. Precisa do `.env` do projeto (o mesmo
que a CLI usa) e de acesso de leitura ao vault.

## As três telas

| Tela | Responde |
|---|---|
| **Painel** (`/`) | O agendador está vivo? O responder está vivo? Quanto tem no acervo, quanto está na fila de triagem, o que rodou por último, onde está o cursor de cada canal. |
| **Execuções** (`/execucoes`) | Uma linha por run, do mais recente para o mais antigo, com status, duração, contadores e erro. Filtra por comando. |
| **Acervo** (`/acervo`) | As notas de `50_LINKS/`, com busca literal por título, resumo, domínio e conceito, e faceta por triagem. |

Cada tela tem um JSON equivalente, para `curl` e para script:

```bash
curl -s localhost:8765/api/estado     | jq .heartbeats
curl -s localhost:8765/api/execucoes  | jq '.execucoes[0]'
curl -s localhost:8765/api/acervo     | jq '.total, .resumo.por_triagem'
curl -s 'localhost:8765/api/acervo?q=nostr&triagem=inbox&limite=20' | jq '.no_filtro'
```

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

## Somente leitura

Nesta versão o painel **não escreve nada**. Qualquer `POST`/`PUT`/`DELETE`/`PATCH`
responde `405`. Botão só entra junto da fronteira de mutação (checagem de
`Origin`, CSRF, token), e não antes — senão existiria uma janela com botão e sem
guarda.

## Por que só em `127.0.0.1`

`python3 -m dlb painel --host 0.0.0.0` **recusa subir**, de propósito.
`http.server` não tem timeout de leitura nem limite de conexão: como servidor de
borda, uma conexão lenta o segura. Para acessar de outra máquina (tailnet, por
exemplo), ponha um proxy de verdade na frente, com TLS e timeouts — e só então
exponha. A decisão está em `docs/DECISOES.md`.

Túnel SSH resolve o caso comum sem proxy nenhum:

```bash
ssh -N -L 8765:127.0.0.1:8765 dotcom
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

## Subir junto com a máquina

Enquanto o serviço systemd não entra (fase 7 do plano), o jeito honesto é rodar à
mão ou num `tmux`. O painel não é um daemon ainda: não tem `--daemon`, não escreve
pidfile e o log vai para o stdout.
