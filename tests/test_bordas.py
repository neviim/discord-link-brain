"""Rode com: python3 -m unittest discover -s tests -t .

Bordas que só aparecem em produção: nome de canal com emoji, página que não
abriu, snowflake do Discord, virada do dia no fuso local.
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dlb.discord_client import DISCORD_EPOCH_MS, snowflake_for
from dlb.enrich import _fallback
from dlb.extract import day_of_message
from dlb.vault import Vault, fonte_name, sanitize


class TestNomeDeArquivo(unittest.TestCase):
    """Nome de canal com emoji — comum no Discord, e ele vira nome de arquivo."""

    def test_emoji_sobrevive_ao_saneamento(self):
        self.assertEqual(sanitize("🔗_links"), "🔗_links")
        self.assertEqual(
            fonte_name("🔗_links", "2026-08-15"),
            "FONTE - Discord 🔗_links - 2026-08-15",
        )

    def test_caracteres_que_quebram_no_obsidian_saem(self):
        # `[`, `]` e `#` viram wikilink/tag acidental; `/` e `:` quebram o path.
        self.assertEqual(sanitize("dev/ops: [beta] #geral"), "devops beta geral")

    def test_nome_que_vira_vazio_ganha_fallback(self):
        # Sem isso o arquivo sairia "FONTE - Discord  - 2026-08-15".
        self.assertEqual(sanitize("###"), "canal")
        self.assertEqual(sanitize("   "), "canal")


class TestPaginaIlegivel(unittest.TestCase):
    """Quando nada da página pôde ser lido, ainda dá para identificar o link."""

    def test_caminho_da_url_vira_pista_legivel(self):
        meta = _fallback("https://github.com/cosmtrek/mindwalk")
        self.assertFalse(meta.legivel)
        self.assertEqual(meta.site, "github.com")
        self.assertIn("cosmtrek/mindwalk", meta.description)

    def test_hifen_e_underscore_viram_espaco(self):
        meta = _fallback("https://blog.com/como-usar_obsidian-bem")
        self.assertIn("como usar obsidian bem", meta.description)

    def test_url_percent_encoded_e_decodificada(self):
        meta = _fallback("https://pt.wikipedia.org/wiki/Programa%C3%A7%C3%A3o")
        self.assertIn("Programação", meta.description)

    def test_raiz_do_dominio_nao_inventa_descricao(self):
        meta = _fallback("https://exemplo.com/")
        self.assertEqual(meta.description, "")
        self.assertEqual(meta.site, "exemplo.com")

    def test_erro_e_guardado_mas_truncado(self):
        meta = _fallback("https://exemplo.com/x", "e" * 500)
        self.assertEqual(len(meta.error), 200)


class TestSnowflake(unittest.TestCase):
    """`after=<snowflake>` é como o pipeline pede 'só o que veio depois disso'."""

    def test_epoch_do_discord_e_o_snowflake_zero(self):
        self.assertEqual(snowflake_for(DISCORD_EPOCH_MS), "0")

    def test_e_monotonico_no_tempo(self):
        antes = int(snowflake_for(DISCORD_EPOCH_MS + 1_000))
        depois = int(snowflake_for(DISCORD_EPOCH_MS + 2_000))
        self.assertLess(antes, depois)

    def test_bate_com_um_id_de_canal(self):
        # Id sintético cujo timestamp embutido é 2025-10-01. O snowflake calculado
        # para um instante anterior tem que ficar abaixo dele, senão `--since`
        # pularia mensagens — é a conta que o `sync` faz sem cursor.
        criado_em = datetime(2025, 9, 7, 0, 0, tzinfo=ZoneInfo("UTC"))
        limite = int(snowflake_for(int(criado_em.timestamp() * 1000)))
        self.assertLess(limite, 1422734760345600000)


class TestViradaDoDia(unittest.TestCase):
    """Link postado 22h no Brasil é do dia de hoje, não do de amanhã em UTC."""

    def msg(self, ts):
        return {"timestamp": ts}

    def test_noite_no_brasil_ainda_e_o_mesmo_dia(self):
        # 2026-08-16T01:30Z = 2026-08-15 22:30 em São Paulo.
        self.assertEqual(
            day_of_message(self.msg("2026-08-16T01:30:00.000000+00:00"), "America/Sao_Paulo"),
            "2026-08-15",
        )

    def test_o_mesmo_instante_em_utc_cai_no_dia_seguinte(self):
        self.assertEqual(
            day_of_message(self.msg("2026-08-16T01:30:00.000000+00:00"), "UTC"),
            "2026-08-16",
        )

    def test_aceita_o_z_do_discord(self):
        self.assertEqual(
            day_of_message(self.msg("2026-08-16T12:00:00Z"), "America/Sao_Paulo"),
            "2026-08-16",
        )


class TestUrlsArquivadasResiliente(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for sub in ("10_FONTES", "20_WIKI", "30_MOC", "40_DIARIO", "90_META"):
            (self.root / sub).mkdir()
        self.vault = Vault(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_arquivo_ilegivel_nao_derruba_a_leitura(self):
        # Um arquivo problemático não pode impedir a sync das 6h.
        bom = self.root / "10_FONTES" / "FONTE - Discord c - 2026-08-15.md"
        bom.write_text("<https://exemplo.com/a>\n", encoding="utf-8")
        ruim = self.root / "10_FONTES" / "FONTE - Discord c - 2026-08-16.md"
        ruim.write_bytes(b"\xff\xfe\x00binario invalido")
        self.assertIn("https://exemplo.com/a", self.vault.archived_urls())

    def test_url_no_meio_do_texto_nao_conta(self):
        # Só `<url>` em linha própria é registro de captura.
        p = self.root / "10_FONTES" / "FONTE - Discord c - 2026-08-15.md"
        p.write_text("Veja <https://exemplo.com/a> no texto.\n", encoding="utf-8")
        self.assertEqual(self.vault.archived_urls(), set())

    def test_conceito_sem_pagina_vira_candidato(self):
        texto, existe = self.vault.link_or_candidate("Harness Engineering")
        self.assertFalse(existe)
        self.assertNotIn("[[", texto)
        self.assertIn("candidato", texto)

    def test_conceito_com_pagina_vira_wikilink_de_verdade(self):
        (self.root / "20_WIKI" / "Obsidian.md").write_text("# Obsidian\n", encoding="utf-8")
        texto, existe = self.vault.link_or_candidate("obsidian")   # caixa diferente
        self.assertTrue(existe)
        self.assertEqual(texto, "[[Obsidian]]")



class TestPaginacao(unittest.TestCase):
    """`iter_messages` é onde um bug some com mensagem sem ninguém notar.

    O Discord devolve no máximo 100 por vez, **da mais nova para a mais
    velha**, e o cursor `after` anda pelo maior id já visto. Errar a ordem ou o
    avanço do cursor causa link perdido ou laço infinito — nenhum dos dois
    aparece no log.
    """

    def cliente(self, lotes):
        from dlb.discord_client import DiscordClient

        c = DiscordClient("Bot x")
        self.pedidos = []

        def falso_get(path, params=None):
            self.pedidos.append((params or {}).get("after"))
            return lotes.pop(0) if lotes else []

        c._get = falso_get
        return c

    def msgs(self, ids):
        # Como o Discord manda: mais nova primeiro.
        return [{"id": str(i), "timestamp": "2026-08-15T12:00:00Z"} for i in sorted(ids, reverse=True)]

    def test_devolve_em_ordem_cronologica_crescente(self):
        c = self.cliente([self.msgs([3, 1, 2])])
        self.assertEqual([m["id"] for m in c.iter_messages("42")], ["1", "2", "3"])

    def test_lote_incompleto_encerra_a_paginacao(self):
        c = self.cliente([self.msgs([1, 2])])
        list(c.iter_messages("42", after="0"))
        self.assertEqual(len(self.pedidos), 1)   # não pediu um segundo lote

    def test_lote_cheio_pede_o_proximo_a_partir_do_maior_id(self):
        cheio = self.msgs(range(1, 101))         # exatamente 100
        c = self.cliente([cheio, self.msgs([101])])
        todas = list(c.iter_messages("42", after="0"))
        self.assertEqual(len(todas), 101)
        self.assertEqual(self.pedidos, ["0", "100"])

    def test_lote_vazio_encerra_sem_erro(self):
        c = self.cliente([[]])
        self.assertEqual(list(c.iter_messages("42", after="0")), [])

    def test_limite_total_interrompe_no_meio_do_lote(self):
        # É o teto que o backfill usa para não puxar o canal inteiro.
        c = self.cliente([self.msgs(range(1, 101))])
        self.assertEqual(len(list(c.iter_messages("42", after="0", limit_total=7))), 7)

    def test_resposta_inesperada_da_api_nao_vira_laco_infinito(self):
        c = self.cliente([{"message": "algo estranho"}])
        self.assertEqual(list(c.iter_messages("42", after="0")), [])

if __name__ == "__main__":
    unittest.main()
