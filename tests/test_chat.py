"""Rode com: python3 -m unittest discover -s tests -t .

O chat do painel: contenção, não busca.

A busca em si é `busca.buscar()`, já coberta por `tests/test_busca.py`. O que se
trava aqui é o que o painel acrescenta, e cada item existe por causa da mesma
restrição: **o modelo roda por binário local já logado, sem chave de API**, e o
painel é o terceiro consumidor da mesma janela de uso — depois da classificação
diária e do responder. Se ele come a quota, a classificação da noite falha e o
painel reporta um erro que ele mesmo causou.

O critério de pronto da fase, escrito no plano: *duas perguntas simultâneas — a
segunda enfileira e não nasce um segundo `codex`*. É `TestFilaDeUm`.
"""

import json
import threading
import time
import unittest

from dlb import busca, chat
from dlb.config import Config

from tests.test_vault import VaultCase, note


class ChatCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.state = self.root / ".state"
        self.cfg = Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.state,
        )
        self.acervo = []
        self.chamadas = []
        self.original = busca.buscar
        busca.buscar = self.buscar_falso
        self.chat = chat.Chat(self.cfg, lambda: self.acervo)

    def tearDown(self):
        busca.buscar = self.original
        super().tearDown()

    def buscar_falso(self, cfg, pergunta, entradas):
        """Dublê do modelo. Registra a chamada e nunca abre subprocess."""
        self.chamadas.append(pergunta)
        return busca.Resposta(
            texto=f"resposta para {pergunta}", confianca="alta", considerados=len(entradas)
        )


class TestRespostaNormal(ChatCase):
    def test_devolve_o_que_busca_buscar_devolveu(self):
        """Sem segundo mecanismo de busca: a mesma função de `dlb buscar`, o mesmo
        acervo, a mesma resposta. Duas implementações divergiriam no dia em que
        alguém ajustasse o prompt de uma delas."""
        registro = self.chat.perguntar("o que eu sei de nostr?")
        self.assertEqual(registro.texto, "resposta para o que eu sei de nostr?")
        self.assertEqual(registro.confianca, "alta")
        self.assertTrue(registro.chamou_modelo)
        self.assertEqual(registro.erro, "")
        self.assertEqual(self.chamadas, ["o que eu sei de nostr?"])

    def test_o_historico_guarda_o_mais_recente_no_topo(self):
        for pergunta in ("primeira", "segunda"):
            self.chat.perguntar(pergunta)
        self.assertEqual([r.pergunta for r in self.chat.historico], ["segunda", "primeira"])

    def test_o_historico_nao_cresce_para_sempre(self):
        chat_curto = chat.Chat(self.cfg, lambda: self.acervo, historico_max=2)
        for i in range(5):
            chat_curto.perguntar(f"p{i}")
        self.assertEqual([r.pergunta for r in chat_curto.historico], ["p4", "p3"])

    def test_os_links_usados_vem_do_acervo_e_nao_do_modelo(self):
        """A garantia que `busca.py` já dá e que a tela não pode perder: URL na
        resposta é URL de nota que existe."""
        self.vault.write_link_note("estudos", note("https://e.com/a", "Relays", []))
        from dlb.corpus import ler_links

        self.acervo = ler_links(self.root)
        busca.buscar = lambda cfg, p, e: busca.Resposta(
            texto="olha", confianca="alta", usados=list(e), considerados=len(e)
        )
        registro = self.chat.perguntar("relays?")
        self.assertEqual(registro.usados[0]["url"], "https://e.com/a")


class TestPerguntaRuim(ChatCase):
    def test_pergunta_vazia_nao_chama_o_modelo(self):
        for vazia in ("", "   ", "\n"):
            with self.subTest(repr(vazia)):
                registro = self.chat.perguntar(vazia)
                self.assertEqual(registro.erro, "pergunta vazia")
        self.assertEqual(self.chamadas, [])

    def test_pergunta_gigante_e_recusada_com_o_mesmo_teto_do_discord(self):
        """500 caracteres é o teto do `responder`. Dois tetos diferentes para a
        mesma pergunta seria configuração que mente."""
        registro = self.chat.perguntar("a" * 501)
        self.assertIn("501 caracteres", registro.erro)
        self.assertIn("500", registro.erro)
        self.assertEqual(self.chamadas, [])

    def test_erro_do_modelo_vira_texto_e_nao_excecao(self):
        """A tela é aberta justamente quando algo está estranho: `BuscaError` não
        pode virar 500."""
        def explode(cfg, p, e):
            raise busca.BuscaError("codex fora do ar")

        busca.buscar = explode
        registro = self.chat.perguntar("e agora?")
        self.assertEqual(registro.erro, "codex fora do ar")
        self.assertTrue(registro.chamou_modelo)   # a quota foi gasta de qualquer forma

    def test_excecao_inesperada_tambem_e_capturada(self):
        busca.buscar = lambda cfg, p, e: 1 / 0
        registro = self.chat.perguntar("e agora?")
        self.assertIn("ZeroDivisionError", registro.erro)


class TestFilaDeUm(ChatCase):
    """O critério de pronto da fase.

    `flock` não serve aqui: a contenção é dentro de um processo, entre threads do
    `ThreadingHTTPServer`. O que se prova é que **um segundo `codex` não nasce**.

    Sem `sleep` para sincronizar: o dublê do modelo bloqueia num `Event` que o
    teste controla, e o teste espera por *condição*, não por tempo. Teste de
    concorrência cronometrado passa na máquina vazia e falha na suíte cheia, que
    é o pior dos dois mundos — parece flakiness e é ambiguidade.
    """

    def setUp(self):
        super().setUp()
        self.liberar = threading.Event()
        self.entraram = threading.Semaphore(0)
        self.simultaneos = []
        self._agora = 0
        self._conta = threading.Lock()
        busca.buscar = self._modelo_bloqueado

    def _modelo_bloqueado(self, cfg, pergunta, entradas):
        with self._conta:
            self._agora += 1
            self.simultaneos.append(self._agora)
        self.chamadas.append(pergunta)
        self.entraram.release()
        try:
            assert self.liberar.wait(timeout=10), "o teste não liberou o modelo"
            return busca.Resposta(texto=f"resposta para {pergunta}", confianca="alta")
        finally:
            with self._conta:
                self._agora -= 1

    def ate(self, condicao, o_que: str, limite: float = 10.0):
        """Espera por condição, com teto. Falha dizendo o que não aconteceu."""
        fim = time.monotonic() + limite
        while time.monotonic() < fim:
            if condicao():
                return
            time.sleep(0.005)
        self.fail(f"tempo esgotado esperando: {o_que}")

    def em_thread(self, pergunta, saida):
        t = threading.Thread(target=lambda: saida.append(self.chat.perguntar(pergunta)))
        t.start()
        return t

    def test_duas_perguntas_simultaneas_nao_chamam_o_modelo_em_paralelo(self):
        saida = []
        primeira = self.em_thread("pergunta 0", saida)
        self.entraram.acquire(timeout=10)                      # a 1a está no modelo
        segunda = self.em_thread("pergunta 1", saida)
        self.ate(lambda: self.chat._esperando == 2, "a 2a entrar na fila")

        # Enquanto a primeira não termina, o modelo foi chamado **uma** vez.
        self.assertEqual(len(self.chamadas), 1)

        self.liberar.set()
        for t in (primeira, segunda):
            t.join(timeout=10)

        self.assertEqual(len(self.chamadas), 2)          # as duas foram respondidas
        # Cada chamada viu concorrência 1: é a prova de serialização, e é livre de
        # relógio. A versão anterior media `esperou_s > 0` numa das respostas e
        # falhava em ~1 de 4 execuções — porque `_esperando` sobe **antes** do
        # `acquire`, então o teste liberava o modelo antes de a segunda thread ter
        # começado a esperar de verdade, e a espera arredondava para 0,0 s. O
        # defeito era do teste; o código sempre serializou.
        self.assertEqual(self.simultaneos, [1, 1])

    def test_a_terceira_e_recusada_em_vez_de_empilhar(self):
        """Fila de um: empilhar dez pedidos numa fila que consome quota é como se
        gasta a janela de uso sem perceber."""
        saida = []
        primeira = self.em_thread("a", saida)
        self.entraram.acquire(timeout=10)
        segunda = self.em_thread("b", saida)
        self.ate(lambda: self.chat._esperando == 2, "a 2a entrar na fila")

        terceira = self.chat.perguntar("c")               # na thread do teste
        self.assertIn("Espere a atual", terceira.erro)
        self.assertFalse(terceira.chamou_modelo)

        self.liberar.set()
        for t in (primeira, segunda):
            t.join(timeout=10)
        self.assertEqual(self.chamadas, ["a", "b"])

    def test_espera_que_estoura_devolve_mensagem_em_vez_de_pendurar(self):
        self.cfg.painel_chat_espera_s = 0.05
        saida = []
        primeira = self.em_thread("lenta", saida)
        self.entraram.acquire(timeout=10)

        registro = self.chat.perguntar("apressada")
        self.assertIn("na fila", registro.erro)
        self.assertFalse(registro.chamou_modelo)

        self.liberar.set()
        primeira.join(timeout=10)

    def test_o_semaforo_e_liberado_mesmo_quando_o_modelo_explode(self):
        """Sem isto, um erro trancaria o chat até reiniciar o painel."""
        busca.buscar = lambda cfg, p, e: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertIn("boom", self.chat.perguntar("primeira").erro)
        busca.buscar = lambda cfg, p, e: busca.Resposta(texto="voltei", confianca="alta")
        self.assertEqual(self.chat.perguntar("segunda").texto, "voltei")


class TestTetoPorHora(ChatCase):
    def test_recusa_ao_atingir_o_teto(self):
        self.cfg.painel_chat_max_por_hora = 2
        for _ in range(2):
            self.chat.perguntar("ok")
        registro = self.chat.perguntar("uma a mais")
        self.assertIn("teto de 2 chamadas por hora", registro.erro)
        self.assertEqual(len(self.chamadas), 2)

    def test_o_teto_sobrevive_a_reinicio_do_painel(self):
        """De propósito: o teto protege a quota, e quota não reinicia junto com o
        processo. Contar em memória deixaria o limite ser burlado por `Ctrl-C`."""
        self.cfg.painel_chat_max_por_hora = 1
        self.chat.perguntar("primeira")
        outro = chat.Chat(self.cfg, lambda: self.acervo)
        self.assertIn("teto de 1", outro.perguntar("segunda").erro)

    def test_chamada_velha_sai_da_conta(self):
        uso = chat.Uso(self.state)
        agora = time.time()
        uso._gravar([agora - 7200, agora - 30])
        self.assertEqual(len(uso.na_ultima_hora(agora)), 1)

    def test_arquivo_de_uso_corrompido_nao_derruba_o_chat(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / chat.ARQUIVO_USO).write_text("{lixo", encoding="utf-8")
        self.assertEqual(self.chat.perguntar("vai?").texto, "resposta para vai?")

    def test_o_arquivo_de_uso_e_so_do_painel(self):
        """`state.py` exige escritor único por arquivo. O painel não escreve no
        cursor de canal nem no do responder — tem o seu."""
        self.chat.perguntar("x")
        dados = json.loads((self.state / "painel.json").read_text(encoding="utf-8"))
        self.assertEqual(len(dados["chamadas"]), 1)
        self.assertIn("atualizado", dados)
        self.assertEqual(
            sorted(p.name for p in self.state.glob("*.json")), ["painel.json"]
        )

    def test_a_situacao_mostra_o_consumo(self):
        self.chat.perguntar("x")
        situacao = self.chat.situacao()
        self.assertEqual(situacao["chamadas_na_hora"], 1)
        self.assertEqual(situacao["teto_por_hora"], 20)
        self.assertFalse(situacao["ocupado"])


if __name__ == "__main__":
    unittest.main()
