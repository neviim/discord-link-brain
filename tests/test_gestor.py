"""Rode com: python3 -m unittest discover -s tests -t .

As oito regras do gestor. Uma classe por regra, e cada uma responde duas
perguntas: **dispara quando deve** e **fica calada quando não deve**.

A segunda metade é a que importa mais. Alerta que aparece sem motivo ensina o
leitor a ignorar o painel, e a partir daí o painel não serve para nada — inclusive
quando estiver certo.

As regras são função pura sobre o dicionário que a tela recebe, então o teste
fabrica o estado direto, sem disco, sem servidor e sem relógio: `agora` é
parâmetro.
"""

import unittest

from dlb.gestor import (
    FALHAS_SEGUIDAS,
    FILA_LIMIAR,
    PENDURADO_S,
    avaliar,
    tudo_quieto,
)

AGORA = 1_800_000_000.0        # instante fixo; nenhuma regra lê o relógio


def iso(segundos_atras: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(AGORA - segundos_atras, timezone.utc).isoformat(
        timespec="seconds"
    )


def visao(**mudancas) -> dict:
    """Estado saudável. Cada teste estraga só o que a sua regra olha."""
    base = {
        "heartbeats": {"sync": 600.0, "responder": 30.0},
        "tolerancia_s": {"sync": 28800, "responder": 300},
        "acervo": {"total": 77, "por_triagem": {"inbox": 5, "aplicado": 72}, "sem_pagina": []},
        "cursores": [
            {
                "canal": "42",
                "nome": "estudos",
                "backfill_cursor": "999",
                "backfill_concluido": True,
            }
        ],
        "ultimas_execucoes": [
            {"comando": "sync", "status": "ok", "inicio": iso(600), "id": "r1"}
        ],
        "ultimos": {
            "sync": {"comando": "sync", "status": "ok", "inicio": iso(600), "id": "r1"},
            "backfill": {"comando": "backfill", "status": "ok", "inicio": iso(3600), "id": "r0"},
            "sintese": None,
            "ressurgir": None,
        },
        "lock": None,
    }
    base.update(mudancas)
    return base


def chaves(v: dict) -> set[str]:
    return {a.chave for a in avaliar(v, agora=AGORA)}


class TestEstadoSaudavelFicaQuieto(unittest.TestCase):
    def test_nada_pede_acao_quando_esta_tudo_bem(self):
        alertas = avaliar(visao(), agora=AGORA)
        self.assertEqual(alertas, [])
        self.assertTrue(tudo_quieto(alertas))


class TestAgendadorParado(unittest.TestCase):
    def test_dispara_quando_o_heartbeat_passa_da_tolerancia(self):
        v = visao(heartbeats={"sync": 40000.0, "responder": 30.0})
        self.assertIn("agendador_parado", chaves(v))

    def test_dispara_quando_nunca_houve_heartbeat(self):
        """Container recém-subido que nunca fechou um ciclo é exatamente o caso em
        que se quer saber."""
        v = visao(heartbeats={"sync": None, "responder": None})
        self.assertIn("agendador_parado", chaves(v))

    def test_fica_calada_dentro_da_tolerancia(self):
        v = visao(heartbeats={"sync": 28799.0, "responder": 30.0})
        self.assertNotIn("agendador_parado", chaves(v))

    def test_a_evidencia_traz_o_numero_e_a_tolerancia(self):
        v = visao(heartbeats={"sync": 40000.0, "responder": 30.0})
        alerta = next(a for a in avaliar(v, agora=AGORA) if a.chave == "agendador_parado")
        self.assertIn("11.1 h", alerta.evidencia)
        self.assertIn("8.0 h", alerta.evidencia)
        self.assertIn("dlb.sh prod", alerta.acao)


class TestResponderParado(unittest.TestCase):
    def test_dispara_quando_a_volta_atrasa(self):
        v = visao(heartbeats={"sync": 600.0, "responder": 900.0})
        self.assertIn("responder_parado", chaves(v))

    def test_nao_alerta_sobre_servico_que_nunca_rodou(self):
        """Heartbeat ausente = responder desligado. Alertar sobre serviço que o
        ninguém ligou é a forma mais rápida de ensinar a ignorar o painel."""
        v = visao(heartbeats={"sync": 600.0, "responder": None})
        self.assertNotIn("responder_parado", chaves(v))


class TestUltimoCicloFalhou(unittest.TestCase):
    def test_dispara_para_sync(self):
        v = visao(ultimos={"sync": {"comando": "sync", "status": "falha", "inicio": iso(120),
                                    "id": "r9", "erro": None}, "backfill": None})
        self.assertIn("ultimo_ciclo_falhou", chaves(v))

    def test_dispara_para_backfill_que_nao_tem_heartbeat_nenhum(self):
        """Sem esta regra o backfill poderia falhar todo dia em silêncio: ele não
        escreve heartbeat, então o cartão do agendador continuaria verde."""
        v = visao(
            ultimos={
                "sync": {"comando": "sync", "status": "ok", "inicio": iso(60), "id": "r1"},
                "backfill": {"comando": "backfill", "status": "erro", "inicio": iso(60),
                             "id": "r2", "erro": "NameError: guilds"},
            }
        )
        alertas = [a for a in avaliar(v, agora=AGORA) if a.chave == "ultimo_ciclo_falhou"]
        self.assertEqual(len(alertas), 1)
        self.assertIn("NameError", alertas[0].evidencia)

    def test_fica_calada_quando_o_ultimo_deu_certo(self):
        self.assertNotIn("ultimo_ciclo_falhou", chaves(visao()))

    def test_recusado_nao_e_falha(self):
        """Recusa pelo lock é o sistema se protegendo. Contar como falha faria a
        tela gritar por causa de dois cliques no mesmo botão."""
        v = visao(ultimos={"sync": {"comando": "sync", "status": "recusado",
                                    "inicio": iso(60), "id": "r3"}, "backfill": None})
        self.assertNotIn("ultimo_ciclo_falhou", chaves(v))


class TestFalhasSeguidas(unittest.TestCase):
    def runs(self, *status):
        return [
            {"comando": "sync", "status": s, "inicio": iso(60 * (i + 1)), "id": f"r{i}"}
            for i, s in enumerate(status)
        ]

    def test_dispara_com_tres_seguidas(self):
        v = visao(ultimas_execucoes=self.runs("falha", "erro", "falha"))
        self.assertIn("falhas_seguidas", chaves(v))

    def test_duas_seguidas_ainda_e_dia_ruim_da_api(self):
        v = visao(ultimas_execucoes=self.runs("falha", "falha", "ok"))
        self.assertNotIn("falhas_seguidas", chaves(v))

    def test_um_sucesso_no_meio_quebra_a_sequencia(self):
        """`falha, ok, falha, falha` não é sequência de três: o sistema se
        recuperou no meio, e a causa provavelmente é intermitente."""
        v = visao(ultimas_execucoes=self.runs("falha", "ok", "falha", "falha"))
        self.assertNotIn("falhas_seguidas", chaves(v))

    def test_comandos_diferentes_nao_somam(self):
        v = visao(
            ultimas_execucoes=[
                {"comando": "sync", "status": "falha", "inicio": iso(60), "id": "a"},
                {"comando": "backfill", "status": "falha", "inicio": iso(120), "id": "b"},
                {"comando": "sintese", "status": "falha", "inicio": iso(180), "id": "c"},
            ]
        )
        self.assertNotIn("falhas_seguidas", chaves(v))

    def test_o_limiar_e_o_documentado(self):
        v = visao(ultimas_execucoes=self.runs(*(["falha"] * FALHAS_SEGUIDAS)))
        self.assertIn("falhas_seguidas", chaves(v))


class TestRunPendurado(unittest.TestCase):
    def test_dispara_com_run_rodando_ha_horas_e_lock_livre(self):
        v = visao(
            lock=None,
            ultimas_execucoes=[
                {"comando": "sync", "status": "rodando", "inicio": iso(PENDURADO_S + 60), "id": "r7"}
            ],
        )
        self.assertIn("run_pendurado", chaves(v))

    def test_fica_calada_se_alguem_esta_com_o_lock(self):
        """Com o lock tomado, o run está rodando de verdade — é o caso normal de
        um sync longo, e alertar aqui seria alarme falso durante trabalho."""
        v = visao(
            lock={"comando": "sync", "pid": 123},
            ultimas_execucoes=[
                {"comando": "sync", "status": "rodando", "inicio": iso(PENDURADO_S + 60), "id": "r7"}
            ],
        )
        self.assertNotIn("run_pendurado", chaves(v))

    def test_painel_e_responder_ficam_de_pe_por_desenho(self):
        """Os dois são processos longos. Contá-los como pendurados faria o painel
        alertar sobre si mesmo, para sempre."""
        for comando in ("painel", "responder"):
            with self.subTest(comando):
                v = visao(
                    ultimas_execucoes=[
                        {"comando": comando, "status": "rodando",
                         "inicio": iso(PENDURADO_S * 10), "id": "r8"}
                    ]
                )
                self.assertNotIn("run_pendurado", chaves(v))

    def test_run_rodando_ha_pouco_e_so_um_run_rodando(self):
        v = visao(
            ultimas_execucoes=[
                {"comando": "sync", "status": "rodando", "inicio": iso(30), "id": "r7"}
            ]
        )
        self.assertNotIn("run_pendurado", chaves(v))


class TestFilaDeTriagem(unittest.TestCase):
    def test_dispara_acima_do_limiar(self):
        v = visao(acervo={"total": 77, "por_triagem": {"inbox": 77}, "sem_pagina": []})
        alerta = next(a for a in avaliar(v, agora=AGORA) if a.chave == "fila_de_triagem")
        self.assertIn("77 de 77", alerta.evidencia)
        self.assertIn("100%", alerta.evidencia)
        self.assertIn("/acervo?triagem=inbox", alerta.acao)

    def test_fica_calada_abaixo_do_limiar(self):
        v = visao(acervo={"total": 77, "por_triagem": {"inbox": FILA_LIMIAR - 1}, "sem_pagina": []})
        self.assertNotIn("fila_de_triagem", chaves(v))

    def test_acervo_vazio_nao_divide_por_zero(self):
        v = visao(acervo={"total": 0, "por_triagem": {}, "sem_pagina": []})
        self.assertNotIn("fila_de_triagem", chaves(v))

    def test_nivel_e_atencao_e_nao_alerta(self):
        """Fila grande não é sistema quebrado — é hábito. Misturar os dois níveis
        faria a tela mostrar vermelho por algo que só uma pessoa resolve, no tempo
        dela."""
        v = visao(acervo={"total": 77, "por_triagem": {"inbox": 77}, "sem_pagina": []})
        alerta = next(a for a in avaliar(v, agora=AGORA) if a.chave == "fila_de_triagem")
        self.assertEqual(alerta.nivel, "atencao")


class TestConceitosSemPagina(unittest.TestCase):
    def test_dispara_com_tres_ou_mais(self):
        v = visao(acervo={"total": 9, "por_triagem": {"inbox": 1},
                          "sem_pagina": ["Nostr", "Relay", "NIP-98"]})
        self.assertIn("conceitos_sem_pagina", chaves(v))

    def test_dois_ainda_e_vocabulario_passageiro(self):
        v = visao(acervo={"total": 9, "por_triagem": {"inbox": 1},
                          "sem_pagina": ["Nostr", "Relay"]})
        self.assertNotIn("conceitos_sem_pagina", chaves(v))

    def test_e_info_e_nao_pede_acao(self):
        v = visao(acervo={"total": 9, "por_triagem": {"inbox": 1},
                          "sem_pagina": ["a", "b", "c"]})
        alertas = avaliar(v, agora=AGORA)
        self.assertEqual([a.nivel for a in alertas], ["info"])
        self.assertTrue(tudo_quieto(alertas))


class TestHistoricoParado(unittest.TestCase):
    def cursor_incompleto(self):
        return [{"canal": "42", "nome": "estudos", "backfill_cursor": "500",
                 "backfill_concluido": False}]

    def test_dispara_quando_incompleto_e_sem_backfill_recente(self):
        v = visao(
            cursores=self.cursor_incompleto(),
            ultimos={"sync": None, "backfill": {"comando": "backfill", "status": "ok",
                                                "inicio": iso(72 * 3600), "id": "r0"}},
        )
        alerta = next(a for a in avaliar(v, agora=AGORA) if a.chave == "historico_parado")
        self.assertIn("estudos", alerta.evidencia)
        self.assertIn("DLB_BACKFILL", alerta.acao)

    def test_dispara_quando_nunca_rodou_backfill(self):
        v = visao(cursores=self.cursor_incompleto(), ultimos={"sync": None, "backfill": None})
        self.assertIn("historico_parado", chaves(v))

    def test_fica_calada_se_o_backfill_rodou_ontem(self):
        """Histórico andando em fatias é o comportamento desenhado — uma fatia por
        ciclo. Alertar aqui seria alertar sobre o sistema funcionando."""
        v = visao(
            cursores=self.cursor_incompleto(),
            ultimos={"sync": None, "backfill": {"comando": "backfill", "status": "ok",
                                                "inicio": iso(3600), "id": "r0"}},
        )
        self.assertNotIn("historico_parado", chaves(v))

    def test_fica_calada_quando_o_historico_esta_concluido(self):
        self.assertNotIn("historico_parado", chaves(visao()))

    def test_canal_que_nunca_comecou_o_backfill_nao_conta(self):
        """Sem cursor, o backfill nunca foi ligado para aquele canal. Não é algo
        parado; é algo que não começou."""
        v = visao(
            cursores=[{"canal": "42", "nome": "estudos", "backfill_cursor": None,
                       "backfill_concluido": False}],
            ultimos={"sync": None, "backfill": None},
        )
        self.assertNotIn("historico_parado", chaves(v))


class TestOrdemERobustez(unittest.TestCase):
    def test_alerta_vem_antes_de_atencao_que_vem_antes_de_info(self):
        v = visao(
            heartbeats={"sync": 99999.0, "responder": None},
            acervo={"total": 77, "por_triagem": {"inbox": 77},
                    "sem_pagina": ["a", "b", "c"]},
        )
        niveis = [a.nivel for a in avaliar(v, agora=AGORA)]
        self.assertEqual(niveis, sorted(niveis, key=["alerta", "atencao", "info"].index))
        self.assertFalse(tudo_quieto(avaliar(v, agora=AGORA)))

    def test_visao_vazia_nao_derruba_o_gestor(self):
        """O painel é o que se abre quando algo já está estranho. Estado
        incompleto — `.state` recém-criado, JSON truncado — não pode virar
        traceback na home."""
        alertas = avaliar({}, agora=AGORA)
        self.assertEqual([a.chave for a in alertas], ["agendador_parado"])

    def test_data_ilegivel_nao_derruba(self):
        v = visao(
            ultimas_execucoes=[{"comando": "sync", "status": "rodando", "inicio": "não é data"}],
            ultimos={"sync": None, "backfill": None},
        )
        self.assertNotIn("regra_quebrada", chaves(v))

    def test_uma_regra_que_estoura_nao_cala_as_outras(self):
        """O estado que faz uma regra falhar é justamente o estado sobre o qual as
        outras têm algo a dizer."""
        v = visao(heartbeats={"sync": 99999.0, "responder": None}, cursores="isto não é lista")
        resultado = chaves(v)
        self.assertIn("agendador_parado", resultado)
        self.assertIn("regra_quebrada", resultado)

    def test_sao_oito_regras(self):
        from dlb.gestor import REGRAS

        self.assertEqual(len(REGRAS), 8)


if __name__ == "__main__":
    unittest.main()
