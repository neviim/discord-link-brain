"""Rode com: python3 -m unittest discover -s tests -t .

A saída do LLM é a única entrada do pipeline que ninguém controla: o modelo
pode cercar em ```json, tagarelar antes do array, inventar categoria fora do
vocabulário ou simplesmente pular um link. Cada um desses casos vira nota no
segundo cérebro de alguém, então cada um tem teste aqui.
"""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from dlb.classify import (
    ClassifyError,
    _fallback_notes,
    _link_payload,
    _parse_json_array,
    build_prompt,
)
from dlb.models import AREAS, CATEGORIAS, LinkHit, PageMeta

TZ = ZoneInfo("America/Sao_Paulo")


def hit(url="https://exemplo.com/a", context="olha isso", author="Fulano"):
    return LinkHit(
        url=url,
        channel_id="42",
        message_id="1",
        author=author,
        posted_at=datetime(2026, 8, 15, 9, 12, tzinfo=TZ),
        context=context,
        jump_url="https://discord.com/channels/7/42/1",
    )


class TestParseJsonArray(unittest.TestCase):
    def test_array_limpo(self):
        self.assertEqual(_parse_json_array('[{"id": 0}]'), [{"id": 0}])

    def test_cercado_em_bloco_de_codigo(self):
        bruto = '```json\n[{"id": 0, "titulo": "A"}]\n```'
        self.assertEqual(_parse_json_array(bruto)[0]["titulo"], "A")

    def test_cerca_sem_a_palavra_json(self):
        self.assertEqual(_parse_json_array('```\n[{"id": 1}]\n```'), [{"id": 1}])

    def test_tagarelice_antes_e_depois(self):
        # "Claro! Aqui está: [...] Espero ter ajudado."
        bruto = 'Claro! Aqui está:\n[{"id": 0}]\nEspero ter ajudado.'
        self.assertEqual(_parse_json_array(bruto), [{"id": 0}])

    def test_colchetes_dentro_dos_valores_nao_confundem(self):
        # find("[") e rfind("]") precisam pegar o array externo, não um interno.
        bruto = '[{"id": 0, "conceitos": ["Obsidian", "Nostr"]}]'
        self.assertEqual(_parse_json_array(bruto)[0]["conceitos"], ["Obsidian", "Nostr"])

    def test_resposta_sem_array_explode_com_mensagem_util(self):
        with self.assertRaises(ClassifyError) as ctx:
            _parse_json_array("Desculpe, não consegui classificar.")
        self.assertIn("não tem array JSON", str(ctx.exception))

    def test_json_quebrado_nao_passa_silenciosamente(self):
        import json

        with self.assertRaises(json.JSONDecodeError):
            _parse_json_array('[{"id": 0, "titulo": }]')


class TestPayloadDoPrompt(unittest.TestCase):
    """O que entra no prompt: campo vazio é token pago à toa."""

    def test_campos_vazios_ficam_de_fora(self):
        item = _link_payload([hit()], {})[0]
        for ausente in ("texto_da_pagina", "canal", "autor", "tipo"):
            self.assertNotIn(ausente, item)

    def test_texto_da_pagina_entra_quando_existe(self):
        metas = {"https://exemplo.com/a": PageMeta(text="O artigo defende que…")}
        item = _link_payload([hit()], metas)[0]
        self.assertEqual(item["texto_da_pagina"], "O artigo defende que…")

    def test_youtube_manda_canal_e_tipo(self):
        url = "https://www.youtube.com/watch?v=abc"
        metas = {url: PageMeta(kind="youtube", author="Canal do Fulano")}
        item = _link_payload([hit(url=url)], metas)[0]
        self.assertEqual(item["tipo"], "video do YouTube")
        self.assertEqual(item["canal"], "Canal do Fulano")
        self.assertNotIn("autor", item)

    def test_site_comum_manda_autor_nao_canal(self):
        metas = {"https://exemplo.com/a": PageMeta(kind="html", author="Sicrana")}
        item = _link_payload([hit()], metas)[0]
        self.assertEqual(item["autor"], "Sicrana")
        self.assertNotIn("canal", item)

    def test_pagina_ilegivel_e_sinalizada_ao_modelo(self):
        # É o que faz o LLM admitir que não leu em vez de inventar.
        metas = {"https://exemplo.com/a": PageMeta(legivel=False)}
        self.assertFalse(_link_payload([hit()], metas)[0]["pagina_legivel"])

    def test_prompt_carrega_o_vocabulario_fechado(self):
        texto = build_prompt([hit()], {})
        for categoria in CATEGORIAS:
            self.assertIn(categoria, texto)
        for area in AREAS:
            self.assertIn(area, texto)

    def test_ids_sao_a_ordem_dos_links(self):
        hits = [hit(url=f"https://exemplo.com/{n}") for n in range(3)]
        self.assertEqual([i["id"] for i in _link_payload(hits, {})], [0, 1, 2])


class TestNotaDeFallback(unittest.TestCase):
    """Quando o LLM não ajuda, o link ainda tem que virar nota identificável."""

    def test_usa_titulo_da_pagina_quando_existe(self):
        metas = {"https://exemplo.com/a": PageMeta(title="Um Título", description="Uma desc")}
        nota = _fallback_notes([hit()], metas)[0]
        self.assertEqual(nota.titulo, "Um Título")
        self.assertEqual(nota.resumo, "Uma desc")

    def test_sem_metadados_cai_no_dominio_e_no_comentario(self):
        nota = _fallback_notes([hit(context="isso aqui é ouro")], {})[0]
        self.assertEqual(nota.titulo, "exemplo.com")
        self.assertEqual(nota.resumo, "isso aqui é ouro")

    def test_sem_nada_ainda_assim_nao_fica_vazio(self):
        nota = _fallback_notes([hit(context="")], {})[0]
        self.assertTrue(nota.resumo)

    def test_cai_no_vocabulario_neutro(self):
        nota = _fallback_notes([hit()], {})[0]
        self.assertIn(nota.categoria, CATEGORIAS)
        self.assertIn(nota.area, AREAS)



def cfg_falso(backend="claude-cli"):
    from pathlib import Path

    from dlb.config import Config

    return Config(
        discord_token="x", discord_token_type="bot", channel_ids=["42"],
        vault_path=Path("/tmp"), llm_backend=backend,
    )


class TestClassifyComLlmFalso(unittest.TestCase):
    """O modelo é criativo; o vocabulário do ESQUEMA não pode ser."""

    def setUp(self):
        import dlb.classify as mod

        self.mod = mod
        self._original = mod._run_claude_cli

    def tearDown(self):
        self.mod._run_claude_cli = self._original

    def responder(self, payload):
        import json as _json

        self.mod._run_claude_cli = lambda cfg, prompt: _json.dumps(payload)

    def test_categoria_inventada_cai_para_outro(self):
        self.responder([{"id": 0, "titulo": "T", "categoria": "podcast-de-nicho",
                         "area": "area/ia"}])
        nota = self.mod.classify(cfg_falso(), [hit()], {})[0]
        self.assertEqual(nota.categoria, "outro")

    def test_area_inventada_cai_para_area_ia(self):
        self.responder([{"id": 0, "titulo": "T", "categoria": "artigo",
                         "area": "area/culinaria"}])
        nota = self.mod.classify(cfg_falso(), [hit()], {})[0]
        self.assertEqual(nota.area, "area/ia")

    def test_categoria_valida_e_respeitada(self):
        self.responder([{"id": 0, "titulo": "T", "categoria": "paper", "area": "area/pkm"}])
        nota = self.mod.classify(cfg_falso(), [hit()], {})[0]
        self.assertEqual((nota.categoria, nota.area), ("paper", "area/pkm"))

    def test_link_pulado_pelo_modelo_ainda_vira_nota(self):
        # O modelo devolveu só o id 1. O link 0 não pode sumir do vault.
        hits = [hit(url="https://exemplo.com/0"), hit(url="https://exemplo.com/1")]
        self.responder([{"id": 1, "titulo": "Segundo", "categoria": "artigo",
                         "area": "area/ia"}])
        notas = self.mod.classify(cfg_falso(), hits, {})
        self.assertEqual(len(notas), 2)
        self.assertEqual(notas[0].titulo, "exemplo.com")   # fallback
        self.assertEqual(notas[1].titulo, "Segundo")

    def test_ordem_das_notas_segue_a_dos_links_nao_a_do_modelo(self):
        hits = [hit(url="https://a.com/1"), hit(url="https://b.com/2")]
        self.responder([
            {"id": 1, "titulo": "B", "categoria": "artigo", "area": "area/ia"},
            {"id": 0, "titulo": "A", "categoria": "artigo", "area": "area/ia"},
        ])
        notas = self.mod.classify(cfg_falso(), hits, {})
        self.assertEqual([n.titulo for n in notas], ["A", "B"])

    def test_conceitos_limitados_a_quatro_e_sem_colchetes(self):
        self.responder([{"id": 0, "titulo": "T", "categoria": "artigo", "area": "area/ia",
                         "conceitos": ["[[Nostr]]", "Obsidian", "", "C", "D", "E"]}])
        nota = self.mod.classify(cfg_falso(), [hit()], {})[0]
        self.assertEqual(len(nota.conceitos), 4)
        self.assertEqual(nota.conceitos[0], "Nostr")
        self.assertNotIn("", nota.conceitos)

    def test_backend_none_nao_chama_llm_nenhum(self):
        def explodir(cfg, prompt):
            raise AssertionError("não deveria chamar o LLM com backend=none")

        self.mod._run_claude_cli = explodir
        notas = self.mod.classify(cfg_falso("none"), [hit()], {})
        self.assertEqual(len(notas), 1)
        self.assertEqual(notas[0].categoria, "outro")

    def test_backend_desconhecido_falha_alto(self):
        with self.assertRaises(ClassifyError):
            self.mod.classify(cfg_falso("gemini"), [hit()], {})

    def test_sem_links_nao_chama_llm(self):
        def explodir(cfg, prompt):
            raise AssertionError("não deveria chamar o LLM sem links")

        self.mod._run_claude_cli = explodir
        self.assertEqual(self.mod.classify(cfg_falso(), [], {}), [])

if __name__ == "__main__":
    unittest.main()
