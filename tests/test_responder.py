"""Rode com: python3 -m unittest discover -s tests -t .

O responder é a primeira coisa neste projeto que **fala** — e falar não tem
desfazer. Cada teste aqui trava um jeito de o bot se comportar mal em público:
responder duas vezes, responder a si mesmo, mencionar gente, ou marcar uma
mensagem como resolvida sem ter respondido.

Nenhum teste toca a rede: o cliente é um dublê que grava as chamadas.
"""

import unittest

import dlb.busca as busca_mod
import dlb.responder as mod
from dlb.busca import Resposta
from dlb.config import Config
from dlb.discord_client import partir_para_discord
from dlb.state import ChannelState, Store

from tests.test_vault import VaultCase, note


def cfg_falso(**kw):
    base = dict(
        discord_token="x", discord_token_type="bot", channel_ids=["42"],
        responder_channel_ids=["42"], post_enabled=True,
    )
    base.update(kw)
    return Config(**base)


def msg(mid, content, autor="Jads", autor_id="7", bot=False, reacoes=None):
    m = {
        "id": mid,
        "content": content,
        "author": {"username": autor, "id": autor_id, "bot": bot},
    }
    if reacoes:
        m["reactions"] = reacoes
    return m


def reacao(emoji, minha=True):
    return {"emoji": {"name": emoji}, "me": minha}


class ClienteFalso:
    """Grava tudo em vez de falar com o Discord."""

    def __init__(self, mensagens, explode_post=False):
        self.mensagens = mensagens
        self.postadas: list[tuple[str, str, str | None]] = []
        self.reacoes: list[tuple[str, str, str]] = []
        self.removidas: list[tuple[str, str, str]] = []
        self.explode_post = explode_post

    def me(self):
        return {"id": "BOT"}

    def iter_messages(self, channel_id, after=None, limit_total=None):
        return list(self.mensagens)

    def post_message(self, channel_id, content, responder_a=None):
        if self.explode_post:
            from dlb.discord_client import DiscordError

            raise DiscordError("403 ao postar: sem permissão")
        self.postadas.append((channel_id, content, responder_a))
        return {"id": "resposta1"}

    def add_reaction(self, channel_id, message_id, emoji):
        self.reacoes.append((channel_id, message_id, emoji))

    def remove_reaction(self, channel_id, message_id, emoji):
        self.removidas.append((channel_id, message_id, emoji))


class ResponderCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.vault.write_link_note("estudos", note("https://e.com/a", "Relays", ["Nostr"]))
        self.store = Store(self.root / ".state", prefix="responder-")
        self._original = busca_mod.buscar
        busca_mod.buscar = self.buscar_falso
        mod.buscar = self.buscar_falso

    def tearDown(self):
        busca_mod.buscar = self._original
        mod.buscar = self._original
        super().tearDown()

    def buscar_falso(self, cfg, pergunta, entradas):
        self.perguntado = pergunta
        return Resposta(texto="Achei isto.", usados=[], confianca="alta")

    def rodar(self, mensagens, cfg=None, **kw):
        cliente = ClienteFalso(mensagens, **kw)
        cfg = cfg or cfg_falso(vault_path=self.root, state_dir=self.root / ".state")
        conta = mod.uma_volta(cfg, cliente, self.store, log=lambda *a: None)
        return cliente, conta


class TestNuncaRespondeDuasVezes(ResponderCase):
    def test_mensagem_ja_marcada_e_pulada(self):
        marcada = msg("1", "?buscar nostr", reacoes=[reacao("💬")])
        cliente, conta = self.rodar([marcada])
        self.assertEqual(conta.respondidas, 0)
        self.assertEqual(cliente.postadas, [])

    def test_reacao_de_humano_com_o_mesmo_emoji_nao_conta(self):
        """`ja_marcada` só considera reação com `me: true`. Alguém reagindo 💬 na
        mão não pode fazer o bot achar que já respondeu."""
        alheia = msg("1", "?buscar nostr", reacoes=[reacao("💬", minha=False)])
        cliente, conta = self.rodar([alheia])
        self.assertEqual(conta.respondidas, 1)

    def test_marca_a_mensagem_depois_de_responder(self):
        cliente, _ = self.rodar([msg("1", "?buscar nostr")])
        emojis = [e for _, _, e in cliente.reacoes]
        self.assertIn("💬", emojis)

    def test_a_marca_de_pendente_e_removida_no_fim(self):
        cliente, _ = self.rodar([msg("1", "?buscar nostr")])
        self.assertIn(("42", "1", "⏳"), cliente.removidas)


class TestNuncaRespondeASiMesmo(ResponderCase):
    def test_mensagem_de_bot_e_ignorada(self):
        cliente, conta = self.rodar([msg("1", "?buscar nostr", bot=True)])
        self.assertEqual(conta.respondidas, 0)

    def test_mensagem_do_proprio_bot_por_id_e_ignorada(self):
        """Cinto além do suspensório: a checagem que mais importa não depende de
        o Discord ter marcado a flag `bot`."""
        propria = msg("1", "?buscar nostr", autor_id="BOT", bot=False)
        cliente, conta = self.rodar([propria])
        self.assertEqual(conta.respondidas, 0)

    def test_a_propria_resposta_nao_dispara_outra(self):
        """Nossas respostas nunca começam com o prefixo — é a terceira barreira."""
        cliente, conta = self.rodar([msg("1", "Achei isto. 📚 no seu acervo:")])
        self.assertEqual(conta.respondidas, 0)


class TestGatilho(ResponderCase):
    def test_mensagem_sem_prefixo_e_ignorada(self):
        _, conta = self.rodar([msg("1", "olha esse link https://e.com/x")])
        self.assertEqual(conta.respondidas, 0)

    def test_atalho_curto_funciona(self):
        _, conta = self.rodar([msg("1", "?b nostr")])
        self.assertEqual(conta.respondidas, 1)

    def test_prefixo_sozinho_sem_pergunta_nao_gasta_chamada(self):
        _, conta = self.rodar([msg("1", "?buscar")])
        self.assertEqual(conta.respondidas, 0)

    def test_pergunta_e_truncada_no_teto(self):
        cfg = cfg_falso(
            vault_path=self.root, state_dir=self.root / ".state",
            responder_max_pergunta=10,
        )
        self.rodar([msg("1", "?buscar " + "a" * 500)], cfg=cfg)
        self.assertEqual(len(self.perguntado), 10)


class TestSoMarcaDepoisDePostar(ResponderCase):
    def test_falha_no_post_nao_marca_como_respondida(self):
        """Mesma regra do sync com o vault: se a escrita explode, a mensagem fica
        sem marca e volta na próxima volta."""
        cliente, conta = self.rodar([msg("1", "?buscar nostr")], explode_post=True)
        self.assertEqual(conta.respondidas, 0)
        self.assertNotIn("💬", [e for _, _, e in cliente.reacoes])
        self.assertTrue(conta.erros)

    def test_falha_no_post_tira_a_marca_de_pendente(self):
        """Senão o ⏳ fica pendurado para sempre numa pergunta não respondida."""
        cliente, _ = self.rodar([msg("1", "?buscar nostr")], explode_post=True)
        self.assertIn(("42", "1", "⏳"), cliente.removidas)

    def test_pendente_e_marcado_antes_da_chamada(self):
        """Em um ciclo a pessoa vê que foi lida, mesmo que a resposta leve 60s."""
        cliente, _ = self.rodar([msg("1", "?buscar nostr")])
        emojis = [e for _, _, e in cliente.reacoes]
        self.assertLess(emojis.index("⏳"), emojis.index("💬"))


class TestCursor(ResponderCase):
    def test_o_cursor_sobe_mesmo_sem_pergunta_nenhuma(self):
        """Senão cada volta relê as mesmas mensagens para sempre."""
        self.rodar([msg("1", "oi"), msg("9", "tchau")])
        self.assertEqual(self.store.load("42").last_message_id, "9")

    def test_o_cursor_do_responder_vive_em_arquivo_proprio(self):
        """Dois processos, dois arquivos: nenhum arquivo com dois escritores."""
        sync = Store(self.root / ".state")
        sync.save("42", ChannelState(last_message_id="100"))
        self.rodar([msg("777", "?buscar nostr")])
        self.assertEqual(sync.load("42").last_message_id, "100")
        self.assertEqual(self.store.load("42").last_message_id, "777")


class TestLimitePorHora(ResponderCase):
    def test_acima_do_teto_recusa_sem_gastar_chamada(self):
        cfg = cfg_falso(
            vault_path=self.root, state_dir=self.root / ".state",
            responder_max_por_hora=1,
        )
        cliente, conta = self.rodar(
            [msg("1", "?buscar a"), msg("2", "?buscar b")], cfg=cfg
        )
        self.assertEqual(conta.respondidas, 1)
        self.assertEqual(conta.recusadas, 1)
        self.assertIn("❌", [e for _, _, e in cliente.reacoes])


class TestResposta(ResponderCase):
    def test_a_resposta_e_um_reply_a_pergunta(self):
        cliente, _ = self.rodar([msg("1", "?buscar nostr")])
        _canal, _texto, responder_a = cliente.postadas[0]
        self.assertEqual(responder_a, "1")

    def test_resposta_longa_sai_em_pedacos_em_ordem(self):
        mod.buscar = lambda cfg, p, e: Resposta(
            texto="\n\n".join(f"Parágrafo {i} bem comprido." for i in range(200)),
            confianca="alta",
        )
        cliente, _ = self.rodar([msg("1", "?buscar nostr")])
        self.assertGreater(len(cliente.postadas), 1)
        for _c, texto, _r in cliente.postadas:
            self.assertLessEqual(len(texto), 1900)
        self.assertIn("Parágrafo 0", cliente.postadas[0][1])
        # Só o primeiro pedaço é reply; o resto vem solto.
        self.assertIsNone(cliente.postadas[1][2])


class TestPartirMensagem(unittest.TestCase):
    def test_texto_curto_sai_inteiro_sem_marcador(self):
        self.assertEqual(partir_para_discord("oi"), ["oi"])

    def test_nenhum_pedaco_passa_do_limite(self):
        texto = "palavra " * 2000
        for p in partir_para_discord(texto):
            self.assertLessEqual(len(p), 1900)

    def test_o_marcador_cabe_dentro_do_limite(self):
        """Regressão: o marcador entrava depois do corte e estourava em 1 char."""
        for p in partir_para_discord("x" * 9000):
            self.assertLessEqual(len(p), 1900)

    def test_url_nao_e_partida_no_meio(self):
        import re

        texto = "\n\n".join(
            f"Item {i}: https://exemplo.com/um/caminho/bem/longo/{i}" for i in range(120)
        )
        for p in partir_para_discord(texto):
            miolo = p.split("\n\n…")[0].rstrip()
            self.assertIsNone(re.search(r"https?://\S*$", miolo + " ") and None)

    def test_texto_vazio_nao_gera_mensagem(self):
        self.assertEqual(partir_para_discord("   "), [])


class TestMarcasNaoColidem(unittest.TestCase):
    """A armadilha entre features. Cada uma destas falha em silêncio."""

    def test_marca_do_responder_nao_faz_o_sync_pular_a_mensagem(self):
        from dlb.discord_client import ja_marcada

        cfg = cfg_falso()
        so_responder = msg("1", "?buscar x https://e.com/y", reacoes=[reacao("💬")])
        self.assertFalse(ja_marcada(so_responder, *cfg.marcas))

    def test_marca_do_sync_nao_faz_o_responder_pular_a_pergunta(self):
        from dlb.discord_client import ja_marcada

        cfg = cfg_falso()
        so_sync = msg("1", "?buscar x", reacoes=[reacao("✅")])
        self.assertFalse(ja_marcada(so_sync, *cfg.marcas_responder))

    def test_os_dois_vocabularios_sao_disjuntos_por_padrao(self):
        cfg = cfg_falso()
        self.assertEqual(set(cfg.marcas) & set(cfg.marcas_responder), set())
        cfg.validar_marcas()

    def test_emoji_repetido_entre_as_duas_e_erro_de_configuracao(self):
        from dlb.config import ConfigError

        cfg = cfg_falso(responder_emoji="✅")
        with self.assertRaises(ConfigError):
            cfg.validar_marcas()


if __name__ == "__main__":
    unittest.main()
