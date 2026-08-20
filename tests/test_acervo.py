"""Rode com: python3 -m unittest discover -s tests -t .

O memo por `(mtime_ns, size)` sobre `50_LINKS/`.

O painel é o primeiro processo do projeto que **vive** — a CLI abre, lê o acervo
uma vez e morre. Reler tudo a cada clique funcionaria com 77 notas e é o tipo de
coisa que só dói quando o acervo cresceu e ninguém lembra por quê.

O teste que importa é o de invalidação: **editar a nota no Obsidian e o painel
mudar no reload**. Um cache que não invalida é pior que nenhum cache, porque a
tela passa a mentir com confiança.
"""

import unittest

from dlb.acervo import Acervo, filtrar, resumo

from tests.test_vault import VaultCase, note


class AcervoCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.acervo = Acervo(self.root)

    def escrever(self, url, titulo, conceitos=(), hhmm="09:12"):
        return self.vault.write_link_note("estudos", note(url, titulo, list(conceitos), hhmm))

    def caminho(self, resultado):
        return resultado.path if hasattr(resultado, "path") else resultado


class TestMemo(AcervoCase):
    def test_primeira_leitura_vem_do_disco_segunda_vem_do_memo(self):
        self.escrever("https://e.com/a", "Relays")
        self.acervo.links()
        self.assertEqual(self.acervo.ultima.lidos_do_disco, 1)
        self.assertEqual(self.acervo.ultima.reusados_do_memo, 0)

        self.acervo.links()
        self.assertEqual(self.acervo.ultima.lidos_do_disco, 0)
        self.assertEqual(self.acervo.ultima.reusados_do_memo, 1)

    def test_editar_a_nota_no_obsidian_muda_o_painel_no_reload(self):
        """O critério de pronto da F1, no nível do cache.

        Simula o que o Dotcom faz: abre a nota no Obsidian, muda a triagem, salva.
        A próxima leitura tem de ver a mudança — e tem de vê-la relendo **só
        aquele arquivo**.
        """
        self.escrever("https://e.com/a", "Relays")
        self.escrever("https://e.com/b", "Nostr")
        (primeira,) = [e for e in self.acervo.links() if e.titulo == "Relays"]
        self.assertEqual(primeira.triagem, "inbox")

        texto = primeira.path.read_text(encoding="utf-8")
        primeira.path.write_text(texto.replace("triagem: inbox", "triagem: aplicado"), encoding="utf-8")

        depois = {e.titulo: e.triagem for e in self.acervo.links()}
        self.assertEqual(depois["Relays"], "aplicado")
        self.assertEqual(self.acervo.ultima.lidos_do_disco, 1)     # só a que mudou
        self.assertEqual(self.acervo.ultima.reusados_do_memo, 1)   # a outra veio do memo

    def test_nota_nova_aparece_sem_reler_as_antigas(self):
        self.escrever("https://e.com/a", "Relays")
        self.acervo.links()
        self.escrever("https://e.com/b", "Nostr")
        self.assertEqual(len(self.acervo.links()), 2)
        self.assertEqual(self.acervo.ultima.lidos_do_disco, 1)
        self.assertEqual(self.acervo.ultima.reusados_do_memo, 1)

    def test_nota_apagada_sai_do_memo_em_vez_de_vazar(self):
        """Sem isto o dicionário cresce para sempre num processo que fica meses
        em pé, guardando notas que não existem mais."""
        resultado = self.escrever("https://e.com/a", "Relays")
        self.acervo.links()
        self.caminho(resultado).unlink()
        self.assertEqual(self.acervo.links(), [])
        self.assertEqual(self.acervo.ultima.esquecidos, 1)
        self.assertEqual(len(self.acervo._memo), 0)

    def test_esquecer_forca_releitura_completa(self):
        self.escrever("https://e.com/a", "Relays")
        self.acervo.links()
        self.acervo.esquecer()
        self.acervo.links()
        self.assertEqual(self.acervo.ultima.lidos_do_disco, 1)

    def test_nota_ilegivel_e_contada_e_nao_derruba_a_leitura(self):
        self.escrever("https://e.com/a", "Relays")
        (self.root / "50_LINKS" / "2026-08-15 quebrada.md").write_text(
            "isto não tem frontmatter nenhum\n", encoding="utf-8"
        )
        self.assertEqual(len(self.acervo.links()), 1)
        self.assertEqual(self.acervo.ultima.ilegiveis, 1)

    def test_ordem_e_a_mesma_de_corpus_ler_links(self):
        """O painel e a busca não podem discordar sobre 'os últimos dez'."""
        from dlb.corpus import ler_links

        self.escrever("https://e.com/a", "A", hhmm="10:00")
        self.escrever("https://e.com/b", "B", hhmm="08:00")
        self.assertEqual(
            [e.nome for e in self.acervo.links()], [e.nome for e in ler_links(self.root)]
        )

    def test_acervo_vazio_nao_explode(self):
        self.assertEqual(self.acervo.links(), [])
        self.assertEqual(self.acervo.ultima.total, 0)


class TestResumo(AcervoCase):
    def test_conta_por_triagem_e_por_categoria(self):
        self.escrever("https://e.com/a", "A")
        self.escrever("https://e.com/b", "B")
        dados = resumo(self.acervo.links())
        self.assertEqual(dados["total"], 2)
        self.assertEqual(dados["por_triagem"], {"inbox": 2})
        self.assertEqual(dados["por_categoria"], {"artigo": 2})
        self.assertEqual(dados["primeiro_dia"], "2026-08-15")

    def test_dominios_frequentes_saem_ordenados_por_quantidade(self):
        for i in range(3):
            self.escrever(f"https://muito.com/{i}", f"M{i}")
        self.escrever("https://pouco.com/x", "P")
        frequentes = list(resumo(self.acervo.links())["dominios_frequentes"])
        self.assertEqual(frequentes[0], "muito.com")

    def test_resumo_de_acervo_vazio_nao_inventa_dia(self):
        dados = resumo([])
        self.assertEqual(dados["total"], 0)
        self.assertIsNone(dados["primeiro_dia"])


class TestFiltro(AcervoCase):
    def setUp(self):
        super().setUp()
        self.escrever("https://relay.exemplo.com/a", "Relays do Nostr", ["Nostr"])
        self.escrever("https://outro.com/b", "Coisa sem graça")

    def test_termo_casa_titulo_url_e_conceito(self):
        entradas = self.acervo.links()
        for termo in ("relays", "relay.exemplo", "nostr"):
            with self.subTest(termo):
                self.assertEqual(len(filtrar(entradas, termo=termo)), 1)

    def test_termo_nao_e_sensivel_a_caixa(self):
        self.assertEqual(len(filtrar(self.acervo.links(), termo="RELAYS")), 1)

    def test_faceta_de_triagem_filtra(self):
        entradas = self.acervo.links()
        self.assertEqual(len(filtrar(entradas, triagem="inbox")), 2)
        self.assertEqual(len(filtrar(entradas, triagem="aplicado")), 0)

    def test_sem_filtro_devolve_tudo(self):
        self.assertEqual(len(filtrar(self.acervo.links())), 2)


if __name__ == "__main__":
    unittest.main()
