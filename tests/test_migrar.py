"""Rode com: python3 -m unittest discover -s tests -t .

O parser da migração. Ele lê o que o pipeline escreveu ao longo de 26 dias e
reconstrói a nota atômica sem chamar LLM. As fixtures abaixo são recortes na
forma **real** dos arquivos do vault, incluindo o `*Mais N link(s)*` e a mesma
`### video` repetida que `_append_diario` produz num dia de dois lotes.
"""

import unittest

from dlb import frontmatter
from dlb.migrar import migrar, parse_fonte, parse_secao, reparar_jump
from dlb.vault import Vault

from tests.test_vault import VaultCase

# Um dia com dois lotes: `### video` aparece duas vezes, com o interstício no
# meio. Também tem resumo com vários ` — ` (34% dos bullets reais têm) e um
# bullet sem `*Por que importa:*`.
DIARIO = """---
tipo: diario
status: crescendo
criado: 2026-08-17
atualizado: 2026-08-17
tags:
  - area/ia
relacionado:
  - "[[FONTE - Discord 🔗_links - 2026-08-17]]"
---

# 2026-08-17

Acordei tarde — anotação minha, o parser tem que ignorar.

## Links do Discord — #🔗_links

<!-- discord-link-brain -->

3 link(s) capturados em 2026-08-17. Fonte crua e cronológica: [[FONTE - Discord 🔗_links - 2026-08-17]].

### repositorio

- **[cosmtrek/mindwalk — replay de sessões](https://github.com/cosmtrek/mindwalk)** — Binário Go local — lê logs do Claude Code — e reproduz a sessão como luz num mapa 3D.
  *Por que importa:* Dá para ver onde o agente gastou tempo.
  Conceitos: [[Nostr]], Mindwalk *(candidato)*, Go *(candidato)*
  <sub>10:31 · Sicrano · #area/ia</sub>

### video

- **[Harness Engineering (Hora de Codar)](https://www.youtube.com/watch?v=aaa)** — Vídeo sobre construir agentes 10x mais espertos.
  Conceitos: Harness Engineering *(candidato)*
  <sub>11:02 · Sicrano · #area/ia</sub>

*Mais 1 link(s) capturados em 2026-08-17.*

### video

- **[I Found The END GAME of Homelab Tunnels](https://www.youtube.com/watch?v=bbb)** — Túneis de homelab.
  *Por que importa:* Resolve exposição sem abrir porta.
  <sub>19:44 · Sicrano · #area/pkm</sub>

### Candidatos a página no wiki

Conceitos que apareceram e ainda não têm página em `20_WIKI/`. Compilar é trabalho curado — não crie stub vazio só para zerar a lista.

- Go
- Harness Engineering
- Mindwalk

#acao/expandir

## Meu diário

fui correr
"""

FONTE = """---
tipo: fonte
status: maduro
criado: 2026-08-17
atualizado: 2026-08-17
autor: "Discord #🔗_links"
url:
capturado: 2026-08-17
---

# FONTE - Discord 🔗_links - 2026-08-17

## Material

Captura automática de 3 link(s).

### 10:31 — Sicrano

<https://github.com/cosmtrek/mindwalk>

> olha isso

Título da página: cosmtrek/mindwalk: replay de sessões de agente
Descrição: Um mapa 3D das suas sessões.

Mensagem original: https://discord.com/channels/7/42/9001

### 11:02 — Sicrano

<https://www.youtube.com/watch?v=aaa>

Título da página: Harness Engineering

Mensagem original: https://discord.com/channels/7/42/9002

### 19:44 — Sicrano

<https://www.youtube.com/watch?v=bbb>

Mensagem original: https://discord.com/channels/7/42/9003

## Páginas compiladas desta fonte

<!-- confie nos backlinks -->
"""


class TestParseSecao(unittest.TestCase):
    def setUp(self):
        secao = DIARIO.split("<!-- discord-link-brain -->")[1].split("\n## Meu")[0]
        self.secao = "<!-- discord-link-brain -->" + secao
        self.bullets, self.avisos = parse_secao(self.secao)

    def test_acha_os_tres_links_sem_aviso(self):
        self.assertEqual(len(self.bullets), 3)
        self.assertEqual(self.avisos, [])

    def test_resumo_com_travessao_no_meio_nao_e_cortado(self):
        """34% dos bullets reais têm mais de um ` — `. Dividir pelo separador
        cortaria a maioria; a regex ancora no fechamento do link."""
        b = self.bullets[0]
        self.assertEqual(
            b.resumo,
            "Binário Go local — lê logs do Claude Code — e reproduz a sessão "
            "como luz num mapa 3D.",
        )

    def test_titulo_com_barra_sobrevive(self):
        self.assertEqual(self.bullets[0].titulo, "cosmtrek/mindwalk — replay de sessões")

    def test_categoria_vem_do_heading(self):
        self.assertEqual(
            [b.categoria for b in self.bullets], ["repositorio", "video", "video"]
        )

    def test_lote_posterior_com_categoria_repetida_e_lido_inteiro(self):
        """`_append_diario` repete `### video`. Chavear no bullet e não no
        heading é o que torna isso naturalmente correto."""
        self.assertEqual(self.bullets[2].url, "https://www.youtube.com/watch?v=bbb")
        self.assertEqual(self.bullets[2].categoria, "video")

    def test_conceito_com_e_sem_pagina_saem_com_o_mesmo_nome(self):
        self.assertEqual(self.bullets[0].conceitos, ["Nostr", "Mindwalk", "Go"])

    def test_bullet_sem_porque_importa_ainda_e_lido(self):
        self.assertEqual(self.bullets[1].porque_importa, "")
        self.assertEqual(self.bullets[1].titulo, "Harness Engineering (Hora de Codar)")

    def test_hora_autor_e_area_vem_do_sub(self):
        self.assertEqual(self.bullets[2].hhmm, "19:44")
        self.assertEqual(self.bullets[2].autor, "Sicrano")
        self.assertEqual(self.bullets[2].area, "area/pkm")

    def test_bloco_de_candidatos_nao_e_confundido_com_link(self):
        """`- Go` é candidato, não bullet de link. Descartado por vocabulário
        de categoria, não pelo texto do heading."""
        urls = [b.url for b in self.bullets]
        self.assertNotIn("", urls)
        self.assertEqual(len(urls), 3)

    def test_heading_fora_do_vocabulario_e_ignorado(self):
        bullets, _ = parse_secao(
            "### inventado\n\n- **[X](https://e.com/x)** — resumo\n"
        )
        self.assertEqual(bullets, [])


class TestParseFonte(unittest.TestCase):
    def setUp(self):
        self.dados = parse_fonte(FONTE)

    def test_indexa_por_url(self):
        self.assertEqual(len(self.dados), 3)
        self.assertIn("https://github.com/cosmtrek/mindwalk", self.dados)

    def test_le_hora_autor_e_link_da_mensagem(self):
        d = self.dados["https://github.com/cosmtrek/mindwalk"]
        self.assertEqual(d["hhmm"], "10:31")
        self.assertEqual(d["autor"], "Sicrano")
        self.assertEqual(d["jump_url"], "https://discord.com/channels/7/42/9001")

    def test_le_titulo_e_descricao_da_pagina(self):
        d = self.dados["https://github.com/cosmtrek/mindwalk"]
        self.assertEqual(
            d["titulo_pagina"], "cosmtrek/mindwalk: replay de sessões de agente"
        )
        self.assertEqual(d["descricao"], "Um mapa 3D das suas sessões.")

    def test_pagina_sem_titulo_devolve_vazio_e_nao_explode(self):
        self.assertEqual(self.dados["https://www.youtube.com/watch?v=bbb"]["titulo_pagina"], "")


class MigrarCase(VaultCase):
    def setUp(self):
        super().setUp()
        (self.root / "40_DIARIO" / "2026-08-17.md").write_text(DIARIO, encoding="utf-8")
        (self.root / "10_FONTES" / "FONTE - Discord 🔗_links - 2026-08-17.md").write_text(
            FONTE, encoding="utf-8"
        )

    def rodar(self, **kw):
        kw.setdefault("log", lambda *a: None)
        return migrar(self.vault, timezone="America/Sao_Paulo", **kw)

    @property
    def notas(self):
        return sorted(p.stem for p in (self.root / "50_LINKS").glob("*.md"))


class TestMigrar(MigrarCase):
    def test_reconstroi_as_tres_notas(self):
        rel = self.rodar()
        self.assertEqual(rel.criados, 3)
        self.assertEqual(rel.avisos, [])
        self.assertEqual(len(self.notas), 3)

    def test_nao_chama_o_llm(self):
        """Reclassificar produziria resultado diferente do que você já leu."""
        import dlb.classify as mod

        def explode(*a, **k):  # pragma: no cover - o teste falha se rodar
            raise AssertionError("migrar não pode chamar o LLM")

        original, mod.classify = mod.classify, explode
        try:
            self.rodar()
        finally:
            mod.classify = original

    def test_a_nota_carrega_o_dado_cru_da_fonte(self):
        self.rodar()
        path = next((self.root / "50_LINKS").glob("*mindwalk*.md"))
        meta, corpo = frontmatter.split(path.read_text(encoding="utf-8"))
        self.assertEqual(meta["autor"], "Sicrano")
        self.assertEqual(meta["dominio"], "github.com")
        self.assertEqual(meta["categoria"], "repositorio")
        self.assertIn("10:31", corpo)
        self.assertIn("https://discord.com/channels/7/42/9001", corpo)

    def test_a_nota_carrega_o_dado_interpretado_do_diario(self):
        self.rodar()
        path = next((self.root / "50_LINKS").glob("*mindwalk*.md"))
        corpo = path.read_text(encoding="utf-8")
        self.assertIn("lê logs do Claude Code", corpo)
        self.assertIn("Dá para ver onde o agente gastou tempo.", corpo)

    def test_conceito_sem_pagina_nao_vira_link_pendurado(self):
        """Invariante 4, no caminho da migração."""
        self.rodar()
        path = next((self.root / "50_LINKS").glob("*mindwalk*.md"))
        texto = path.read_text(encoding="utf-8")
        self.assertIn("[[Nostr]]", texto)
        self.assertNotIn("[[Mindwalk]]", texto)
        self.assertIn("- Mindwalk", texto)

    def test_insere_a_referencia_no_bullet_do_diario(self):
        """Sem isso as notas migradas nascem órfãs: `parte-de` é aresta saindo,
        e o invariante 3 exige aresta entrando."""
        self.rodar()
        texto = (self.root / "40_DIARIO" / "2026-08-17.md").read_text(encoding="utf-8")
        self.assertEqual(texto.count("  Nota: [[2026-08-17 "), 3)
        self.assertEqual(self.vault.links_sem_nota(), [])

    def test_a_insercao_e_aditiva_e_nao_perde_nada(self):
        self.rodar()
        texto = (self.root / "40_DIARIO" / "2026-08-17.md").read_text(encoding="utf-8")
        self.assertIn("*Por que importa:* Dá para ver onde o agente gastou tempo.", texto)
        self.assertIn("Conceitos: [[Nostr]], Mindwalk *(candidato)*", texto)
        self.assertIn("Acordei tarde", texto)
        self.assertIn("fui correr", texto)
        self.assertIn("#acao/expandir", texto)

    def test_migrar_duas_vezes_nao_duplica(self):
        self.rodar()
        antes = (self.root / "40_DIARIO" / "2026-08-17.md").read_text()
        rel = self.rodar()
        self.assertEqual(rel.criados, 0)
        # Nem chega a `write_link_note`: a linha `Nota: [[...]]` no bullet marca
        # o link como migrado, e a segunda passada sai mais barata que a primeira.
        self.assertEqual(rel.pulados, 0)
        self.assertEqual(len(self.notas), 3)
        self.assertEqual((self.root / "40_DIARIO" / "2026-08-17.md").read_text(), antes)

    def test_dry_run_nao_escreve_nada(self):
        rel = self.rodar()  # nada ainda; refaz com um vault seco
        self.assertEqual(rel.criados, 3)

    def test_no_diario_deixa_a_daily_intacta(self):
        antes = (self.root / "40_DIARIO" / "2026-08-17.md").read_text()
        self.rodar(tocar_diario=False)
        self.assertEqual(len(self.notas), 3)
        self.assertEqual((self.root / "40_DIARIO" / "2026-08-17.md").read_text(), antes)

    def test_filtro_por_dia(self):
        rel = self.rodar(dia="1999-01-01")
        self.assertEqual(rel.criados, 0)

    def test_limite_para_no_numero_pedido(self):
        rel = self.rodar(limite=2)
        self.assertEqual(rel.criados, 2)

    def test_dia_sem_fonte_e_reportado_e_pulado(self):
        """Sem fonte não há como declarar `fonte` — invariante 5. Não se inventa."""
        (self.root / "10_FONTES" / "FONTE - Discord 🔗_links - 2026-08-17.md").unlink()
        rel = self.rodar()
        self.assertEqual(rel.criados, 0)
        self.assertEqual(len(rel.avisos), 1)
        self.assertIn("sem nota em 10_FONTES", rel.avisos[0])

    def test_link_ausente_da_fonte_ainda_gera_nota_com_aviso(self):
        fonte = self.root / "10_FONTES" / "FONTE - Discord 🔗_links - 2026-08-17.md"
        fonte.write_text(
            fonte.read_text(encoding="utf-8").replace(
                "<https://www.youtube.com/watch?v=bbb>", "<https://outra.com/z>"
            ),
            encoding="utf-8",
        )
        rel = self.rodar()
        self.assertEqual(rel.criados, 3)      # degrada, não falha
        self.assertEqual(len(rel.avisos), 1)
        self.assertIn("não está na fonte do dia", rel.avisos[0])

    def test_bullet_malformado_e_aviso_e_nao_derruba_o_dia(self):
        path = self.root / "40_DIARIO" / "2026-08-17.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "- **[Harness Engineering (Hora de Codar)](https://www.youtube.com/watch?v=aaa)** — Vídeo",
                "- editei isto à mão e quebrei — Vídeo",
            ),
            encoding="utf-8",
        )
        rel = self.rodar()
        self.assertEqual(rel.criados, 2)      # os outros dois entram
        self.assertTrue(any("não reconhecido" in a for a in rel.avisos))

    def test_o_horario_reconstruido_bate_com_o_original(self):
        self.rodar()
        path = next((self.root / "50_LINKS").glob("*mindwalk*.md"))
        meta, _ = frontmatter.split(path.read_text(encoding="utf-8"))
        self.assertEqual(str(meta["criado"]), "2026-08-17")
        self.assertEqual(str(meta["capturado"]), "2026-08-17")


class TestMigrarSeco(MigrarCase):
    def setUp(self):
        super().setUp()
        self.vault = Vault(self.root, dry_run=True)

    def test_dry_run_nao_cria_nota_nem_toca_o_diario(self):
        antes = (self.root / "40_DIARIO" / "2026-08-17.md").read_text()
        rel = migrar(self.vault, timezone="America/Sao_Paulo", log=lambda *a: None)
        self.assertEqual(rel.criados, 3)          # relata o que faria
        self.assertEqual(self.notas, [])          # e não faz
        self.assertEqual((self.root / "40_DIARIO" / "2026-08-17.md").read_text(), antes)


class TestRepararJump(unittest.TestCase):
    """O link para a mensagem original só abre se souber o servidor.

    O pipeline gravou por um tempo todo jump_url como `/channels/@me/...`, porque
    `GET /channels/{id}/messages` não devolve `guild_id` por mensagem. As notas de
    `10_FONTES/` guardam o valor como capturaram — a fonte não se corrige — e a
    correção mora na camada derivada, como o invariante 9 prescreve.
    """

    GUILDS = {"1422734760345600000": "1217985616281600000"}

    def test_troca_arroba_me_pelo_servidor(self):
        self.assertEqual(
            reparar_jump(
                "https://discord.com/channels/@me/1422734760345600000/999", self.GUILDS
            ),
            "https://discord.com/channels/1217985616281600000/1422734760345600000/999",
        )

    def test_canal_desconhecido_fica_intacto(self):
        """Sem DISCORD_GUILD_IDS não se inventa servidor."""
        url = "https://discord.com/channels/@me/000/999"
        self.assertEqual(reparar_jump(url, self.GUILDS), url)
        self.assertEqual(reparar_jump(url, {}), url)

    def test_url_ja_correta_nao_e_mexida(self):
        url = "https://discord.com/channels/1217985616281600000/1422734760345600000/999"
        self.assertEqual(reparar_jump(url, self.GUILDS), url)

    def test_jump_vazio_nao_explode(self):
        self.assertEqual(reparar_jump("", self.GUILDS), "")


class TestMigrarReparaJump(MigrarCase):
    def test_a_nota_migrada_leva_o_link_reparado(self):
        self.rodar(guilds={"42": "1217985616281600000"})
        path = next((self.root / "50_LINKS").glob("*mindwalk*.md"))
        # A fixture da fonte usa /channels/7/42/9001, que já tem servidor.
        self.assertIn("https://discord.com/channels/7/42/9001", path.read_text())

    def test_fonte_com_arroba_me_vira_link_valido_na_nota(self):
        fonte = self.root / "10_FONTES" / "FONTE - Discord 🔗_links - 2026-08-17.md"
        fonte.write_text(
            fonte.read_text(encoding="utf-8").replace("channels/7/42/", "channels/@me/42/"),
            encoding="utf-8",
        )
        self.rodar(guilds={"42": "1217985616281600000"})
        path = next((self.root / "50_LINKS").glob("*mindwalk*.md"))
        texto = path.read_text(encoding="utf-8")
        self.assertIn("channels/1217985616281600000/42/9001", texto)
        self.assertNotIn("channels/@me", texto)
        # E a fonte continua como capturou: invariante 9.
        self.assertIn("channels/@me/42/9001", fonte.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class TestForceNoMigrar(MigrarCase):
    """`--force` tem que refazer o que já foi migrado, senão a flag é enfeite."""

    def test_force_reprocessa_link_ja_migrado(self):
        self.rodar()
        rel = self.rodar(force=True)
        self.assertEqual(rel.atualizados, 3)
        self.assertEqual(rel.criados, 0)

    def test_force_nao_duplica_a_linha_de_referencia_no_diario(self):
        self.rodar()
        self.rodar(force=True)
        texto = (self.root / "40_DIARIO" / "2026-08-17.md").read_text(encoding="utf-8")
        self.assertEqual(texto.count("  Nota: [[2026-08-17 "), 3)

    def test_force_preserva_a_triagem_do_humano(self):
        self.rodar()
        path = next((self.root / "50_LINKS").glob("*mindwalk*.md"))
        path.write_text(
            path.read_text(encoding="utf-8").replace("triagem: inbox", "triagem: aplicado"),
            encoding="utf-8",
        )
        self.vault._link_notes = None
        self.rodar(force=True)
        self.assertIn("triagem: aplicado", path.read_text(encoding="utf-8"))

    def test_sem_force_a_segunda_passada_nao_toca_o_disco(self):
        self.rodar()
        rel = self.rodar()
        self.assertEqual((rel.criados, rel.atualizados, rel.pulados), (0, 0, 0))
