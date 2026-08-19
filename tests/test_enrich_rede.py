"""Rode com: python3 -m unittest discover -s tests -t .

O caminho de rede do enriquecimento, com a rede dublada.

Esta é a superfície de entrada mais hostil do projeto: `enrich` encosta em site
arbitrário, que pode devolver HTML quebrado, 50 MB de nada, encoding errado,
Cloudflare, ou um JSON onde se esperava página. O `test_enrich.py` cobre as
funções puras de extração; aqui o que se trava é o **envelope**: o teto de bytes,
o que acontece quando a página não abre, e a garantia de que nada disso interrompe.

A regra que a ARQUITETURA registra: *falhar aqui nunca interrompe — o pior caso é
`legivel=False`, e o prompt instrui o modelo a dizer que não leu em vez de
inventar*.
"""

import unittest

import dlb.enrich as mod
from dlb.enrich import MAX_BYTES, fetch_meta


class RespostaFalsa:
    def __init__(self, corpo=b"", content_type="text/html", status=200,
                 encoding="utf-8", json_corpo=None):
        self.corpo = corpo if isinstance(corpo, bytes) else corpo.encode()
        self.headers = {"Content-Type": content_type}
        self.status_code = status
        self.encoding = encoding
        self._json = json_corpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, tamanho):
        for i in range(0, len(self.corpo), tamanho):
            yield self.corpo[i : i + tamanho]

    def json(self):
        if self._json is None:
            raise ValueError("não é json")
        return self._json

    @property
    def text(self):
        return self.corpo.decode(self.encoding, errors="replace")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class EnrichCase(unittest.TestCase):
    def setUp(self):
        self._get = mod.requests.get
        self.pedidos = []

    def tearDown(self):
        mod.requests.get = self._get

    def responder(self, *respostas):
        fila = list(respostas)

        def falso(url, **kw):
            self.pedidos.append((url, kw))
            r = fila.pop(0) if len(fila) > 1 else fila[0]
            if isinstance(r, Exception):
                raise r
            return r

        mod.requests.get = falso


PAGINA = """<html><head>
<meta property="og:title" content="Título de verdade">
<meta property="og:description" content="Uma descrição honesta da página.">
<meta property="og:site_name" content="Exemplo">
<meta name="author" content="Fulano">
<title>título de fallback</title>
</head><body><article>
<p>Este é um parágrafo com mais de quarenta caracteres para entrar no texto central.</p>
</article></body></html>"""


class TestPaginaNormal(EnrichCase):
    def test_le_titulo_descricao_site_e_autor(self):
        self.responder(RespostaFalsa(PAGINA))
        m = fetch_meta("https://exemplo.com/x")
        self.assertEqual(m.title, "Título de verdade")
        self.assertEqual(m.description, "Uma descrição honesta da página.")
        self.assertEqual(m.site, "Exemplo")
        self.assertEqual(m.author, "Fulano")
        self.assertEqual(m.kind, "html")
        self.assertTrue(m.legivel)

    def test_sem_og_cai_para_a_tag_title(self):
        self.responder(RespostaFalsa("<html><head><title>Só o title</title></head></html>"))
        self.assertEqual(fetch_meta("https://e.com/x").title, "Só o title")

    def test_o_texto_central_entra_para_o_modelo_resumir(self):
        self.responder(RespostaFalsa(PAGINA))
        self.assertIn("quarenta caracteres", fetch_meta("https://e.com/x").text)

    def test_manda_user_agent_de_navegador(self):
        """Vários sites devolvem 403 para user-agent de script."""
        self.responder(RespostaFalsa(PAGINA))
        fetch_meta("https://e.com/x")
        _url, kw = self.pedidos[0]
        self.assertIn("Mozilla", kw["headers"]["User-Agent"])

    def test_segue_redirecionamento(self):
        self.responder(RespostaFalsa(PAGINA))
        fetch_meta("https://e.com/x")
        self.assertTrue(self.pedidos[0][1]["allow_redirects"])


class TestOEnvelopeAguenta(EnrichCase):
    """O que a ARQUITETURA promete: falhar aqui nunca interrompe."""

    def test_pagina_que_nao_abre_devolve_ilegivel_em_vez_de_explodir(self):
        self.responder(RuntimeError("connection reset"))
        m = fetch_meta("https://e.com/artigo-sobre-nostr")
        self.assertFalse(m.legivel)
        self.assertIn("connection reset", m.error)

    def test_403_do_cloudflare_degrada_sem_explodir(self):
        self.responder(RespostaFalsa(status=403))
        self.assertFalse(fetch_meta("https://e.com/x").legivel)

    def test_timeout_degrada_sem_explodir(self):
        self.responder(TimeoutError("timed out"))
        self.assertFalse(fetch_meta("https://e.com/x").legivel)

    def test_o_caminho_da_url_vira_descricao_quando_nada_mais_da(self):
        """Melhor que nada: o modelo ao menos sabe do que o endereço fala."""
        self.responder(RuntimeError("nope"))
        m = fetch_meta("https://e.com/rust-async-tokio")
        self.assertIn("rust", m.description.lower())

    def test_content_type_que_nao_e_html_e_recusado(self):
        self.responder(RespostaFalsa(b"%PDF-1.7", content_type="application/pdf"))
        m = fetch_meta("https://e.com/artigo.pdf")
        self.assertFalse(m.legivel)
        self.assertIn("pdf", m.error)

    def test_html_vazio_e_ilegivel_e_nao_finge_o_contrario(self):
        self.responder(RespostaFalsa("<html><body></body></html>"))
        self.assertFalse(fetch_meta("https://e.com/x").legivel)

    def test_encoding_errado_nao_derruba(self):
        """`errors='replace'`: byte inválido vira �, o resto é lido."""
        self.responder(RespostaFalsa(b"<title>caf\xe9 quebrado</title>", encoding="utf-8"))
        self.assertTrue(fetch_meta("https://e.com/x").title)


class TestTetoDeBytes(EnrichCase):
    def test_pagina_gigante_e_cortada_no_teto(self):
        """Sem o teto, uma página de 50 MB come a memória do container."""
        enorme = "<title>ok</title>" + ("<p>" + "x" * 200 + "</p>") * 20000
        self.responder(RespostaFalsa(enorme))
        m = fetch_meta("https://e.com/x")
        self.assertEqual(m.title, "ok")
        self.assertLess(len(m.text), MAX_BYTES)

    def test_o_texto_entregue_ao_modelo_tem_teto_proprio(self):
        """MAX_BYTES protege a memória; MAX_TEXT protege o prompt."""
        corpo = "<title>t</title><article>" + \
                "".join(f"<p>{'palavra ' * 20}</p>" for _ in range(500)) + "</article>"
        self.responder(RespostaFalsa(corpo))
        self.assertLessEqual(len(fetch_meta("https://e.com/x").text), mod.MAX_TEXT)


class TestYouTube(EnrichCase):
    def test_usa_oembed_e_nao_raspagem(self):
        """A raspagem do /watch devolve 429 para servidor — decisão registrada."""
        import json as _json
        self.responder(RespostaFalsa(
            _json.dumps({"title": "Vídeo bacana", "author_name": "Canal X"}),
            content_type="application/json",
        ))
        m = fetch_meta("https://www.youtube.com/watch?v=abc12345678")
        self.assertEqual(m.kind, "youtube")
        self.assertEqual(m.title, "Vídeo bacana")
        self.assertEqual(m.author, "Canal X")
        self.assertIn("oembed", self.pedidos[0][0].lower())

    def test_oembed_fora_do_ar_ainda_devolve_algo(self):
        self.responder(RuntimeError("500"))
        m = fetch_meta("https://www.youtube.com/watch?v=abc12345678")
        self.assertEqual(m.kind, "youtube")
        self.assertFalse(m.legivel)

    def test_shorts_e_reconhecido_como_video(self):
        import json as _json
        self.responder(RespostaFalsa(
            _json.dumps({"title": "Curto", "author_name": "C"}),
            content_type="application/json",
        ))
        self.assertEqual(fetch_meta("https://youtube.com/shorts/abc12345678").kind, "youtube")


if __name__ == "__main__":
    unittest.main()
