"""Rode com: python3 -m unittest discover -s tests -t .

O frontmatter é escrito à mão, não por um dumper de YAML. Isso é decisão de
projeto (o vault exige um dialeto: wikilink em propriedade entre aspas), mas
significa que a citação de escalar é responsabilidade nossa.

Estes testes existem porque a nota de link põe texto livre em propriedade — o
autor vem do Discord, o domínio vem da URL. Enquanto só datas e `area/*`
entravam ali, a citação frouxa passava. Não passa mais.
"""

import unittest

import yaml

from dlb import frontmatter


# Cada caso é um caractere a que o YAML dá significado especial no início de um
# escalar. Todos já quebraram de verdade antes da correção.
HAZARDS = {
    "colchete": "[Vídeo] explica como o runtime do Tokio faz work-stealing",
    "traco": "- comeca com traco",
    "asterisco": "*ancora* do yaml",
    "chave": "{isto} nao e um mapa",
    "crase": "``codigo`` no titulo",
    "porcento": "%diretiva",
    "arroba": "@mencao",
    "interrogacao": "? pergunta",
    "exclamacao": "!tag",
    "e_comercial": "&ancora",
    "pipe": "|bloco",
    "maior": ">dobrado",
    "virgula": ",lista",
    "dois_pontos_inicio": ":chave",
    "aspas_dentro": 'coisas que "parecem ilegais"',
    "apostrofo": "o 'jeito' certo",
    "dois_pontos_meio": "CS25: Transformers United! (Stanford)",
    "barra": "cosmtrek/mindwalk — replay de sessões",
    "cerquilha": "Discord #🔗_arquivar",
    "emoji": "canal 🔗_arquivar",
    "wikilink": "[[MOC - Captura Discord]]",
    "contrabarra": "caminho\\com\\barra",
    "espaco_nas_pontas": "  espaco nas pontas  ",
    "pipe_no_titulo": "De TikTok à BYD | In Hsieh – TalksbyLeo 212",
}


class TestEscalarSobreviveAoRoundtrip(unittest.TestCase):
    """Escrever e reler tem que devolver exatamente o que entrou.

    O modo de falha que isto impede é em cascata, não isolado: `safe_load`
    levanta, `split()` devolve ({}, arquivo inteiro), e a próxima escrita trata
    o frontmatter antigo como corpo e prepende um segundo bloco `---`. O arquivo
    fica com dois frontmatters e o Obsidian para de ler as propriedades.
    """

    def test_texto_livre_em_propriedade_sobrevive(self):
        for nome, valor in HAZARDS.items():
            with self.subTest(nome):
                texto = frontmatter.compose({"autor": valor}, "# corpo\n")
                meta, corpo = frontmatter.split(texto)
                self.assertEqual(meta.get("autor"), valor)
                self.assertEqual(corpo, "# corpo\n")

    def test_texto_livre_em_lista_sobrevive(self):
        for nome, valor in HAZARDS.items():
            with self.subTest(nome):
                texto = frontmatter.compose({"aliases": [valor]}, "# corpo\n")
                meta, _ = frontmatter.split(texto)
                self.assertEqual(meta.get("aliases"), [valor])

    def test_o_yaml_gerado_e_valido_para_o_parser_de_verdade(self):
        """Não basta o nosso split() aceitar — o Obsidian usa um parser YAML."""
        for nome, valor in HAZARDS.items():
            with self.subTest(nome):
                bloco = frontmatter.render({"autor": valor})
                # Tira as cercas `---` e entrega o miolo ao yaml puro.
                miolo = bloco.split("---")[1]
                self.assertEqual(yaml.safe_load(miolo), {"autor": valor})


class TestSegundoBlocoNuncaAparece(unittest.TestCase):
    """A cascata: reescrever uma nota não pode gerar dois frontmatters."""

    def test_reescrever_nota_com_texto_hostil_mantem_um_frontmatter(self):
        meta = {"tipo": "link", "autor": "- Jads", "titulo": "[Vídeo] do Tokio"}
        texto = frontmatter.compose(meta, "# corpo\n")

        # Segunda passada, como faz `_append_diario` e `write_link_note --force`.
        lido, corpo = frontmatter.split(texto)
        lido["atualizado"] = "2026-08-18"
        final = frontmatter.compose(lido, corpo)

        self.assertEqual(final.count("---"), 2)
        conferido, _ = frontmatter.split(final)
        self.assertEqual(conferido["autor"], "- Jads")
        self.assertEqual(conferido["titulo"], "[Vídeo] do Tokio")


class TestOrdemCanonica(unittest.TestCase):
    def test_triagem_sai_junto_de_status(self):
        """Os dois eixos de estado se leem juntos: maturidade e fluxo."""
        rendered = frontmatter.render(
            {"fonte": ["[[F]]"], "triagem": "inbox", "tipo": "link", "status": "semente"}
        )
        chaves = [l.split(":")[0] for l in rendered.splitlines() if ":" in l]
        self.assertEqual(chaves[:3], ["tipo", "status", "triagem"])

    def test_chave_desconhecida_vai_para_o_fim_em_ordem_alfabetica(self):
        rendered = frontmatter.render({"zeta": "z", "alfa": "a", "tipo": "link"})
        chaves = [l.split(":")[0] for l in rendered.splitlines() if ":" in l]
        self.assertEqual(chaves, ["tipo", "alfa", "zeta"])


if __name__ == "__main__":
    unittest.main()
