# A camada `50_LINKS/` — nota atômica por link

Guia de uso dos recursos acrescentados em 2026-08-18. Se você só quer saber
**qual comando rodar**, pule para a [referência rápida](#referência-rápida).

## O que mudou, em uma frase

Antes, o link virava um bullet dentro de uma seção de uma nota diária. Agora vira
**uma nota própria** em `50_LINKS/`, com um campo `triagem` que você move à mão.

Por que isso importa: um bullet não tem backlink, não aparece no grafo, não é
filtrável e não tem estado. Seis meses depois ele é invisível. Uma nota é um nó de
primeira classe — e a triagem é o que impede a pasta de virar depósito.

```
Discord → extrai → lê a página → LLM classifica → 10_FONTES/  captura crua, imutável
                                                → 50_LINKS/   uma nota por link  ← novo
                                                → 40_DIARIO/  aponta para as notas
                                                → 30_MOC/     hub + Links.base   ← novo
                                                → ✅ no Discord
```

---

## Passo 1 — Preparar o vault

**Na sua máquina isto já está feito.** Vale ler se você for apontar o pipeline
para outro vault.

A camada nova exige vocabulário que o `90_META/ESQUEMA.md` do vault não tinha, e a
regra do próprio ESQUEMA é: *mude a regra ali primeiro, não abra exceção
silenciosa*. O que precisa estar registrado lá:

| No ESQUEMA | O quê |
|---|---|
| tabela de camadas | linha para `50_LINKS/` |
| `tipo` | o valor `link` |
| `triagem` | `inbox` · `lido` · `aplicado` · `descartado` |
| `dominio` | host da URL, sem `www.` |
| `categoria` | as 10 de `dlb/models.py` |
| `nota` | 1–5, avaliação humana |

Depois, crie a pasta e confira:

```bash
mkdir -p "$OBSIDIAN_VAULT/50_LINKS"
python3 -m dlb doctor
```

O `doctor` passa a mostrar duas linhas novas:

```
notas de link   ligado · 50_LINKS/ (70 notas)
triagem         70 inbox
```

> `50_LINKS/` **não** entra em `validate_vault()`, de propósito: um vault que
> ainda não foi emendado continua funcionando. Mas ligar `LINK_NOTES=1` sem
> emendar o ESQUEMA é escrever contra um contrato.

---

## Passo 2 — Migrar o histórico

**Também já feito na sua máquina:** 70 notas, 26 dias.

`dlb migrar` cria a nota dos links que já estavam no vault. **Não chama LLM e não
toca a rede** — tudo que o modelo produziu está nos bullets do diário, e tudo que é
cru está na fonte. Reclassificar gastaria 26 chamadas para produzir um resultado
*diferente* do que você já leu.

Ordem recomendada, do mais seguro ao definitivo:

```bash
# 1. a seco: quantas notas criaria, e quantos avisos de parser
python3 -m dlb migrar --dry-run

# 2. um dia só, para abrir no Obsidian e conferir o resultado
python3 -m dlb migrar --day 2026-08-17

# 3. tudo
python3 -m dlb migrar
```

Saída esperada ao final:

```
70 criada(s) · 0 atualizada(s) · 0 pulada(s) · 25 diário(s) referenciado(s)
```

**Aviso não é falha.** Um bullet que você editou à mão e que não casa o formato é
reportado com arquivo e linha, e os outros seguem. Zero avisos nos 70 links reais.

### O que ele faz no `40_DIARIO/`

Insere **uma linha** sob cada bullet antigo:

```markdown
- **[Mindwalk: mapa do código…](https://www.youtube.com/watch?v=T8B39zn7-o8)** — Vídeo…
  *Por que importa:* É uma demonstração prática em português…
  Conceitos: Mindwalk *(candidato)*, Matheus Battisti *(candidato)*
  <sub>18:55 · Jads · #area/ia</sub>
  Nota: [[2026-08-17 Mindwalk - mapa do código e análise de alterações em tempo real (Hora de Codar)]]
```

Puramente aditivo — nada seu é perdido, e não há caminho de código que apague
seção. Isso **não é enfeite**: sem essa linha as 70 notas nasceriam órfãs, porque
`parte-de` é aresta *saindo* e o invariante 3 do vault exige aresta *entrando*.
`--no-diario` desliga a inserção, ao preço das órfãs.

Rodar de novo é seguro: idempotente por URL, e os diários ficam byte-idênticos.

---

## Passo 3 — Abrir os painéis

As views ficam em `30_MOC/Links.base`, embutidas em `MOC - Captura Discord` numa
seção `## Painéis`. Abra o MOC no Obsidian — é a porta de entrada.

| View | Filtro | Para quê |
|---|---|---|
| **Inbox** | `triagem == inbox` | A fila de trabalho. Se ela só cresce, a triagem não está acontecendo. |
| **Por tema** | agrupado por `categoria` | Onde seu interesse realmente está. |
| **Aplicados** | `triagem == aplicado` | O acervo de valor comprovado. |
| **Fontes** | agrupado por `dominio` | De onde vem o que você salva. |
| **Descartados** | `triagem == descartado` | Revisar o que você recusou é trabalho legítimo. |

Para conferir da linha de comando (exige o app aberto — o `obsidian` CLI fala com
ele, e não funciona dentro do container):

```bash
obsidian reload && sleep 3      # o índice tem latência; consultar antes mente
obsidian base:query path="30_MOC/Links.base" view=Inbox format=md
obsidian base:query path="30_MOC/Links.base" view=Fontes format=md
```

### O que a view Fontes já revelou no seu vault

```
48  youtube.com
 8  instagram.com
 2  github.com
 …  mais 12 domínios com 1 link cada
```

**80% de duas fontes.** É o tipo de coisa que só aparece quando o domínio é uma
propriedade e não texto solto — e sugere que assinar dois feeds pode render mais
que capturar link por link.

---

## Passo 4 — O ritual de triagem

Este é o passo que **não dá para automatizar**, e é o que decide se a camada vale
algo. Sem ele você tem um depósito com frontmatter bonito.

Reserve 20 minutos numa sexta. **Não para ler tudo — só para classificar.** Abra a
view Inbox e mova cada link:

| De → para | Quando |
|---|---|
| `inbox` → `lido` | Você leu ou assistiu. Rendeu ideia? Escreva abaixo de `## Minhas notas`. |
| `inbox` → `aplicado` | Virou uso real: código, decisão, um hábito. Vale um `nota: 4` ou `5`. |
| `inbox` → `descartado` | Não serviu. **É a transição mais importante.** |

A maioria vai virar `descartado`, e está tudo bem. Reconhecer que algo não serviu é
o que limpa o ruído. O que nunca é triado não é acervo, é entulho.

Editar é no painel de propriedades do Obsidian — `triagem` e `nota` estão lá,
prontas para um clique. Acompanhe o total com:

```bash
python3 -m dlb doctor | grep triagem
```

### A fronteira: o que é seu e o que é da máquina

A nota de link é o **único** artefato do vault que você edita por desenho. Por isso:

```markdown
# Título
…                          ← miolo gerado. `--force` refaz.
## Minhas notas
<!-- Daqui para baixo é seu. -->
…                          ← seu. NADA reescreve, nem --force.
```

No frontmatter, `triagem`, `nota` e `status` são preservados na reescrita, junto
com relações que você acrescentou à mão. E nota que você escreveu do zero, sem a
linha `## Minhas notas`, não é tocada nem com `--force` — sem a fronteira não há
como distinguir o gerado do seu.

---

## Passo 5 — O dia a dia

Nada muda no seu fluxo: você joga link no canal, e o agendador faz o resto de 6 em
6 horas. A diferença é que agora cada link ganha nota, e a nota entra na fila.

```bash
./dlb.sh prod up            # sobe o agendador
./dlb.sh prod logs          # acompanha
./dlb.sh dev sync --dry-run # ensaio, sem escrever
```

A ordem de escrita importa e está travada por teste: **fonte → nota atômica →
diário → MOC**, e só então a reação no Discord. O diário precisa do nome do arquivo
da nota para montar o wikilink; e marcar só depois de tudo escrito garante que, se
algo explodir, a mensagem volta na próxima execução.

Para desligar a camada sem mexer em código:

```bash
LINK_NOTES=0
```

O bullet do diário volta a ser rico (resumo, por que importa, conceitos embutidos)
e nada é escrito em `50_LINKS/`. O que já existe não é apagado.

---

## Manutenção e reparo

### Link no diário que não tem nota

A escrita no vault não é transacional. Se o processo morrer entre a fonte e a nota
atômica, o dedup por `10_FONTES/` nunca mais recria aquele link. O `doctor` avisa:

```
                3 link(s) no diário sem nota atômica — rode `dlb migrar`
```

`dlb migrar` é o caminho de reparo. Rode e o número volta a zero.

### O link "Mensagem original" não abre

Captura anterior a 2026-08-18 gravou `channels/@me/...`, porque
`GET /channels/{id}/messages` não devolve `guild_id` por mensagem. Num canal de
servidor esse endereço não abre.

Já corrigido na origem — captura nova sai certa. Para o histórico:

```bash
# o id do servidor está na URL do Discord no navegador
echo 'DISCORD_GUILD_IDS=<id_canal>:<id_servidor>' >> .env
python3 -m dlb migrar --force
```

As notas de `10_FONTES/` **continuam** com o valor original, e isso é de propósito:
a fonte não se corrige (invariante 9 do vault), a correção mora na camada derivada.
O endereço certo é integralmente derivável de (servidor, canal, mensagem).

### Refazer o miolo de uma nota

```bash
python3 -m dlb migrar --force            # todas
python3 -m dlb sync --day AAAA-MM-DD --force   # reclassifica com o LLM
```

`--force` no `migrar` refaz o texto gerado a partir do que está no vault, sem LLM.
`--force` no `sync` reclassifica de verdade. Os dois preservam o que é seu.

---

## Referência rápida

```bash
# situação atual
python3 -m dlb doctor                    # notas, triagem, pendências

# migrar o histórico
python3 -m dlb migrar --dry-run          # ensaio
python3 -m dlb migrar --day 2026-08-17   # um dia
python3 -m dlb migrar                    # tudo
python3 -m dlb migrar --force            # refaz o miolo, preserva o seu
python3 -m dlb migrar --no-diario        # não insere referência (gera órfãs)
python3 -m dlb migrar --limite 10        # fatia

# no container (vault :ro no modo dev)
./dlb.sh dev migrar --dry-run
./dlb.sh dev --rw migrar

# painéis (exige o app Obsidian aberto)
obsidian reload && sleep 3
obsidian base:query path="30_MOC/Links.base" view=Inbox format=md

# invariantes do vault, depois de escrever
obsidian unresolved verbose && obsidian deadends && obsidian orphans
```

| Variável | Padrão | O quê |
|---|---|---|
| `LINK_NOTES` | `1` | Escrever nota em `50_LINKS/` |
| `DISCORD_GUILD_IDS` | — | `id_canal:id_servidor`, só o `migrar` usa |

---

## Armadilhas

**Não consulte o grafo no mesmo instante da escrita.** O índice do Obsidian tem
latência e a consulta *mente*. Sempre `obsidian reload`, espere ~3s, e só então
`orphans` / `deadends` / `unresolved`.

**Não apague uma nota para "descartar" o link.** `50_LINKS/` não é a autoridade do
dedup — `10_FONTES/` é. Mas apagar a nota faz o `doctor` reportar pendência e
`migrar` recriá-la. O estado certo é `triagem: descartado`, que mantém o arquivo e
o histórico da sua decisão.

**Não ponha tag de assunto na nota.** Um link sobre Rust leva
`relacionado: "[[Rust]]"`, não `#rust`. O vault manda usar link para assunto, e num
acervo de centenas de links a tag de assunto produziria trezentas tags de um uso
cada. Conceito que ainda não tem página aparece como texto em `## Conceitos sem
página`, com `#acao/expandir` — nunca como wikilink pendurado.

**Não acrescente propriedade sem emendar o ESQUEMA.** O passo a passo está em
[DESENVOLVIMENTO.md](DESENVOLVIMENTO.md#acrescentar-uma-propriedade-à-nota-de-link).

---

## Onde está o resto

| Quero… | Leia |
|---|---|
| o **porquê** de cada escolha desta camada | [DECISOES.md](DECISOES.md) |
| como as quatro camadas se encaixam | [ARQUITETURA.md](ARQUITETURA.md) |
| todas as variáveis de ambiente | [CONFIGURACAO.md](CONFIGURACAO.md) |
| rodar, agendar, resolver problema | [OPERACAO.md](OPERACAO.md) |
