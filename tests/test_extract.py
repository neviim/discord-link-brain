"""Rode com: python3 -m unittest discover -s tests -t ."""

import unittest
from datetime import datetime

from dlb.extract import extract_links, group_by_day, normalize_url


def msg(mid, content, ts="2026-08-15T12:30:00.000000+00:00", bot=False, **extra):
    base = {
        "id": mid,
        "content": content,
        "timestamp": ts,
        "author": {"username": "dotcom", "global_name": "Dotcom", "bot": bot},
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
        self.assertEqual(hits[0].author, "Dotcom")
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
        canonica = "https://x.com/aaditsh/status/1965535452582326333"
        for url in (
            "https://x.com/aaditsh/status/1965535452582326333",
            "https://twitter.com/aaditsh/status/1965535452582326333",
            "https://www.twitter.com/aaditsh/status/1965535452582326333",
            "https://mobile.twitter.com/aaditsh/status/1965535452582326333",
            "https://x.com/aaditsh/status/1965535452582326333?t=4kPdzh7k&s=33",
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
