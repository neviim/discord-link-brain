"""Rode com: python3 -m unittest discover -s tests -t ."""

import unittest

from dlb.discord_client import ja_marcada
from dlb.enrich import main_text
from dlb.extract import youtube_id


class TestYoutubeId(unittest.TestCase):
    def test_reconhece_as_variantes_de_url(self):
        casos = {
            "https://www.youtube.com/watch?v=REBIKyFokAM": "REBIKyFokAM",
            "https://youtu.be/REBIKyFokAM": "REBIKyFokAM",
            "https://youtube.com/shorts/REBIKyFokAM?is=Ftg7": "REBIKyFokAM",
            "https://m.youtube.com/watch?v=abc123&t=90": "abc123",
            "https://www.youtube.com/embed/abc123": "abc123",
            "https://www.youtube.com/live/abc123": "abc123",
        }
        for url, esperado in casos.items():
            with self.subTest(url=url):
                self.assertEqual(youtube_id(url), esperado)

    def test_nao_confunde_outros_sites(self):
        for url in ("https://vimeo.com/watch?v=123",
                    "https://notyoutube.com/watch?v=123",
                    "https://obsidian.md/cli"):
            with self.subTest(url=url):
                self.assertEqual(youtube_id(url), "")

    def test_canal_do_youtube_nao_tem_id_de_video(self):
        # Página de canal não é vídeo: cai no caminho HTML comum.
        self.assertEqual(youtube_id("https://www.youtube.com/@bagoftips"), "")


class TestMainText(unittest.TestCase):
    def test_pega_o_artigo_e_descarta_menu_e_script(self):
        html = """
        <html><body>
          <nav><p>Início · Sobre · Contato · Assine a newsletter agora mesmo</p></nav>
          <script>var x = "<p>isto nao e conteudo de verdade, e codigo</p>";</script>
          <article>
            <h1>Quatro anos de Obsidian</h1>
            <p>Faz quatro anos que uso o Obsidian para gerenciar minhas notas e meu
               trabalho, e o hábito mudou bastante nesse período.</p>
          </article>
          <footer><p>Todos os direitos reservados para este site pessoal aqui</p></footer>
        </body></html>
        """
        texto = main_text(html)
        self.assertIn("Faz quatro anos que uso o Obsidian", texto)
        self.assertNotIn("Assine a newsletter", texto)
        self.assertNotIn("isto nao e conteudo", texto)
        self.assertNotIn("direitos reservados", texto)

    def test_descarta_linhas_curtas_de_interface(self):
        html = "<main><p>Ler mais</p><p>OK</p></main>"
        self.assertEqual(main_text(html), "")

    def test_pagina_so_de_javascript_devolve_vazio(self):
        # Sem <p>, os metadados og: é que assumem — não é erro.
        self.assertEqual(main_text("<html><body><div id='root'></div></body></html>"), "")

    def test_respeita_o_teto_de_tamanho(self):
        html = "<article>" + "<p>" + ("palavra " * 200) + "</p>" * 50 + "</article>"
        self.assertLessEqual(len(main_text(html)), 3000)


class TestMarcaDeProcessado(unittest.TestCase):
    def test_reconhece_a_marca_do_proprio_bot(self):
        msg = {"reactions": [{"me": True, "emoji": {"name": "✅"}}]}
        self.assertTrue(ja_marcada(msg, "✅"))

    def test_reacao_de_humano_nao_conta(self):
        # Se contasse, marcar um link à mão o excluiria do arquivamento.
        msg = {"reactions": [{"me": False, "count": 3, "emoji": {"name": "✅"}}]}
        self.assertFalse(ja_marcada(msg, "✅"))

    def test_outro_emoji_do_bot_nao_conta(self):
        msg = {"reactions": [{"me": True, "emoji": {"name": "👀"}}]}
        self.assertFalse(ja_marcada(msg, "✅"))

    def test_mensagem_sem_reacao_alguma(self):
        self.assertFalse(ja_marcada({"id": "1"}, "✅"))
        self.assertFalse(ja_marcada({"reactions": None}, "✅"))

    def test_qualquer_uma_das_duas_marcas_conta_como_resolvida(self):
        # Para o pipeline as duas significam a mesma coisa: já foi tratada.
        arquivada = {"reactions": [{"me": True, "emoji": {"name": "✅"}}]}
        ja_tinha = {"reactions": [{"me": True, "emoji": {"name": "📚"}}]}
        for m in (arquivada, ja_tinha):
            with self.subTest(m=m):
                self.assertTrue(ja_marcada(m, "✅", "📚"))

    def test_marca_de_duplicata_do_humano_tambem_nao_conta(self):
        msg = {"reactions": [{"me": False, "emoji": {"name": "📚"}}]}
        self.assertFalse(ja_marcada(msg, "✅", "📚"))


if __name__ == "__main__":
    unittest.main()
