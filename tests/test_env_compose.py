"""Rode com: python3 -m unittest discover -s tests -t .

O leitor do `.env` e a leitura do container — as duas metades de "o painel não
pode mentir sobre a configuração".

O fato que organiza este arquivo está no commit `4ee85aa`: **`restart` não relê o
`.env`; `up` relê.** Então o arquivo e o processo podem discordar, sem erro nenhum,
por semanas. Um painel que mostrasse só o arquivo estaria certo metade do tempo — e
a metade errada é justamente quando alguém mexeu na configuração e quer conferir.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dlb import compose, env_io


class EnvCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.arquivo = Path(self._tmp.name) / ".env"

    def tearDown(self):
        self._tmp.cleanup()

    def escrever(self, texto):
        self.arquivo.write_text(texto, encoding="utf-8")
        return self.arquivo


class TestLeitorDoEnv(EnvCase):
    def test_a_primeira_ocorrencia_ganha_como_no_load_dotenv(self):
        """`load_dotenv` usa `os.environ.setdefault`, então a primeira vale. Um
        leitor que preferisse a última mostraria um valor que o sistema não usa —
        e foi exatamente esse o defeito do `.env.example` (linha 15 contra 118)."""
        caminho = self.escrever("DISCORD_POST_ENABLED=1\nOUTRA=x\nDISCORD_POST_ENABLED=0\n")
        self.assertEqual(env_io.ler(caminho)["DISCORD_POST_ENABLED"], "1")

    def test_aponta_as_linhas_das_chaves_repetidas(self):
        caminho = self.escrever("A=1\nB=2\nA=3\nB=4\nC=5\n")
        self.assertEqual(env_io.duplicadas(caminho), {"A": [1, 3], "B": [2, 4]})

    def test_arquivo_sem_repeticao_nao_reporta_nada(self):
        self.assertEqual(env_io.duplicadas(self.escrever("A=1\nB=2\n")), {})

    def test_aspas_saem_das_pontas_sem_interpretar_escape(self):
        """O mesmo que o `load_dotenv` faz. Um parser mais esperto aqui divergiria
        do valor que realmente chega ao processo."""
        caminho = self.escrever('A="com espaço"\nB=\'simples\'\nC=nada\n')
        self.assertEqual(
            env_io.ler(caminho), {"A": "com espaço", "B": "simples", "C": "nada"}
        )

    def test_valor_vazio_e_chave_presente_com_valor_vazio(self):
        """Não é o mesmo que ausente para o compose (`${VAR:-6}` troca vazio pelo
        padrão), e a tela de agenda precisa distinguir os dois."""
        self.assertEqual(env_io.ler(self.escrever("DLB_SYNC_EVERY_HOURS=\n")), {"DLB_SYNC_EVERY_HOURS": ""})

    def test_comentario_e_linha_em_branco_sao_preservados(self):
        """A escrita futura reescreve o arquivo mantendo tudo: um `.env` que perde
        os comentários perde a documentação que ele carrega."""
        caminho = self.escrever("# título\n\nA=1\n# B=2\n")
        todas = env_io.linhas(caminho)
        self.assertEqual([l.bruta for l in todas], ["# título", "", "A=1", "# B=2"])
        self.assertEqual([l.numero for l in todas], [1, 2, 3, 4])
        self.assertEqual([l.chave for l in todas if l.e_atribuicao], ["A"])

    def test_atribuicao_comentada_e_reconhecida_mas_nao_vale(self):
        """`# DLB_SYNC_AT=03:30` no exemplo é sugestão, não configuração."""
        caminho = self.escrever("# DLB_SYNC_AT=03:30\n")
        comentada = env_io.linhas(caminho)[0]
        self.assertTrue(comentada.comentada)
        self.assertEqual(comentada.chave, "DLB_SYNC_AT")
        self.assertEqual(env_io.ler(caminho), {})

    def test_linha_sem_igual_nao_derruba(self):
        self.assertEqual(env_io.ler(self.escrever("lixo\nA=1\n")), {"A": "1"})

    def test_arquivo_inexistente_e_dicionario_vazio(self):
        """O painel tem de abrir num clone sem `.env` — é assim que se descobre
        que falta configurar."""
        self.assertEqual(env_io.ler(Path(self._tmp.name) / "nao-existe"), {})
        self.assertEqual(env_io.linhas(Path(self._tmp.name) / "nao-existe"), [])


class TestDivergencias(unittest.TestCase):
    CHAVES = ("DLB_SYNC_EVERY_HOURS", "DLB_BACKFILL")

    def test_aponta_so_o_que_difere(self):
        fora = compose.divergencias(
            {"DLB_SYNC_EVERY_HOURS": "3", "DLB_BACKFILL": "1"},
            {"DLB_SYNC_EVERY_HOURS": "6", "DLB_BACKFILL": "1"},
            self.CHAVES,
        )
        self.assertEqual(
            fora, [{"chave": "DLB_SYNC_EVERY_HOURS", "arquivo": "3", "container": "6"}]
        )

    def test_ausente_e_vazio_sao_a_mesma_coisa(self):
        """Para o `[ -z ]` do shell também são. Reportar diferença aqui seria ruído
        em toda linha que ninguém configurou."""
        self.assertEqual(compose.divergencias({"DLB_BACKFILL": ""}, {}, self.CHAVES), [])

    def test_sem_container_nao_ha_divergencia_a_reportar(self):
        """Nada com que comparar não é "está tudo igual" — e a tela diz de onde
        veio o número em vez de fingir que conferiu."""
        self.assertEqual(compose.divergencias({"DLB_BACKFILL": "1"}, None, self.CHAVES), [])

    def test_tz_vem_de_timezone_e_nao_e_divergencia(self):
        """O compose define `TZ` no container a partir de `TIMEZONE` no `.env`.
        Sem o alias, toda instalação reportaria divergência em `TZ` para sempre —
        e alarme falso permanente é como se ensina alguém a ignorar o painel.
        Apareceu rodando contra o container de verdade."""
        self.assertEqual(
            compose.divergencias(
                {"TIMEZONE": "America/Sao_Paulo"},
                {"TZ": "America/Sao_Paulo"},
                ("TZ",),
            ),
            [],
        )

    def test_tz_que_realmente_difere_ainda_aparece(self):
        fora = compose.divergencias(
            {"TIMEZONE": "Europe/Lisbon"}, {"TZ": "America/Sao_Paulo"}, ("TZ",)
        )
        self.assertEqual(fora[0]["arquivo"], "Europe/Lisbon")

    def test_chave_sobrescrita_pelo_compose_nunca_e_divergencia(self):
        """`OBSIDIAN_VAULT` é `/vault` dentro do container por bind mount, sempre.
        Compará-la deixaria uma linha de "pendente" que nunca sai da tela — e o
        painel que sempre tem algo a mostrar é o painel que ninguém olha.
        Apareceu no primeiro teste contra o container de produção."""
        self.assertEqual(
            compose.divergencias(
                {"OBSIDIAN_VAULT": "~/Documents/vault"},
                {"OBSIDIAN_VAULT": "/vault"},
                ("OBSIDIAN_VAULT",),
            ),
            [],
        )

    def test_espaco_conta_como_diferenca(self):
        """`6` e `6 ` são diferentes para o shell. Normalizar aqui esconderia
        justamente o defeito que a função existe para expor."""
        fora = compose.divergencias(
            {"DLB_SYNC_EVERY_HOURS": "6"}, {"DLB_SYNC_EVERY_HOURS": "6x"}, self.CHAVES
        )
        self.assertEqual(len(fora), 1)


class TestAmbienteAtivo(unittest.TestCase):
    def test_container_no_ar_manda(self):
        ambiente, fonte = compose.ambiente_ativo(
            {"A": "arquivo"}, {"rodando": True, "env": {"A": "container"}}
        )
        self.assertEqual((ambiente["A"], fonte), ("container", "container"))

    def test_container_parado_vale_o_arquivo(self):
        ambiente, fonte = compose.ambiente_ativo(
            {"A": "arquivo"}, {"rodando": False, "env": {"A": "velho"}}
        )
        self.assertEqual((ambiente["A"], fonte), ("arquivo", "arquivo"))

    def test_sem_docker_vale_o_arquivo(self):
        self.assertEqual(compose.ambiente_ativo({"A": "arquivo"}, None)[1], "arquivo")


class TestInspecionar(unittest.TestCase):
    """`docker inspect` é leitura e roda no host — não precisa de socket montado.

    O que se trava aqui é a degradação: sem docker, com container parado, com
    daemon travando ou com JSON estranho, a resposta é `None` e o painel abre.
    """

    def setUp(self):
        self.originais = {"which": shutil.which, "run": subprocess.run}

    def tearDown(self):
        shutil.which = self.originais["which"]
        subprocess.run = self.originais["run"]

    def fingir(self, *, tem_docker=True, saida="", codigo=0, erro=None):
        shutil.which = lambda _nome: "/usr/bin/docker" if tem_docker else None

        def falso(*a, **k):
            if erro is not None:
                raise erro
            return subprocess.CompletedProcess(a[0], codigo, saida, "")

        subprocess.run = falso

    def test_le_o_ambiente_e_o_estado(self):
        self.fingir(
            saida=json.dumps(
                [
                    {
                        "Created": "2026-08-19T10:00:00Z",
                        "Config": {
                            "Image": "discord-link-brain:prod",
                            "Env": ["TZ=America/Sao_Paulo", "DLB_SYNC_EVERY_HOURS=6", "SEM_VALOR="],
                        },
                        "State": {
                            "Running": True,
                            "Status": "running",
                            "Health": {"Status": "healthy"},
                            "StartedAt": "2026-08-19T10:00:01Z",
                        },
                    }
                ]
            )
        )
        dados = compose.inspecionar()
        self.assertTrue(dados["rodando"])
        self.assertEqual(dados["saude"], "healthy")
        self.assertEqual(dados["env"]["DLB_SYNC_EVERY_HOURS"], "6")
        self.assertEqual(dados["env"]["SEM_VALOR"], "")

    def test_sem_docker_instalado_devolve_none(self):
        self.fingir(tem_docker=False)
        self.assertIsNone(compose.inspecionar())

    def test_container_inexistente_devolve_none(self):
        self.fingir(codigo=1, saida="")
        self.assertIsNone(compose.inspecionar())

    def test_json_estranho_devolve_none(self):
        for saida in ("[]", "{}", "não é json", ""):
            with self.subTest(saida=saida):
                self.fingir(saida=saida)
                self.assertIsNone(compose.inspecionar())

    def test_timeout_do_daemon_devolve_none_em_vez_de_pendurar_a_tela(self):
        self.fingir(erro=subprocess.TimeoutExpired("docker", 5))
        self.assertIsNone(compose.inspecionar())

    def test_docker_que_nao_executa_devolve_none(self):
        self.fingir(erro=OSError("permission denied"))
        self.assertIsNone(compose.inspecionar())


class TestAplicar(unittest.TestCase):
    """`./dlb.sh prod up`, e o que acontece quando ele não pode rodar.

    O painel chama o wrapper em vez de `docker compose` direto porque o compose
    precisa de meia dúzia de caminhos do host exportados e falha alto sem eles —
    reimplementar essa resolução aqui seria uma segunda chance de errar.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self._tmp.name)
        (self.raiz / "dlb.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        self.originais = {"which": shutil.which, "run": subprocess.run}
        shutil.which = lambda _n: "/usr/bin/docker"

    def tearDown(self):
        shutil.which = self.originais["which"]
        subprocess.run = self.originais["run"]
        self._tmp.cleanup()

    def test_sucesso_devolve_a_saida_do_comando(self):
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, "Container dlb-prod Recreated\n", ""
        )
        resultado = compose.aplicar(self.raiz)
        self.assertTrue(resultado["ok"])
        self.assertIn("Recreated", resultado["saida"])
        self.assertEqual(resultado["comando"], "./dlb.sh prod up")

    def test_o_ambiente_do_painel_nao_vaza_para_o_compose(self):
        """O defeito que só apareceu rodando ao vivo.

        `load_dotenv` faz `setdefault` no ambiente **do processo**, então o painel
        carrega em si os valores que o `.env` tinha quando subiu. O subprocess
        herda, e a interpolação do compose prefere shell a arquivo — o container
        era recriado com o valor velho, com código 0 e a tela dizendo "recriado".
        """
        import os

        (self.raiz / ".env").write_text(
            "DLB_HEALTH_TOLERANCE=28800\nDISCORD_TOKEN=x\n", encoding="utf-8"
        )
        capturado = {}

        def falso(cmd, **kwargs):
            capturado.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        subprocess.run = falso
        antigo = os.environ.get("DLB_HEALTH_TOLERANCE")
        os.environ["DLB_HEALTH_TOLERANCE"] = "28801"      # como o painel ficaria
        try:
            compose.aplicar(self.raiz)
        finally:
            if antigo is None:
                os.environ.pop("DLB_HEALTH_TOLERANCE", None)
            else:
                os.environ["DLB_HEALTH_TOLERANCE"] = antigo

        ambiente = capturado["env"]
        self.assertNotIn("DLB_HEALTH_TOLERANCE", ambiente)   # o compose lê do arquivo
        self.assertNotIn("DISCORD_TOKEN", ambiente)
        # E o que o wrapper precisa continua lá.
        self.assertIn("PATH", ambiente)

    def test_usa_up_e_nunca_restart(self):
        """`restart` não relê o `.env`. Um botão que usasse restart não faria
        efeito e não avisaria — é o defeito que o painel existe para não repetir."""
        self.assertEqual(compose.COMANDO_APLICAR, ("./dlb.sh", "prod", "up"))
        self.assertNotIn("restart", " ".join(compose.COMANDO_APLICAR))

    def test_falha_do_comando_vem_com_codigo_e_saida(self):
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, "", "erro: porta ocupada"
        )
        resultado = compose.aplicar(self.raiz)
        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["codigo"], 1)
        self.assertIn("porta ocupada", resultado["saida"])

    def test_saida_gigante_e_cortada_no_fim(self):
        """A saída do compose é verbosa e o que interessa é o fim."""
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, "\n".join(f"linha {i}" for i in range(200)), ""
        )
        saida = compose.aplicar(self.raiz)["saida"]
        self.assertEqual(len(saida.splitlines()), 25)
        self.assertIn("linha 199", saida)

    def test_sem_o_wrapper_explica_em_vez_de_estourar(self):
        (self.raiz / "dlb.sh").unlink()
        resultado = compose.aplicar(self.raiz)
        self.assertFalse(resultado["ok"])
        self.assertIn("raiz do projeto", resultado["saida"])

    def test_sem_docker_explica(self):
        shutil.which = lambda _n: None
        self.assertIn("PATH", compose.aplicar(self.raiz)["saida"])

    def test_timeout_avisa_que_pode_ter_subido_de_qualquer_forma(self):
        """Abandonar o `up` não desfaz o que ele já fez — e dizer o contrário
        mandaria quem opera procurar problema onde não há."""
        def estoura(*a, **k):
            raise subprocess.TimeoutExpired("dlb.sh", 600)

        subprocess.run = estoura
        resultado = compose.aplicar(self.raiz, timeout=600)
        self.assertFalse(resultado["ok"])
        self.assertIn("pode ter subido", resultado["saida"])

    def test_erro_de_execucao_nao_levanta(self):
        def estoura(*a, **k):
            raise OSError("permission denied")

        subprocess.run = estoura
        self.assertIn("permission denied", compose.aplicar(self.raiz)["saida"])


if __name__ == "__main__":
    unittest.main()
