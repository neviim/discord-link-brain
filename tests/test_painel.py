"""Rode com: python3 -m unittest discover -s tests -t .

O servidor do painel, subido de verdade em porta efêmera e consultado por HTTP.

Não é teste de unidade das views: é o fio inteiro — rota, leitura do disco,
montagem do HTML, cabeçalho de resposta. É o que pega o erro que teste de unidade
não pega, do tipo "a rota existe mas o handler chama um atributo que não existe".

Três coisas aqui não são detalhe:

- **XSS no acervo.** Um título de página raspada da web entra na tela do painel.
  O teste passa `<script>` por um título de nota de verdade, escrita pelo `Vault`,
  e confere que sai escapado — provando o escape na ponta, não só na `render.py`.
- **Somente leitura.** Enquanto não existe fronteira de mutação (`Origin`, CSRF,
  token), qualquer método que escreva tem de ser 405. Sem isso haveria uma janela
  em que existe botão e não existe guarda.
- **Recusa fora de loopback.** `http.server` não tem timeout nem teto de conexão;
  na borda um slowloris o derruba. Enquanto não há proxy nem auth, não sobe.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from dlb.config import Config
from dlb.painel import Erro, construir
from dlb import journal

from tests.test_vault import VaultCase, note


class PainelCase(VaultCase):
    def setUp(self):
        super().setUp()
        journal._atual = None
        self.state = self.root / ".state"
        self.cfg = Config(
            discord_token="x",
            discord_token_type="bot",
            channel_ids=["42"],
            channel_names={"42": "estudos"},
            vault_path=self.root,
            state_dir=self.state,
        )
        self.servidor = construir(self.cfg, porta=0, silencioso=True)
        self.base = f"http://127.0.0.1:{self.servidor.server_port}"
        # `poll_interval` padrão é 0,5 s, e cada `shutdown()` espera um ciclo
        # inteiro. Com 23 testes isso custaria 11 s de suíte para nada.
        self.thread = threading.Thread(
            target=self.servidor.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.servidor.shutdown()
        self.servidor.server_close()
        self.thread.join(timeout=5)
        journal._atual = None
        super().tearDown()

    def escrever(self, url, titulo, conceitos=(), hhmm="09:12"):
        return self.vault.write_link_note("estudos", note(url, titulo, list(conceitos), hhmm))

    def pegar(self, rota, metodo="GET"):
        pedido = urllib.request.Request(self.base + rota, method=metodo)
        try:
            with urllib.request.urlopen(pedido, timeout=10) as resposta:
                return resposta.status, dict(resposta.headers), resposta.read().decode("utf-8")
        except urllib.error.HTTPError as erro:
            return erro.code, dict(erro.headers), erro.read().decode("utf-8")

    def json_de(self, rota):
        codigo, _, corpo = self.pegar(rota)
        self.assertEqual(codigo, 200, corpo[:400])
        return json.loads(corpo)


class TestTelas(PainelCase):
    def test_painel_abre_e_mostra_os_cartoes(self):
        codigo, cabecalhos, html = self.pegar("/")
        self.assertEqual(codigo, 200)
        self.assertTrue(cabecalhos["Content-Type"].startswith("text/html"))
        for esperado in ("Painel", "Agendador", "Responder", "Acervo", "Fila de triagem"):
            self.assertIn(esperado, html)

    def test_painel_sobrevive_a_state_dir_que_nao_existe(self):
        """O painel é o que se abre quando algo já está errado. Vault novo, sem
        `.state`, sem heartbeat e sem run nenhum não pode dar traceback."""
        codigo, _, html = self.pegar("/")
        self.assertEqual(codigo, 200)
        self.assertIn("nunca", html)         # heartbeat ausente
        self.assertIn("Nenhuma execução registrada", html)

    def test_execucoes_lista_o_que_o_journal_gravou(self):
        with journal.registrar(self.state, "sync", ["sync"]) as run:
            journal.contar("links_novos", 3)
            run.fechar(exit_code=0)
        with journal.registrar(self.state, "backfill", ["backfill"]) as run:
            run.fechar(exit_code=1)

        codigo, _, html = self.pegar("/execucoes")
        self.assertEqual(codigo, 200)
        self.assertIn("links_novos: 3", html)
        self.assertIn("selo-ok", html)
        self.assertIn("selo-falha", html)

    def test_filtro_por_comando_nas_execucoes(self):
        with journal.registrar(self.state, "sintese", []) as run:
            run.fechar(exit_code=0)
        _, _, html = self.pegar("/execucoes?comando=sync")
        self.assertIn("Nenhuma execução registrada", html)
        _, _, html = self.pegar("/execucoes?comando=sintese")
        self.assertIn("sintese", html)

    def test_acervo_mostra_as_notas_e_o_filtro_de_busca(self):
        self.escrever("https://relay.exemplo.com/a", "Relays do Nostr", ["Nostr"])
        self.escrever("https://outro.com/b", "Coisa sem graça")

        _, _, html = self.pegar("/acervo")
        self.assertIn("Relays do Nostr", html)
        self.assertIn("Coisa sem graça", html)

        _, _, html = self.pegar("/acervo?q=relays")
        self.assertIn("Relays do Nostr", html)
        self.assertNotIn("Coisa sem graça", html)

    def test_a_navegacao_marca_a_tela_atual(self):
        _, _, html = self.pegar("/acervo")
        self.assertIn('href="/acervo" class="ativo"', html)


class TestXSSNaPonta(PainelCase):
    def test_titulo_hostil_de_nota_sai_escapado(self):
        """Título vem de página raspada da web ou de LLM. Nenhum dos dois é
        confiável, e o modelo de ameaça é browser hostil na mesma máquina."""
        self.escrever("https://mau.com/a", '<script>alert("xss")</script>')
        _, _, html = self.pegar("/acervo")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_termo_de_busca_hostil_volta_escapado_no_formulario(self):
        """O valor digitado é reexibido dentro de um atributo `value=`. É o
        caminho clássico de XSS refletido."""
        _, _, html = self.pegar('/acervo?q=%22%3E%3Cimg+src%3Dx+onerror%3Dalert(1)%3E')
        self.assertNotIn("<img src=x", html)
        self.assertIn("&quot;", html)

    def test_erro_de_run_hostil_sai_escapado_nas_execucoes(self):
        """A mensagem de erro vem de exceção, que pode carregar texto de fora —
        stderr de subprocess, por exemplo."""
        with journal.registrar(self.state, "sync", []) as run:
            run.fechar(erro="<script>alert(1)</script>")
        _, _, html = self.pegar("/execucoes")
        self.assertNotIn("<script>alert", html)


class TestAPI(PainelCase):
    def test_api_acervo_devolve_as_notas(self):
        """O critério de pronto da F1: `/api/acervo` responde o acervo inteiro."""
        for i in range(3):
            self.escrever(f"https://e.com/{i}", f"Nota {i}")
        dados = self.json_de("/api/acervo")
        self.assertEqual(dados["total"], 3)
        self.assertEqual(dados["no_filtro"], 3)
        self.assertEqual(len(dados["links"]), 3)
        primeiro = dados["links"][0]
        for campo in ("url", "titulo", "dia", "dominio", "categoria", "triagem", "resumo"):
            self.assertIn(campo, primeiro)

    def test_api_acervo_aceita_filtro_e_limite(self):
        for i in range(5):
            self.escrever(f"https://e.com/{i}", f"Nota {i}")
        self.assertEqual(len(self.json_de("/api/acervo?limite=2")["links"]), 2)
        self.assertEqual(self.json_de("/api/acervo?q=Nota+3")["no_filtro"], 1)
        self.assertEqual(self.json_de("/api/acervo?triagem=aplicado")["no_filtro"], 0)

    def test_api_estado_traz_heartbeat_cursor_e_execucoes(self):
        (self.state).mkdir(parents=True, exist_ok=True)
        (self.state / "heartbeat").write_text("1787000000", encoding="utf-8")
        dados = self.json_de("/api/estado")
        self.assertIsNotNone(dados["heartbeats"]["sync"])
        self.assertIsNone(dados["heartbeats"]["responder"])
        self.assertEqual(dados["cursores"][0]["canal"], "42")
        self.assertEqual(dados["cursores"][0]["nome"], "estudos")
        self.assertEqual(dados["tolerancia_s"]["sync"], 28800)

    def test_api_estado_expoe_o_memo_do_acervo(self):
        """O painel diz quantas notas releu — é como se vê o cache funcionando
        sem instrumentar nada por fora."""
        self.escrever("https://e.com/a", "A")
        self.assertEqual(self.json_de("/api/estado")["leitura"]["lidos_do_disco"], 1)
        self.assertEqual(self.json_de("/api/estado")["leitura"]["reusados_do_memo"], 1)

    def test_api_execucoes_respeita_o_limite(self):
        for _ in range(3):
            with journal.registrar(self.state, "sync", []) as run:
                run.fechar(exit_code=0)
        self.assertLessEqual(len(self.json_de("/api/execucoes?limite=1")["execucoes"]), 1)


class TestGestorNaTela(PainelCase):
    def test_vault_novo_mostra_o_alerta_do_agendador(self):
        """Sem heartbeat nenhum, a primeira coisa que a tela diz é que o
        agendador não fechou ciclo — com evidência e ação, não só vermelho."""
        _, _, html = self.pegar("/")
        self.assertIn("faixa-alerta", html)
        self.assertIn("O agendador não conclui um ciclo", html)
        self.assertIn("dlb.sh prod", html)

    def test_fila_de_triagem_aparece_como_atencao(self):
        for i in range(45):
            self.escrever(f"https://e.com/{i}", f"Nota {i}")
        _, _, html = self.pegar("/")
        self.assertIn("faixa-atencao", html)
        self.assertIn("45 notas esperando triagem", html)

    def test_api_estado_traz_os_alertas_do_gestor(self):
        alertas = self.json_de("/api/estado")["alertas"]
        self.assertEqual([a["chave"] for a in alertas], ["agendador_parado"])
        for campo in ("nivel", "titulo", "evidencia", "acao", "dados"):
            self.assertIn(campo, alertas[0])

    def test_o_ciclo_mostra_os_quatro_comandos(self):
        """Três dos quatro não escrevem heartbeat. Sem esta tabela, um `sintese`
        que nunca roda é invisível."""
        _, _, html = self.pegar("/")
        for comando in ("sync", "backfill", "sintese", "ressurgir"):
            self.assertIn(comando, html)


class TestFronteira(PainelCase):
    def test_metodo_que_escreve_e_405_enquanto_nao_existe_guarda(self):
        for metodo in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(metodo):
                codigo, cabecalhos, _ = self.pegar("/", metodo=metodo)
                self.assertEqual(codigo, 405)
                self.assertEqual(cabecalhos.get("Allow"), "GET, HEAD")

    def test_rota_inexistente_e_404_em_json(self):
        codigo, cabecalhos, corpo = self.pegar("/nao-existe")
        self.assertEqual(codigo, 404)
        self.assertTrue(cabecalhos["Content-Type"].startswith("application/json"))
        self.assertIn("rota inexistente", corpo)

    def test_estatico_serve_so_a_lista_branca(self):
        codigo, cabecalhos, corpo = self.pegar("/static/painel.css")
        self.assertEqual(codigo, 200)
        self.assertTrue(cabecalhos["Content-Type"].startswith("text/css"))
        self.assertIn("--acento", corpo)
        self.assertEqual(self.pegar("/static/painel.js")[0], 200)

    def test_travessia_de_caminho_nao_passa(self):
        """Lista branca em vez de sanear caminho: `..%2f` e amigos são um jogo
        que não se ganha por filtro."""
        for tentativa in (
            "/static/../config.py",
            "/static/..%2f..%2fetc%2fpasswd",
            "/static/%2e%2e/%2e%2e/dlb/config.py",
            "/static/painel.css.bak",
        ):
            with self.subTest(tentativa):
                self.assertEqual(self.pegar(tentativa)[0], 404)

    def test_cabecalhos_de_seguranca_e_de_cache(self):
        _, cabecalhos, _ = self.pegar("/")
        self.assertEqual(cabecalhos["Cache-Control"], "no-store")
        self.assertEqual(cabecalhos["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", cabecalhos["Content-Security-Policy"])

    def test_nao_anuncia_a_versao_do_python(self):
        _, cabecalhos, _ = self.pegar("/")
        self.assertNotIn("Python", cabecalhos.get("Server", ""))

    def test_head_nao_traz_corpo_mas_traz_o_tamanho(self):
        codigo, cabecalhos, corpo = self.pegar("/", metodo="HEAD")
        self.assertEqual(codigo, 200)
        self.assertEqual(corpo, "")
        self.assertGreater(int(cabecalhos["Content-Length"]), 0)


class TestBind(VaultCase):
    def cfg(self):
        return Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.root / ".state",
        )

    def test_recusa_subir_fora_de_loopback(self):
        """Sem auth e sem proxy, expor `http.server` na tailnet é servidor de
        borda sem timeout nem teto de conexão. A resposta certa é não subir."""
        with self.assertRaises(Erro) as caixa:
            construir(self.cfg(), host="0.0.0.0", porta=0)
        self.assertIn("não é loopback", str(caixa.exception))
        self.assertIn("proxy", str(caixa.exception))

    def test_loopback_sobe(self):
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host):
                servidor = construir(self.cfg(), host=host, porta=0, silencioso=True)
                servidor.server_close()


if __name__ == "__main__":
    unittest.main()
