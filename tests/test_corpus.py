"""Rode com: python3 -m unittest discover -s tests -t .

O leitor de `50_LINKS/`. É a entrada da síntese periódica — e depois da busca e do
ressurgimento — então o que ele deixa passar errado se propaga para tudo.
"""

import unittest
from pathlib import Path

from dlb.corpus import (
    da_semana,
    do_mes,
    ler_links,
    mes_de,
    semana_anterior,
    semana_de,
    semanas_do_mes,
)

from tests.test_vault import VaultCase, note


class TestSemanaISO(unittest.TestCase):
    """A numeração ISO não bate com o ano do calendário, e errar isso é um bug
    silencioso que aparece uma vez por ano."""

    def test_31_de_dezembro_pode_pertencer_ao_ano_seguinte(self):
        # 2019-12-31 é terça da semana 2020-W01. Usar `.year` daria 2019-W01.
        self.assertEqual(semana_de("2019-12-30"), "2020-W01")
        self.assertEqual(semana_de("2019-12-31"), "2020-W01")

    def test_1_de_janeiro_pode_pertencer_ao_ano_anterior(self):
        # 2027-01-01 é sexta da semana 2026-W53.
        self.assertEqual(semana_de("2027-01-01"), "2026-W53")

    def test_semana_do_meio_do_ano(self):
        self.assertEqual(semana_de("2026-08-18"), "2026-W34")

    def test_semana_e_zero_preenchida(self):
        self.assertEqual(semana_de("2026-01-05"), "2026-W02")

    def test_dia_invalido_devolve_vazio_em_vez_de_explodir(self):
        for ruim in ("", "nao-e-data", "2026-13-45"):
            with self.subTest(ruim):
                self.assertEqual(semana_de(ruim), "")

    def test_semana_anterior_atravessa_a_virada_do_ano(self):
        self.assertEqual(semana_anterior("2027-01-04"), "2026-W53")

    def test_mes_de(self):
        self.assertEqual(mes_de("2026-08-18"), "2026-08")
        self.assertEqual(mes_de("lixo"), "")


class CorpusCase(VaultCase):
    def escrever(self, url, titulo, conceitos=(), hhmm="09:12"):
        return self.vault.write_link_note(
            "estudos", note(url, titulo, list(conceitos), hhmm)
        )

    def ler(self):
        return ler_links(self.root)


class TestLeituraDaNota(CorpusCase):
    def test_le_os_campos_do_frontmatter(self):
        self.escrever("https://www.youtube.com/watch?v=x", "Relays", ["Nostr"])
        e = self.ler()[0]
        self.assertEqual(e.url, "https://www.youtube.com/watch?v=x")
        self.assertEqual(e.dia, "2026-08-15")
        self.assertEqual(e.dominio, "youtube.com")
        self.assertEqual(e.categoria, "artigo")
        self.assertEqual(e.area, "area/ia")
        self.assertEqual(e.triagem, "inbox")
        self.assertEqual(e.autor, "Dotcom")
        self.assertEqual(e.fonte, "FONTE - Discord estudos - 2026-08-15")

    def test_le_resumo_e_porque_importa_do_corpo(self):
        self.escrever("https://e.com/a", "Relays")
        e = self.ler()[0]
        self.assertEqual(e.resumo, "Um resumo curto.")
        self.assertEqual(e.porque_importa, "Muda como penso relays.")

    def test_conceito_com_pagina_vem_de_relacionado(self):
        self.escrever("https://e.com/a", "Relays", ["Nostr"])
        e = self.ler()[0]
        self.assertIn("Nostr", e.conceitos)
        self.assertEqual(e.sem_pagina, [])

    def test_conceito_sem_pagina_vem_do_corpo(self):
        self.escrever("https://e.com/a", "Relays", ["Nostr", "Relay Nostr"])
        e = self.ler()[0]
        self.assertEqual(e.sem_pagina, ["Relay Nostr"])
        self.assertIn("Nostr", e.conceitos)
        self.assertIn("Relay Nostr", e.conceitos)

    def test_a_daily_e_o_moc_nao_contam_como_conceito(self):
        """Os dois estão em `relacionado`/`parte-de` e não são assunto."""
        self.escrever("https://e.com/a", "Relays", ["Nostr"])
        e = self.ler()[0]
        self.assertNotIn("2026-08-15", e.conceitos)
        self.assertFalse(any(c.startswith("MOC - ") for c in e.conceitos))

    def test_nao_le_nada_abaixo_de_minhas_notas(self):
        """O que você escreve ali é seu e não entra no prompt sem você pedir."""
        r = self.escrever("https://e.com/a", "Relays")
        r.path.write_text(
            r.path.read_text(encoding="utf-8")
            + "\n> segredo meu que não vai para o modelo\n",
            encoding="utf-8",
        )
        e = self.ler()[0]
        self.assertNotIn("segredo", e.resumo)
        self.assertNotIn("segredo", e.porque_importa)

    def test_le_a_avaliacao_humana_quando_existe(self):
        r = self.escrever("https://e.com/a", "Relays")
        r.path.write_text(
            r.path.read_text(encoding="utf-8").replace("nota:\n", "nota: 4\n"),
            encoding="utf-8",
        )
        self.assertEqual(self.ler()[0].nota, 4)

    def test_nota_ausente_e_none_e_nao_zero(self):
        self.escrever("https://e.com/a", "Relays")
        self.assertIsNone(self.ler()[0].nota)

    def test_titulo_completo_vem_do_alias_quando_o_nome_foi_encurtado(self):
        longo = "CS25: Transformers United! um título com dois pontos"
        self.escrever("https://e.com/a", longo)
        self.assertEqual(self.ler()[0].titulo, longo)

    def test_nome_e_o_texto_do_wikilink(self):
        self.escrever("https://e.com/a", "Relays")
        self.assertEqual(self.ler()[0].nome, "2026-08-15 Relays")


class TestOQueNaoEhNotaDeLink(CorpusCase):
    def test_arquivo_humano_na_pasta_e_ignorado(self):
        (self.root / "50_LINKS" / "Minhas ideias.md").write_text(
            "---\ntipo: conceito\nstatus: semente\n---\n\n# Ideias\n", encoding="utf-8"
        )
        self.escrever("https://e.com/a", "Relays")
        self.assertEqual(len(self.ler()), 1)

    def test_arquivo_sem_frontmatter_e_ignorado(self):
        (self.root / "50_LINKS" / "rascunho.md").write_text("só texto\n", encoding="utf-8")
        self.assertEqual(self.ler(), [])

    def test_pasta_ausente_devolve_lista_vazia(self):
        self.assertEqual(ler_links(Path("/nao/existe")), [])


class TestFiltros(CorpusCase):
    def setUp(self):
        super().setUp()
        for url, titulo, dia in (
            ("https://e.com/a", "A", "2026-08-15"),
            ("https://e.com/b", "B", "2026-08-18"),
            ("https://e.com/c", "C", "2026-09-01"),
        ):
            n = note(url, titulo, [])
            n.hit.posted_at = n.hit.posted_at.replace(
                year=int(dia[:4]), month=int(dia[5:7]), day=int(dia[8:])
            )
            self.vault.write_link_note("estudos", n)

    def test_da_semana_filtra_por_semana_iso(self):
        # 2026-08-15 é sábado da W33; 2026-08-18, terça da W34.
        self.assertEqual([e.titulo for e in da_semana(self.ler(), "2026-W33")], ["A"])
        self.assertEqual([e.titulo for e in da_semana(self.ler(), "2026-W34")], ["B"])

    def test_do_mes_filtra_por_mes(self):
        self.assertEqual(
            sorted(e.titulo for e in do_mes(self.ler(), "2026-08")), ["A", "B"]
        )

    def test_semanas_do_mes_lista_em_ordem(self):
        self.assertEqual(semanas_do_mes(self.ler(), "2026-08"), ["2026-W33", "2026-W34"])

    def test_acervo_sai_ordenado_por_dia(self):
        self.assertEqual([e.titulo for e in self.ler()], ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
