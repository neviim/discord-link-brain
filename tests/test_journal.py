"""Rode com: python3 -m unittest discover -s tests -t .

O histórico de execução e o lock — os dois pedaços que não existiam e sem os quais
qualquer painel mente.

O que se trava aqui é o que dá para perder em silêncio:

- **um escritor por diretório de run.** Se dois processos escrevessem o mesmo
  arquivo de histórico, o registro sairia intercalado e ninguém notaria — é a
  mesma armadilha que `state.py` resolveu separando o cursor do responder.
- **o lock recusando em vez de esperar.** Duas execuções sobre o mesmo cursor de
  canal fazem read-modify-write concorrente: um trecho do canal deixa de ser lido
  e a deduplicação por URL nunca recria o que nunca foi lido.
- **status distinguindo falha de recusa.** `erro` pede que alguém olhe; `recusado`
  é o sistema funcionando como desenhado. Somar os dois faz alerta gritar sozinho.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

import dlb.cli as cli
import dlb.journal as journal
from dlb.config import Config


class JournalCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / ".state"
        journal._atual = None

    def tearDown(self):
        journal._atual = None
        self._tmp.cleanup()

    def resumos(self):
        return journal.listar(self.state, limite=None)


class TestUmDiretorioPorRun(JournalCase):
    def test_abre_com_status_rodando_antes_de_terminar(self):
        """Quem olha durante a execução tem que ver que ela está em pé.

        É a única informação que precisa estar em disco *durante* o run — o
        resto pode esperar o fim. Sem isto, o painel não distingue "nada rodou
        hoje" de "está rodando agora".
        """
        run = journal.abrir(self.state, "sync", ["sync"])
        dados = journal.ler(run.diretorio)
        self.assertEqual(dados["status"], "rodando")
        self.assertIsNone(dados["fim"])
        self.assertEqual(dados["comando"], "sync")
        self.assertEqual(dados["versao"], journal.VERSAO)

    def test_o_nome_do_diretorio_ordena_por_tempo(self):
        """A ordem sai do nome, não do conteúdo: listar não abre arquivo para ordenar."""
        run = journal.abrir(self.state, "sync", [])
        instante, comando, pid = run.id.split("-")
        self.assertRegex(instante, r"^\d{8}T\d{6}Z$")
        self.assertEqual(comando, "sync")
        self.assertEqual(run.diretorio.name, run.id)
        self.assertEqual((run.diretorio / "summary.json").name, "summary.json")

    def test_fechar_duas_vezes_nao_sobrescreve(self):
        """Idempotente porque `registrar` fecha no caminho de erro e o chamador
        pode ter fechado antes."""
        run = journal.abrir(self.state, "sync", [])
        run.fechar(exit_code=0)
        run.fechar(exit_code=1)
        self.assertEqual(journal.ler(run.diretorio)["status"], "ok")


class TestProcessoLongevo(JournalCase):
    """`painel` e `responder` ficam de pé por desenho, e não são job pendurado.

    Descoberto matando o painel com SIGTERM durante o teste ao vivo: sinal não é
    exceção, o `registrar` não o vê, e o resumo fica `rodando` para sempre. Para
    `sync` isso é anomalia e o gestor alerta; para o painel é o normal.
    """

    def test_painel_e_responder_saem_marcados(self):
        for comando in ("painel", "responder"):
            with self.subTest(comando):
                run = journal.abrir(self.state, comando, [])
                self.assertTrue(journal.ler(run.diretorio)["longevo"])

    def test_comando_de_ciclo_nao_e_longevo(self):
        for comando in ("sync", "backfill", "sintese", "ressurgir"):
            with self.subTest(comando):
                run = journal.abrir(self.state, comando, [])
                self.assertFalse(journal.ler(run.diretorio)["longevo"])


class TestOsQuatroDesfechos(JournalCase):
    def test_codigo_zero_e_ok(self):
        with journal.registrar(self.state, "sync", []) as run:
            run.fechar(exit_code=0)
        self.assertEqual(self.resumos()[0]["status"], "ok")

    def test_codigo_diferente_de_zero_e_falha_nao_erro(self):
        """O comando reportou o problema sozinho — canal sem permissão, dia de
        classificação que não deu. Não é bug, e a tela não deve tratar como tal."""
        with journal.registrar(self.state, "sync", []) as run:
            run.fechar(exit_code=1)
        dados = self.resumos()[0]
        self.assertEqual(dados["status"], "falha")
        self.assertEqual(dados["exit_code"], 1)
        self.assertIsNone(dados["erro"])

    def test_excecao_e_erro_e_continua_subindo(self):
        """O journal observa; não engole. Painel dizendo 'erro' e terminal dizendo
        'ok' seria pior que não ter histórico."""
        with self.assertRaises(ZeroDivisionError):
            with journal.registrar(self.state, "sync", []):
                1 / 0
        dados = self.resumos()[0]
        self.assertEqual(dados["status"], "erro")
        self.assertIn("ZeroDivisionError", dados["erro"])

    def test_ctrl_c_tambem_fica_registrado(self):
        """`KeyboardInterrupt` não é `Exception`. Um `except Exception` deixaria o
        run eternamente 'rodando' e o painel mostraria um sync fantasma."""
        with self.assertRaises(KeyboardInterrupt):
            with journal.registrar(self.state, "sync", []):
                raise KeyboardInterrupt
        self.assertEqual(self.resumos()[0]["status"], "erro")


class TestContadores(JournalCase):
    def test_somam_e_vao_para_o_disco(self):
        with journal.registrar(self.state, "sync", []) as run:
            journal.contar("links_novos", 2)
            journal.contar("links_novos", 3)
            journal.contar("canais")
            run.fechar(exit_code=0)
        self.assertEqual(
            self.resumos()[0]["contadores"], {"canais": 1, "links_novos": 5}
        )

    def test_contar_zero_nao_cria_a_chave(self):
        """Contador em zero e contador ausente dizem a mesma coisa, e a tela fica
        mais limpa sem seis zeros."""
        with journal.registrar(self.state, "sync", []) as run:
            journal.contar("erros", 0)
            run.fechar(exit_code=0)
        self.assertEqual(self.resumos()[0]["contadores"], {})

    def test_contar_sem_run_aberto_nao_explode(self):
        """É o que permite instrumentar `cmd_sync` sem mudar a assinatura dele.

        Os testes que chamam `cmd_sync` direto não montam journal nenhum; se
        `contar` exigisse um, a instrumentação quebraria a suíte inteira.
        """
        journal.contar("links_novos", 7)
        self.assertIsNone(journal.atual())
        self.assertEqual(self.resumos(), [])


class TestLeitura(JournalCase):
    def test_lista_do_mais_recente_para_o_mais_antigo(self):
        for comando in ("sync", "backfill", "sintese"):
            with journal.registrar(self.state, comando, []) as run:
                run.fechar(exit_code=0)
            time.sleep(0.001)
        # Mesmo segundo e mesmo pid: o desempate é o nome, e o que importa é que
        # os três apareçam. A ordem por comando é checada no filtro abaixo.
        self.assertEqual(len(self.resumos()), 3)

    def test_ultimo_de_um_comando_ignora_os_outros(self):
        with journal.registrar(self.state, "sintese", []) as run:
            run.fechar(exit_code=0)
        with journal.registrar(self.state, "sync", []) as run:
            run.fechar(exit_code=1)
        self.assertEqual(journal.ultimo(self.state, "sync")["status"], "falha")
        self.assertEqual(journal.ultimo(self.state, "sintese")["status"], "ok")
        self.assertIsNone(journal.ultimo(self.state, "backfill"))

    def test_run_sem_summary_e_ignorado_em_vez_de_derrubar(self):
        """Processo morto entre o `mkdir` e a escrita deixa diretório vazio. Um
        painel que estourasse por isso ficaria inútil justo quando deu errado."""
        (self.state / "runs" / "20260101T000000Z-sync-1").mkdir(parents=True)
        (self.state / "runs" / "20260101T000001Z-sync-2").mkdir(parents=True)
        (self.state / "runs" / "20260101T000001Z-sync-2" / "summary.json").write_text(
            "{lixo", encoding="utf-8"
        )
        self.assertEqual(self.resumos(), [])

    def test_listar_sem_pasta_de_runs_e_lista_vazia(self):
        self.assertEqual(journal.listar(self.state), [])


class TestPoda(JournalCase):
    def test_mantem_os_mais_recentes_e_apaga_o_resto(self):
        """Quatro comandos por ciclo, quatro ciclos por dia: sem poda passa de
        cinco mil diretórios por ano."""
        for i in range(6):
            d = self.state / "runs" / f"20260101T0000{i:02d}Z-sync-{i}"
            d.mkdir(parents=True)
            (d / "summary.json").write_text(json.dumps({"id": str(i)}), encoding="utf-8")
        self.assertEqual(journal.podar(self.state, manter=2), 4)
        nomes = sorted(p.name for p in (self.state / "runs").iterdir())
        self.assertEqual(nomes, ["20260101T000004Z-sync-4", "20260101T000005Z-sync-5"])

    def test_nao_apaga_pasta_que_nao_e_de_run(self):
        """`.state/` é do sistema. Apagar recursivamente o que apareceu ali porque
        estava velho é o tipo de coisa que se descobre tarde."""
        alheia = self.state / "runs" / "20260101T000000Z-sync-1"
        alheia.mkdir(parents=True)
        (alheia / "coisa-de-outro.txt").write_text("não me apague", encoding="utf-8")
        outra = self.state / "runs" / "nome-que-nao-e-run"
        outra.mkdir(parents=True)

        self.assertEqual(journal.podar(self.state, manter=0), 0)
        self.assertTrue(alheia.is_dir())
        self.assertTrue(outra.is_dir())


class TestLock(JournalCase):
    def test_a_segunda_execucao_e_recusada_com_ha_quantos_segundos(self):
        """O critério do plano, no nível do lock.

        `flock` é por descritor aberto, não por processo — então duas aberturas
        no mesmo processo disputam de verdade, do mesmo jeito que o container e o
        host disputariam sobre o bind mount.
        """
        with journal.travar(self.state, "canais", "sync", run_id="r1"):
            with self.assertRaises(journal.Ocupado) as caixa:
                with journal.travar(self.state, "canais", "sync", run_id="r2"):
                    self.fail("o segundo não devia ter entrado")
        mensagem = str(caixa.exception)
        self.assertIn("sync já está rodando há menos de 1 s", mensagem)
        self.assertIn("corrompem o cursor", mensagem)
        self.assertEqual(caixa.exception.dono["run_id"], "r1")

    def test_solta_o_lock_ao_sair_inclusive_por_excecao(self):
        with self.assertRaises(RuntimeError):
            with journal.travar(self.state, "canais", "sync"):
                raise RuntimeError("explodiu no meio do sync")
        with journal.travar(self.state, "canais", "sync"):
            pass  # entrou: o lock não ficou preso ao processo morto

    def test_dono_diz_quem_esta_com_ele_e_none_quando_livre(self):
        self.assertIsNone(journal.dono(self.state, "canais"))
        with journal.travar(self.state, "canais", "backfill", run_id="r9"):
            atual = journal.dono(self.state, "canais")
            self.assertEqual(atual["comando"], "backfill")
            self.assertEqual(atual["run_id"], "r9")
        self.assertIsNone(journal.dono(self.state, "canais"))

    def test_metadado_orfao_de_processo_morto_nao_tranca_ninguem(self):
        """`kill -9` deixa o `.json` para trás. A verdade é o `flock`, não o
        arquivo — é assim que 'rodando' se distingue de 'morreu no meio' sem
        inventar timeout."""
        pasta = self.state / "locks"
        pasta.mkdir(parents=True)
        (pasta / "canais.lock").touch()
        (pasta / "canais.json").write_text(
            json.dumps({"comando": "sync", "pid": 999999}), encoding="utf-8"
        )
        self.assertIsNone(journal.dono(self.state, "canais"))
        with journal.travar(self.state, "canais", "sync"):
            pass

    def test_comandos_sem_lock_nao_se_atrapalham(self):
        """`responder` tem cursor próprio e roda de 60 em 60 s. Travá-lo junto do
        sync faria o bot ficar mudo durante um sync longo, de graça."""
        with journal.travar_comando(self.state, "sync", "r1"):
            with journal.travar_comando(self.state, "responder", "r2"):
                with journal.travar_comando(self.state, "buscar", "r3"):
                    pass
        self.assertEqual(journal.LOCK_DE_COMANDO.get("backfill"), "canais")


class TestMainRegistraERecusa(JournalCase):
    """O critério de pronto da F0 no nível do comando, não do módulo."""

    def setUp(self):
        super().setUp()
        self.chamou = []
        self.originais = {"Config": cli.Config, "cmd_sync": cli.cmd_sync}
        cfg = Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=Path(self._tmp.name), state_dir=self.state,
        )
        cli.Config = type("ConfigFalso", (), {"from_env": staticmethod(lambda: cfg)})
        cli.cmd_sync = lambda cfg, args: (self.chamou.append(args), 0)[1]

    def tearDown(self):
        for nome, valor in self.originais.items():
            setattr(cli, nome, valor)
        super().tearDown()

    def test_execucao_normal_deixa_run_ok_no_disco(self):
        self.assertEqual(cli.main(["sync", "--dry-run"]), 0)
        self.assertEqual(len(self.chamou), 1)
        dados = journal.ultimo(self.state, "sync")
        self.assertEqual(dados["status"], "ok")
        self.assertEqual(dados["argv"], ["sync", "--dry-run"])

    def test_segundo_sync_e_recusado_com_codigo_3_e_nem_roda(self):
        """Recusar precisa acontecer **antes** do comando: se ele rodasse e o
        journal só anotasse depois, o cursor já estaria corrompido."""
        with journal.travar(self.state, "canais", "sync", run_id="r1"):
            self.assertEqual(cli.main(["sync"]), 3)
        self.assertEqual(self.chamou, [])
        dados = journal.ultimo(self.state, "sync")
        self.assertEqual(dados["status"], "recusado")
        self.assertIn("já está rodando há", dados["erro"])

    def test_comando_que_falha_sai_com_o_codigo_dele_e_status_falha(self):
        cli.cmd_sync = lambda cfg, args: 1
        self.assertEqual(cli.main(["sync"]), 1)
        self.assertEqual(journal.ultimo(self.state, "sync")["status"], "falha")

    def test_excecao_no_comando_e_registrada_antes_de_subir(self):
        def explode(cfg, args):
            raise OSError("disco cheio")

        cli.cmd_sync = explode
        with self.assertRaises(OSError):
            cli.main(["sync"])
        self.assertEqual(journal.ultimo(self.state, "sync")["status"], "erro")


if __name__ == "__main__":
    unittest.main()
