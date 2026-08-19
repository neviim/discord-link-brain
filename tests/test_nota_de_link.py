"""Rode com: python3 -m unittest discover -s tests -t .

A camada `50_LINKS/`: uma nota atômica por link. O que estes testes travam é o
contrato com `90_META/ESQUEMA.md` — vocabulário fechado, wikilink em propriedade
entre aspas, nenhum link pendurado — e a fronteira entre o que o pipeline
reescreve e o que é seu.
"""

import unittest

import yaml

from dlb import frontmatter
from dlb.vault import MINHAS_NOTAS, titulo_de_arquivo

from tests.test_vault import VaultCase, note


class TestNomeDeArquivo(unittest.TestCase):
    """O nome é o texto do wikilink: tem que ser legível e válido no FS."""

    def test_barra_vira_hifen_em_vez_de_desaparecer(self):
        # `sanitize()` apagaria a barra e produziria "cosmtrekmindwalk".
        self.assertEqual(titulo_de_arquivo("cosmtrek/mindwalk"), "cosmtrek-mindwalk")

    def test_dois_pontos_viram_separador_legivel(self):
        self.assertEqual(
            titulo_de_arquivo("CS25: Transformers United!"),
            "CS25 - Transformers United!",
        )

    def test_nao_sobra_caractere_que_quebra_no_obsidian(self):
        sujo = 'A/B\\C:D*E?F"G<H>I|J#K[L]M'
        limpo = titulo_de_arquivo(sujo)
        for ch in '*?"<>|#[]':
            self.assertNotIn(ch, limpo)

    def test_emoji_sobrevive_no_nome(self):
        self.assertIn("🔗", titulo_de_arquivo("canal 🔗 de links"))

    def test_titulo_longo_e_cortado_em_fronteira_de_palavra(self):
        nome = titulo_de_arquivo("palavra " * 40)
        self.assertLessEqual(len(nome), 100)
        self.assertFalse(nome.endswith(" "))
        self.assertTrue(nome.endswith("palavra"))

    def test_ponto_no_fim_e_removido(self):
        """Nome terminando em ponto quebra no Windows, e o vault sincroniza."""
        self.assertFalse(titulo_de_arquivo("versão 1.0.").endswith("."))

    def test_titulo_vazio_cai_para_o_dominio(self):
        self.assertEqual(titulo_de_arquivo("", "youtube.com"), "youtube.com")


class LinkNoteCase(VaultCase):
    def escrever(self, n, label="estudos", force=False):
        r = self.vault.write_link_note(label, n, force=force)
        return r, r.path.read_text(encoding="utf-8")


class TestContratoComOEsquema(LinkNoteCase):
    def test_nota_nasce_em_triagem_inbox(self):
        """Sem isso a camada não tem fila, e sem fila vira depósito."""
        _, texto = self.escrever(note("https://e.com/a", "Relays", ["Nostr"]))
        meta, _ = frontmatter.split(texto)
        self.assertEqual(meta["triagem"], "inbox")

    def test_status_e_semente_porque_e_rascunho_de_maquina(self):
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        self.assertEqual(frontmatter.split(texto)[0]["status"], "semente")

    def test_tipo_e_link(self):
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        self.assertEqual(frontmatter.split(texto)[0]["tipo"], "link")

    def test_wikilink_em_propriedade_sai_entre_aspas(self):
        """A regra do ESQUEMA que mais quebra na prática."""
        _, texto = self.escrever(note("https://e.com/a", "Relays", ["Nostr"]))
        self.assertIn('  - "[[MOC - Captura Discord]]"', texto)
        # Sem as aspas o YAML quebra em silêncio — é isto que o teste trava.
        meta = yaml.safe_load(texto.split("\n---")[0][3:])
        self.assertEqual(meta["parte-de"], ["[[MOC - Captura Discord]]"])
        self.assertIn("[[Nostr]]", meta["relacionado"])

    def test_nao_tem_tag_de_assunto(self):
        """"Assunto não é trabalho de tag, é trabalho de link" — ESQUEMA."""
        _, texto = self.escrever(note("https://e.com/a", "Rust async", ["Nostr"]))
        meta, _ = frontmatter.split(texto)
        self.assertEqual(meta["tags"], ["area/ia"])

    def test_dominio_vem_sem_www(self):
        _, texto = self.escrever(note("https://www.youtube.com/watch?v=x", "V", []))
        self.assertEqual(frontmatter.split(texto)[0]["dominio"], "youtube.com")

    def test_nota_sai_vazia_para_o_humano_preencher(self):
        """Ausente != zero: o pipeline nunca chuta uma avaliação sua."""
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        self.assertIn("\nnota:\n", texto)
        self.assertIsNone(frontmatter.split(texto)[0]["nota"])

    def test_declara_fonte_da_captura(self):
        """Invariante 5: toda página compilada declara proveniência."""
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        meta, _ = frontmatter.split(texto)
        self.assertEqual(meta["fonte"], ["[[FONTE - Discord estudos - 2026-08-15]]"])


class TestInvariantesDoGrafo(LinkNoteCase):
    def test_conceito_sem_pagina_nao_vira_wikilink(self):
        """Invariante 4. É o teste que impede o pipeline de encher o vault de
        link não-resolvido, o que derrubaria também o invariante 2."""
        _, texto = self.escrever(
            note("https://e.com/a", "Relays", ["Nostr", "Relay Nostr"])
        )
        meta, corpo = frontmatter.split(texto)
        self.assertIn("[[Nostr]]", meta["relacionado"])          # página existe
        self.assertNotIn("[[Relay Nostr]]", texto)               # não existe
        self.assertIn("- Relay Nostr", corpo)                    # fica como texto

    def test_conceito_com_pagina_nao_aparece_duas_vezes(self):
        """A mesma aresta em dois lugares divergiria — o ESQUEMA proíbe."""
        _, texto = self.escrever(note("https://e.com/a", "Relays", ["Nostr"]))
        _, corpo = frontmatter.split(texto)
        self.assertNotIn("## Conceitos sem página", corpo)

    def test_tem_aresta_saindo_mesmo_sem_conceito_nenhum(self):
        """Invariante 2: `parte-de` é incondicional, não decorativo."""
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        meta, _ = frontmatter.split(texto)
        self.assertTrue(meta["parte-de"])
        self.assertTrue(meta["relacionado"])

    def test_aponta_para_a_daily_do_dia(self):
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        self.assertIn("[[2026-08-15]]", frontmatter.split(texto)[0]["relacionado"])


class TestIdempotencia(LinkNoteCase):
    def test_o_mesmo_link_duas_vezes_nao_cria_segunda_nota(self):
        n = note("https://e.com/a", "Relays", [])
        self.escrever(n)
        r, _ = self.escrever(n)
        self.assertEqual(r.action, "pulado")
        self.assertEqual(len(list((self.root / "50_LINKS").glob("*.md"))), 1)

    def test_nota_renomeada_pelo_humano_e_encontrada_pela_url(self):
        """A idempotência é por URL, não por nome — você pode renomear no app."""
        n = note("https://e.com/a", "Relays", [])
        r, _ = self.escrever(n)
        r.path.rename(r.path.with_name("Meu nome melhor.md"))
        self.vault._link_notes = None  # invalida o cache, como faria outra execução

        segundo, _ = self.escrever(n)
        self.assertEqual(segundo.action, "pulado")
        self.assertEqual(segundo.path.stem, "Meu nome melhor")

    def test_titulos_iguais_no_mesmo_dia_geram_arquivos_distintos(self):
        a = note("https://e.com/a", "Reel do Instagram", [])
        b = note("https://e.com/b", "Reel do Instagram", [])
        ra, _ = self.escrever(a)
        rb, _ = self.escrever(b)
        self.assertNotEqual(ra.path, rb.path)
        self.assertEqual(len(list((self.root / "50_LINKS").glob("*.md"))), 2)

    def test_o_sufixo_de_desambiguacao_e_estavel_entre_execucoes(self):
        """Vem de hash da URL, não de um contador: contador daria nome
        diferente a cada execução se a ordem mudasse."""
        b = note("https://e.com/b", "Reel do Instagram", [])
        self.escrever(note("https://e.com/a", "Reel do Instagram", []))
        primeiro, _ = self.escrever(b)

        # Segundo vault, mesma URL, ordem de escrita irrelevante.
        outro = LinkNoteCase("run")
        outro.setUp()
        try:
            outro.escrever(note("https://e.com/z", "Reel do Instagram", []))
            segundo, _ = outro.escrever(b)
            self.assertEqual(primeiro.path.name, segundo.path.name)
        finally:
            outro.tearDown()

    def test_dry_run_nao_escreve_arquivo(self):
        self.vault.dry_run = True
        r = self.vault.write_link_note("estudos", note("https://e.com/a", "T", []))
        self.assertEqual(r.action, "criado")          # relata o que faria
        self.assertEqual(list((self.root / "50_LINKS").glob("*.md")), [])


class TestForcePreservaOHumano(LinkNoteCase):
    """`--force` refaz o que a máquina escreveu e não toca no que é seu."""

    def setUp(self):
        super().setUp()
        self.n = note("https://e.com/a", "Relays", ["Nostr"])
        self.r, _ = self.escrever(self.n)

    def _editar(self, transformar):
        self.r.path.write_text(
            transformar(self.r.path.read_text(encoding="utf-8")), encoding="utf-8"
        )
        self.vault._link_notes = None

    def test_triagem_do_humano_sobrevive(self):
        self._editar(lambda t: t.replace("triagem: inbox", "triagem: aplicado"))
        r, texto = self.escrever(self.n, force=True)
        self.assertEqual(r.action, "atualizado")
        self.assertEqual(frontmatter.split(texto)[0]["triagem"], "aplicado")

    def test_avaliacao_do_humano_sobrevive(self):
        self._editar(lambda t: t.replace("nota:\n", "nota: 4\n"))
        _, texto = self.escrever(self.n, force=True)
        self.assertEqual(frontmatter.split(texto)[0]["nota"], 4)

    def test_maturidade_promovida_pelo_humano_nao_e_rebaixada(self):
        self._editar(lambda t: t.replace("status: semente", "status: maduro"))
        _, texto = self.escrever(self.n, force=True)
        self.assertEqual(frontmatter.split(texto)[0]["status"], "maduro")

    def test_bloco_minhas_notas_sobrevive_intacto(self):
        self._editar(lambda t: t + "\nEntendi que relays são caros de operar.\n")
        _, texto = self.escrever(self.n, force=True)
        self.assertIn("Entendi que relays são caros de operar.", texto)

    def test_relacao_acrescentada_a_mao_sobrevive(self):
        self._editar(lambda t: t.replace(
            'relacionado:\n', 'relacionado:\n  - "[[Obsidian]]"\n'
        ))
        _, texto = self.escrever(self.n, force=True)
        self.assertIn("[[Obsidian]]", frontmatter.split(texto)[0]["relacionado"])

    def test_nota_escrita_a_mao_sem_a_fronteira_nao_e_sobrescrita(self):
        """Sem `## Minhas notas` não há como saber o que é gerado. Não se toca."""
        self.r.path.write_text(
            "---\ntipo: link\nurl: https://e.com/a\n---\n\n# Meu\n\nescrevi tudo à mão\n",
            encoding="utf-8",
        )
        self.vault._link_notes = None
        r, texto = self.escrever(self.n, force=True)
        self.assertEqual(r.action, "pulado")
        self.assertIn("escrevi tudo à mão", texto)


class TestCorpo(LinkNoteCase):
    def test_url_sai_em_linha_propria_entre_angulos(self):
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        self.assertIn("\n<https://e.com/a>\n", texto)

    def test_contexto_da_mensagem_sobrevive(self):
        _, texto = self.escrever(note("https://e.com/a", "Relays", []))
        self.assertIn("olha isso", texto)
        self.assertIn("Dotcom", texto)
        self.assertIn("09:12", texto)

    def test_pagina_ilegivel_ainda_gera_nota(self):
        n = note("https://e.com/a", "", [])
        n.titulo = ""
        r, texto = self.escrever(n)
        self.assertEqual(r.action, "criado")
        self.assertIn("e.com", r.path.stem)

    def test_a_fronteira_do_humano_e_a_ultima_coisa(self):
        _, texto = self.escrever(note("https://e.com/a", "Relays", ["Relay Nostr"]))
        self.assertIn(MINHAS_NOTAS, texto)
        self.assertGreater(texto.index(MINHAS_NOTAS), texto.index("Relay Nostr"))


class TestContagem(LinkNoteCase):
    def test_contar_triagem_agrupa_por_estado(self):
        self.escrever(note("https://e.com/a", "A", []))
        r, _ = self.escrever(note("https://e.com/b", "B", []))
        r.path.write_text(
            r.path.read_text().replace("triagem: inbox", "triagem: lido"),
            encoding="utf-8",
        )
        self.assertEqual(self.vault.contar_triagem(), {"inbox": 1, "lido": 1})


if __name__ == "__main__":
    unittest.main()
