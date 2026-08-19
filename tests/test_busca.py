"""Rode com: python3 -m unittest discover -s tests -t .

A busca no acervo. O que se trava: a pergunta digitada sem acento acha a nota com
acento, o termo que aparece em tudo pesa menos que o raro, e — o mais importante —
**a URL da resposta vem do acervo, nunca do modelo**.
"""

import json
import unittest

import dlb.busca as mod
from dlb.busca import (
    Resposta,
    _ranquear_lexico,
    buscar,
    formatar_para_discord,
    selecionar,
    tokenizar,
)
from dlb.config import Config
from dlb.corpus import ler_links

from tests.test_vault import VaultCase, note


def cfg_falso(**kw):
    base = dict(discord_token="x", discord_token_type="bot", channel_ids=["1"])
    base.update(kw)
    return Config(**base)


class TestTokenizar(unittest.TestCase):
    def test_acento_e_caixa_desaparecem(self):
        self.assertEqual(tokenizar("Inteligência ARTIFICIAL"), ["inteligencia", "artificial"])

    def test_stopword_sai(self):
        """Pergunta em português é cheia delas e nenhuma discrimina nada."""
        self.assertEqual(tokenizar("o que eu salvei sobre isso"), ["salvei"])

    def test_pontuacao_nao_vira_token(self):
        self.assertEqual(tokenizar("agentes de IA?"), ["agentes", "ia"])


class BuscaCase(VaultCase):
    def setUp(self):
        super().setUp()
        notas = [
            note("https://e.com/rust", "Rust async e o runtime do Tokio", ["Nostr"]),
            note("https://e.com/guitarra", "Improvisação na guitarra", []),
            note("https://e.com/ia1", "Agentes de IA com contexto", []),
            note("https://e.com/ia2", "Mais um vídeo sobre IA", []),
        ]
        self.vault.write_link_notes("estudos", notas)
        self.acervo = ler_links(self.root)


class TestRanqueamento(BuscaCase):
    def test_pergunta_sem_acento_acha_nota_com_acento(self):
        ranking = _ranquear_lexico(cfg_falso(), "improvisacao guitarra", self.acervo)
        self.assertIn("Improvisação", ranking[0][1].titulo)

    def test_termo_raro_pesa_mais_que_termo_comum(self):
        """`IA` aparece em duas notas; `guitarra` em uma. O IDF é o que impede o
        termo onipresente de dominar o ranking."""
        ranking = dict(
            (e.titulo, s) for s, e in _ranquear_lexico(cfg_falso(), "IA guitarra", self.acervo)
        )
        self.assertGreater(ranking["Improvisação na guitarra"], ranking["Mais um vídeo sobre IA"])

    def test_prefixo_casa_plural(self):
        """`agente` acha `Agentes` sem precisar de stemmer."""
        ranking = _ranquear_lexico(cfg_falso(), "agente", self.acervo)
        self.assertIn("Agentes", ranking[0][1].titulo)

    def test_conceito_conta_no_ranking(self):
        ranking = _ranquear_lexico(cfg_falso(), "nostr", self.acervo)
        self.assertIn("Rust", ranking[0][1].titulo)

    def test_pergunta_so_com_stopword_devolve_tudo_por_recencia(self):
        ranking = _ranquear_lexico(cfg_falso(), "o que sobre isso", self.acervo)
        self.assertEqual(len(ranking), len(self.acervo))


class TestOrcamentoDePrompt(BuscaCase):
    def test_acervo_pequeno_entra_inteiro(self):
        """A tese do desenho: a 70 notas o corte nunca dispara e o ranking é
        um no-op. Mandar tudo tem recall melhor que top-k."""
        selecionadas, truncou = selecionar(cfg_falso(), "IA", self.acervo)
        self.assertEqual(len(selecionadas), len(self.acervo))
        self.assertFalse(truncou)

    def test_acima_do_orcamento_corta_pelo_score_e_avisa(self):
        cfg = cfg_falso(busca_max_chars=200)
        selecionadas, truncou = selecionar(cfg, "guitarra", self.acervo)
        self.assertTrue(truncou)
        self.assertLess(len(selecionadas), len(self.acervo))
        # O que sobra é o mais relevante, não o primeiro do diretório.
        self.assertIn("guitarra", selecionadas[0].titulo.lower())

    def test_backend_desconhecido_explode(self):
        with self.assertRaises(mod.BuscaError):
            selecionar(cfg_falso(busca_backend="embeddings"), "x", self.acervo)


class TestResposta(BuscaCase):
    def responder(self, payload):
        mod._run_codex = lambda cfg, p, s, schema=None: json.dumps(payload)

    def setUp(self):
        super().setUp()
        self._original = mod._run_codex

    def tearDown(self):
        mod._run_codex = self._original
        super().tearDown()

    def test_resposta_limpa_vira_objeto(self):
        self.responder({"resposta": "Você salvou X.", "usados": [0], "confianca": "alta"})
        r = buscar(cfg_falso(), "rust", self.acervo)
        self.assertEqual(r.texto, "Você salvou X.")
        self.assertEqual(r.confianca, "alta")
        self.assertEqual(len(r.usados), 1)

    def test_id_fora_da_faixa_e_descartado(self):
        """É o que impede a resposta de citar link que não existe."""
        self.responder({"resposta": "x", "usados": [0, 99, -3], "confianca": "alta"})
        r = buscar(cfg_falso(), "rust", self.acervo)
        self.assertEqual(len(r.usados), 1)

    def test_a_url_da_resposta_vem_do_acervo_e_nao_do_modelo(self):
        """O modelo inventa uma URL; ela não pode aparecer na mensagem final."""
        self.responder({
            "resposta": "Veja https://inventado.example/nao-existe",
            "usados": [0],
            "confianca": "alta",
        })
        r = buscar(cfg_falso(), "rust", self.acervo)
        texto = formatar_para_discord(r)
        # A URL fabricada só sobrevive como texto da prosa, nunca como link do
        # acervo — e a lista de fontes tem só endereços nossos.
        fontes = texto.split("no seu acervo:", 1)[1]
        self.assertNotIn("inventado.example", fontes)
        self.assertIn("e.com", fontes)

    def test_a_url_nao_vai_no_prompt(self):
        vistos = {}
        mod._run_codex = lambda cfg, p, s, schema=None: (
            vistos.setdefault("prompt", p),
            json.dumps({"resposta": "x", "usados": [], "confianca": "nenhuma"}),
        )[1]
        buscar(cfg_falso(), "rust", self.acervo)
        self.assertNotIn("https://e.com/rust", vistos["prompt"])
        self.assertIn('"id": 0', vistos["prompt"])

    def test_confianca_invalida_cai_para_nenhuma(self):
        self.responder({"resposta": "x", "usados": [], "confianca": "altissima"})
        self.assertEqual(buscar(cfg_falso(), "x", self.acervo).confianca, "nenhuma")

    def test_acervo_vazio_responde_com_franqueza_sem_gastar_chamada(self):
        chamou = []
        mod._run_codex = lambda *a, **k: chamou.append(1)
        r = buscar(cfg_falso(), "qualquer coisa", [])
        self.assertEqual(r.confianca, "nenhuma")
        self.assertEqual(chamou, [])

    def test_pergunta_vazia_explode(self):
        with self.assertRaises(mod.BuscaError):
            buscar(cfg_falso(), "   ", self.acervo)


class TestFormatacao(unittest.TestCase):
    def test_sem_fonte_nao_gera_secao_de_acervo(self):
        texto = formatar_para_discord(Resposta(texto="Não achei nada.", confianca="nenhuma"))
        self.assertNotIn("no seu acervo", texto)

    def test_truncamento_e_dito_ao_leitor(self):
        r = Resposta(texto="x", confianca="alta", considerados=42, truncado=True)
        self.assertIn("42", formatar_para_discord(r))

    def test_resposta_vazia_nao_sai_como_mensagem_em_branco(self):
        self.assertIn("Não consegui", formatar_para_discord(Resposta()))


if __name__ == "__main__":
    unittest.main()
