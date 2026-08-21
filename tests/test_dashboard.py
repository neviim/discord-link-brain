"""Rode com: python3 -m unittest discover -s tests -t .

O dashboard: agregados, SVG e o custo de cada atualização.

Dois grupos de garantia aqui, e o segundo é o que o Dotcom pediu explicitamente:

- **o número está certo** — os agregados saem de uma passada sobre as entradas, e um
  erro de contagem num dashboard é pior que nenhum dashboard, porque ele é olhado
  todo dia e acreditado;
- **o custo é o prometido** — o cache por versão do acervo é o que faz o tempo real
  não pesar. Se ele parar de acertar a versão, tudo continua **funcionando** e fica
  vinte vezes mais caro, em silêncio. Daí ter teste sobre o cache, não só sobre o
  resultado.

E as especificações de marca do padrão de dataviz, que só se conferem no markup:
espessura com teto, vão entre marcas que se tocam, nada de borda desenhada em volta,
nada de número em cada ponto.
"""

import re
import unittest
from pathlib import Path

from dlb import painel_dados, svg
from dlb.painel_dados import Agregado, calcular

from tests.test_vault import VaultCase, note


class DadosCase(VaultCase):
    def escrever(self, url, titulo, **kw):
        return self.vault.write_link_note("estudos", note(url, titulo, kw.pop("conceitos", []), **kw))

    def acervo(self):
        from dlb.corpus import ler_links

        return ler_links(self.root)


class TestAgregados(DadosCase):
    def test_conta_por_categoria_dominio_e_triagem_numa_passada(self):
        self.escrever("https://youtube.com/a", "Vídeo A")
        self.escrever("https://youtube.com/b", "Vídeo B")
        self.escrever("https://github.com/c", "Repo C")
        d = calcular(self.acervo())
        self.assertEqual(d["total"], 3)
        self.assertEqual(
            [(x["rotulo"], x["quantos"]) for x in d["por_dominio"]],
            [("youtube.com", 2), ("github.com", 1)],
        )
        self.assertEqual(d["por_triagem"]["inbox"], 3)
        self.assertEqual(d["triados"], 0)

    def test_a_serie_tem_30_colunas_mesmo_sem_captura(self):
        """Buraco na série temporal mentiria sobre o ritmo, sugerindo que o dia não
        existiu. Dia sem captura é uma coluna de valor zero."""
        self.escrever("https://e.com/a", "A")
        d = calcular(self.acervo())
        self.assertEqual(len(d["serie"]), painel_dados.JANELA_DIAS)
        self.assertEqual(sum(1 for x in d["serie"] if x["quantos"] == 0), 29)

    def test_a_cauda_das_categorias_vira_outros_em_vez_de_desaparecer(self):
        """Cortar a cauda em silêncio faria o gráfico somar menos que o total, e
        ninguém confere soma de gráfico. Somar é honesto, e o rótulo diz quantas."""
        from dlb.models import LinkNote

        for i in range(12):
            resultado = self.escrever(f"https://d{i}.com/x", f"Nota {i}")
            caminho = resultado.path
            caminho.write_text(
                caminho.read_text(encoding="utf-8").replace(
                    "categoria: artigo", f"categoria: cat{i}"
                ),
                encoding="utf-8",
            )
        d = calcular(self.acervo())
        self.assertLessEqual(len(d["por_categoria"]), painel_dados.TOPO_CATEGORIAS + 1)
        self.assertEqual(sum(x["quantos"] for x in d["por_categoria"]), 12)
        self.assertTrue(d["por_categoria"][-1]["rotulo"].startswith("outros ("))

    def test_acervo_vazio_nao_divide_por_zero_nem_inventa_dia(self):
        d = calcular([])
        self.assertEqual(d["total"], 0)
        self.assertIsNone(d["primeiro_dia"])
        self.assertEqual(d["media_dia"], 0)
        self.assertEqual(d["por_categoria"], [])

    def test_conceitos_sem_pagina_sao_contados_por_conceito(self):
        """Conta conceito distinto, e conta em quantas notas cada um aparece.

        `Nostr` de propósito fora da conta: o vault de teste **tem**
        `20_WIKI/Nostr.md`, então ele não é candidato a página. É exatamente a
        distinção que o campo faz — conceito citado sem página, não conceito citado.
        """
        self.escrever("https://e.com/a", "A", conceitos=["Nostr", "Relay", "NIP-98"])
        self.escrever("https://e.com/b", "B", conceitos=["Nostr", "Relay"])
        d = calcular(self.acervo())
        self.assertEqual(d["conceitos_sem_pagina_total"], 2)      # Relay e NIP-98
        self.assertEqual(d["conceitos_sem_pagina"][0], {"rotulo": "Relay", "quantos": 2})
        self.assertNotIn("Nostr", [x["rotulo"] for x in d["conceitos_sem_pagina"]])


class TestCachePorVersao(DadosCase):
    """O que faz o tempo real ser barato. Sem isso nada quebra — só fica caro."""

    def test_reusa_enquanto_o_acervo_nao_muda(self):
        self.escrever("https://e.com/a", "A")
        entradas = self.acervo()
        ag = Agregado()
        ag.de(entradas)
        ag.de(entradas)
        ag.de(entradas)
        self.assertEqual(ag.calculos, 1)
        self.assertEqual(ag.reusos, 2)

    def test_recalcula_quando_nota_e_editada(self):
        resultado = self.escrever("https://e.com/a", "A")
        ag = Agregado()
        ag.de(self.acervo())
        caminho = resultado.path
        caminho.write_text(
            caminho.read_text(encoding="utf-8").replace("triagem: inbox", "triagem: lido"),
            encoding="utf-8",
        )
        d = ag.de(self.acervo())
        self.assertEqual(ag.calculos, 2)
        self.assertEqual(d["por_triagem"]["lido"], 1)

    def test_recalcula_quando_nota_entra(self):
        self.escrever("https://e.com/a", "A")
        ag = Agregado()
        ag.de(self.acervo())
        self.escrever("https://e.com/b", "B")
        self.assertEqual(ag.de(self.acervo())["total"], 2)
        self.assertEqual(ag.calculos, 2)

    def test_a_versao_nao_abre_arquivo(self):
        """Se a versão precisasse ler conteúdo, ela custaria o que o cálculo custa e
        não haveria economia nenhuma."""
        self.escrever("https://e.com/a", "A")
        entradas = self.acervo()
        antes = painel_dados.versao(entradas)
        self.assertEqual(antes[0], 1)
        self.assertGreater(antes[1], 0)
        self.assertEqual(antes, painel_dados.versao(entradas))


class TestPendencias(DadosCase):
    def test_separa_pendencia_humana_de_pendencia_de_maquina(self):
        """Misturar as duas produz uma lista que ninguém sabe atacar."""
        self.escrever("https://e.com/a", "A", conceitos=["Nostr", "Relay", "NIP"])
        agregado = calcular(self.acervo())
        itens = painel_dados.pendencias(
            agregado,
            [{"canal": "42", "backfill_cursor": "9", "backfill_concluido": False}],
            {"sintese": None},
        )
        de_quem = {x["o_que"]: x["de_quem"] for x in itens}
        self.assertEqual(de_quem["notas esperando triagem"], "você")
        self.assertEqual(de_quem["canais com histórico incompleto"], "o pipeline")
        self.assertEqual(de_quem["conceitos citados sem página no wiki"], "você")

    def test_acervo_triado_e_historico_pronto_nao_geram_pendencia(self):
        agregado = calcular([])
        itens = painel_dados.pendencias(
            agregado,
            [{"canal": "42", "backfill_cursor": "9", "backfill_concluido": True}],
            {"sintese": {"status": "ok"}},
        )
        self.assertEqual(itens, [])


class TestEspecificacoesDeMarca(unittest.TestCase):
    """As regras do padrão de dataviz que só se verificam no markup gerado."""

    def fatias(self, n=5):
        return [svg.Fatia(rotulo=f"r{i}", valor=(i + 1) * 3) for i in range(n)]

    def espessuras(self, markup: str, coluna: bool) -> list[float]:
        return [
            float(m.group(1 if coluna else 2))
            for m in re.finditer(r'width="([\d.]+)" height="([\d.]+)"', markup)
        ]

    def test_barra_horizontal_respeita_o_teto_de_espessura(self):
        markup = str(svg.barras(self.fatias(), altura_faixa=60))
        self.assertLessEqual(max(self.espessuras(markup, coluna=False)), svg.ESPESSURA_MAX)

    def test_coluna_respeita_o_teto_de_espessura(self):
        markup = str(svg.colunas(self.fatias(2), largura=600))
        self.assertLessEqual(max(self.espessuras(markup, coluna=True)), svg.ESPESSURA_MAX)

    def test_nenhuma_marca_tem_borda_desenhada(self):
        """O que separa é o vão de 2 px na cor da superfície, nunca um traço em volta:
        borda soma tinta com peso de dado que não é dado."""
        for markup in (str(svg.barras(self.fatias())),
                       str(svg.colunas(self.fatias())),
                       str(svg.empilhada(self.fatias(4))),
                       str(svg.faixa_status([("ok", "x"), ("falha", "y")]))):
            with self.subTest(markup[:40]):
                self.assertNotIn("stroke=", markup)
                self.assertNotIn("stroke-dasharray", markup)

    def test_a_empilhada_deixa_vao_entre_segmentos(self):
        """Sem o vão, dois passos vizinhos da rampa encostam e parecem um só."""
        fatias = [svg.Fatia(rotulo="a", valor=25) for _ in range(4)]
        markup = str(svg.empilhada(fatias, largura=400))
        larguras = [float(m.group(1)) for m in re.finditer(r'width="([\d.]+)" height="22"', markup)]
        # Três dos quatro segmentos cedem 2 px; o último encosta na borda.
        self.assertEqual(sorted(larguras), [98.0, 98.0, 98.0, 100.0])

    def test_rotulo_e_seletivo_e_nao_um_por_marca(self):
        """Número em cada coluna é ruído e ninguém lê. No máximo dois: o maior e o
        último."""
        markup = str(svg.colunas(self.fatias(20)))
        self.assertLessEqual(markup.count("viz-val"), 2)
        self.assertEqual(markup.count("<rect"), 20)

    def test_barra_horizontal_rotula_todo_valor_fora_da_ponta(self):
        """Aqui o rótulo em cada barra é certo: são poucas, o nome já está no eixo, e
        o valor fora da ponta nunca é recortado — o que aconteceria dentro da barra
        curta, que é justamente a que mais precisa do número."""
        markup = str(svg.barras(self.fatias(4)))
        self.assertEqual(markup.count("viz-val"), 4)

    def test_toda_marca_tem_dica_nativa(self):
        markup = str(svg.barras(self.fatias(6)))
        self.assertEqual(markup.count("<title>"), 6)

    def test_dado_hostil_sai_escapado_no_svg(self):
        """Rótulo vem de domínio de URL e de categoria escrita por LLM. `<title>` e
        `<text>` são conteúdo, e conteúdo não confiável precisa de escape igual ao
        do HTML."""
        markup = str(svg.barras([svg.Fatia(rotulo="<script>x</script>", valor=1)]))
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)

    def test_serie_vazia_diz_isso_em_vez_de_desenhar_nada(self):
        for chamada in (svg.barras([]), svg.colunas([]), svg.empilhada([]),
                        svg.faixa_status([])):
            with self.subTest(str(chamada)[:30]):
                self.assertIn("viz-vazio", str(chamada))
                self.assertNotIn("<svg", str(chamada))

    def test_medidor_nao_estoura_nem_desaparece(self):
        self.assertIn('width="200"', str(svg.medidor(10, 10, "tudo")))
        self.assertIn('width="2"', str(svg.medidor(0, 10, "nada")))
        self.assertIn("viz-trilho", str(svg.medidor(0, 0, "vazio")))

    def test_legenda_existe_quando_ha_mais_de_uma_serie(self):
        """Identidade nunca só por cor: com quatro séries, a legenda é obrigatória."""
        legenda = str(svg.legenda_ordinal(self.fatias(4)))
        self.assertEqual(legenda.count("viz-chip"), 4)
        for i in range(1, 5):
            self.assertIn(f"viz-ord-{i}", legenda)


if __name__ == "__main__":
    unittest.main()
