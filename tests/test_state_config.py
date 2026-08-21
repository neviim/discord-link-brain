"""Rode com: python3 -m unittest discover -s tests -t .

Cursor e configuração são a memória do pipeline. Um bug aqui não aparece na
saída: aparece como link reprocessado, link perdido, ou container lendo o vault
errado. Por isso as invariantes ficam presas em teste.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

import dlb.config as mod_config
from dlb.config import Config, ConfigError, load_dotenv
from dlb.state import ChannelState, Store


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_canal_desconhecido_devolve_estado_vazio(self):
        estado = self.store.load("nunca-visto")
        self.assertIsNone(estado.last_message_id)
        self.assertFalse(estado.backfill_done)

    def test_ida_e_volta_preserva_tudo(self):
        self.store.save("42", ChannelState("111", None, "arquivar", "999", False))
        lido = self.store.load("42")
        self.assertEqual(lido.last_message_id, "111")
        self.assertEqual(lido.channel_name, "arquivar")
        self.assertEqual(lido.backfill_after, "999")

    def test_os_dois_cursores_sao_independentes(self):
        # O sync anda para frente sem mexer no cursor do histórico, e vice-versa.
        self.store.save("42", ChannelState("100", None, "c", backfill_after="5"))
        estado = self.store.load("42")
        estado.last_message_id = "200"
        self.store.save("42", estado)
        self.assertEqual(self.store.load("42").backfill_after, "5")

    def test_arquivo_corrompido_nao_derruba_o_pipeline(self):
        # Melhor recomeçar do zero do que abortar a execução das 6h.
        (Path(self._tmp.name) / "42.json").write_text("{lixo", encoding="utf-8")
        self.assertIsNone(self.store.load("42").last_message_id)

    def test_campo_desconhecido_de_versao_futura_nao_quebra(self):
        (Path(self._tmp.name) / "42.json").write_text(
            '{"last_message_id": "7", "campo_do_futuro": 1}', encoding="utf-8"
        )
        self.assertIsNone(self.store.load("42").last_message_id)

    def test_save_carimba_a_hora_e_cria_o_diretorio(self):
        sub = Store(Path(self._tmp.name) / "ainda-nao-existe")
        sub.save("42", ChannelState("1"))
        self.assertTrue(sub.load("42").last_run)

    def test_json_fica_legivel_para_humano(self):
        # Você vai abrir esse arquivo para depurar algum dia.
        self.store.save("42", ChannelState("1", None, "🔗_links"))
        bruto = (Path(self._tmp.name) / "42.json").read_text(encoding="utf-8")
        self.assertIn("🔗_links", bruto)      # sem \u escapado
        self.assertEqual(json.loads(bruto)["last_message_id"], "1")


class ConfigCase(unittest.TestCase):
    CHAVES = (
        "DISCORD_TOKEN", "DISCORD_TOKEN_TYPE", "DISCORD_CHANNEL_IDS",
        "DISCORD_CHANNEL_NAMES", "DISCORD_GUILD_IDS", "OBSIDIAN_VAULT",
        "TIMEZONE", "LLM_BACKEND",
        "MARK_PROCESSED", "PROCESSED_EMOJI", "DUPLICATE_EMOJI", "ENRICH_LINKS",
        "LINK_NOTES",
    )

    def setUp(self):
        self._salvo = {k: os.environ.pop(k, None) for k in self.CHAVES}

    def tearDown(self):
        for k, v in self._salvo.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


class TestConfig(ConfigCase):
    """`from_env` lê o `.env` do projeto; aqui isso é ruído.

    Sem neutralizar, os testes do caminho de erro nunca disparam na máquina de
    quem tem um `.env` preenchido — passariam em CI e falhariam localmente, ou
    o contrário. O carregamento do arquivo em si é testado em `TestDotenv`.
    """

    def setUp(self):
        super().setUp()
        import dlb.config as mod

        self._mod = mod
        self._load_real = mod.load_dotenv
        mod.load_dotenv = lambda path=None: None

    def tearDown(self):
        self._mod.load_dotenv = self._load_real
        super().tearDown()

    def test_sem_token_falha_dizendo_o_que_fazer(self):
        os.environ["DISCORD_CHANNEL_IDS"] = "1"
        with self.assertRaises(ConfigError) as ctx:
            Config.from_env()
        self.assertIn(".env", str(ctx.exception))

    def test_sem_canal_falha(self):
        os.environ["DISCORD_TOKEN"] = "abc"
        with self.assertRaises(ConfigError):
            Config.from_env()

    def test_canais_separados_por_virgula_com_espaco(self):
        os.environ.update(DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS=" 1, 2 ,3 ")
        self.assertEqual(Config.from_env().channel_ids, ["1", "2", "3"])

    def test_apelidos_de_canal(self):
        os.environ.update(
            DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS="1,2",
            DISCORD_CHANNEL_NAMES="1:arquivar, 2:estudos",
        )
        cfg = Config.from_env()
        self.assertEqual(cfg.channel_label("1"), "arquivar")
        self.assertEqual(cfg.channel_label("2"), "estudos")

    def test_canal_sem_apelido_cai_no_proprio_id(self):
        os.environ.update(DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS="9")
        self.assertEqual(Config.from_env().channel_label("9"), "9")

    def test_header_de_bot_e_de_usuario(self):
        os.environ.update(DISCORD_TOKEN="segredo", DISCORD_CHANNEL_IDS="1")
        self.assertEqual(Config.from_env().auth_header, "Bot segredo")
        os.environ["DISCORD_TOKEN_TYPE"] = "user"
        self.assertEqual(Config.from_env().auth_header, "segredo")

    def test_til_do_vault_e_expandido(self):
        os.environ.update(DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS="1",
                          OBSIDIAN_VAULT="~/vault-de-teste")
        self.assertEqual(Config.from_env().vault_path, Path.home() / "vault-de-teste")

    def test_as_duas_marcas_sao_configuraveis(self):
        os.environ.update(DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS="1",
                          PROCESSED_EMOJI="🟢", DUPLICATE_EMOJI="🔵")
        cfg = Config.from_env()
        self.assertEqual(cfg.marcas, ("🟢", "🔵"))

    def test_marcas_traz_as_duas_por_padrao(self):
        os.environ.update(DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS="1")
        cfg = Config.from_env()
        self.assertEqual(len(set(cfg.marcas)), 2)

    def test_booleanos_aceitam_as_formas_usuais(self):
        for valor, esperado in (("0", False), ("false", False), ("no", False),
                                ("1", True), ("sim", True)):
            with self.subTest(valor=valor):
                os.environ.update(DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS="1",
                                  MARK_PROCESSED=valor, ENRICH_LINKS=valor)
                cfg = Config.from_env()
                self.assertEqual(cfg.mark_processed, esperado)
                self.assertEqual(cfg.enrich, esperado)

    def test_validate_vault_reclama_das_pastas_que_faltam(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "10_FONTES").mkdir()
            os.environ.update(DISCORD_TOKEN="abc", DISCORD_CHANNEL_IDS="1",
                              OBSIDIAN_VAULT=tmp)
            with self.assertRaises(ConfigError) as ctx:
                Config.from_env().validate_vault()
            self.assertIn("20_WIKI", str(ctx.exception))
            self.assertNotIn("10_FONTES", str(ctx.exception))


class TestDotenv(ConfigCase):
    """O container depende disto: variável do ambiente vence a do arquivo.

    É o que permite o compose apontar OBSIDIAN_VAULT=/vault sem que o `.env`
    do projeto (que traz o caminho do host) sobrescreva de volta.
    """

    def test_ambiente_vence_o_arquivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("OBSIDIAN_VAULT=/caminho/do/host\n", encoding="utf-8")
            os.environ["OBSIDIAN_VAULT"] = "/vault"
            load_dotenv(env)
            self.assertEqual(os.environ["OBSIDIAN_VAULT"], "/vault")

    def test_arquivo_preenche_o_que_falta(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("TIMEZONE=Europe/Lisbon\n", encoding="utf-8")
            load_dotenv(env)
            self.assertEqual(os.environ["TIMEZONE"], "Europe/Lisbon")

    def test_comentario_linha_vazia_e_aspas(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "# um comentário\n\nLLM_BACKEND='none'\nPROCESSED_EMOJI=\"✅\"\n",
                encoding="utf-8",
            )
            load_dotenv(env)
            self.assertEqual(os.environ["LLM_BACKEND"], "none")
            self.assertEqual(os.environ["PROCESSED_EMOJI"], "✅")

    def test_arquivo_ausente_nao_e_erro(self):
        load_dotenv(Path("/tmp/nao-existe-mesmo-.env"))


class TestConfigDaCamadaDeLinks(ConfigCase):
    """As duas chaves novas. Sem elas em CHAVES, um `.env` local com
    `LINK_NOTES=0` faria teste passar na máquina e falhar em CI."""

    def setUp(self):
        super().setUp()
        os.environ.update(DISCORD_TOKEN="x", DISCORD_CHANNEL_IDS="1")
        self._dotenv = mod_config.load_dotenv
        mod_config.load_dotenv = lambda *a, **k: None

    def tearDown(self):
        mod_config.load_dotenv = self._dotenv
        super().tearDown()

    def test_link_notes_ligado_por_padrao(self):
        self.assertTrue(Config.from_env().link_notes)

    def test_link_notes_desligavel_por_ambiente(self):
        for valor in ("0", "false", "no"):
            with self.subTest(valor):
                os.environ["LINK_NOTES"] = valor
                self.assertFalse(Config.from_env().link_notes)

    def test_guild_ids_vem_como_mapa_canal_para_servidor(self):
        os.environ["DISCORD_GUILD_IDS"] = "111:999, 222:888"
        self.assertEqual(Config.from_env().guild_ids, {"111": "999", "222": "888"})

    def test_guild_ids_ausente_e_mapa_vazio(self):
        """Sem a config, `migrar` não inventa servidor — deixa o link como está."""
        self.assertEqual(Config.from_env().guild_ids, {})

    def test_par_malformado_e_ignorado_em_silencio(self):
        os.environ["DISCORD_GUILD_IDS"] = "111:999,lixo,:,"
        self.assertEqual(Config.from_env().guild_ids, {"111": "999"})


if __name__ == "__main__":
    unittest.main()


class TestEnvExampleNaoFicaParaTras(unittest.TestCase):
    """Toda variável que o código lê tem que estar no `.env.example`.

    Este teste existe porque o buraco aconteceu: `RESSURGIR_CANAL` foi
    implementada, documentada no CONFIGURACAO.md e esquecida no exemplo — e quem
    foi procurar não achou. Documentação some do radar; teste não.
    """

    # Postas pelo docker-compose, não por quem edita o .env. Estarem no exemplo
    # sugeriria que você deve defini-las, e você não deve.
    INTERNAS = {"DLB_MODE", "DLB_HEARTBEAT", "DLB_HEARTBEAT_RESPONDER"}

    def test_toda_variavel_lida_esta_no_exemplo(self):
        raiz = Path(__file__).resolve().parent.parent
        codigo = (raiz / "dlb" / "config.py").read_text(encoding="utf-8")
        lidas = set(re.findall(r'os\.environ\.get\(\s*"([A-Z][A-Z0-9_]*)"', codigo))
        exemplo = set(re.findall(
            r"^\s*#?\s*([A-Z][A-Z0-9_]*)=",
            (raiz / ".env.example").read_text(encoding="utf-8"),
            re.M,
        ))
        faltando = sorted(lidas - exemplo - self.INTERNAS)
        self.assertEqual(
            faltando, [],
            f"variáveis lidas pelo código e ausentes do .env.example: {faltando}",
        )

    def test_nenhuma_chave_aparece_duas_vezes_no_exemplo(self):
        """Chave repetida no exemplo é default mentindo, não desarrumação.

        `load_dotenv` usa `os.environ.setdefault`, então em `.env` a **primeira**
        ocorrência ganha e a segunda não avisa nada. Este teste existe porque o
        buraco aconteceu: `DISCORD_POST_ENABLED` estava duas vezes, `=1` na linha
        15 e `=0` na 118 — quem copiasse o exemplo saía com o bot autorizado a
        postar, o oposto do default que o comentário da segunda linha explica.
        """
        raiz = Path(__file__).resolve().parent.parent
        chaves = re.findall(
            r"^([A-Z][A-Z0-9_]*)=",
            (raiz / ".env.example").read_text(encoding="utf-8"),
            re.M,
        )
        repetidas = sorted({k for k in chaves if chaves.count(k) > 1})
        self.assertEqual(
            repetidas, [],
            f"chaves duplicadas no .env.example (a primeira ganha, em silêncio): {repetidas}",
        )

    def test_toda_variavel_lida_esta_documentada(self):
        raiz = Path(__file__).resolve().parent.parent
        codigo = (raiz / "dlb" / "config.py").read_text(encoding="utf-8")
        lidas = set(re.findall(r'os\.environ\.get\(\s*"([A-Z][A-Z0-9_]*)"', codigo))
        doc = (raiz / "docs" / "CONFIGURACAO.md").read_text(encoding="utf-8")
        faltando = sorted(v for v in lidas - self.INTERNAS if v not in doc)
        self.assertEqual(
            faltando, [],
            f"variáveis sem menção em docs/CONFIGURACAO.md: {faltando}",
        )
