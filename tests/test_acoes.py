"""Rode com: python3 -m unittest discover -s tests -t .

As duas ações do painel: triar uma nota e disparar um comando do ciclo.

O teste que manda neste arquivo é `test_o_arquivo_sai_identico_fora_da_linha_da_triagem`.
A triagem é a **única** coisa que o painel escreve no vault, e o vault é o trabalho
do Dotcom: o que ele digitou abaixo de `## Minhas notas`, as relações que
acrescentou à mão, a ordem em que deixou o frontmatter. Uma escrita que
"normalizasse" o arquivo enquanto muda uma palavra seria uma perda silenciosa, e
silenciosa é a pior categoria — ninguém confere um arquivo que a ferramenta diz ter
atualizado.

Daí a implementação ser uma troca de linha por regex em vez do caminho óbvio
(`frontmatter.split` → dicionário → `compose`), e daí este teste comparar o arquivo
inteiro, antes e depois, exigindo que a única diferença sejam as duas linhas
esperadas.
"""

import subprocess
import sys
import unittest
from pathlib import Path

from dlb import acoes
from dlb.config import Config
from dlb.models import TRIAGENS

from tests.test_vault import VaultCase, note


class AcoesCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.cfg = Config(
            discord_token="x", discord_token_type="bot", channel_ids=["42"],
            vault_path=self.root, state_dir=self.root / ".state",
            env_path=self.root / ".env",
        )
        (self.root / ".env").write_text("DISCORD_TOKEN=x\n", encoding="utf-8")

    def escrever_nota(self, url="https://e.com/a", titulo="Relays"):
        resultado = self.vault.write_link_note("estudos", note(url, titulo, ["Nostr"]))
        return resultado.path.stem


class TestTriar(AcoesCase):
    def test_muda_a_triagem_e_o_corpus_le_de_volta(self):
        nome = self.escrever_nota()
        resultado = acoes.triar(self.cfg, nome, "aplicado")
        self.assertEqual((resultado.de, resultado.para), ("inbox", "aplicado"))

        from dlb.corpus import ler_links

        (entrada,) = ler_links(self.root)
        self.assertEqual(entrada.triagem, "aplicado")

    def test_o_arquivo_sai_identico_fora_da_linha_da_triagem(self):
        """O teste que justifica a escrita cirúrgica.

        Só duas linhas podem mudar: `triagem:` e `atualizado:`. Qualquer outra
        diferença é a ferramenta mexendo no trabalho do humano sem pedir.
        """
        nome = self.escrever_nota()
        caminho = self.root / "50_LINKS" / f"{nome}.md"

        # O humano escreve embaixo da fronteira e acrescenta uma relação à mão.
        texto = caminho.read_text(encoding="utf-8")
        texto = texto.replace(
            "## Minhas notas\n",
            "## Minhas notas\n\nIsto é meu. `--force` não encosta aqui.\n\n- [ ] reler\n",
        ).replace("relacionado:\n", "relacionado:\n  - \"[[Coisa Que Eu Liguei]]\"\n")
        caminho.write_text(texto, encoding="utf-8")
        antes = caminho.read_text(encoding="utf-8").splitlines(keepends=True)

        acoes.triar(self.cfg, nome, "descartado", hoje="2026-09-01")

        depois = caminho.read_text(encoding="utf-8").splitlines(keepends=True)
        self.assertEqual(len(antes), len(depois))
        diferentes = [
            (a.strip(), b.strip()) for a, b in zip(antes, depois) if a != b
        ]
        self.assertEqual(
            diferentes,
            [("triagem: inbox", "triagem: descartado"),
             ("atualizado: 2026-08-15", "atualizado: 2026-09-01")],
        )

    def test_nada_abaixo_de_minhas_notas_e_tocado(self):
        nome = self.escrever_nota()
        caminho = self.root / "50_LINKS" / f"{nome}.md"
        caminho.write_text(
            caminho.read_text(encoding="utf-8")
            + "\ntriagem: isto é texto meu, não propriedade\n",
            encoding="utf-8",
        )
        cauda_antes = caminho.read_text(encoding="utf-8").split(acoes.MINHAS_NOTAS, 1)[1]

        acoes.triar(self.cfg, nome, "lido")

        cauda_depois = caminho.read_text(encoding="utf-8").split(acoes.MINHAS_NOTAS, 1)[1]
        self.assertEqual(cauda_antes, cauda_depois)
        self.assertIn("isto é texto meu", cauda_depois)

    def test_a_escrita_e_atomica(self):
        """Temporário e `os.replace`, como em todo lugar que escreve neste projeto.
        Sem isto, matar o painel no meio deixa a nota truncada — e nota truncada é
        link perdido, porque o dedup por `10_FONTES/` não recria a nota."""
        nome = self.escrever_nota()
        acoes.triar(self.cfg, nome, "lido")
        self.assertEqual(
            sorted(p.name for p in (self.root / "50_LINKS").iterdir()), [f"{nome}.md"]
        )

    def test_triagem_igual_a_atual_e_recusada_sem_reescrever(self):
        nome = self.escrever_nota()
        caminho = self.root / "50_LINKS" / f"{nome}.md"
        antes = caminho.read_bytes()
        with self.assertRaises(acoes.Recusado) as caixa:
            acoes.triar(self.cfg, nome, "inbox")
        self.assertIn("já está em", str(caixa.exception))
        self.assertEqual(caminho.read_bytes(), antes)

    def test_valor_fora_do_vocabulario_e_recusado(self):
        """`TRIAGENS` é o vocabulário fechado do `ESQUEMA` do vault. Escrever um
        valor fora dele quebraria o contrato que a nota declara seguir."""
        nome = self.escrever_nota()
        with self.assertRaises(acoes.Recusado) as caixa:
            acoes.triar(self.cfg, nome, "quase-lido")
        for valida in TRIAGENS:
            self.assertIn(valida, str(caixa.exception))

    def test_as_quatro_triagens_do_esquema_funcionam(self):
        nome = self.escrever_nota()
        atual = "inbox"
        for destino in ("lido", "aplicado", "descartado", "inbox"):
            with self.subTest(f"{atual} -> {destino}"):
                self.assertEqual(acoes.triar(self.cfg, nome, destino).para, destino)
                atual = destino

    def test_nota_inexistente_e_recusada(self):
        with self.assertRaises(acoes.Recusado):
            acoes.triar(self.cfg, "não existe", "lido")

    def test_fuga_de_diretorio_e_recusada(self):
        """O nome vem de requisição HTTP. Checagem por `resolve()` e comparação de
        pai, não por filtro de substring — filtro de substring é jogo que não se
        ganha."""
        for tentativa in ("../../.ssh/id_rsa", "..", "sub/nota", "a\\b", " nota", ""):
            with self.subTest(tentativa):
                with self.assertRaises(acoes.Recusado):
                    acoes.triar(self.cfg, tentativa, "lido")

    def test_nota_sem_frontmatter_e_recusada(self):
        (self.root / "50_LINKS" / "solta.md").write_text("# só texto\n", encoding="utf-8")
        with self.assertRaises(acoes.Recusado) as caixa:
            acoes.triar(self.cfg, "solta", "lido")
        self.assertIn("frontmatter", str(caixa.exception))

    def test_nota_sem_a_propriedade_triagem_e_recusada(self):
        """Nota de wiki não se "lê" nem se "descarta" — `triagem` só existe em
        `tipo: link`, e o painel não inventa a propriedade onde ela não pertence."""
        (self.root / "50_LINKS" / "outra.md").write_text(
            "---\ntipo: conceito\nstatus: semente\n---\n\n# Outra\n", encoding="utf-8"
        )
        with self.assertRaises(acoes.Recusado) as caixa:
            acoes.triar(self.cfg, "outra", "lido")
        self.assertIn("triagem", str(caixa.exception))


class TestRodar(AcoesCase):
    def setUp(self):
        super().setUp()
        self.original = subprocess.run
        self.chamadas = []

    def tearDown(self):
        subprocess.run = self.original
        super().tearDown()

    def fingir(self, codigo=0, stdout="pronto", stderr=""):
        def falso(cmd, **kwargs):
            self.chamadas.append((cmd, kwargs.get("cwd")))
            return subprocess.CompletedProcess(cmd, codigo, stdout, stderr)

        subprocess.run = falso

    def test_dispara_pelo_modulo_e_nao_pela_funcao(self):
        """`python3 -m dlb sync` em subprocess, não `cmd_sync(cfg, args)`.

        É o que faz o job passar pelo `main()`, onde vivem o `flock` e o journal.
        Chamar a função direto seria um segundo caminho de execução — e o segundo
        caminho é sempre o que não tem proteção.
        """
        self.fingir()
        resultado = acoes.rodar(self.cfg, "sync")
        self.assertTrue(resultado.ok)
        cmd, cwd = self.chamadas[0]
        self.assertEqual(cmd, [sys.executable, "-m", "dlb", "sync"])
        self.assertEqual(cwd, str(self.root))

    def test_codigo_3_e_recusa_e_nao_falha(self):
        """Recusa pelo lock é o sistema se protegendo. Tratar como falha faria a
        tela gritar por causa de dois cliques no mesmo botão."""
        self.fingir(codigo=3, stdout="", stderr="Recusado: sync já está rodando")
        resultado = acoes.rodar(self.cfg, "sync")
        self.assertTrue(resultado.recusado)
        self.assertFalse(resultado.ok)
        self.assertIn("já está rodando", resultado.saida)

    def test_falha_de_verdade_nao_e_recusa(self):
        self.fingir(codigo=1, stdout="", stderr="ERRO: canal sem permissão")
        resultado = acoes.rodar(self.cfg, "sync")
        self.assertFalse(resultado.recusado)
        self.assertEqual(resultado.codigo, 1)

    def test_comando_fora_da_lista_branca_nao_roda(self):
        """`ressurgir` **posta no Discord**, que é a única ação irreversível do
        projeto; `responder` é laço infinito; `migrar` reescreve nota em massa.
        Nenhum dos três tem botão."""
        self.fingir()
        for proibido in ("ressurgir", "responder", "migrar", "doctor", "rm"):
            with self.subTest(proibido):
                resultado = acoes.rodar(self.cfg, proibido)
                self.assertFalse(resultado.ok)
                self.assertIn("não é permitido", resultado.saida)
        self.assertEqual(self.chamadas, [])

    def test_a_lista_branca_e_a_documentada(self):
        self.assertEqual(sorted(acoes.COMANDOS), ["backfill", "sintese", "sync", "temas"])

    def test_saida_gigante_e_cortada(self):
        self.fingir(stdout="\n".join(f"linha {i}" for i in range(500)))
        self.assertEqual(len(acoes.rodar(self.cfg, "sync").saida.splitlines()), 40)

    def test_timeout_avisa_que_o_journal_tem_o_run(self):
        def estoura(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1800)

        subprocess.run = estoura
        resultado = acoes.rodar(self.cfg, "sync")
        self.assertIn("journal", resultado.saida)

    def test_erro_de_execucao_nao_levanta(self):
        def estoura(cmd, **kwargs):
            raise OSError("sem permissão")

        subprocess.run = estoura
        self.assertIn("sem permissão", acoes.rodar(self.cfg, "sync").saida)


class TestPropostas(unittest.TestCase):
    """O chat **oferece**; quem decide é o clique.

    A heurística é deliberadamente boba: esforço em adivinhar melhor produziria
    sugestão mais convincente para uma decisão que não é da máquina. Um modelo
    marcando um link como `descartado` apagaria julgamento humano — e `descartado` é
    estado terminal.
    """

    def test_notas_citadas_viram_oferta_de_triagem(self):
        usados = [{"nome": "2026-08-19 Mojo", "titulo": "Mojo", "url": "u", "dia": "d"}]
        (proposta,) = acoes.propostas("o que sei de linguagens?", "texto", usados)
        self.assertEqual(proposta["tipo"], "triagem")
        self.assertEqual(proposta["nota"], "2026-08-19 Mojo")
        self.assertEqual(proposta["opcoes"], ["lido", "aplicado", "descartado"])

    def test_nao_oferece_inbox_de_volta(self):
        """Voltar para `inbox` é desfazer triagem, e isso não é sugestão de
        ferramenta — é decisão de quem triou."""
        usados = [{"nome": "n", "titulo": "t", "url": "u", "dia": "d"}]
        self.assertNotIn("inbox", acoes.propostas("x", "y", usados)[0]["opcoes"])

    def test_pergunta_sobre_o_agora_oferece_sync(self):
        for pergunta in ("o que entrou hoje?", "tem algo novo?", "a última captura"):
            with self.subTest(pergunta):
                tipos = [p["tipo"] for p in acoes.propostas(pergunta, "", [])]
                self.assertIn("comando", tipos)

    def test_pergunta_atemporal_nao_oferece_sync(self):
        self.assertEqual(acoes.propostas("o que é nostr?", "", []), [])

    def test_no_maximo_cinco_ofertas_de_triagem(self):
        usados = [{"nome": f"n{i}", "titulo": "t", "url": "u", "dia": "d"} for i in range(9)]
        self.assertEqual(len(acoes.propostas("x", "", usados)), 5)


if __name__ == "__main__":
    unittest.main()
