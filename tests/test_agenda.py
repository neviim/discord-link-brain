"""Rode com: python3 -m unittest discover -s tests -t .

A tabela de paridade entre `dlb/agenda.py` e `docker/scheduler.sh`.

`dlb/agenda.py` é uma **segunda implementação** da grade de agendamento: a
primeira é shell, dentro do container. Duas implementações da mesma regra
divergem, e aqui a divergência é silenciosa — nada quebra quando a tela mostra
"próximo sync às 18:00" e o sync sai às 12:00. Quem confere é uma pessoa, uma vez,
e depois para de confiar no painel.

Por isso este arquivo não tem uma tabela de valores que eu escrevi olhando o
script: ele **executa o Python que está embutido no `scheduler.sh`**, com o
relógio fixado, e compara com a nossa função. Se alguém editar a aritmética do
shell, o teste falha no dia da edição — não meses depois, na tela.

O que a paridade cobre: os três modos, a precedência entre eles, `EVERY_HOURS` que
não divide 24 (o caso em que a grade desliza para o dia seguinte), e `SYNC_AT` na
fronteira exata do minuto.
"""

import contextlib
import io
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dlb import agenda

SCHEDULER = Path(__file__).resolve().parent.parent / "docker" / "scheduler.sh"
TZ = ZoneInfo("America/Sao_Paulo")


def snippet(funcao: str) -> str:
    """O corpo Python embutido numa função do `scheduler.sh`.

    O script chama `python - "$1" <<'PY' … PY`. Pegar o bloco pelo nome da função
    que o contém é o que amarra este teste ao arquivo de verdade em vez de a uma
    cópia.
    """
    texto = SCHEDULER.read_text(encoding="utf-8")
    corpo = re.search(rf"^{funcao}\(\) \{{(.*?)^\}}", texto, re.S | re.M)
    assert corpo, f"função {funcao} não encontrada em {SCHEDULER}"
    bloco = re.search(r"<<'PY'\n(.*?)\nPY", corpo.group(1), re.S)
    assert bloco, f"heredoc PY não encontrado em {funcao}"
    return bloco.group(1)


def shell_segundos(funcao: str, argumento: str, agora: datetime) -> int:
    """Roda o snippet do shell com `now` fixado e devolve o que ele imprime.

    A substituição é textual — `datetime.datetime.now()` vira um literal — para não
    depender de monkeypatch nem de variável de ambiente, e para o teste continuar
    valendo se o snippet trocar `now` por `agora` (os dois nomes aparecem lá).
    """
    codigo = snippet(funcao)
    literal = f"datetime.datetime({agora.year}, {agora.month}, {agora.day}, {agora.hour}, {agora.minute}, {agora.second})"
    codigo = codigo.replace("datetime.datetime.now()", literal)
    codigo = codigo.replace("sys.argv[1]", repr(argumento))
    saida = io.StringIO()
    with contextlib.redirect_stdout(saida):
        exec(compile(codigo, str(SCHEDULER), "exec"), {})  # noqa: S102
    return int(saida.getvalue().strip())


def shell_alvo(funcao: str, argumento: str, agora: datetime) -> datetime:
    return agora + timedelta(seconds=shell_segundos(funcao, argumento, agora))


class TestOSnippetFoiEncontrado(unittest.TestCase):
    """Se a extração parar de achar o bloco, o resto do arquivo passaria vazio."""

    def test_as_duas_funcoes_de_relogio_existem_no_script(self):
        for funcao in ("seconds_until", "seconds_until_slot"):
            with self.subTest(funcao):
                self.assertIn("datetime", snippet(funcao))

    def test_a_precedencia_esta_escrita_no_script(self):
        """A ordem que `agenda.modo_de` copia. Se o shell inverter, o comentário
        aqui deixa de valer e o teste de precedência abaixo é o que pega."""
        texto = SCHEDULER.read_text(encoding="utf-8")
        self.assertIn('if [ -n "$INTERVAL" ]', texto)
        self.assertIn('elif [ -n "$EVERY_HOURS" ]', texto)


class TestOCompostoNaoSequestraOModo(unittest.TestCase):
    """O compose não pode tornar um modo do scheduler inalcançável.

    `${VAR:-6}` troca **vazio** pelo padrão, não só ausente. Com isso, um
    `DLB_SYNC_EVERY_HOURS=` vazio no `.env` chegava ao container como `6`, e a
    precedência do scheduler descartava `DLB_SYNC_AT` sem avisar: o modo diário
    era inalcançável pelo perfil `prod`, apesar de documentado.

    Conferido com `docker compose config` antes e depois do conserto.
    """

    def compose_yml(self):
        return (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(
            encoding="utf-8"
        )

    def test_every_hours_usa_o_padrao_so_quando_a_chave_esta_ausente(self):
        self.assertIn("DLB_SYNC_EVERY_HOURS: ${DLB_SYNC_EVERY_HOURS-6}", self.compose_yml())
        self.assertNotIn("${DLB_SYNC_EVERY_HOURS:-6}", self.compose_yml())

    def test_os_outros_dois_modos_nao_ganham_padrao_nenhum(self):
        """Se `DLB_SYNC_AT` ou `DLB_SYNC_INTERVAL` ganhassem valor por padrão, a
        precedência mudaria para todo mundo de uma vez."""
        yml = self.compose_yml()
        self.assertIn("DLB_SYNC_AT: ${DLB_SYNC_AT:-}", yml)
        self.assertIn("DLB_SYNC_INTERVAL: ${DLB_SYNC_INTERVAL:-}", yml)


class TestParidadeGrade(unittest.TestCase):
    """`modo × config → próximo disparo`, conferido contra o shell."""

    def cenarios(self):
        # (passo em horas, instante local)
        return [
            (6, datetime(2026, 8, 20, 0, 0)),
            (6, datetime(2026, 8, 20, 0, 1)),
            (6, datetime(2026, 8, 20, 5, 59)),
            (6, datetime(2026, 8, 20, 6, 0)),
            (6, datetime(2026, 8, 20, 23, 59)),
            (1, datetime(2026, 8, 20, 13, 30)),
            (12, datetime(2026, 8, 20, 11, 59)),
            (24, datetime(2026, 8, 20, 15, 0)),
            # Não divide 24: é o caso em que a grade desliza para o dia seguinte.
            (5, datetime(2026, 8, 20, 20, 0)),
            (5, datetime(2026, 8, 21, 0, 30)),
            (7, datetime(2026, 8, 20, 21, 0)),
        ]

    def test_a_grade_bate_com_o_shell(self):
        for passo, agora in self.cenarios():
            with self.subTest(passo=passo, agora=agora.isoformat()):
                esperado = shell_alvo("seconds_until_slot", str(passo), agora)
                nosso = agenda.proximo(
                    {"DLB_SYNC_EVERY_HOURS": str(passo)}, agora.replace(tzinfo=TZ)
                )
                self.assertEqual(nosso.replace(tzinfo=None), esperado)

    def test_o_padrao_sem_nada_configurado_e_a_grade_de_6h(self):
        """O shell tem esse padrão embutido: sem os três, `EVERY_HOURS=6`."""
        agora = datetime(2026, 8, 20, 7, 15)
        esperado = shell_alvo("seconds_until_slot", "6", agora)
        self.assertEqual(
            agenda.proximo({}, agora.replace(tzinfo=TZ)).replace(tzinfo=None), esperado
        )
        self.assertEqual(agenda.modo_de({}).horas, 6)

    def test_passo_de_5h_desliza_e_estabiliza(self):
        """Regressão da minha primeira versão, que clampava na meia-noite.

        Parecia mais correto e divergia: o shell deixa o alvo passar da meia-noite
        (25h após ela) e recalcula na volta seguinte, o que estabiliza a grade em
        01,05,10,15,20 — não em 00,05,10,15,20.
        """
        t = datetime(2026, 8, 20, 0, 1, tzinfo=TZ)
        vistos = []
        for _ in range(7):
            t = agenda.proximo({"DLB_SYNC_EVERY_HOURS": "5"}, t)
            vistos.append(t.strftime("%d %H:%M"))
        self.assertEqual(
            vistos,
            ["20 05:00", "20 10:00", "20 15:00", "20 20:00", "21 01:00", "21 05:00", "21 10:00"],
        )


class TestParidadeDiario(unittest.TestCase):
    def test_o_horario_fixo_bate_com_o_shell(self):
        for hhmm, agora in [
            ("03:30", datetime(2026, 8, 20, 3, 29)),
            ("03:30", datetime(2026, 8, 20, 3, 30)),
            ("03:30", datetime(2026, 8, 20, 3, 31)),
            ("00:00", datetime(2026, 8, 20, 23, 59)),
            ("23:59", datetime(2026, 8, 20, 0, 0)),
        ]:
            with self.subTest(hhmm=hhmm, agora=agora.isoformat()):
                esperado = shell_alvo("seconds_until", hhmm, agora)
                nosso = agenda.proximo(
                    {"DLB_SYNC_AT": hhmm}, agora.replace(tzinfo=TZ)
                )
                self.assertEqual(nosso.replace(tzinfo=None), esperado)

    def test_o_minuto_exato_pula_para_amanha(self):
        """O shell usa `<=`: no instante exato, o disparo é o de amanhã. Um `<`
        aqui faria a tela prometer um sync que já passou."""
        agora = datetime(2026, 8, 20, 3, 30, tzinfo=TZ)
        self.assertEqual(agenda.proximo({"DLB_SYNC_AT": "03:30"}, agora).day, 21)


class TestPrecedencia(unittest.TestCase):
    def test_interval_vence_os_outros_dois(self):
        modo = agenda.modo_de(
            {"DLB_SYNC_INTERVAL": "900", "DLB_SYNC_EVERY_HOURS": "6", "DLB_SYNC_AT": "03:30"}
        )
        self.assertEqual(modo.nome, "intervalo")
        self.assertEqual(len(modo.avisos), 2)
        self.assertTrue(all("ignorado" in a for a in modo.avisos))

    def test_every_hours_vence_o_at_e_avisa(self):
        """A armadilha real: quem põe `DLB_SYNC_AT=03:30` e deixa `EVERY_HOURS`
        preenchido jura que configurou 3h30 e vê o sync de 6 em 6 horas."""
        modo = agenda.modo_de({"DLB_SYNC_EVERY_HOURS": "6", "DLB_SYNC_AT": "03:30"})
        self.assertEqual(modo.nome, "grade")
        self.assertEqual(len(modo.avisos), 1)
        self.assertIn("DLB_SYNC_AT=03:30", modo.avisos[0])
        self.assertIn("vazio ao container", modo.avisos[0])

    def test_at_sozinho_vale(self):
        modo = agenda.modo_de({"DLB_SYNC_EVERY_HOURS": "", "DLB_SYNC_AT": "03:30"})
        self.assertEqual(modo.nome, "diario")
        self.assertEqual(modo.avisos, [])

    def test_vazio_conta_como_ausente_igual_ao_shell(self):
        """`[ -z "$VAR" ]` não distingue vazio de ausente, e o compose passa
        string vazia. Distinguir aqui inventaria um quarto modo."""
        self.assertEqual(agenda.modo_de({"DLB_SYNC_INTERVAL": "  "}).nome, "grade")

    def test_intervalo_nao_promete_horario(self):
        """O shell dorme N segundos **depois** do ciclo terminar, então o próximo
        disparo depende de quando o último acabou. Inventar um horário aqui seria
        a tela mentir com precisão."""
        self.assertIsNone(
            agenda.proximo({"DLB_SYNC_INTERVAL": "900"}, datetime(2026, 8, 20, 1, 0, tzinfo=TZ))
        )

    def test_grade_que_nao_divide_24_avisa_do_deslize(self):
        modo = agenda.modo_de({"DLB_SYNC_EVERY_HOURS": "5"})
        self.assertEqual(len(modo.avisos), 1)
        self.assertIn("desliza", modo.avisos[0])

    def test_grade_que_divide_24_nao_avisa_nada(self):
        for n in ("1", "2", "3", "4", "6", "8", "12", "24"):
            with self.subTest(n=n):
                self.assertEqual(agenda.modo_de({"DLB_SYNC_EVERY_HOURS": n}).avisos, [])


class TestOsQuatroSlots(unittest.TestCase):
    """Uma tela que mostrasse só o sync esconderia três quartos do ciclo."""

    AGORA = datetime(2026, 8, 20, 7, 0, tzinfo=TZ)   # quinta-feira

    def slots(self, **env):
        base = {"DLB_SYNC_EVERY_HOURS": "6"}
        base.update(env)
        return {s.comando: s for s in agenda.slots(base, self.AGORA)}

    def test_os_quatro_aparecem_sempre_mesmo_desligados(self):
        s = self.slots()
        self.assertEqual(list(s), list(agenda.CICLO))
        self.assertTrue(s["sync"].ativo)
        for desligado in ("backfill", "sintese", "ressurgir"):
            self.assertFalse(s[desligado].ativo)
            self.assertIn("=0", s[desligado].porque)

    def test_sync_sempre_ativo_porque_e_o_laco(self):
        self.assertTrue(self.slots(DLB_BACKFILL="0")["sync"].ativo)

    def test_backfill_liga_com_1_e_mostra_os_dias(self):
        s = self.slots(DLB_BACKFILL="1", DLB_BACKFILL_DAYS="3")
        self.assertTrue(s["backfill"].ativo)
        self.assertIn("--days 3", s["backfill"].observacao)
        self.assertEqual(s["backfill"].proximo, s["sync"].proximo)

    def test_qualquer_coisa_que_nao_seja_1_fica_desligado(self):
        """O shell compara com `= "1"` literalmente: `true` não liga nada, e um
        painel que aceitasse `true` mostraria ativo o que não roda."""
        for valor in ("true", "sim", "yes", "0", "01", ""):
            with self.subTest(valor=valor):
                self.assertFalse(self.slots(DLB_BACKFILL=valor)["backfill"].ativo)

    def test_sintese_cai_no_primeiro_ciclo_do_dia_da_semana_configurado(self):
        """O shell testa `date +%u` **no momento do ciclo**, então o próximo
        disparo não é o próximo ciclo: é o próximo ciclo que cair na segunda."""
        s = self.slots(DLB_SINTESE="1", DLB_SINTESE_DIA="1")
        self.assertEqual(s["sintese"].proximo.isoweekday(), 1)
        self.assertEqual(s["sintese"].proximo.strftime("%d %H:%M"), "24 00:00")

    def test_sintese_no_proprio_dia_pega_o_ciclo_de_hoje(self):
        quinta = datetime(2026, 8, 20, 7, 0, tzinfo=TZ)
        s = {x.comando: x for x in agenda.slots(
            {"DLB_SYNC_EVERY_HOURS": "6", "DLB_SINTESE": "1", "DLB_SINTESE_DIA": "4"}, quinta
        )}
        self.assertEqual(s["sintese"].proximo.strftime("%d %H:%M"), "20 12:00")

    def test_rollup_mensal_aparece_na_observacao(self):
        s = self.slots(DLB_SINTESE="1", DLB_SINTESE_MES="1")
        self.assertIn("rollup mensal", s["sintese"].observacao)

    def test_ressurgir_diz_que_se_protege_sozinho(self):
        s = self.slots(DLB_RESSURGIR="1")
        self.assertTrue(s["ressurgir"].ativo)
        self.assertIn("uma vez por dia", s["ressurgir"].observacao)


class TestVisao(unittest.TestCase):
    def test_usa_o_fuso_do_agendador_e_nao_utc(self):
        """O `scheduler.sh` usa `date` local do container, e o compose define `TZ`.
        Calcular em UTC mostraria a hora certa deslocada em três horas — erro que
        parece plausível na tela e é o pior tipo."""
        dados = agenda.visao({"TZ": "America/Sao_Paulo", "DLB_SYNC_EVERY_HOURS": "6"})
        self.assertEqual(dados["fuso"], "America/Sao_Paulo")
        self.assertIn("modo", dados)
        self.assertEqual(len(dados["slots"]), 4)

    def test_fuso_invalido_nao_derruba(self):
        """Cai no padrão neutro (`UTC`) em vez de estourar `ZoneInfoNotFoundError`.

        O padrão é `UTC` de propósito: fuso de um lugar específico embutido no
        código faz o sistema mentir a hora na máquina de todo mundo que não mora
        lá. Quem tem fuso próprio declara em `TIMEZONE`, e o teste acima cobre
        esse caminho."""
        self.assertEqual(str(agenda.fuso({"TZ": "Marte/Olympus"})), "UTC")

    def test_run_on_start_e_1_por_padrao(self):
        self.assertTrue(agenda.visao({})["roda_ao_subir"])
        self.assertFalse(agenda.visao({"DLB_RUN_ON_START": "0"})["roda_ao_subir"])


if __name__ == "__main__":
    unittest.main()
