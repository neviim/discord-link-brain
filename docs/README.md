# Documentação do discord-link-brain

O `README.md` na raiz é a porta de entrada: o que o projeto faz e como colocar
para rodar. Aqui está o resto, separado por **o que você está tentando fazer**.

| Quero… | Leia |
|---|---|
| entender como funciona por dentro | [ARQUITETURA.md](ARQUITETURA.md) |
| configurar o Discord, o vault, o LLM | [CONFIGURACAO.md](CONFIGURACAO.md) |
| rodar, agendar, recuperar histórico, resolver problema | [OPERACAO.md](OPERACAO.md) |
| saber **por que** algo foi feito daquele jeito | [DECISOES.md](DECISOES.md) |
| mexer no código, rodar teste, estender | [DESENVOLVIMENTO.md](DESENVOLVIMENTO.md) |

## Em uma frase

Lê os links que você jogou num canal do Discord, descobre do que cada um trata,
classifica com um LLM, e arquiva no vault Obsidian de forma consultável — por
dia, por categoria e por conceito.

```
Discord  →  extrai links  →  lê a página   →  LLM classifica  →  vault Obsidian  →  marca no Discord
            (dedup, limpa)   (oEmbed, og:,    (resumo, cat.)     (fonte + diário     (✅ ou 📚)
                              texto central)                      + MOC)
```

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
