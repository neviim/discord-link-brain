"""Rode com: python3 -m unittest discover -s tests -t .

O primeiro minuto de quem clonou o projeto agora — e o ciclo sem saída que havia.

O painel **é** o editor de configuração, e até este trabalho ele exigia
configuração válida para abrir: `Config.from_env()` recusava sem `DISCORD_TOKEN`, e
`cmd_painel` recusava sem vault. Num clone novo não sobrava caminho a não ser
editar o `.env` às cegas, que é exatamente o que a tela existe para evitar.

Havia **três** bloqueios, e só o primeiro era o citado:

1. a construção estrita, que recusa sem token;
2. `cfg.validate_vault()` no `cmd_painel`, que recusa sem as pastas do vault —
   mesmo com o token preenchido;
3. o pior, porque só aparece depois de a pessoa achar que resolveu: `load_dotenv`
   faz `os.environ.setdefault`, então o painel carrega em si o `DISCORD_TOKEN=`
   **vazio** que vem do `.env.example`. O comando disparado pela tela herdava esse
   ambiente e recusava com "DISCORD_TOKEN não definido" **depois** de o token ter
   sido salvo — a tela cumprindo seu papel e o sistema dizendo que não.

Este arquivo trava os três, e trava também o que **não** mudou: quem fala com o
Discord continua estrito. A tolerância é uma lista branca de dois comandos, não uma
política.

E trava a contrapartida do "abre": a faixa. Painel que abre com configuração
incompleta e não diz o que falta trocaria uma recusa que nomeia a variável por uma
tela silenciosamente inútil — pior que a recusa de antes.
"""

import json
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from dlb import acoes, compose, env_io, journal
from dlb.config import Config, ConfigError
from dlb.painel import construir
from dlb.render import MENU

from tests.test_vault import VaultCase


class TestConstrucaoTolerante(unittest.TestCase):
    """`from_env(estrito=False)`: o único ponto do projeto que aceita incompleto."""

    def setUp(self):
        import dlb.config as mod

        self._dotenv = mod.load_dotenv
        mod.load_dotenv = lambda *a, **k: None
        self.addCleanup(lambda: setattr(mod, "load_dotenv", self._dotenv))
        import os

        self._salvos = {
            k: os.environ.pop(k, None) for k in ("DISCORD_TOKEN", "DISCORD_CHANNEL_IDS")
        }

        def restaurar():
            for k, v in self._salvos.items():
                if v is not None:
                    os.environ[k] = v

        self.addCleanup(restaurar)

    def test_estrito_continua_recusando(self):
        """O padrão não muda. Se este teste passar a falhar, todo o pipeline
        aceitou rodar sem token — e falar com o Discord sem token não é
        degradação graciosa, é erro."""
        with self.assertRaises(ConfigError) as caso:
            Config.from_env()
        self.assertIn("DISCORD_TOKEN", str(caso.exception))

    def test_tolerante_nao_levanta_e_diz_o_que_faltou(self):
        cfg = Config.from_env(estrito=False)
        self.assertEqual(cfg.faltando, ("DISCORD_TOKEN", "DISCORD_CHANNEL_IDS"))

    def test_tolerante_nao_inventa_valor(self):
        """Vazio de verdade, não um "bot " ou um canal fantasma: um valor
        fabricado aqui viraria requisição ao Discord com credencial inválida."""
        cfg = Config.from_env(estrito=False)
        self.assertEqual(cfg.discord_token, "")
        self.assertEqual(cfg.channel_ids, [])

    def test_construcao_estrita_nunca_reporta_falta(self):
        """`faltando` é estado de exceção declarada, não de configuração boa: se
        vazasse para a construção estrita, a faixa do painel acusaria falta de
        token num sistema configurado."""
        import os

        os.environ["DISCORD_TOKEN"] = "t"
        os.environ["DISCORD_CHANNEL_IDS"] = "42"
        self.addCleanup(lambda: os.environ.pop("DISCORD_TOKEN", None))
        self.addCleanup(lambda: os.environ.pop("DISCORD_CHANNEL_IDS", None))
        self.assertEqual(Config.from_env().faltando, ())


class PainelSemConfigCase(VaultCase):
    """Painel de pé com a configuração que um clone novo tem: nenhuma."""

    def setUp(self):
        super().setUp()
        journal._atual = None
        self.env = self.root / ".env"
        self.cfg = Config(
            discord_token="",
            discord_token_type="bot",
            channel_ids=[],
            faltando=("DISCORD_TOKEN", "DISCORD_CHANNEL_IDS"),
            # O vault que o `.env.example` sugere e que o clone novo não tem.
            vault_path=self.root / "vault-que-nao-existe",
            state_dir=self.root / ".state",
            env_path=self.env,
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

    def pegar(self, rota):
        with urllib.request.urlopen(self.base + rota, timeout=5) as r:
            return r.status, r.read().decode("utf-8")


class TestPainelAbreSemConfiguracao(PainelSemConfigCase):
    def test_toda_tela_do_menu_responde(self):
        """Não é "a home abre": é toda tela do menu. Uma que estourasse por
        `channel_ids` vazio deixaria o painel pela metade justamente no estado em
        que ele é a única ferramenta disponível."""
        for rota, rotulo in MENU:
            with self.subTest(rota=rota):
                status, corpo = self.pegar(rota)
                self.assertEqual(status, 200, rotulo)
                self.assertIn("<main>", corpo)

    def test_api_tambem_responde(self):
        status, corpo = self.pegar("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertIn("acervo", json.loads(corpo))

    def test_a_faixa_aparece_em_toda_tela_e_nomeia_o_que_falta(self):
        """A contrapartida de abrir. Em **toda** tela, porque a aba pode ter sido
        aberta em qualquer uma — um aviso só na tela de configuração perderia
        justamente quem não sabe que precisa ir lá."""
        for rota, _ in MENU:
            with self.subTest(rota=rota):
                _, corpo = self.pegar(rota)
                self.assertIn("alertas-config", corpo)
                self.assertIn("Configuração incompleta", corpo)
                self.assertIn("DISCORD_TOKEN", corpo)

    def test_a_faixa_do_vault_diz_qual_caminho_o_painel_esta_lendo(self):
        """Acervo vazio por caminho errado é idêntico a acervo vazio por acervo
        novo. Sem o caminho na tela, a única saída seria desconfiar."""
        _, corpo = self.pegar("/acervo")
        self.assertIn("Vault não encontrado", corpo)
        self.assertIn("vault-que-nao-existe", corpo)

    def test_arquivo_inexistente_e_arquivo_incompleto_pedem_passos_diferentes(self):
        """Mandar "preencha o .env" quando o `.env` não existe faz procurar o que
        não está lá. Salvar na tela cria o arquivo, e é isso que ela diz."""
        _, corpo = self.pegar("/config")
        self.assertIn("ainda não existe", corpo)
        self.assertIn("criado no primeiro salvamento", corpo)

        self.env.write_text("DISCORD_TOKEN=\nDISCORD_CHANNEL_IDS=\n", encoding="utf-8")
        _, corpo = self.pegar("/config")
        self.assertNotIn("ainda não existe", corpo)
        self.assertIn("não define", corpo)

    def test_a_faixa_le_o_arquivo_agora_nao_o_de_quando_o_painel_subiu(self):
        """O teste que impede a faixa de virar mentira: sem releitura por
        requisição, ela seguiria acusando falta de token depois de o token ser
        salvo — e o painel passaria a errar sobre a única coisa que ele sabe."""
        self.env.write_text(
            f"DISCORD_TOKEN=t\nDISCORD_CHANNEL_IDS=42\nOBSIDIAN_VAULT={self.root}\n",
            encoding="utf-8",
        )
        _, corpo = self.pegar("/config")
        self.assertNotIn("Configuração incompleta", corpo)
        # E aparece o estado seguinte, que é diferente: o arquivo já tem, este
        # processo ainda não releu. Sem esta faixa, a tela ficaria muda num
        # momento em que a pessoa está esperando confirmação.
        self.assertIn("ainda está com a de antes", corpo)
        self.assertIn("restart dlb-painel", corpo)


class TestFaixaNaoVazaAntesDeAutenticar(VaultCase):
    """Com token de painel, a tela de login não conta o que falta na configuração
    de quem hospeda. Não é segredo, é superfície: nada sobre a máquina antes de
    provar quem é."""

    def setUp(self):
        super().setUp()
        journal._atual = None
        self.cfg = Config(
            discord_token="",
            discord_token_type="bot",
            channel_ids=[],
            faltando=("DISCORD_TOKEN",),
            vault_path=self.root / "nao-existe",
            state_dir=self.root / ".state",
            env_path=self.root / ".env",
            painel_token="segredo",
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

    def test_login_nao_mostra_a_faixa(self):
        with self.assertRaises(urllib.error.HTTPError) as caso:
            urllib.request.urlopen(self.base + "/config", timeout=5)
        corpo = caso.exception.read().decode("utf-8")
        self.assertEqual(caso.exception.code, 401)
        self.assertIn("entrar", corpo)
        self.assertNotIn("alertas-config", corpo)
        self.assertNotIn("Configuração incompleta", corpo)


class TestComandoDisparadoLeOArquivoENaoOPainel(VaultCase):
    """O terceiro bloqueio, que é o que fecha o ciclo do clone novo."""

    def setUp(self):
        super().setUp()
        self.cfg = Config(
            discord_token="", discord_token_type="bot", channel_ids=[],
            vault_path=self.root, state_dir=self.root / ".state",
            env_path=self.root / ".env",
        )
        # `token-de-teste-N` é o placeholder que `tests/test_segredos.py` aceita
        # por lista branca. Um valor inventado aqui faria a guarda de segredo
        # acusar este arquivo — e ela está certa: token com cara de token dentro
        # do repositório é indistinguível de vazamento para quem varre depois.
        (self.root / ".env").write_text(
            "DISCORD_TOKEN=token-de-teste-1\nDISCORD_CHANNEL_IDS=42\n", encoding="utf-8"
        )
        self._run = subprocess.run
        self.addCleanup(lambda: setattr(subprocess, "run", self._run))

    def test_o_ambiente_do_painel_nao_vaza_para_o_comando(self):
        """Mesma causa do defeito já travado em `test_env_compose`, outro caminho:
        lá era o `docker compose`, aqui é `python3 -m dlb <cmd>`. O painel sobe com
        `DISCORD_TOKEN=` vazio do `.env.example`, `setdefault` não sobrescreve
        vazio, e o filho recusava depois de o token ter sido salvo."""
        import os

        capturado = {}

        def falso(cmd, **kwargs):
            capturado.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        subprocess.run = falso
        antigo = os.environ.get("DISCORD_TOKEN")
        os.environ["DISCORD_TOKEN"] = ""          # como o painel fica ao subir
        try:
            acoes.rodar(self.cfg, "temas")
        finally:
            if antigo is None:
                os.environ.pop("DISCORD_TOKEN", None)
            else:
                os.environ["DISCORD_TOKEN"] = antigo

        ambiente = capturado["env"]
        # Ausente, e não corrigido: o filho chama `load_dotenv` e lê o arquivo.
        # Passar o valor certo aqui seria o painel virar uma segunda fonte da
        # configuração, que é a divergência que este projeto recusa em toda parte.
        self.assertNotIn("DISCORD_TOKEN", ambiente)
        self.assertNotIn("DISCORD_CHANNEL_IDS", ambiente)
        self.assertIn("PATH", ambiente)

    def test_o_que_o_arquivo_nao_define_continua_valendo(self):
        """A limpeza é cirúrgica. Levar `HOME` embora quebraria o filho de um jeito
        que nada nesta tela explicaria."""
        import os

        ambiente = env_io.ambiente_limpo(self.cfg.env_path)
        for chave in ("PATH", "HOME"):
            if chave in os.environ:
                self.assertEqual(ambiente.get(chave), os.environ[chave])


class TestCmdPainelNaoExigeVault(VaultCase):
    """O segundo bloqueio, e o que quase passou sem guarda.

    Os outros testes deste arquivo sobem o servidor por `construir()`, então
    nenhum deles passava por `cmd_painel` — devolver `cfg.validate_vault()` para
    lá deixava a suíte inteira verde e o clone novo travado do mesmo jeito. Este
    teste existe por causa dessa lacuna, achada mutando o código de propósito.
    """

    def test_painel_sobe_com_vault_inexistente(self):
        import dlb.cli as cli
        import dlb.painel as painel

        cfg = Config(
            discord_token="", discord_token_type="bot", channel_ids=[],
            vault_path=self.root / "nao-existe",
            state_dir=self.root / ".state",
            env_path=self.root / ".env",
        )
        chegou = []
        original_servir = painel.servir
        painel.servir = lambda cfg, host, porta: (chegou.append((host, porta)), 0)[1]
        self.addCleanup(lambda: setattr(painel, "servir", original_servir))

        original_config = cli.Config
        cli.Config = type(
            "ConfigFalso", (), {"from_env": staticmethod(lambda estrito=True: cfg)}
        )
        self.addCleanup(lambda: setattr(cli, "Config", original_config))
        journal._atual = None
        self.addCleanup(lambda: setattr(journal, "_atual", None))

        self.assertEqual(cli.main(["painel", "--porta", "0"]), 0)
        self.assertEqual(chegou, [("127.0.0.1", 0)])   # chegou a servir, não recusou


class TestProblemaDeConfiguracaoNaoSaiComoTraceback(VaultCase):
    """Código 2 e uma frase, não um traceback — porque o painel mostra esta saída
    numa caixa de texto, e traceback ali é ruído sobre um problema que tem nome."""

    def test_vault_errado_no_meio_do_comando_sai_com_codigo_2(self):
        import dlb.cli as cli
        import dlb.config as mod_config

        cfg = Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root / "nao-existe",
            state_dir=self.root / ".state",
            env_path=self.root / ".env",
        )
        originais = {"Config": cli.Config}
        cli.Config = type(
            "ConfigFalso", (), {"from_env": staticmethod(lambda estrito=True: cfg)}
        )
        self.addCleanup(lambda: setattr(cli, "Config", originais["Config"]))
        journal._atual = None
        self.addCleanup(lambda: setattr(journal, "_atual", None))

        self.assertEqual(cli.main(["temas"]), 2)


if __name__ == "__main__":
    unittest.main()
