"""Rode com: python3 -m unittest discover -s tests -t .

A escada de retry do cliente do Discord.

Nunca tinha teste porque `test_bordas` troca o `_get` inteiro por um dublê — o que
é certo para testar paginação, e deixa a lógica **dentro** do `_get` sem nenhuma
cobertura. Justamente a parte cuja falha tem consequência fora da nossa máquina:
tratar 429 errado significa martelar a API do Discord e levar bloqueio.

Aqui o dublê é uma sessão falsa, então a escada roda de verdade. `time.sleep` é
substituído para a suíte não esperar de fato, e as esperas são conferidas — dormir
o tempo errado é tão ruim quanto não dormir.
"""

import unittest

import dlb.discord_client as mod
from dlb.discord_client import API, DiscordClient, DiscordError


class RespostaFalsa:
    def __init__(self, status, corpo=None, headers=None, texto="{}"):
        self.status_code = status
        self._corpo = corpo if corpo is not None else {}
        self.headers = headers or {}
        self.content = texto.encode()

    def json(self):
        return self._corpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"raise_for_status inesperado em {self.status_code}")


class SessaoFalsa:
    """Devolve uma resposta programada por chamada. Não toca a rede."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.headers = {}

    def _proxima(self, metodo, url, **kw):
        self.chamadas.append((metodo, url, kw))
        if not self.respostas:
            raise AssertionError("chamadas além do programado")
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, **kw):
        return self._proxima("GET", url, **kw)

    def put(self, url, **kw):
        return self._proxima("PUT", url, **kw)

    def post(self, url, **kw):
        return self._proxima("POST", url, **kw)

    def delete(self, url, **kw):
        return self._proxima("DELETE", url, **kw)


class RetryCase(unittest.TestCase):
    def setUp(self):
        self.dormiu = []
        self._sleep = mod.time.sleep
        mod.time.sleep = self.dormiu.append

    def tearDown(self):
        mod.time.sleep = self._sleep

    def cliente(self, respostas):
        c = DiscordClient("Bot x")
        c.session = SessaoFalsa(respostas)
        return c


class TestRateLimit(RetryCase):
    def test_429_espera_o_retry_after_e_tenta_de_novo(self):
        c = self.cliente([
            RespostaFalsa(429, {"retry_after": 2.0}, {"Retry-After": "2"}),
            RespostaFalsa(200, [{"id": "1"}]),
        ])
        self.assertEqual(c._get("/x"), [{"id": "1"}])
        self.assertEqual(len(self.dormiu), 1)
        self.assertAlmostEqual(self.dormiu[0], 2.25)   # +0.25 de folga

    def test_o_corpo_da_resposta_manda_no_cabecalho(self):
        """O Discord às vezes pede mais no JSON do que no header."""
        c = self.cliente([
            RespostaFalsa(429, {"retry_after": 5.0}, {"Retry-After": "1"}),
            RespostaFalsa(200, []),
        ])
        c._get("/x")
        self.assertAlmostEqual(self.dormiu[0], 5.25)

    def test_espera_absurda_e_limitada(self):
        """Um `Retry-After` de uma hora não pode travar o agendador."""
        c = self.cliente([
            RespostaFalsa(429, {"retry_after": 3600.0}, {"Retry-After": "3600"}),
            RespostaFalsa(200, []),
        ])
        c._get("/x")
        self.assertLessEqual(self.dormiu[0], 30.25)

    def test_corpo_ilegivel_cai_para_o_cabecalho(self):
        r = RespostaFalsa(429, None, {"Retry-After": "3"})
        r.json = lambda: (_ for _ in ()).throw(ValueError("não é json"))
        c = self.cliente([r, RespostaFalsa(200, [])])
        c._get("/x")
        self.assertAlmostEqual(self.dormiu[0], 3.25)


class TestErroDoServidor(RetryCase):
    def test_5xx_recua_progressivamente(self):
        c = self.cliente([
            RespostaFalsa(500), RespostaFalsa(503), RespostaFalsa(200, []),
        ])
        c._get("/x")
        # Recuo crescente: 1.5, 3.0 — não pode ser constante.
        self.assertEqual(self.dormiu, [1.5, 3.0])

    def test_desiste_depois_de_insistir_e_diz_onde(self):
        c = self.cliente([RespostaFalsa(500) for _ in range(6)])
        with self.assertRaises(DiscordError) as ctx:
            c._get("/channels/42")
        self.assertIn("/channels/42", str(ctx.exception))


class TestErroQueNaoAdiantaInsistir(RetryCase):
    def test_403_explode_na_hora_dizendo_qual_permissao(self):
        c = self.cliente([RespostaFalsa(403)])
        with self.assertRaises(DiscordError) as ctx:
            c._get("/channels/42")
        self.assertIn("permissão", str(ctx.exception))
        self.assertEqual(self.dormiu, [])          # não insistiu

    def test_401_explode_na_hora(self):
        c = self.cliente([RespostaFalsa(401)])
        with self.assertRaises(DiscordError):
            c._get("/x")
        self.assertEqual(self.dormiu, [])

    def test_404_diz_que_o_canal_nao_existe(self):
        c = self.cliente([RespostaFalsa(404)])
        with self.assertRaises(DiscordError) as ctx:
            c._get("/channels/999")
        self.assertIn("não encontrado", str(ctx.exception))


class TestReagir(RetryCase):
    def test_429_ao_reagir_tambem_espera(self):
        c = self.cliente([
            RespostaFalsa(429, {}, {"Retry-After": "1"}), RespostaFalsa(204),
        ])
        c.add_reaction("42", "1", "✅")
        self.assertEqual(len(self.dormiu), 1)

    def test_403_ao_reagir_nomeia_a_permissao_que_falta(self):
        c = self.cliente([RespostaFalsa(403)])
        with self.assertRaises(DiscordError) as ctx:
            c.add_reaction("42", "1", "✅")
        self.assertIn("Adicionar reações", str(ctx.exception))

    def test_emoji_vai_percent_encoded_na_url(self):
        """`✅` cru na URL quebra a requisição."""
        c = self.cliente([RespostaFalsa(204)])
        c.add_reaction("42", "1", "✅")
        _metodo, url, _kw = c.session.chamadas[0]
        self.assertNotIn("✅", url)
        self.assertIn("%E2%9C%85", url)

    def test_remover_reacao_que_nao_existia_e_silencioso(self):
        c = self.cliente([RespostaFalsa(404)])
        c.remove_reaction("42", "1", "✅")          # não levanta


class TestPostar(RetryCase):
    def test_429_ao_postar_espera_e_reenvia(self):
        c = self.cliente([
            RespostaFalsa(429, {"retry_after": 1.0}, {"Retry-After": "1"}),
            RespostaFalsa(200, {"id": "m1"}),
        ])
        self.assertEqual(c.post_message("42", "oi")["id"], "m1")
        self.assertEqual(len(self.dormiu), 1)

    def test_403_ao_postar_diz_o_inteiro_de_permissao(self):
        c = self.cliente([RespostaFalsa(403)])
        with self.assertRaises(DiscordError) as ctx:
            c.post_message("42", "oi")
        self.assertIn("68672", str(ctx.exception))

    def test_o_bot_nunca_consegue_mencionar_ninguem(self):
        """Mais barato desligar a capacidade do que filtrar o conteúdo."""
        c = self.cliente([RespostaFalsa(200, {"id": "m1"})])
        c.post_message("42", "oi @everyone")
        _m, _u, kw = c.session.chamadas[0]
        self.assertEqual(kw["json"]["allowed_mentions"], {"parse": []})

    def test_reply_referencia_a_mensagem_e_tolera_apagada(self):
        c = self.cliente([RespostaFalsa(200, {"id": "m1"})])
        c.post_message("42", "oi", responder_a="99")
        ref = c.session.chamadas[0][2]["json"]["message_reference"]
        self.assertEqual(ref["message_id"], "99")
        self.assertFalse(ref["fail_if_not_exists"])

    def test_5xx_ao_postar_recua_e_tenta_de_novo(self):
        c = self.cliente([RespostaFalsa(502), RespostaFalsa(200, {"id": "m1"})])
        c.post_message("42", "oi")
        self.assertEqual(self.dormiu, [1.5])

    def test_apagar_mensagem_inexistente_e_silencioso(self):
        c = self.cliente([RespostaFalsa(404)])
        c.delete_message("42", "1")


class TestCabecalhos(unittest.TestCase):
    def test_o_token_e_o_user_agent_vao_na_sessao(self):
        c = DiscordClient("Bot segredo")
        self.assertEqual(c.session.headers["Authorization"], "Bot segredo")
        self.assertIn("discord-link-brain", c.session.headers["User-Agent"])

    def test_a_url_base_e_a_v10(self):
        self.assertIn("/v10", API)


if __name__ == "__main__":
    unittest.main()
