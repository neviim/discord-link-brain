"""Rode com: python3 -m unittest discover -s tests -t ."""

import unittest
from datetime import datetime

from dlb.extract import extract_links, group_by_day, normalize_url


def msg(mid, content, ts="2026-08-15T12:30:00.000000+00:00", bot=False, **extra):
    base = {
        "id": mid,
        "content": content,
        "timestamp": ts,
        "author": {"username": "fulano", "global_name": "Fulano", "bot": bot},
    }
    base.update(extra)
    return base


class TestNormalize(unittest.TestCase):
    def test_strips_tracking_and_trailing_punctuation(self):
        self.assertEqual(
            normalize_url("https://Example.com/post/?utm_source=x&id=7)."),
            "https://example.com/post?id=7",
        )

    def test_keeps_balanced_parens(self):
        url = "https://en.wikipedia.org/wiki/Foo_(bar)"
        self.assertEqual(normalize_url(url), url)


class TestExtract(unittest.TestCase):
    def test_dedupes_across_messages_and_drops_bots(self):
        messages = [
            msg("1", "olha isso https://exemplo.com/a"),
            msg("2", "de novo <https://exemplo.com/a/>"),
            msg("3", "spam https://exemplo.com/b", bot=True),
        ]
        hits = extract_links(messages, channel_id="42", guild_id="7")

        self.assertEqual([h.url for h in hits], ["https://exemplo.com/a"])
        self.assertEqual(hits[0].author, "Fulano")
        self.assertEqual(hits[0].jump_url, "https://discord.com/channels/7/42/1")

    def test_context_excludes_the_url_itself(self):
        hits = extract_links([msg("1", "leitura de hoje https://exemplo.com/a boa")], "42")
        self.assertEqual(hits[0].context, "leitura de hoje boa")

    def test_embed_and_attachment_urls_are_captured(self):
        messages = [
            msg("1", "", embeds=[{"url": "https://exemplo.com/embed"}]),
            msg("2", "", attachments=[{"url": "https://exemplo.com/arquivo.pdf"}]),
        ]
        self.assertEqual(len(extract_links(messages, "42")), 2)

    def test_discord_cdn_is_ignored(self):
        messages = [msg("1", "", attachments=[{"url": "https://cdn.discordapp.com/x.png"}])]
        self.assertEqual(extract_links(messages, "42"), [])

    def test_group_by_day_uses_local_timezone(self):
        # 02:30 UTC vira 23:30 do dia anterior em São Paulo.
        hits = extract_links(
            [msg("1", "https://exemplo.com/a", ts="2026-08-16T02:30:00+00:00")],
            "42",
            tz="America/Sao_Paulo",
        )
        self.assertEqual(list(group_by_day(hits)), ["2026-08-15"])
        self.assertIsInstance(hits[0].posted_at, datetime)


if __name__ == "__main__":
    unittest.main()


class TestCanonizacaoYoutube(unittest.TestCase):
    """O botão de compartilhar do YouTube posta duas formas do mesmo vídeo."""

    def test_todas_as_formas_viram_a_mesma_url(self):
        canonica = "https://www.youtube.com/watch?v=REBIKyFokAM"
        for url in (
            "https://www.youtube.com/watch?v=REBIKyFokAM",
            "https://youtu.be/REBIKyFokAM",
            "https://youtube.com/shorts/REBIKyFokAM?is=Ftg7UZLZEwyIHOYA",
            "https://m.youtube.com/watch?v=REBIKyFokAM&t=90",
            "https://www.youtube.com/embed/REBIKyFokAM",
            "https://youtu.be/REBIKyFokAM?is=s3lwKLtKhiKsMxbj",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_url(url), canonica)

    def test_shorts_e_watch_do_mesmo_video_deduplicam(self):
        hits = extract_links(
            [msg("1", "https://youtube.com/shorts/abc123?is=xyz "
                      "https://www.youtube.com/watch?v=abc123")],
            "canal",
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://www.youtube.com/watch?v=abc123")

    def test_pagina_de_canal_nao_e_tocada(self):
        # Canal e playlist não são vídeo: continuam como estão.
        for url in ("https://www.youtube.com/@bagoftips",
                    "https://www.youtube.com/playlist?list=PL123"):
            with self.subTest(url=url):
                self.assertEqual(normalize_url(url), url)

    def test_videos_diferentes_continuam_diferentes(self):
        self.assertNotEqual(
            normalize_url("https://youtu.be/aaa111"),
            normalize_url("https://youtu.be/bbb222"),
        )


class TestCanonizacaoX(unittest.TestCase):
    """O X manteve twitter.com vivo: o mesmo tweet circula nas duas formas."""

    def test_twitter_e_x_viram_a_mesma_url(self):
        canonica = "https://x.com/alguem/status/1900000000000000000"
        for url in (
            "https://x.com/alguem/status/1900000000000000000",
            "https://twitter.com/alguem/status/1900000000000000000",
            "https://www.twitter.com/alguem/status/1900000000000000000",
            "https://mobile.twitter.com/alguem/status/1900000000000000000",
            "https://x.com/alguem/status/1900000000000000000?t=4kPdzh7k&s=33",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_url(url), canonica)

    def test_o_mesmo_tweet_nos_dois_dominios_deduplica(self):
        hits = extract_links(
            [msg("1", "https://x.com/a/status/123?t=xy&s=33 "
                      "https://twitter.com/a/status/123")],
            "canal",
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://x.com/a/status/123")

    def test_nao_mexe_em_parametro_util_de_outro_site(self):
        # `s` e `t` só são descartados no X; noutro domínio podem significar algo.
        self.assertEqual(
            normalize_url("https://exemplo.com/busca?s=nostr&t=1"),
            "https://exemplo.com/busca?s=nostr&t=1",
        )


class TestJumpUrlDoServidor(unittest.TestCase):
    """O link para a mensagem original só abre se souber o servidor.

    Regressão de um bug real: `GET /channels/{id}/messages` **não** devolve
    `guild_id` em cada mensagem, e o `cli.py` não passava o do canal — então
    todo link saía como `/channels/@me/...` e não abria num canal de servidor.
    Medido no vault: 70 links gravados com o endereço errado.
    """

    def mensagens(self):
        return [msg("999", "olha https://exemplo.com/x")]

    def test_com_guild_o_link_aponta_para_o_servidor(self):
        hit = extract_links(self.mensagens(), "42", guild_id="1217985616281600000")[0]
        self.assertEqual(
            hit.jump_url, "https://discord.com/channels/1217985616281600000/42/999"
        )

    def test_sem_guild_cai_para_arroba_me(self):
        """DM não tem servidor, e aí `@me` é o endereço certo."""
        hit = extract_links(self.mensagens(), "42")[0]
        self.assertEqual(hit.jump_url, "https://discord.com/channels/@me/42/999")


class TestMensagemDoProprioBot(unittest.TestCase):
    """O ressurgimento posta URLs no canal todo dia. Elas não podem voltar
    para a captura — seria um laço: arquiva, ressurge, rearquiva.

    Duas barreiras, e a segunda importa porque a primeira tem uma flag que a
    desliga: (1) mensagem de bot é descartada por padrão; (2) mesmo com
    `--include-bots`, a URL já está em `10_FONTES` e o dedup por URL a barra.
    """

    def ressurgimento(self):
        # A forma exata que `ressurgir.formatar` produz, com <url> entre ângulos.
        return [msg("1",
                    "🕰️ **Ressurgimento** — 3 coisas que você salvou\n\n"
                    "**1. Um vídeo antigo**\n"
                    "<sub>2025-09-07 · há 300 dias · video</sub>\n"
                    "<https://www.youtube.com/watch?v=abc>\n",
                    bot=True)]

    def test_link_da_propria_mensagem_nao_e_recapturado(self):
        self.assertEqual(extract_links(self.ressurgimento(), "42"), [])

    def test_com_include_bots_ele_apareceria(self):
        """Documenta a pegadinha: a flag existe para casos legítimos (um bot que
        publica links no canal), e com ela o ressurgimento entra na fila. O que
        segura nesse caso é o dedup por URL contra `10_FONTES`."""
        achados = extract_links(self.ressurgimento(), "42", include_bots=True)
        self.assertEqual([h.url for h in achados],
                         ["https://www.youtube.com/watch?v=abc"])
