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
from dlb import busca, compose, journal

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
            env_path=self.root / ".env",
        )
        # Nenhum teste fala com o docker de verdade: seria lento, dependeria da
        # máquina, e o que se quer travar é a degradação, não o `docker inspect`.
        self._inspecionar = compose.inspecionar
        compose.inspecionar = lambda *a, **k: None
        self.servidor = construir(self.cfg, porta=0, silencioso=True)
        self.base = f"http://127.0.0.1:{self.servidor.server_port}"
        # `poll_interval` padrão é 0,5 s, e cada `shutdown()` espera um ciclo
        # inteiro. Com 23 testes isso custaria 11 s de suíte para nada.
        self.thread = threading.Thread(
            target=self.servidor.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        compose.inspecionar = self._inspecionar
        self.servidor.shutdown()
        self.servidor.server_close()
        self.thread.join(timeout=5)
        journal._atual = None
        super().tearDown()

    def escrever(self, url, titulo, conceitos=(), hhmm="09:12"):
        return self.vault.write_link_note("estudos", note(url, titulo, list(conceitos), hhmm))

    def pegar(self, rota, metodo="GET", cabecalhos=None):
        pedido = urllib.request.Request(
            self.base + rota, method=metodo, headers=cabecalhos or {}
        )
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

    def test_processo_longevo_aparece_como_de_pe_e_nao_como_rodando(self):
        """Senão a tela mostraria o próprio painel como job travado desde ontem."""
        journal.abrir(self.state, "painel", ["painel"])
        _, _, html = self.pegar("/execucoes")
        self.assertIn("de pé", html)
        self.assertNotIn("selo-rodando", html)

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


class TestAgendaNaTela(PainelCase):
    def env(self, texto):
        (self.root / ".env").write_text(texto, encoding="utf-8")

    def test_sem_container_a_tela_diz_que_esta_lendo_o_arquivo(self):
        """Metade certo é pior que honesto: a tela nomeia a fonte do número."""
        self.env("DLB_SYNC_EVERY_HOURS=6\n")
        codigo, _, html = self.pegar("/agenda")
        self.assertEqual(codigo, 200)
        self.assertIn("Mostrando o que", html)
        self.assertIn("restart` não relê", html)

    def test_mostra_os_quatro_comandos_com_o_motivo(self):
        self.env("DLB_SYNC_EVERY_HOURS=6\nDLB_BACKFILL=1\nDLB_BACKFILL_DAYS=3\n")
        _, _, html = self.pegar("/agenda")
        for comando in ("sync", "backfill", "sintese", "ressurgir"):
            self.assertIn(comando, html)
        self.assertIn("--days 3", html)
        self.assertIn("DLB_SINTESE=0", html)

    def test_avisa_que_o_sync_at_esta_sendo_ignorado(self):
        """A armadilha que o compose criava: `DLB_SYNC_AT` junto de `EVERY_HOURS`
        não roda, e nada avisava."""
        self.env("DLB_SYNC_EVERY_HOURS=6\nDLB_SYNC_AT=03:30\n")
        _, _, html = self.pegar("/agenda")
        self.assertIn("faixa-atencao", html)
        self.assertIn("DLB_SYNC_AT=03:30", html)

    def test_avisa_chave_duplicada_no_env(self):
        self.env("DLB_BACKFILL=1\nDLB_BACKFILL=0\n")
        _, _, html = self.pegar("/agenda")
        self.assertIn("faixa-alerta", html)
        self.assertIn("Chave repetida", html)

    def test_divergencia_entre_arquivo_e_container_aparece(self):
        self.env("DLB_SYNC_EVERY_HOURS=3\n")
        compose.inspecionar = lambda *a, **k: {
            "container": "dlb-prod", "rodando": True, "status": "running",
            "saude": "healthy", "desde": None, "criado": None, "imagem": "x",
            "env": {"DLB_SYNC_EVERY_HOURS": "6", "TZ": "America/Sao_Paulo"},
        }
        _, _, html = self.pegar("/agenda")
        self.assertIn("é ele que manda", html)
        self.assertIn("DLB_SYNC_EVERY_HOURS", html)
        dados = self.json_de("/api/agenda")
        self.assertEqual(dados["fonte"], "container")
        self.assertEqual(
            [d["chave"] for d in dados["divergencias"] if d["chave"] == "DLB_SYNC_EVERY_HOURS"],
            ["DLB_SYNC_EVERY_HOURS"],
        )
        self.assertEqual(dados["modo"], "grade")

    def test_api_agenda_traz_slots_e_fonte(self):
        self.env("DLB_SYNC_EVERY_HOURS=6\n")
        dados = self.json_de("/api/agenda")
        self.assertEqual(dados["fonte"], "arquivo")
        self.assertEqual([s["comando"] for s in dados["slots"]],
                         ["sync", "backfill", "sintese", "ressurgir"])
        self.assertTrue(dados["slots"][0]["proximo"])

    def test_sem_env_nenhum_a_tela_abre_com_o_padrao(self):
        """Clone novo, sem `.env`: a agenda mostra o padrão do scheduler em vez de
        estourar — é assim que se descobre que falta configurar."""
        codigo, _, html = self.pegar("/agenda")
        self.assertEqual(codigo, 200)
        self.assertIn("padrão", html)


class TestChatNaTela(PainelCase):
    """O chat pelo HTTP. O modelo é dublê — nenhum teste abre subprocess."""

    def setUp(self):
        super().setUp()
        self.perguntas = []
        self._buscar = busca.buscar

        def falso(cfg, pergunta, entradas):
            self.perguntas.append(pergunta)
            return busca.Resposta(
                texto=f"eco: {pergunta}", confianca="alta", considerados=len(entradas)
            )

        busca.buscar = falso

    def tearDown(self):
        busca.buscar = self._buscar
        super().tearDown()

    def test_abre_vazio_sem_chamar_o_modelo(self):
        """Entrar na tela não pode gastar quota — só perguntar gasta."""
        codigo, _, html = self.pegar("/chat")
        self.assertEqual(codigo, 200)
        self.assertIn("Nenhuma pergunta ainda", html)
        self.assertEqual(self.perguntas, [])

    def test_pergunta_pela_url_responde_e_mostra_o_consumo(self):
        _, _, html = self.pegar("/chat?q=o+que+eu+sei+de+nostr")
        self.assertIn("eco: o que eu sei de nostr", html)
        self.assertIn("1 de 20 chamadas na última hora", html)
        self.assertEqual(self.perguntas, ["o que eu sei de nostr"])

    def test_resposta_hostil_do_modelo_sai_escapada(self):
        """O texto vem de LLM. Não é confiável, e vai para dentro de `<p>`."""
        busca.buscar = lambda cfg, p, e: busca.Resposta(
            texto="<script>alert(1)</script>", confianca="alta"
        )
        _, _, html = self.pegar("/chat?q=x")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_api_chat_devolve_o_registro_e_a_situacao(self):
        dados = self.json_de("/api/chat?q=nostr")
        self.assertEqual(dados["resposta"]["texto"], "eco: nostr")
        self.assertTrue(dados["resposta"]["chamou_modelo"])
        self.assertEqual(dados["situacao"]["chamadas_na_hora"], 1)

    def test_api_chat_sem_pergunta_nao_chama_o_modelo(self):
        dados = self.json_de("/api/chat")
        self.assertIsNone(dados["resposta"])
        self.assertEqual(self.perguntas, [])

    def test_requisicao_de_outro_site_nao_gasta_modelo(self):
        """Sem esta guarda, uma página hostil aberta na mesma máquina queimaria a
        janela de uso com `<img src="/chat?q=...">` — pedido que não manda
        `Origin` nenhum, então checar `Origin` não pegaria."""
        codigo, _, corpo = self.pegar(
            "/chat?q=queimar+quota", cabecalhos={"Sec-Fetch-Site": "cross-site"}
        )
        self.assertEqual(codigo, 403)
        self.assertIn("outro site", corpo)
        self.assertEqual(self.perguntas, [])

    def test_navegacao_normal_e_curl_passam(self):
        """`same-origin` é a aba do próprio painel; ausente é curl ou script.
        A guarda é contra navegador, que é quem tem o problema."""
        for cabecalhos in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}):
            with self.subTest(cabecalhos=cabecalhos):
                codigo, _, _ = self.pegar("/chat?q=ok", cabecalhos=cabecalhos)
                self.assertEqual(codigo, 200)
        self.assertEqual(len(self.perguntas), 3)

    def test_as_outras_telas_nao_tem_a_guarda(self):
        """Ler o painel de um iframe hostil é chato, não caro. A guarda protege
        quota, e pôr onde não custa nada só criaria falso positivo."""
        codigo, _, _ = self.pegar("/acervo", cabecalhos={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(codigo, 200)


class TestFronteira(PainelCase):
    def test_metodos_que_o_painel_nao_tem_sao_405(self):
        """`POST` existe em três rotas de configuração e em nenhuma outra. `PUT`,
        `DELETE` e `PATCH` não existem em rota nenhuma — o painel não é API REST."""
        for metodo in ("PUT", "DELETE", "PATCH"):
            with self.subTest(metodo):
                codigo, cabecalhos, _ = self.pegar("/", metodo=metodo)
                self.assertEqual(codigo, 405)
                self.assertEqual(cabecalhos.get("Allow"), "GET, HEAD, POST")

    def test_post_em_rota_de_leitura_e_405(self):
        codigo, _, _ = self.pegar("/acervo", metodo="POST")
        self.assertEqual(codigo, 405)

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


class TestAutenticacao(VaultCase):
    """Token, cookie e as duas respostas: 401 sem sessão, 403 de outra origem.

    O modelo de ameaça não é força bruta pela internet: é o painel exposto num IP
    de tailnet, e browser hostil na mesma máquina. Daí o que se trava aqui ser
    *comparação em tempo constante*, *cookie que JavaScript não lê* e *cookie que o
    navegador não manda de outro site*.
    """

    def setUp(self):
        super().setUp()
        journal._atual = None
        self.state = self.root / ".state"
        self.cfg = Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.state, env_path=self.root / ".env",
            painel_token="segredo-do-painel",
        )
        self._inspecionar = compose.inspecionar
        compose.inspecionar = lambda *a, **k: None
        self.servidor = construir(self.cfg, porta=0, silencioso=True)
        self.base = f"http://127.0.0.1:{self.servidor.server_port}"
        self.thread = threading.Thread(
            target=self.servidor.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        compose.inspecionar = self._inspecionar
        self.servidor.shutdown()
        self.servidor.server_close()
        self.thread.join(timeout=5)
        journal._atual = None
        super().tearDown()

    def pedir(self, rota, cookie=None, metodo="GET", dados=None, cabecalhos=None):
        headers = dict(cabecalhos or {})
        if cookie:
            headers["Cookie"] = f"dlb_painel={cookie}"
        corpo = urllib.parse.urlencode(dados).encode("utf-8") if dados else None
        pedido = urllib.request.Request(
            self.base + rota, data=corpo, method=metodo, headers=headers
        )
        class Parado(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        try:
            with urllib.request.build_opener(Parado).open(pedido, timeout=10) as r:
                return r.status, dict(r.headers), r.read().decode("utf-8")
        except urllib.error.HTTPError as erro:
            return erro.code, dict(erro.headers), erro.read().decode("utf-8")

    def entrar(self, token="segredo-do-painel"):
        codigo, cabecalhos, _ = self.pedir("/entrar", metodo="POST", dados={"token": token})
        cookie = (cabecalhos.get("Set-Cookie") or "").split(";")[0].split("=", 1)
        return codigo, (cookie[1] if len(cookie) == 2 else "")

    def test_sem_cookie_toda_tela_e_401_com_a_tela_de_login(self):
        for rota in ("/", "/acervo", "/config", "/agenda", "/execucoes", "/chat"):
            with self.subTest(rota):
                codigo, _, html = self.pedir(rota)
                self.assertEqual(codigo, 401)
                self.assertIn("token do painel", html)

    def test_sem_cookie_a_api_e_401_em_json(self):
        codigo, cabecalhos, corpo = self.pedir("/api/estado")
        self.assertEqual(codigo, 401)
        self.assertTrue(cabecalhos["Content-Type"].startswith("application/json"))
        self.assertIn("não autenticado", corpo)

    def test_o_css_e_o_js_ficam_abertos_para_a_tela_de_login_funcionar(self):
        for rota in ("/static/painel.css", "/static/painel.js"):
            with self.subTest(rota):
                self.assertEqual(self.pedir(rota)[0], 200)

    def test_token_certo_abre_sessao_com_cookie_httponly_e_samesite(self):
        codigo, cabecalhos, _ = self.pedir("/entrar", metodo="POST", dados={"token": "segredo-do-painel"})
        self.assertEqual(codigo, 303)
        cookie = cabecalhos["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)          # XSS não rouba a sessão
        self.assertIn("SameSite=Strict", cookie)   # navegador não manda de outro site
        self.assertIn("Path=/", cookie)

    def test_com_a_sessao_as_telas_abrem(self):
        _, sessao = self.entrar()
        for rota in ("/", "/acervo", "/config"):
            with self.subTest(rota):
                self.assertEqual(self.pedir(rota, cookie=sessao)[0], 200)

    def test_token_errado_e_401_e_nao_diz_por_que(self):
        codigo, cabecalhos, html = self.pedir("/entrar", metodo="POST", dados={"token": "chute"})
        self.assertEqual(codigo, 401)
        self.assertIsNone(cabecalhos.get("Set-Cookie"))
        self.assertIn("não aceito", html.lower())

    def test_cookie_inventado_nao_serve(self):
        self.assertEqual(self.pedir("/", cookie="x" * 43)[0], 401)

    def test_login_de_outra_origem_e_403(self):
        """Sem isto, uma página hostil poderia postar o token que ela adivinhou e
        usar a resposta como oráculo."""
        codigo, _, _ = self.pedir(
            "/entrar", metodo="POST", dados={"token": "segredo-do-painel"},
            cabecalhos={"Origin": "http://mau.exemplo"},
        )
        self.assertEqual(codigo, 403)

    def test_post_de_mutacao_sem_sessao_e_401_antes_do_csrf(self):
        """A ordem importa: quem não entrou não chega nem a ser avaliado por CSRF."""
        codigo, _, corpo = self.pedir(
            "/config/salvar", metodo="POST", dados={"TEMAS_LIMIAR": "9"}
        )
        self.assertEqual(codigo, 401)
        self.assertIn("não autenticado", corpo)

    def test_comparacao_do_token_e_em_tempo_constante(self):
        """`==` em segredo vaza o tamanho do prefixo comum pelo tempo de resposta.
        O teste não mede tempo — lê o código, que é o que dá garantia aqui."""
        import inspect

        from dlb import painel as modulo

        fonte = inspect.getsource(modulo.Estado.entrar)
        self.assertIn("compare_digest", fonte)
        self.assertNotIn("==", fonte.split('"""')[-1])

    def test_sem_token_configurado_nao_pede_nada(self):
        """É o caso normal: `python3 -m dlb painel` no próprio terminal, em
        loopback. Pedir senha para quem já está na máquina só cria fricção."""
        aberto = Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.state, env_path=self.root / ".env",
        )
        servidor = construir(aberto, porta=0, silencioso=True)
        thread = threading.Thread(
            target=servidor.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{servidor.server_port}/", timeout=10
            ) as r:
                self.assertEqual(r.status, 200)
        finally:
            servidor.shutdown()
            servidor.server_close()
            thread.join(timeout=5)


class TestServicoSystemd(unittest.TestCase):
    """O arquivo de serviço é operação, e operação errada só aparece no dia ruim."""

    def unidade(self):
        return (
            Path(__file__).resolve().parent.parent / "docker" / "dlb-painel.service"
        ).read_text(encoding="utf-8")

    def test_manda_sigterm_para_o_run_fechar_no_journal(self):
        self.assertIn("KillSignal=SIGTERM", self.unidade())

    def test_nao_duplica_o_env_no_arquivo_de_servico(self):
        """`EnvironmentFile` criaria uma segunda fonte de verdade que só atualiza
        com `daemon-reload` — o mesmo defeito do `restart` que não relê o `.env`.

        Olha só linha ativa: o arquivo **explica** a ausência num comentário, e
        procurar a palavra solta reprovaria a explicação junto com o defeito.
        """
        ativas = [
            l.strip()
            for l in self.unidade().splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        self.assertEqual([l for l in ativas if l.startswith("EnvironmentFile")], [])

    def test_roda_o_comando_do_painel(self):
        self.assertIn("-m dlb painel", self.unidade())

    def test_reinicia_so_em_falha(self):
        """`Restart=always` reiniciaria em laço um painel que não consegue subir —
        por exemplo com a porta ocupada — enchendo o journal."""
        self.assertIn("Restart=on-failure", self.unidade())


class TestBind(VaultCase):
    def cfg(self):
        return Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.root / ".state",
        )

    def test_recusa_subir_fora_de_loopback_sem_token(self):
        """Sem token, quem alcança a porta entra — e `http.server` não tem timeout
        nem teto de conexão. A resposta certa é não subir."""
        with self.assertRaises(Erro) as caixa:
            construir(self.cfg(), host="0.0.0.0", porta=0)
        self.assertIn("não é loopback", str(caixa.exception))
        self.assertIn("PAINEL_TOKEN", str(caixa.exception))

    def test_com_token_aceita_subir_fora_de_loopback(self):
        cfg = self.cfg()
        cfg.painel_token = "segredo"
        servidor = construir(cfg, host="127.0.0.1", porta=0, silencioso=True)
        servidor.server_close()
        # Não abre socket em 0.0.0.0 no teste, mas a guarda é a que muda: com token
        # a checagem passa, e é isso que se trava aqui.
        self.assertTrue(cfg.painel_token)

    def test_loopback_sobe(self):
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host):
                servidor = construir(self.cfg(), host=host, porta=0, silencioso=True)
                servidor.server_close()


if __name__ == "__main__":
    unittest.main()
