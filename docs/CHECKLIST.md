# Checklist de pendências

Lista executável do que falta — pendências operacionais e de código, com o
motivo e os arquivos envolvidos. Quem for executar não precisa reabrir a
conversa original pra entender o contexto.

## 1. Fora do código

- [ ] **Recriar o `DISCORD_TOKEN` de produção.** O token em
      `/opt/discord-link-brain/.env` (servidor `192.168.15.17`) é rejeitado
      pelo Discord com `401 Unauthorized` — testado direto contra
      `GET /users/@me` com o token exato do `.env`, via `curl`, sem passar
      pelo código do projeto. Não é bug de parsing (`dlb/config.py:23` já
      trata aspas e espaço em volta do valor) — o token em si não é aceito.
      `dlb-prod` (sync, `dlb/cli.py`) e `dlb-responder` (`dlb/responder.py`)
      estão os dois rodando sem conseguir falar com o Discord; `dlb-prod`
      aparece `healthy` no `docker ps` mesmo assim — o healthcheck só confere
      o heartbeat do laço, não se o Discord responde.

      Passo a passo (mais detalhe em `docs/CONFIGURACAO.md`, seção
      "O Discord"):
      1. <https://discord.com/developers/applications> → a aplicação → aba
         **Bot** → **Reset Token**, copiar o valor novo (aparece uma vez só).
         Cuidado pra não confundir com o **Client Secret** da aba OAuth2 —
         ficam perto e um reset acidental do lado errado não resolve nada.
      2. Conferir se **Message Content Intent** continua ligado (mesma aba
         *Bot*) — reset de token não mexe nisso, mas vale conferir junto.
      3. Atualizar `DISCORD_TOKEN=` em `/opt/discord-link-brain/.env`, no
         servidor.
      4. `./dlb.sh prod up` pra recriar os containers com o token novo —
         `prod restart` **não** relê o `.env` (`docs/OPERACAO.md`).
      5. Verificar: `docker logs --tail 20 dlb-prod` não deve mais mostrar
         `401`; `docker logs --tail 20 dlb-responder` não deve mais mostrar
         "não consegui identificar o bot".
