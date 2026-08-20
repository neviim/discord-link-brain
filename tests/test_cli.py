"""Rode com: python3 -m unittest discover -s tests -t .

A orquestração. `cli.py` ficou muito tempo em 0% com a justificativa de que
testá-lo exigiria encenar Discord, LLM e vault ao mesmo tempo — verdade para
`sync` e `backfill`, e falso para metade dos comandos novos, que só leem o vault.

O que se trava aqui: o parser (um argumento renomeado quebra tudo em silêncio), a
resolução de semana ISO, a guarda de postar, e o fio entre comando e módulo — que
é justamente o que os testes de unidade não pegam.
"""

import io
import os
import sys
import unittest

import dlb.cli as cli
import dlb.config as mod_config
from dlb.busca import Resposta
from dlb.config import Config, ConfigError
from dlb.sintese import Sintese, Tema

from tests.test_vault import VaultCase, note


class TestParser(unittest.TestCase):
    """O parser é pura estrutura, e é a coisa mais barata de travar: um
    argumento renomeado sem querer quebra o comando inteiro em produção."""

    def parser(self):
        return cli.build_parser()

    def test_todos_os_comandos_existem(self):
        sub = next(
            a for a in self.parser()._actions if a.dest == "command"
        )
        self.assertEqual(
            sorted(sub.choices),
            ["backfill", "buscar", "doctor", "migrar", "painel", "responder",
             "ressurgir", "semana", "sintese", "sync", "temas"],
        )

    def test_cada_comando_tem_funcao_ligada(self):
        for nome in ("sync", "backfill", "migrar", "sintese", "buscar",
                     "responder", "ressurgir", "temas", "semana", "doctor",
                     "painel"):
            with self.subTest(nome):
                args = self.parser().parse_args([nome] + (["x"] if nome == "buscar" else []))
                self.assertTrue(callable(args.func))

    def test_comando_e_obrigatorio(self):
        with self.assertRaises(SystemExit):
            self.parser().parse_args([])

    def test_migrar_aceita_as_flags_documentadas(self):
        a = self.parser().parse_args(
            ["migrar", "--dry-run", "--force", "--no-diario", "--day", "2026-08-17",
             "--limite", "5"]
        )
        self.assertTrue(a.dry_run and a.force and a.no_diario)
        self.assertEqual((a.day, a.limite), ("2026-08-17", 5))

    def test_sintese_aceita_semana_e_mes(self):
        a = self.parser().parse_args(["sintese", "--semana", "anterior"])
        self.assertEqual(a.semana, "anterior")
        self.assertEqual(self.parser().parse_args(["sintese", "--mes", "2026-08"]).mes, "2026-08")

    def test_buscar_junta_a_pergunta_em_varias_palavras(self):
        a = self.parser().parse_args(["buscar", "o", "que", "salvei"])
        self.assertEqual(a.pergunta, ["o", "que", "salvei"])

    def test_doctor_tem_testar_envio(self):
        self.assertTrue(self.parser().parse_args(["doctor", "--testar-envio"]).testar_envio)


class TestSemanaPedida(unittest.TestCase):
    """`--semana` aceita três formas, e errar a resolução gera a nota do período
    errado — falha silenciosa, porque a nota fica plausível."""

    def resolver(self, valor, hoje="2026-08-19"):
        original = cli._hoje_local
        cli._hoje_local = lambda: hoje
        try:
            args = cli.build_parser().parse_args(
                ["sintese"] + (["--semana", valor] if valor else [])
            )
            return cli._rotulo_semana(args)
        finally:
            cli._hoje_local = original

    def test_padrao_e_a_semana_corrente(self):
        self.assertEqual(self.resolver(None), "2026-W34")

    def test_atual_e_sinonimo_do_padrao(self):
        self.assertEqual(self.resolver("atual"), "2026-W34")

    def test_anterior_volta_uma_semana(self):
        self.assertEqual(self.resolver("anterior"), "2026-W33")

    def test_rotulo_explicito_e_normalizado(self):
        self.assertEqual(self.resolver("2026-w34"), "2026-W34")

    def test_anterior_na_virada_do_ano_usa_o_ano_iso(self):
        self.assertEqual(self.resolver("anterior", hoje="2027-01-04"), "2026-W53")

    def test_valor_sem_sentido_explode_com_mensagem_util(self):
        with self.assertRaises(ConfigError) as ctx:
            self.resolver("semana passada mesmo")
        self.assertIn("AAAA-Www", str(ctx.exception))


class TestGuardaDePostar(unittest.TestCase):
    def cfg(self, **kw):
        base = dict(discord_token="x", discord_token_type="bot", channel_ids=["1"])
        base.update(kw)
        return Config(**base)

    def test_com_postar_desligado_recusa_antes_de_qualquer_coisa(self):
        with self.assertRaises(ConfigError) as ctx:
            cli._exigir_post(self.cfg(post_enabled=False))
        self.assertIn("DISCORD_POST_ENABLED", str(ctx.exception))

    def test_marca_colidindo_recusa_mesmo_com_postar_ligado(self):
        """A colisão é catastrófica e silenciosa: pergunta com link seria pulada
        pelo sync para sempre. Barrar aqui é a última linha de defesa."""
        with self.assertRaises(ConfigError):
            cli._exigir_post(self.cfg(post_enabled=True, responder_emoji="✅"))

    def test_configuracao_sadia_passa(self):
        cli._exigir_post(self.cfg(post_enabled=True))


class ComandoCase(VaultCase):
    """Vault temporário + `.env` neutralizado, para rodar comandos de verdade."""

    def setUp(self):
        super().setUp()
        self._dotenv = mod_config.load_dotenv
        mod_config.load_dotenv = lambda *a, **k: None
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("DISCORD_TOKEN", "DISCORD_CHANNEL_IDS", "OBSIDIAN_VAULT")}
        os.environ.update(DISCORD_TOKEN="x", DISCORD_CHANNEL_IDS="42")
        self.saida = []
        self._log = cli._log
        cli._log = lambda m="": self.saida.append(str(m))
        # Os comandos também usam `print` direto; capturar evita poluir a saída
        # da suíte e deixa o texto conferível.
        self._stdout = sys.stdout
        sys.stdout = self._buffer = io.StringIO()

    def tearDown(self):
        sys.stdout = self._stdout
        cli._log = self._log
        mod_config.load_dotenv = self._dotenv
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        super().tearDown()

    def cfg(self, **kw):
        base = dict(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.root / ".state",
        )
        base.update(kw)
        return Config(**base)

    def rodar(self, argv, cfg=None):
        args = cli.build_parser().parse_args(argv)
        return args.func(cfg or self.cfg(), args)

    @property
    def texto(self):
        return "\n".join(self.saida) + "\n" + self._buffer.getvalue()


class TestComandosQueSoLeem(ComandoCase):
    """Os que não tocam Discord nem modelo. Metade dos comandos novos."""

    def semear(self):
        self.vault.write_link_notes(
            "estudos",
            [note("https://e.com/a", "Relays", ["Nostr"]),
             note("https://e.com/b", "Outro", ["Nostr"])],
        )

    def test_temas_roda_e_nao_cria_nada(self):
        self.semear()
        antes = sorted(p.name for p in self.root.rglob("*"))
        self.assertEqual(self.rodar(["temas", "--limiar", "2"]), 0)
        self.assertEqual(sorted(p.name for p in self.root.rglob("*")), antes)

    def test_temas_escreve_no_arquivo_pedido(self):
        self.semear()
        alvo = self.root / "relatorio.md"
        self.rodar(["temas", "--limiar", "2", "--out", str(alvo)])
        self.assertIn("Nostr", alvo.read_text(encoding="utf-8"))

    def test_temas_sem_acervo_avisa_em_vez_de_explodir(self):
        self.assertEqual(self.rodar(["temas"]), 0)
        self.assertIn("dlb migrar", self.texto)

    def test_ressurgir_a_seco_nao_posta_nem_grava(self):
        self.semear()
        self.assertEqual(self.rodar(["ressurgir", "--dry-run", "--dias", "0"]), 0)
        self.assertFalse((self.root / ".state" / "ressurgir.json").exists())

    def test_ressurgir_sem_link_velho_o_bastante_nao_faz_nada(self):
        self.semear()
        self.assertEqual(self.rodar(["ressurgir", "--dry-run", "--dias", "9999"]), 0)
        self.assertIn("nenhum link", self.texto)

    def test_migrar_a_seco_nao_escreve(self):
        self.assertEqual(self.rodar(["migrar", "--dry-run"]), 0)
        self.assertIn("nada foi escrito", self.texto)


class TestComandosComModeloDublado(ComandoCase):
    def setUp(self):
        super().setUp()
        self.vault.write_link_notes("estudos", [note("https://e.com/a", "Relays", [])])
        self.chamadas = []

    def test_buscar_imprime_a_resposta(self):
        cli.buscar = lambda cfg, p, e: Resposta(
            texto="Achei isto.", usados=[], confianca="alta", considerados=1
        )
        self.assertEqual(self.rodar(["buscar", "o", "que", "salvei"]), 0)
        self.assertIn("confiança alta", self.texto)

    def test_buscar_sem_pergunta_devolve_codigo_de_uso(self):
        self.assertEqual(self.rodar(["buscar", "   "]), 2)

    def test_buscar_propaga_falha_do_modelo_como_codigo_1(self):
        from dlb.busca import BuscaError

        def explode(cfg, p, e):
            raise BuscaError("modelo fora do ar")

        cli.buscar = explode
        self.assertEqual(self.rodar(["buscar", "x"]), 1)

    def test_sintese_a_seco_chama_o_modelo_e_nao_escreve(self):
        cli.gerar = lambda cfg, rot, ent: (
            self.chamadas.append(rot),
            Sintese(panorama="Foi assim.", temas=[Tema("T", "x", [0])]),
        )[1]
        self.assertEqual(self.rodar(["sintese", "--semana", "2026-W33", "--dry-run"]), 0)
        self.assertEqual(self.chamadas, ["2026-W33"])
        self.assertFalse((self.root / "40_DIARIO" / "Semanal 2026-W33.md").exists())

    def test_sintese_de_semana_sem_link_nao_gasta_chamada(self):
        cli.gerar = lambda *a: self.chamadas.append(a) or Sintese()
        self.assertEqual(self.rodar(["sintese", "--semana", "1999-W01"]), 0)
        self.assertEqual(self.chamadas, [])
        self.assertIn("nenhum link", self.texto)

    def test_sintese_mensal_sem_semanal_reporta_e_falha(self):
        self.assertEqual(self.rodar(["sintese", "--mes", "2026-08"]), 1)
        self.assertIn("sem síntese", self.texto)

    def test_mes_mal_formado_explode_com_mensagem_util(self):
        with self.assertRaises(ConfigError):
            self.rodar(["sintese", "--mes", "agosto"])


class TestMain(unittest.TestCase):
    def test_configuracao_ruim_sai_com_codigo_2(self):
        salvos = {k: os.environ.pop(k, None) for k in ("DISCORD_TOKEN", "DISCORD_CHANNEL_IDS")}
        original = mod_config.load_dotenv
        mod_config.load_dotenv = lambda *a, **k: None
        try:
            self.assertEqual(cli.main(["doctor"]), 2)
        finally:
            mod_config.load_dotenv = original
            for k, v in salvos.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
