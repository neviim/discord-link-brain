"""Rode com: python3 -m unittest discover -s tests -t ."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from dlb import frontmatter
from dlb.models import LinkHit, LinkNote, PageMeta
from dlb.vault import Vault, fonte_name

TZ = ZoneInfo("America/Sao_Paulo")


def note(url, titulo, conceitos, hhmm="09:12"):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return LinkNote(
        hit=LinkHit(
            url=url,
            channel_id="42",
            message_id="1",
            author="Fulano",
            posted_at=datetime(2026, 8, 15, hour, minute, tzinfo=TZ),
            context="olha isso",
            jump_url="https://discord.com/channels/7/42/1",
        ),
        meta=PageMeta(title=titulo, description="desc"),
        titulo=titulo,
        resumo="Um resumo curto.",
        porque_importa="Muda como penso relays.",
        categoria="artigo",
        area="area/ia",
        conceitos=conceitos,
    )


class VaultCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for sub in ("10_FONTES", "20_WIKI", "30_MOC", "40_DIARIO", "50_LINKS", "90_META"):
            (self.root / sub).mkdir()
        (self.root / "20_WIKI" / "Nostr.md").write_text("# Nostr\n", encoding="utf-8")
        (self.root / "30_MOC" / "MOC - Índice.md").write_text(
            "---\ntipo: moc\n---\n\n# MOC - Índice\n\n"
            "## MOCs de tema\n\n- [[MOC - PKM]] — método.\n\n## Saúde do grafo\n\nfim\n",
            encoding="utf-8",
        )
        self.vault = Vault(self.root)

    def tearDown(self):
        self._tmp.cleanup()


class TestFonte(VaultCase):
    def test_is_immutable_without_force(self):
        notes = [note("https://exemplo.com/a", "Relays", ["Nostr"])]
        self.assertEqual(self.vault.write_fonte("estudos", "2026-08-15", notes).action, "criado")
        self.assertEqual(self.vault.write_fonte("estudos", "2026-08-15", notes).action, "pulado")
        self.assertEqual(
            self.vault.write_fonte("estudos", "2026-08-15", notes, force=True).action, "criado"
        )

    def test_frontmatter_matches_schema(self):
        notes = [note("https://exemplo.com/a", "Relays", ["Nostr"])]
        result = self.vault.write_fonte("estudos", "2026-08-15", notes)
        meta, body = frontmatter.split(result.path.read_text(encoding="utf-8"))

        self.assertEqual(meta["tipo"], "fonte")
        self.assertEqual(meta["status"], "maduro")
        # O YAML do Obsidian tipa `criado` como Date — safe_load devolve date.
        self.assertEqual(str(meta["criado"]), "2026-08-15")
        self.assertEqual(meta["parte-de"], ["[[MOC - Captura Discord]]"])
        self.assertIn("https://exemplo.com/a", body)
        self.assertIn("09:12 — Fulano", body)

    def test_wikilinks_in_properties_are_quoted(self):
        self.vault.write_fonte("estudos", "2026-08-15", [note("https://e.com/a", "T", [])])
        path = self.root / "10_FONTES" / f"{fonte_name('estudos', '2026-08-15')}.md"
        raw = path.read_text(encoding="utf-8")

        self.assertIn('  - "[[MOC - Captura Discord]]"', raw)
        # Sem as aspas o YAML quebra silenciosamente — é isto que o teste trava.
        parsed = yaml.safe_load(raw.split("\n---")[0][3:])
        self.assertEqual(parsed["parte-de"], ["[[MOC - Captura Discord]]"])


class TestDiario(VaultCase):
    def test_links_only_to_existing_wiki_pages(self):
        """Sem nota atômica (LINK_NOTES=0) o bullet continua rico, como sempre foi."""
        notes = [note("https://exemplo.com/a", "Relays", ["Nostr", "Relay Nostr"])]
        text = self.vault.write_diario("estudos", "2026-08-15", notes).path.read_text(
            encoding="utf-8"
        )

        self.assertIn("[[Nostr]]", text)                      # página existe
        self.assertIn("Relay Nostr *(candidato)*", text)      # não existe: sem link pendurado
        self.assertNotIn("[[Relay Nostr]]", text)
        self.assertIn("#acao/expandir", text)

    def test_appends_to_existing_note_without_losing_content(self):
        path = self.root / "40_DIARIO" / "2026-08-15.md"
        path.write_text(
            "---\ntipo: diario\nstatus: crescendo\ncriado: 2026-08-15\n"
            "atualizado: 2026-08-15\n---\n\n# 2026-08-15\n\nAnotação do humano.\n",
            encoding="utf-8",
        )
        self.vault.write_diario("estudos", "2026-08-15", [note("https://e.com/a", "T", [])])

        text = path.read_text(encoding="utf-8")
        meta, _ = frontmatter.split(text)
        self.assertIn("Anotação do humano.", text)
        self.assertIn("## Links do Discord — #estudos", text)
        self.assertEqual(meta["relacionado"], ["[[FONTE - Discord estudos - 2026-08-15]]"])

    def test_section_is_idempotent(self):
        notes = [note("https://e.com/a", "T", [])]
        self.assertEqual(self.vault.write_diario("estudos", "2026-08-15", notes).action, "criado")
        self.assertEqual(self.vault.write_diario("estudos", "2026-08-15", notes).action, "pulado")

        forced = self.vault.write_diario("estudos", "2026-08-15", notes, force=True)
        self.assertEqual(forced.action, "atualizado")
        text = forced.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("## Links do Discord — #estudos"), 1)


class TestMoc(VaultCase):
    def test_registers_capture_once_and_indexes_itself(self):
        self.vault.ensure_moc()
        self.assertEqual(self.vault.register_moc_in_index().action, "atualizado")
        self.assertEqual(self.vault.register_moc_in_index().action, "pulado")

        index = (self.root / "30_MOC" / "MOC - Índice.md").read_text(encoding="utf-8")
        self.assertIn("[[MOC - PKM]]", index)   # não comeu o bullet que já existia
        self.assertIn("[[MOC - Captura Discord]]", index)
        self.assertIn("## Saúde do grafo", index)

        self.assertEqual(self.vault.register_capture("estudos", "2026-08-15", 3).action, "atualizado")
        self.assertEqual(self.vault.register_capture("estudos", "2026-08-15", 3).action, "pulado")


class TestDryRun(VaultCase):
    def test_writes_nothing(self):
        dry = Vault(self.root, dry_run=True)
        dry.ensure_moc()
        dry.write_fonte("estudos", "2026-08-15", [note("https://e.com/a", "T", [])])
        self.assertEqual(list((self.root / "10_FONTES").iterdir()), [])
        self.assertEqual(
            list((self.root / "30_MOC").iterdir()), [self.root / "30_MOC" / "MOC - Índice.md"]
        )


class TestFrontmatter(unittest.TestCase):
    def test_roundtrip_preserves_horizontal_rule_in_body(self):
        text = "---\ntipo: conceito\n---\n\n# T\n\ncorpo\n\n---\n\nrodapé\n"
        meta, body = frontmatter.split(text)
        self.assertEqual(meta["tipo"], "conceito")
        self.assertIn("rodapé", body)


class TestLotePosteriorNoMesmoDia(VaultCase):
    """A grade de 6h traz vários lotes por dia — todos têm que entrar.

    Regressão de um bug real: o segundo lote do dia era pulado (fonte e seção já
    existiam) mas a mensagem saía marcada como arquivada no Discord. O link
    sumia em silêncio e nunca mais voltava.
    """

    def primeiro_lote(self):
        return [note("https://exemplo.com/manha", "Da manhã", ["Nostr"], "09:00")]

    def segundo_lote(self):
        return [note("https://exemplo.com/tarde", "Da tarde", ["Obsidian"], "15:00")]

    def test_fonte_recebe_o_lote_novo(self):
        self.vault.write_fonte("estudos", "2026-08-15", self.primeiro_lote())
        r = self.vault.write_fonte("estudos", "2026-08-15", self.segundo_lote())
        self.assertEqual(r.action, "atualizado")
        texto = (self.root / "10_FONTES" / f"{fonte_name('estudos', '2026-08-15')}.md").read_text()
        self.assertIn("https://exemplo.com/manha", texto)
        self.assertIn("https://exemplo.com/tarde", texto)

    def test_fonte_mantem_o_rodape_no_fim(self):
        self.vault.write_fonte("estudos", "2026-08-15", self.primeiro_lote())
        self.vault.write_fonte("estudos", "2026-08-15", self.segundo_lote())
        texto = (self.root / "10_FONTES" / f"{fonte_name('estudos', '2026-08-15')}.md").read_text()
        self.assertLess(texto.index("https://exemplo.com/tarde"),
                        texto.index("## Páginas compiladas desta fonte"))

    def test_diario_recebe_o_lote_novo(self):
        self.vault.write_diario("estudos", "2026-08-15", self.primeiro_lote())
        r = self.vault.write_diario("estudos", "2026-08-15", self.segundo_lote())
        self.assertEqual(r.action, "atualizado")
        texto = (self.root / "40_DIARIO" / "2026-08-15.md").read_text()
        self.assertIn("Da manhã", texto)
        self.assertIn("Da tarde", texto)

    def test_diario_mantem_uma_secao_so(self):
        self.vault.write_diario("estudos", "2026-08-15", self.primeiro_lote())
        self.vault.write_diario("estudos", "2026-08-15", self.segundo_lote())
        texto = (self.root / "40_DIARIO" / "2026-08-15.md").read_text()
        self.assertEqual(texto.count("## Links do Discord — #estudos"), 1)

    def test_diario_mantem_os_candidatos_no_fim_da_secao(self):
        self.vault.write_diario("estudos", "2026-08-15", self.primeiro_lote())
        self.vault.write_diario("estudos", "2026-08-15", self.segundo_lote())
        texto = (self.root / "40_DIARIO" / "2026-08-15.md").read_text()
        self.assertLess(texto.index("Da tarde"), texto.index("#acao/expandir"))

    def test_conteudo_humano_do_dia_sobrevive(self):
        caminho = self.root / "40_DIARIO" / "2026-08-15.md"
        self.vault.write_diario("estudos", "2026-08-15", self.primeiro_lote())
        caminho.write_text(caminho.read_text() + "\n## Meu diário\n\nfui correr\n",
                           encoding="utf-8")
        self.vault.write_diario("estudos", "2026-08-15", self.segundo_lote())
        texto = caminho.read_text()
        self.assertIn("fui correr", texto)
        self.assertIn("Da tarde", texto)

    def test_o_mesmo_lote_duas_vezes_nao_duplica(self):
        self.vault.write_fonte("estudos", "2026-08-15", self.primeiro_lote())
        r = self.vault.write_fonte("estudos", "2026-08-15", self.primeiro_lote())
        self.assertEqual(r.action, "pulado")
        self.vault.write_diario("estudos", "2026-08-15", self.primeiro_lote())
        r = self.vault.write_diario("estudos", "2026-08-15", self.primeiro_lote())
        self.assertEqual(r.action, "pulado")

    def test_dry_run_nao_escreve_no_lote_posterior(self):
        from dlb.vault import Vault

        self.vault.write_fonte("estudos", "2026-08-15", self.primeiro_lote())
        antes = (self.root / "10_FONTES" / f"{fonte_name('estudos', '2026-08-15')}.md").read_text()
        Vault(self.root, dry_run=True).write_fonte("estudos", "2026-08-15", self.segundo_lote())
        depois = (self.root / "10_FONTES" / f"{fonte_name('estudos', '2026-08-15')}.md").read_text()
        self.assertEqual(antes, depois)



class TestUrlsArquivadas(VaultCase):
    """A fonte é a autoridade sobre o que já foi arquivado."""

    def test_le_as_urls_das_capturas_existentes(self):
        self.vault.write_fonte(
            "arquivar", "2026-08-15",
            [note("https://exemplo.com/a", "A", []),
             note("https://www.youtube.com/watch?v=abc123", "B", [])],
        )
        self.assertEqual(
            self.vault.archived_urls(),
            {"https://exemplo.com/a", "https://www.youtube.com/watch?v=abc123"},
        )

    def test_vault_sem_captura_devolve_conjunto_vazio(self):
        self.assertEqual(self.vault.archived_urls(), set())

    def test_ignora_fonte_que_nao_e_do_pipeline(self):
        # Fonte escrita à mão não segue o formato <url> em linha própria.
        (self.root / "10_FONTES" / "FONTE - Pesquisa manual - 2026-08-01.md").write_text(
            "---\ntipo: fonte\n---\n\n# Pesquisa\n\nVeja <https://outra.com/x> no texto.\n",
            encoding="utf-8",
        )
        self.assertEqual(self.vault.archived_urls(), set())

    def test_junta_urls_de_dias_diferentes(self):
        self.vault.write_fonte("arquivar", "2026-08-15",
                               [note("https://exemplo.com/a", "A", [])])
        self.vault.write_fonte("arquivar", "2026-08-16",
                               [note("https://exemplo.com/b", "B", [])])
        self.assertEqual(
            self.vault.archived_urls(),
            {"https://exemplo.com/a", "https://exemplo.com/b"},
        )


class TestDiarioComNotaAtomica(VaultCase):
    """Com a camada `50_LINKS/` ligada, o diário aponta em vez de repetir.

    Duas cópias da mesma prosa divergem no primeiro `--force`, e o ESQUEMA
    proíbe manter a mesma verdade em dois lugares.
    """

    def escrever(self, notes, day="2026-08-15"):
        self.vault.write_link_notes("estudos", notes)
        self.vault.write_diario("estudos", day, notes)
        return (self.root / "40_DIARIO" / f"{day}.md").read_text(encoding="utf-8")

    def test_bullet_aponta_por_wikilink_para_a_nota(self):
        texto = self.escrever([note("https://e.com/a", "Relays", [])])
        self.assertIn("- [[2026-08-15 Relays]] — Um resumo curto.", texto)

    def test_a_url_continua_no_bullet(self):
        """A chave de idempotência: `_append_diario` procura `](url)`.

        Trocar por `<url>` ou por wikilink puro faz o segundo lote do dia sumir
        em silêncio, com a mensagem já marcada como arquivada no Discord.
        """
        texto = self.escrever([note("https://e.com/a", "Relays", [])])
        self.assertIn("[abrir](https://e.com/a)", texto)
        import re
        self.assertIn(
            "https://e.com/a", re.findall(r"\]\((https?://[^)\s]+)\)", texto)
        )

    def test_a_prosa_nao_e_repetida_no_diario(self):
        texto = self.escrever([note("https://e.com/a", "Relays", [])])
        self.assertNotIn("Por que importa", texto)

    def test_candidatos_do_dia_continuam_no_diario(self):
        """Um `#acao/expandir` por dia, não um por link — o namespace afogaria."""
        texto = self.escrever([note("https://e.com/a", "Relays", ["Relay Nostr"])])
        self.assertIn("Relay Nostr", texto)
        self.assertIn("#acao/expandir", texto)

    def test_segundo_lote_do_dia_e_detectado_na_forma_nova(self):
        """A regressão que `TestLotePosteriorNoMesmoDia` trava, agora com o
        bullet magro: escrever o mesmo link duas vezes não pode duplicar."""
        manha = [note("https://e.com/manha", "Da manhã", [], "09:00")]
        self.escrever(manha)
        r = self.vault.write_diario("estudos", "2026-08-15", manha)
        self.assertEqual(r.action, "pulado")

    def test_lote_posterior_com_link_novo_entra_na_forma_nova(self):
        self.escrever([note("https://e.com/manha", "Da manhã", [], "09:00")])
        tarde = [note("https://e.com/tarde", "Da tarde", [], "15:00")]
        self.vault.write_link_notes("estudos", tarde)
        r = self.vault.write_diario("estudos", "2026-08-15", tarde)

        self.assertEqual(r.action, "atualizado")
        texto = (self.root / "40_DIARIO" / "2026-08-15.md").read_text()
        self.assertIn("[[2026-08-15 Da manhã]]", texto)
        self.assertIn("[[2026-08-15 Da tarde]]", texto)
        self.assertEqual(texto.count("## Links do Discord — #estudos"), 1)

    def test_secao_na_forma_antiga_ainda_reconhece_o_link(self):
        """Compatibilidade retroativa com os 26 dias já escritos: a URL está em
        `**[titulo](url)**` lá e em `[abrir](url)` aqui — o mesmo regex acha."""
        notes = [note("https://e.com/a", "Relays", [])]
        self.vault.write_diario("estudos", "2026-08-15", notes)   # forma rica
        self.vault.write_link_notes("estudos", notes)
        r = self.vault.write_diario("estudos", "2026-08-15", notes)
        self.assertEqual(r.action, "pulado")


class TestAutoridadeDoDedup(VaultCase):
    def test_nota_de_link_nao_conta_como_url_arquivada(self):
        """`10_FONTES/` continua a única autoridade.

        Se `50_LINKS/` contasse, apagar uma nota faria o link ser recapturado —
        e apagar é justamente o que você faz com o que não quer. O estado certo
        para isso é `triagem: descartado`, que mantém o arquivo.
        """
        self.vault.write_link_note("estudos", note("https://so-na-nota.com/x", "X", []))
        self.assertEqual(self.vault.archived_urls(), set())


class TestLinksSemNota(VaultCase):
    def test_aponta_o_link_do_diario_que_nao_tem_nota(self):
        notes = [note("https://e.com/a", "Relays", [])]
        self.vault.write_diario("estudos", "2026-08-15", notes)
        self.assertEqual(
            self.vault.links_sem_nota(), [("2026-08-15", "https://e.com/a")]
        )

    def test_depois_de_migrar_nao_sobra_pendencia(self):
        notes = [note("https://e.com/a", "Relays", [])]
        self.vault.write_link_notes("estudos", notes)
        self.vault.write_diario("estudos", "2026-08-15", notes)
        self.assertEqual(self.vault.links_sem_nota(), [])

    def test_prosa_humana_na_daily_e_invisivel(self):
        """Só seção com o marcador do pipeline conta."""
        (self.root / "40_DIARIO" / "2026-08-15.md").write_text(
            "---\ntipo: diario\n---\n\n# 2026-08-15\n\n"
            "Li o [artigo](https://humano.com/x) hoje.\n",
            encoding="utf-8",
        )
        self.assertEqual(self.vault.links_sem_nota(), [])


if __name__ == "__main__":
    unittest.main()
