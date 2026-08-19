"""Rode com: python3 -m unittest discover -s tests -t .

A fronteira do LLM na síntese, e a escrita da nota de período.

Nenhum teste aqui chama modelo: o backend é substituído por dublê, como em
`test_classify.py`. O que se trava é o que acontece com resposta suja, id
inventado, e o que sobrevive a uma regeneração.
"""

import json
import unittest

import yaml

import dlb.sintese as mod
from dlb import frontmatter
from dlb.config import Config
from dlb.corpus import ler_links
from dlb.sintese import hash_fonte

from tests.test_vault import VaultCase, note


def cfg_falso(backend="codex"):
    return Config(
        discord_token="x", discord_token_type="bot", channel_ids=["1"],
        sintese_backend=backend,
    )


RESPOSTA = {
    "panorama": "A semana foi sobre agentes.",
    "temas": [
        {"nome": "Agentes como sistema", "texto": "O que se repete é o arcabouço.",
         "links": [0, 1]},
    ],
    "conexoes": [{"texto": "A e B se encontram no contexto.", "links": [0, 1]}],
    "perguntas": ["Quanto contexto é suficiente?", "O que medir?"],
}


class SinteseCase(VaultCase):
    """Vault temporário com duas notas de link e o backend dublado."""

    def setUp(self):
        super().setUp()
        self.vault.write_link_notes(
            "estudos",
            [
                note("https://e.com/a", "Primeiro", ["Nostr"]),
                note("https://e.com/b", "Segundo", []),
            ],
        )
        self.entradas = ler_links(self.root)
        self.chamadas = []
        self._original = mod._run_codex
        mod._run_codex = self._dublê

    def tearDown(self):
        mod._run_codex = self._original
        super().tearDown()

    def _dublê(self, cfg, prompt, system):
        self.chamadas.append((prompt, system))
        return json.dumps(self.resposta)

    resposta = RESPOSTA

    def responder(self, payload):
        self.resposta = payload


class TestFronteiraDoModelo(SinteseCase):
    def test_resposta_limpa_vira_sintese(self):
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.assertEqual(s.panorama, "A semana foi sobre agentes.")
        self.assertEqual(len(s.temas), 1)
        self.assertEqual(s.temas[0].links, [0, 1])
        self.assertEqual(len(s.perguntas), 2)

    def test_resposta_cercada_em_json_ainda_e_lida(self):
        """O backend `claude-cli` não tem schema e devolve texto livre."""
        self.responder(RESPOSTA)
        mod._run_codex = lambda c, p, s: "```json\n" + json.dumps(RESPOSTA) + "\n```"
        self.assertTrue(mod.gerar(cfg_falso(), "2026-W34", self.entradas).panorama)

    def test_tagarelice_em_volta_do_json_e_descartada(self):
        mod._run_codex = lambda c, p, s: (
            "Claro! Aqui está:\n" + json.dumps(RESPOSTA) + "\nEspero que ajude."
        )
        self.assertTrue(mod.gerar(cfg_falso(), "2026-W34", self.entradas).temas)

    def test_id_fora_da_faixa_e_descartado_em_silencio(self):
        """É o que impede o modelo de apontar para link que não existe."""
        self.responder({**RESPOSTA, "temas": [
            {"nome": "T", "texto": "x", "links": [0, 7, 99, -1]}
        ]})
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.assertEqual(s.temas[0].links, [0])

    def test_tema_sem_nome_nao_entra(self):
        self.responder({**RESPOSTA, "temas": [
            {"nome": "", "texto": "x", "links": []},
            {"nome": "Vale", "texto": "y", "links": []},
        ]})
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.assertEqual([t.nome for t in s.temas], ["Vale"])

    def test_id_escrito_na_prosa_e_removido(self):
        """O modelo às vezes escreve "Links: 6, 7" apesar da instrução. Número
        interno não diz nada a quem lê a nota."""
        self.responder({**RESPOSTA, "conexoes": [
            {"texto": "A e B se ligam porque X. Links: 0, 1", "links": [0, 1]}
        ]})
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.assertEqual(s.conexoes[0].texto, "A e B se ligam porque X.")
        self.assertEqual(s.conexoes[0].links, [0, 1])

    def test_conexao_como_string_solta_ainda_e_aproveitada(self):
        """Backend sem schema pode devolver a forma antiga; perder a conexão
        seria pior que renderizá-la sem link."""
        self.responder({**RESPOSTA, "conexoes": ["A e B se encontram."]})
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.assertEqual(s.conexoes[0].texto, "A e B se encontram.")
        self.assertEqual(s.conexoes[0].links, [])

    def test_resposta_sem_json_explode_com_mensagem_util(self):
        mod._run_codex = lambda c, p, s: "não consegui fazer isso"
        with self.assertRaises(mod.SinteseError) as ctx:
            mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.assertIn("objeto JSON", str(ctx.exception))

    def test_json_invalido_explode_com_mensagem_util(self):
        mod._run_codex = lambda c, p, s: '{"panorama": "sem fechar'
        with self.assertRaises(mod.SinteseError):
            mod.gerar(cfg_falso(), "2026-W34", self.entradas)

    def test_backend_desconhecido_explode(self):
        with self.assertRaises(mod.SinteseError) as ctx:
            mod.gerar(cfg_falso("gemini"), "2026-W34", self.entradas)
        self.assertIn("SINTESE_BACKEND", str(ctx.exception))

    def test_periodo_vazio_explode_antes_de_gastar_chamada(self):
        with self.assertRaises(mod.SinteseError):
            mod.gerar(cfg_falso(), "2026-W34", [])
        self.assertEqual(self.chamadas, [])


class TestPrompt(SinteseCase):
    def test_a_url_nao_vai_para_o_modelo(self):
        """Assim ele não tem como emitir endereço: todo link do texto final é
        montado por nós, a partir dos dados."""
        mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        prompt, _ = self.chamadas[0]
        self.assertNotIn("https://e.com/a", prompt)
        self.assertIn('"id": 0', prompt)

    def test_o_prompt_leva_titulo_resumo_e_conceitos(self):
        mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        prompt, _ = self.chamadas[0]
        self.assertIn("Primeiro", prompt)
        self.assertIn("Um resumo curto.", prompt)
        self.assertIn("Nostr", prompt)


class TestHash(SinteseCase):
    def test_mesmo_material_mesmo_hash(self):
        self.assertEqual(hash_fonte(self.entradas), hash_fonte(self.entradas))

    def test_link_novo_muda_o_hash(self):
        antes = hash_fonte(self.entradas)
        self.vault.write_link_note("estudos", note("https://e.com/c", "Terceiro", []))
        self.assertNotEqual(antes, hash_fonte(ler_links(self.root)))

    def test_a_ordem_das_entradas_nao_muda_o_hash(self):
        self.assertEqual(
            hash_fonte(self.entradas), hash_fonte(list(reversed(self.entradas)))
        )


class TestEscritaDaNota(SinteseCase):
    def escrever(self, rotulo="2026-W34", fechada=False, **kw):
        s = mod.gerar(cfg_falso(), rotulo, self.entradas)
        r = self.vault.write_sintese(
            rotulo, s, self.entradas, fechada=fechada, **kw
        )
        return r, r.path.read_text(encoding="utf-8")

    def test_nome_do_arquivo_segue_a_convencao(self):
        r, _ = self.escrever()
        self.assertEqual(r.path.name, "Semanal 2026-W34.md")
        self.assertEqual(r.path.parent.name, "40_DIARIO")

    def test_rollup_mensal_tem_o_outro_prefixo(self):
        self.assertEqual(
            self.vault.sintese_path("2026-08").name, "Mensal 2026-08.md"
        )

    def test_frontmatter_obedece_o_vocabulario_fechado(self):
        """`tipo: diario` — não se abriu vocabulário novo para um único uso."""
        _, texto = self.escrever()
        meta, _ = frontmatter.split(texto)
        self.assertEqual(meta["tipo"], "diario")
        self.assertEqual(meta["status"], "crescendo")
        self.assertEqual(meta["tags"], ["area/pkm"])

    def test_semana_fechada_nasce_madura(self):
        _, texto = self.escrever(fechada=True)
        self.assertEqual(frontmatter.split(texto)[0]["status"], "maduro")

    def test_wikilink_em_propriedade_sai_entre_aspas(self):
        _, texto = self.escrever(relacionado=['[[2026-08-15]]'])
        self.assertIn('  - "[[MOC - Captura Discord]]"', texto)
        meta = yaml.safe_load(texto.split("\n---")[0][3:])
        self.assertEqual(meta["parte-de"], ["[[MOC - Captura Discord]]"])
        self.assertIn("[[2026-08-15]]", meta["relacionado"])

    def test_tem_aresta_saindo(self):
        """Invariante 2: `parte-de` para o MOC é incondicional."""
        _, texto = self.escrever()
        self.assertTrue(frontmatter.split(texto)[0]["parte-de"])

    def test_o_link_do_tema_e_montado_por_nos(self):
        _, texto = self.escrever()
        self.assertIn("[[2026-08-15 Primeiro]]", texto)
        self.assertIn("[[2026-08-15 Segundo]]", texto)

    def test_a_conexao_rende_wikilink_e_nao_numero(self):
        _, texto = self.escrever()
        secao = texto.split("### Conexões", 1)[1]
        self.assertIn("[[2026-08-15 Primeiro]]", secao)
        self.assertNotIn("Links: 0", secao)

    def test_perguntas_ganham_acao_revisar(self):
        _, texto = self.escrever()
        self.assertIn("#acao/revisar", texto)

    def test_dry_run_nao_escreve(self):
        self.vault.dry_run = True
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        r = self.vault.write_sintese("2026-W34", s, self.entradas)
        self.assertEqual(r.action, "criado")       # relata o que faria
        self.assertFalse(r.path.exists())          # e não faz


class TestRegeneracao(SinteseCase):
    def setUp(self):
        super().setUp()
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.r = self.vault.write_sintese("2026-W34", s, self.entradas)

    def test_o_hash_e_gravado_na_cerca(self):
        self.assertEqual(
            self.vault.sintese_hash("2026-W34"), hash_fonte(self.entradas)
        )

    def test_texto_humano_fora_da_cerca_sobrevive(self):
        self.r.path.write_text(
            self.r.path.read_text(encoding="utf-8") + "\nMinha leitura própria.\n",
            encoding="utf-8",
        )
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.vault.write_sintese("2026-W34", s, self.entradas)
        texto = self.r.path.read_text(encoding="utf-8")
        self.assertEqual(texto.count("Minha leitura própria."), 1)

    def test_regenerar_nao_duplica_o_miolo(self):
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.vault.write_sintese("2026-W34", s, self.entradas)
        texto = self.r.path.read_text(encoding="utf-8")
        self.assertEqual(texto.count("### Temas"), 1)
        self.assertEqual(texto.count("<!-- /dlb:sintese -->"), 1)

    def test_nota_feita_a_mao_recebe_a_cerca_em_vez_de_ser_sobrescrita(self):
        self.r.path.write_text(
            "---\ntipo: diario\n---\n\n# Semanal 2026-W34\n\nescrevi à mão\n",
            encoding="utf-8",
        )
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.vault.write_sintese("2026-W34", s, self.entradas)
        texto = self.r.path.read_text(encoding="utf-8")
        self.assertIn("escrevi à mão", texto)
        self.assertIn("### Temas", texto)

    def test_o_bloco_e_recuperavel_para_o_rollup_mensal(self):
        """O mês se faz das semanas, não dos links crus."""
        bloco = self.vault.sintese_bloco("2026-W34")
        self.assertIn("### Temas", bloco)
        self.assertNotIn("dlb:sintese", bloco)

    def test_bloco_de_semana_inexistente_e_vazio(self):
        self.assertEqual(self.vault.sintese_bloco("1999-W01"), "")


class TestRegistroNoMoc(SinteseCase):
    def test_a_sintese_e_listada_no_moc(self):
        """Invariante 3: sem isso ela nasce órfã."""
        self.vault.ensure_moc()
        r = self.vault.register_sintese("2026-W34")
        self.assertEqual(r.action, "atualizado")
        texto = r.path.read_text(encoding="utf-8")
        self.assertIn("## Sínteses de período", texto)
        self.assertIn("[[Semanal 2026-W34]]", texto)

    def test_registrar_duas_vezes_nao_duplica(self):
        self.vault.ensure_moc()
        self.vault.register_sintese("2026-W34")
        r = self.vault.register_sintese("2026-W34")
        self.assertEqual(r.action, "pulado")
        self.assertEqual(
            r.path.read_text(encoding="utf-8").count("[[Semanal 2026-W34]]"), 1
        )

    def test_duas_sinteses_ficam_na_mesma_secao(self):
        self.vault.ensure_moc()
        self.vault.register_sintese("2026-W33")
        r = self.vault.register_sintese("2026-W34")
        texto = r.path.read_text(encoding="utf-8")
        self.assertEqual(texto.count("## Sínteses de período"), 1)
        self.assertIn("[[Semanal 2026-W33]]", texto)
        self.assertIn("[[Semanal 2026-W34]]", texto)

    def test_sem_moc_nao_explode(self):
        r = self.vault.register_sintese("2026-W34")
        self.assertEqual(r.action, "pulado")


class TestRollupMensal(SinteseCase):
    def test_o_mes_consome_as_semanas_e_nao_os_links(self):
        s = mod.gerar(cfg_falso(), "2026-W34", self.entradas)
        self.vault.write_sintese("2026-W34", s, self.entradas)
        blocos = {"2026-W34": self.vault.sintese_bloco("2026-W34")}

        mod._run_codex = self._dublê
        mod.gerar_mes(cfg_falso(), "2026-08", blocos)
        prompt, system = self.chamadas[-1]
        self.assertIn("2026-W34", prompt)
        self.assertIn("### Temas", prompt)
        self.assertIn("rollup mensal", system)

    def test_mes_sem_semanal_explode_antes_de_gastar_chamada(self):
        self.chamadas.clear()
        with self.assertRaises(mod.SinteseError):
            mod.gerar_mes(cfg_falso(), "2026-08", {})
        self.assertEqual(self.chamadas, [])


if __name__ == "__main__":
    unittest.main()
