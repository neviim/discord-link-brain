"""Rode com: python3 -m unittest discover -s tests -t .

O escape do painel. Este arquivo é a **condição** da escolha de não usar Jinja.

`string.Template` não escapa nada; Jinja escapa por padrão. Essa era a única
vantagem de segurança real de um framework de template, então ela virou requisito
e é aqui que se prova. Se estes testes forem removidos ou afrouxados, a decisão
registrada em `docs/DECISOES.md` deixa de valer.

O que entra no painel vem de fora — título raspado da web, nome de canal do
Discord, resumo escrito por LLM, mensagem de erro de subprocess. Nenhum é
confiável.
"""

import unittest

from dlb.render import Raw, escapar, juntar, lista, pagina, substituir, tabela

ATAQUES = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "' onmouseover='alert(1)",
    "</td></tr><script>x</script>",
    "javascript:alert(1)",
    "<!--<script>-->",
]


class TestEscapePorConstrucao(unittest.TestCase):
    def test_todo_valor_interpolado_e_escapado(self):
        for bruto in ATAQUES:
            with self.subTest(bruto):
                saida = substituir("<p>$x</p>", x=bruto)
                self.assertNotIn("<script", saida.lower())
                self.assertNotIn("<img", saida.lower())

    def test_aspa_dupla_nao_escapa_de_atributo(self):
        """Sem `quote=True` um título com aspas fecha o atributo e o resto vira
        marcação: `<a title="x" onmouseover=...>`."""
        saida = substituir('<a title="$t">link</a>', t='x" onmouseover="alert(1)')
        self.assertIn("&quot;", saida)
        self.assertNotIn('onmouseover="alert', saida)

    def test_none_vira_vazio_e_nao_a_palavra_none(self):
        self.assertEqual(substituir("<p>$x</p>", x=None), "<p></p>")

    def test_numero_e_convertido(self):
        self.assertEqual(substituir("<p>$x</p>", x=77), "<p>77</p>")

    def test_raw_passa_intacto(self):
        """É a válvula de escape, e é de propósito que ela precise ser escrita.

        `grep -rn 'Raw(' dlb/` lista todo lugar onde HTML não escapado entra numa
        página do painel. Um `html.escape` espalhado por chamada não dá essa lista.
        """
        self.assertEqual(substituir("<p>$x</p>", x=Raw("<b>ok</b>")), "<p><b>ok</b></p>")

    def test_resultado_de_substituir_e_raw_para_poder_aninhar(self):
        interno = substituir("<b>$x</b>", x="<i>")
        externo = substituir("<p>$y</p>", y=interno)
        self.assertEqual(externo, "<p><b>&lt;i&gt;</b></p>")

    def test_chave_faltando_e_erro_e_nao_dolar_na_tela(self):
        """`safe_substitute` deixaria `$titulo` literal aparecendo na tela.
        Erro no teste é melhor que erro na tela."""
        with self.assertRaises(KeyError):
            substituir("<p>$a$b</p>", a="1")


class TestTabela(unittest.TestCase):
    def test_celula_e_escapada(self):
        html = tabela(["a"], [["<script>x</script>"]])
        self.assertNotIn("<script", html)
        self.assertIn("&lt;script&gt;", html)

    def test_cabecalho_tambem_e_escapado(self):
        self.assertNotIn("<script", tabela(["<script>"], [["x"]]))

    def test_celula_raw_carrega_marcacao(self):
        """O único caso legítimo: um `<span class=selo>` de status."""
        html = tabela(["s"], [[Raw("<span class=selo>ok</span>")]])
        self.assertIn("<span class=selo>ok</span>", html)

    def test_tabela_vazia_diz_o_que_fazer_em_vez_de_ficar_em_branco(self):
        html = tabela(["a"], [], vazio="Nada ainda — começa no próximo run.")
        self.assertIn("Nada ainda", html)
        self.assertNotIn("<table", html)


class TestPagina(unittest.TestCase):
    def test_marca_o_item_ativo_do_menu(self):
        html = pagina("Acervo", Raw("<p>x</p>"), ativo="/acervo")
        self.assertIn('href="/acervo" class="ativo"', html)
        self.assertIn('href="/" class=""', html)

    def test_corpo_ja_montado_nao_e_reescapado(self):
        html = pagina("t", substituir("<p>$x</p>", x="a & b"))
        self.assertIn("<p>a &amp; b</p>", html)
        self.assertNotIn("&lt;p&gt;", html)

    def test_titulo_e_caminho_do_vault_sao_escapados(self):
        html = pagina("<script>", Raw(""), vault="/tmp/<script>")
        self.assertNotIn("<script>", html)


class TestAjudantes(unittest.TestCase):
    def test_lista_escapa_e_tem_estado_vazio(self):
        self.assertIn("&lt;b&gt;", lista(["<b>"]))
        self.assertIn("nenhum", lista([], vazio="nenhum"))

    def test_juntar_preserva_a_ordem(self):
        self.assertEqual(juntar(["a", "b"], "-"), "a-b")

    def test_escapar_e_idempotente_sobre_raw(self):
        """Chamar `escapar` num pedaço já montado não pode escapar duas vezes —
        senão `&amp;amp;` aparece na tela."""
        self.assertEqual(escapar(Raw("&amp;")), "&amp;")


if __name__ == "__main__":
    unittest.main()
