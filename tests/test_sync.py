"""Rode com: python3 -m unittest discover -s tests -t .

`cmd_sync` de ponta a ponta, com dublês para Discord, leitura de página e modelo.

Este é o comando que roda sozinho de 6 em 6 horas e que ninguém está olhando
quando falha. O que se trava aqui não é "o caminho feliz funciona" — é o que
acontece **quando algo explode no meio**, porque é aí que link some em silêncio.

O invariante central, escrito em docs/ARQUITETURA.md: *só marca no Discord depois
que a nota está no vault*. Se a escrita falhar, a mensagem tem que ficar sem marca
e voltar na próxima execução. Inverter isso perde o link para sempre — o dedup por
`10_FONTES/` nunca mais o recria.
"""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import dlb.cli as cli
from dlb.config import Config
from dlb.models import LinkNote, PageMeta
from dlb.state import ChannelState, Store

from tests.test_vault import VaultCase

TZ = ZoneInfo("America/Sao_Paulo")


def msg(mid, content, ts="2026-08-15T12:30:00.000000+00:00", bot=False, reacoes=None):
    m = {
        "id": mid,
        "content": content,
        "timestamp": ts,
        "author": {"username": "Jads", "id": "7", "bot": bot},
    }
    if reacoes:
        m["reactions"] = [{"emoji": {"name": e}, "me": True} for e in reacoes]
    return m


class DiscordFalso:
    """Grava o que foi pedido. Nunca toca a rede."""

    def __init__(self, mensagens, explode_reacao=False):
        self.mensagens = mensagens
        self.reacoes: list[tuple[str, str]] = []
        self.explode_reacao = explode_reacao

    def channel_name(self, channel_id):
        return "estudos"

    def guild_id(self, channel_id):
        return "999"

    def iter_messages(self, channel_id, after=None, limit_total=None):
        return list(self.mensagens)

    def add_reaction(self, channel_id, message_id, emoji):
        if self.explode_reacao:
            from dlb.discord_client import DiscordError

            raise DiscordError("403 ao reagir")
        self.reacoes.append((message_id, emoji))


class SyncCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.originais = {
            "DiscordClient": cli.DiscordClient,
            "fetch_meta": cli.fetch_meta,
            "classify": cli.classify,
        }
        cli.fetch_meta = lambda url, timeout=10.0: PageMeta(title="Título", description="d")
        cli.classify = self.classificar
        self.classificou = []

    def tearDown(self):
        for k, v in self.originais.items():
            setattr(cli, k, v)
        super().tearDown()

    def classificar(self, cfg, hits, metas):
        """Dublê do modelo: devolve uma nota por link, sem chamar nada."""
        self.classificou.append([h.url for h in hits])
        return [
            LinkNote(
                hit=h, meta=metas.get(h.url, PageMeta()),
                titulo=f"Nota de {h.url.rsplit('/', 1)[-1]}",
                resumo="Um resumo.", categoria="artigo", area="area/ia",
                conceitos=["Nostr"], porque_importa="Importa.",
            )
            for h in hits
        ]

    def cfg(self, **kw):
        base = dict(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.root / ".state",
        )
        base.update(kw)
        return Config(**base)

    def rodar(self, mensagens, argv=("sync",), cfg=None, **kw):
        self.cliente = DiscordFalso(mensagens, **kw)
        cli.DiscordClient = lambda *a, **k: self.cliente
        args = cli.build_parser().parse_args(list(argv))
        saida = []
        original_log, cli._log = cli._log, lambda m="": saida.append(str(m))
        try:
            codigo = cli.cmd_sync(cfg or self.cfg(), args)
        finally:
            cli._log = original_log
        self.saida = "\n".join(saida)
        return codigo

    @property
    def cursor(self):
        return Store(self.root / ".state").load("42").last_message_id

    def notas(self):
        return sorted(p.stem for p in (self.root / "50_LINKS").glob("*.md"))


class TestCaminhoCompleto(SyncCase):
    def test_escreve_as_quatro_camadas_e_marca(self):
        self.assertEqual(self.rodar([msg("1", "olha https://e.com/a")]), 0)
        self.assertTrue((self.root / "10_FONTES").glob("FONTE - Discord estudos*"))
        self.assertEqual(len(self.notas()), 1)
        self.assertTrue((self.root / "40_DIARIO" / "2026-08-15.md").exists())
        self.assertTrue((self.root / "30_MOC" / "MOC - Captura Discord.md").exists())
        self.assertEqual(self.cliente.reacoes, [("1", "✅")])

    def test_o_cursor_avanca(self):
        self.rodar([msg("1", "https://e.com/a"), msg("9", "https://e.com/b")])
        self.assertEqual(self.cursor, "9")

    def test_o_link_da_mensagem_leva_o_servidor(self):
        """Regressão: sem `guild_id` o link sai como /channels/@me/ e não abre."""
        self.rodar([msg("1", "https://e.com/a")])
        nota = next((self.root / "50_LINKS").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("channels/999/42/1", nota)

    def test_mensagem_de_bot_e_ignorada(self):
        self.assertEqual(self.rodar([msg("1", "https://e.com/a", bot=True)]), 0)
        self.assertEqual(self.notas(), [])

    def test_mensagem_ja_marcada_e_pulada(self):
        self.rodar([msg("1", "https://e.com/a", reacoes=["✅"])])
        self.assertEqual(self.notas(), [])
        self.assertEqual(self.cursor, "1")   # o cursor sobe mesmo assim


class TestOrdemDeEscritaEMarcacao(SyncCase):
    """O invariante que o projeto mais teme quebrar."""

    def test_falha_na_escrita_nao_marca_e_nao_avanca_o_cursor(self):
        """Se a nota não entrou no vault, a mensagem tem que voltar na próxima
        execução. Marcar antes perderia o link para sempre."""
        from dlb.vault import Vault

        original = Vault.write_fonte
        Vault.write_fonte = lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio"))
        try:
            with self.assertRaises(OSError):
                self.rodar([msg("1", "https://e.com/a")])
        finally:
            Vault.write_fonte = original

        self.assertEqual(self.cliente.reacoes, [])
        self.assertIsNone(self.cursor)

    def test_falha_do_modelo_nao_marca_o_dia_e_devolve_codigo_1(self):
        from dlb.classify import ClassifyError

        def explode(cfg, hits, metas):
            raise ClassifyError("modelo fora do ar")

        cli.classify = explode
        self.assertEqual(self.rodar([msg("1", "https://e.com/a")]), 1)
        self.assertEqual(self.cliente.reacoes, [])
        self.assertEqual(self.notas(), [])

    def test_falha_ao_reagir_nao_derruba_a_execucao(self):
        """A marca é sinal para humano; o cursor é quem garante idempotência."""
        self.assertEqual(
            self.rodar([msg("1", "https://e.com/a")], explode_reacao=True), 0
        )
        self.assertEqual(len(self.notas()), 1)
        self.assertEqual(self.cursor, "1")


class TestDeduplicacao(SyncCase):
    def test_link_ja_arquivado_leva_a_marca_de_repetido_e_nao_vira_nota(self):
        self.rodar([msg("1", "https://e.com/a")])
        antes = self.notas()

        self.rodar([msg("2", "de novo https://e.com/a")])
        self.assertEqual(self.notas(), antes)
        self.assertIn(("2", "📚"), self.cliente.reacoes)

    def test_a_autoridade_do_dedup_e_10_FONTES(self):
        """Apagar a nota de link não faz o link voltar — a fonte é que manda."""
        self.rodar([msg("1", "https://e.com/a")])
        for p in (self.root / "50_LINKS").glob("*.md"):
            p.unlink()
        self.rodar([msg("2", "https://e.com/a")])
        self.assertEqual(self.notas(), [])
        self.assertIn(("2", "📚"), self.cliente.reacoes)

    def test_link_repetido_nao_gasta_chamada_de_modelo(self):
        self.rodar([msg("1", "https://e.com/a")])
        self.classificou.clear()
        self.rodar([msg("2", "https://e.com/a")])
        self.assertEqual(self.classificou, [])


class TestModoSeco(SyncCase):
    def test_dry_run_nao_escreve_nem_marca_nem_move_o_cursor(self):
        self.assertEqual(self.rodar([msg("1", "https://e.com/a")], ("sync", "--dry-run")), 0)
        self.assertEqual(self.notas(), [])
        self.assertFalse((self.root / "40_DIARIO" / "2026-08-15.md").exists())
        self.assertEqual(self.cliente.reacoes, [])
        self.assertIsNone(self.cursor)

    def test_dry_run_ainda_chama_o_modelo_para_voce_ver_o_resultado(self):
        self.rodar([msg("1", "https://e.com/a")], ("sync", "--dry-run"))
        self.assertEqual(self.classificou, [["https://e.com/a"]])


class TestEnriquecimento(SyncCase):
    def test_no_enrich_nao_abre_pagina_nenhuma(self):
        chamou = []
        cli.fetch_meta = lambda url, timeout=10.0: chamou.append(url) or PageMeta()
        self.rodar([msg("1", "https://e.com/a")], ("sync", "--no-enrich"))
        self.assertEqual(chamou, [])

    def test_pagina_que_explode_nao_derruba_o_link(self):
        """Falhar ao ler a página nunca interrompe: o pior caso é `legivel=False`."""
        def explode(url, timeout=10.0):
            raise RuntimeError("timeout")

        cli.fetch_meta = explode
        self.assertEqual(self.rodar([msg("1", "https://e.com/a")]), 0)
        self.assertEqual(len(self.notas()), 1)


class TestLinkNotesDesligado(SyncCase):
    def test_com_link_notes_0_o_diario_volta_a_ser_rico(self):
        self.rodar([msg("1", "https://e.com/a")], cfg=self.cfg(link_notes=False))
        self.assertEqual(self.notas(), [])
        diario = (self.root / "40_DIARIO" / "2026-08-15.md").read_text(encoding="utf-8")
        self.assertIn("Por que importa", diario)
        self.assertIn("Um resumo.", diario)


class TestSemNada(SyncCase):
    def test_canal_sem_mensagem_nova_nao_faz_nada(self):
        self.assertEqual(self.rodar([]), 0)
        self.assertIn("nada novo", self.saida)

    def test_mensagem_sem_link_avanca_o_cursor_e_para(self):
        self.rodar([msg("1", "bom dia, sem link nenhum")])
        self.assertEqual(self.cursor, "1")
        self.assertEqual(self.notas(), [])


if __name__ == "__main__":
    unittest.main()
