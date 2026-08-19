"""Rode com: python3 -m unittest discover -s tests -t .

Ressurgimento programado e o relatório de temas. Os dois leem `50_LINKS/` e
nenhum chama modelo.

O teste mais importante do arquivo é `test_nao_cria_nada_no_vault`: o relatório de
temas **propõe** um MOC e não escreve nenhum — vault cheio de MOC vazio é pior que
vault sem MOC.
"""

import unittest
from datetime import date, timedelta

from dlb.corpus import ler_links
from dlb.ressurgir import (
    Historico,
    elegiveis,
    escolher,
    formatar,
)
from dlb.temas import PLATAFORMAS, _chave, levantar
from dlb.temas import formatar as formatar_temas

from tests.test_vault import VaultCase, note

HOJE = "2026-08-18"


def antigo(dias: int) -> str:
    return (date.fromisoformat(HOJE) - timedelta(days=dias)).isoformat()


class AcervoCase(VaultCase):
    """Notas com data controlada, para os filtros de idade serem testáveis."""

    def criar(self, url, titulo, dias, conceitos=(), porque="Muda como penso relays."):
        n = note(url, titulo, list(conceitos))
        n.porque_importa = porque
        d = date.fromisoformat(antigo(dias))
        n.hit.posted_at = n.hit.posted_at.replace(year=d.year, month=d.month, day=d.day)
        return self.vault.write_link_note("estudos", n)

    def acervo(self):
        return ler_links(self.root)


class TestElegiveis(AcervoCase):
    def test_link_novo_nao_ressurge(self):
        self.criar("https://e.com/novo", "Novo", 5)
        self.assertEqual(elegiveis(self.acervo(), HOJE, 30), [])

    def test_link_com_exatamente_x_dias_entra(self):
        """A fronteira é `>=`, e está documentada porque off-by-one aqui é sutil."""
        self.criar("https://e.com/borda", "Na borda", 30)
        self.assertEqual(len(elegiveis(self.acervo(), HOJE, 30)), 1)

    def test_descartado_nao_ressurge(self):
        """Você já disse que não serviu; trazer de volta contradiz sua decisão."""
        r = self.criar("https://e.com/ruim", "Descartado", 60)
        r.path.write_text(
            r.path.read_text(encoding="utf-8").replace("triagem: inbox", "triagem: descartado"),
            encoding="utf-8",
        )
        self.assertEqual(elegiveis(self.acervo(), HOJE, 30), [])

    def test_link_sem_porque_importa_fica_para_o_fim(self):
        """Medido no acervo real: esse campo é vazio exatamente nos links que o
        modelo não conseguiu descrever. Ressurgir 'conteúdo não identificado' é
        ruído, não memória."""
        self.criar("https://e.com/bom", "Com substância", 60)
        self.criar("https://e.com/vazio", "Sem substância", 60, porque="")
        titulos = [e.titulo for e in elegiveis(self.acervo(), HOJE, 30)]
        self.assertEqual(titulos, ["Com substância"])

    def test_se_sobrar_so_o_fraco_ele_ainda_ressurge(self):
        """Preferência, não corte: melhor lembrar de algo do que de nada."""
        self.criar("https://e.com/vazio", "Sem substância", 60, porque="")
        self.assertEqual(len(elegiveis(self.acervo(), HOJE, 30)), 1)


class TestEscolher(AcervoCase):
    def setUp(self):
        super().setUp()
        for i in range(8):
            self.criar(f"https://e.com/{i}", f"Link {i}", 40 + i)
        self.pool = elegiveis(self.acervo(), HOJE, 30)

    def test_mesma_data_escolhe_os_mesmos_links(self):
        """Semente determinística: rodar duas vezes no mesmo dia é idempotente
        sem depender do histórico."""
        a = escolher(self.pool, Historico(), HOJE, 3, "dlb")
        b = escolher(self.pool, Historico(), HOJE, 3, "dlb")
        self.assertEqual([x.url for x in a], [x.url for x in b])

    def test_dia_diferente_escolhe_outros(self):
        a = escolher(self.pool, Historico(), HOJE, 3, "dlb")
        b = escolher(self.pool, Historico(), "2026-08-19", 3, "dlb")
        self.assertNotEqual([x.url for x in a], [x.url for x in b])

    def test_link_em_carencia_nao_repete(self):
        h = Historico(postados={e.url: HOJE for e in self.pool[:6]})
        escolhidos = escolher(self.pool, h, HOJE, 2, "dlb")
        self.assertTrue(all(e.url not in h.postados for e in escolhidos))

    def test_poco_seco_volta_o_mais_antigo_em_vez_de_nada(self):
        """Não postar é falha silenciosa, e este recurso existe para lembrar."""
        h = Historico(postados={e.url: HOJE for e in self.pool})
        self.assertEqual(len(escolher(self.pool, h, HOJE, 3, "dlb")), 3)

    def test_pool_menor_que_o_pedido_devolve_o_que_tem(self):
        self.assertEqual(len(escolher(self.pool[:2], Historico(), HOJE, 5, "dlb")), 2)

    def test_acervo_vazio_devolve_vazio(self):
        self.assertEqual(escolher([], Historico(), HOJE, 3, "dlb"), [])


class TestHistorico(VaultCase):
    def test_poda_o_que_passou_da_carencia(self):
        h = Historico(postados={"a": antigo(200), "b": antigo(10)})
        h.podar(HOJE, 180)
        self.assertEqual(list(h.postados), ["b"])

    def test_salva_e_carrega(self):
        d = self.root / ".state"
        Historico(ultima_execucao=HOJE, postados={"a": HOJE}).salvar(d)
        lido = Historico.carregar(d)
        self.assertEqual(lido.ultima_execucao, HOJE)
        self.assertEqual(lido.postados, {"a": HOJE})

    def test_arquivo_corrompido_devolve_historico_vazio(self):
        d = self.root / ".state"
        d.mkdir(parents=True, exist_ok=True)
        (d / "ressurgir.json").write_text("{ nao é json", encoding="utf-8")
        self.assertEqual(Historico.carregar(d).postados, {})

    def test_escrita_e_atomica_e_nao_deixa_tmp(self):
        d = self.root / ".state"
        Historico(ultima_execucao=HOJE).salvar(d)
        self.assertFalse(any(p.suffix == ".tmp" for p in d.iterdir()))


class TestMensagemDoRessurgimento(AcervoCase):
    def test_a_url_sai_entre_angulos_para_nao_gerar_previa(self):
        """Com 3 links a mensagem viraria parede de cartões — e assim não precisa
        da permissão Embed Links."""
        self.criar("https://e.com/a", "Um", 60)
        texto = formatar(elegiveis(self.acervo(), HOJE, 30), HOJE, 220)
        self.assertIn("<https://e.com/a>", texto)

    def test_mostra_a_idade_em_dias(self):
        self.criar("https://e.com/a", "Um", 60)
        texto = formatar(elegiveis(self.acervo(), HOJE, 30), HOJE, 220)
        self.assertIn("há 60 dias", texto)

    def test_resumo_longo_e_cortado_em_fronteira_de_palavra(self):
        r = self.criar("https://e.com/a", "Um", 60)
        longo = "palavra " * 100
        r.path.write_text(
            r.path.read_text(encoding="utf-8").replace("> Um resumo curto.", f"> {longo}"),
            encoding="utf-8",
        )
        texto = formatar(elegiveis(self.acervo(), HOJE, 30), HOJE, 60)
        self.assertIn("…", texto)
        self.assertNotIn("palavra palavra palavra palavra palavra palavra palavra p…", texto)

    def test_cabe_no_limite_do_discord(self):
        for i in range(3):
            self.criar(f"https://e.com/{i}", f"Link {i}" * 10, 60 + i)
        texto = formatar(elegiveis(self.acervo(), HOJE, 30), HOJE, 220)
        self.assertLessEqual(len(texto), 1900)

    def test_nada_escolhido_nao_gera_mensagem(self):
        self.assertEqual(formatar([], HOJE, 220), "")


class TestChaveDeTema(unittest.TestCase):
    def test_acento_e_caixa_nao_criam_dois_temas(self):
        self.assertEqual(_chave("Inteligência"), _chave("inteligencia"))

    def test_espaco_duplicado_e_colapsado(self):
        self.assertEqual(_chave("Deep  Learning"), "deep learning")

    def test_sigla_terminando_em_s_nao_e_mutilada(self):
        """Regressão: a regra de plural por tamanho fazia `SaaS` virar `saa`."""
        self.assertEqual(_chave("SaaS"), "saas")
        self.assertEqual(_chave("n8n"), "n8n")


class TestTemas(AcervoCase):
    def setUp(self):
        super().setUp()
        for i in range(4):
            self.criar(f"https://e.com/n{i}", f"n8n {i}", 10 + i, ["n8n", "YouTube"])
        self.criar("https://e.com/x", "Sobre Nostr", 5, ["Nostr"])
        self.rel = levantar(self.root, self.acervo(), limiar=3)

    def test_conceito_acima_do_limiar_e_reportado(self):
        self.assertEqual([t.nome for t in self.rel.maduros], ["n8n"])
        self.assertEqual(self.rel.maduros[0].total, 4)

    def test_plataforma_sai_do_ranking_mas_aparece_nos_descartados(self):
        """Medido no acervo real: os líderes brutos eram Instagram e YouTube —
        onde o link mora, não do que trata. Ignorado nunca sai em silêncio."""
        nomes = [t.nome for t in self.rel.maduros + self.rel.chegando]
        self.assertNotIn("YouTube", nomes)
        self.assertIn("YouTube", [n for n, _c in self.rel.descartados])

    def test_conceito_com_pagina_e_marcado(self):
        rel = levantar(self.root, self.acervo(), limiar=1)
        nostr = next(t for t in rel.maduros if t.nome == "Nostr")
        self.assertTrue(nostr.tem_pagina)

    def test_conta_dias_distintos_e_nao_so_links(self):
        self.assertEqual(self.rel.maduros[0].dias, 4)

    def test_alias_do_wiki_soma_no_nome_canonico(self):
        (self.root / "20_WIKI" / "Automação.md").write_text(
            '---\ntipo: ferramenta\naliases:\n  - "n8n"\n---\n\n# Automação\n',
            encoding="utf-8",
        )
        rel = levantar(self.root, self.acervo(), limiar=3)
        self.assertEqual([t.nome for t in rel.maduros], ["Automação"])
        self.assertTrue(rel.maduros[0].tem_pagina)

    def test_backlinks_contam_link_em_propriedade(self):
        """O ESQUEMA provou que link em propriedade é aresta de verdade."""
        rel = levantar(self.root, self.acervo(), limiar=1, com_backlinks=True)
        nostr = next(t for t in rel.maduros if t.nome == "Nostr")
        self.assertGreater(nostr.backlinks, 0)

    def test_variantes_de_grafia_sao_reportadas(self):
        self.criar("https://e.com/v", "Variante", 9, ["N8N"])
        rel = levantar(self.root, self.acervo(), limiar=3)
        n8n = next(t for t in rel.maduros if _chave(t.nome) == "n8n")
        self.assertIn("N8N", n8n.variantes)


    def test_plural_junta_com_singular_quando_os_dois_aparecem(self):
        """Observa em vez de adivinhar: é a diferença entre regra previsível e
        stemmer."""
        self.criar("https://e.com/t1", "T1", 20, ["Transformers"])
        self.criar("https://e.com/t2", "T2", 21, ["Transformer"])
        rel = levantar(self.root, self.acervo(), limiar=2)
        nomes = [t.nome for t in rel.maduros + rel.chegando]
        self.assertEqual(nomes.count("Transformers") + nomes.count("Transformer"), 1)
        junto = next(t for t in rel.maduros + rel.chegando if "ransformer" in t.nome)
        self.assertEqual(junto.total, 2)

    def test_sigla_sozinha_nao_e_juntada_com_nada(self):
        self.criar("https://e.com/s", "S", 20, ["SaaS"])
        rel = levantar(self.root, self.acervo(), limiar=1)
        self.assertIn("SaaS", [t.nome for t in rel.maduros])

    def test_nao_cria_nada_no_vault(self):
        """DECISOES: compilar é trabalho curado. Vault cheio de MOC vazio é pior
        que vault sem MOC nenhum."""
        antes = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        levantar(self.root, self.acervo(), limiar=1, com_backlinks=True)
        formatar_temas(levantar(self.root, self.acervo(), limiar=1), markdown=True)
        depois = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        self.assertEqual(antes, depois)

    def test_o_markdown_propoe_o_moc_sem_escrever(self):
        saida = formatar_temas(self.rel, markdown=True)
        self.assertIn("# MOC - n8n", saida)
        self.assertIn("com suas palavras", saida)

    def test_relatorio_de_texto_diz_que_nada_foi_criado(self):
        self.assertIn("Nada foi criado no vault", formatar_temas(self.rel))

    def test_limiar_padrao_e_o_do_vault_e_nao_o_do_plan(self):
        """O `MOC - Índice` do vault diz ~8; o plan.md dizia 15. Vale o do vault."""
        from dlb.config import Config

        cfg = Config(discord_token="x", discord_token_type="bot", channel_ids=["1"])
        self.assertEqual(cfg.temas_limiar, 8)


if __name__ == "__main__":
    unittest.main()
