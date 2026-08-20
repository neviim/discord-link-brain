"""Rode com: python3 -m unittest discover -s tests -t .

`cmd_backfill` de ponta a ponta, com os mesmos dublês do `test_sync.py`.

Este arquivo existe por causa de um defeito que ficou vivo porque **nenhum teste
entrava no corpo do laço**: `cmd_backfill` lia `guilds`, uma variável que só era
atribuída dentro de `cmd_sync`, e levantava `NameError` no primeiro canal com
mensagem. Os testes de `test_cli.py` cobriam o parser e o despacho — não o miolo.

Daí a regra deste arquivo: todo teste aqui **processa mensagem de verdade**. Um
teste de backfill que não chega a `extract_links` não protege nada.
"""

import unittest

import dlb.cli as cli
from dlb.state import ChannelState, Store

from tests.test_sync import DiscordFalso, SyncCase, msg


class BackfillCase(SyncCase):
    def rodar(self, mensagens, argv=("backfill",), cfg=None, **kw):
        self.cliente = DiscordFalso(mensagens, **kw)
        cli.DiscordClient = lambda *a, **k: self.cliente
        args = cli.build_parser().parse_args(list(argv))
        saida = []
        original_log, cli._log = cli._log, lambda m="": saida.append(str(m))
        try:
            codigo = cli.cmd_backfill(cfg or self.cfg(), args)
        finally:
            cli._log = original_log
        self.saida = "\n".join(saida)
        return codigo

    @property
    def cursor(self):
        return Store(self.root / ".state").load("42").backfill_after


class TestOMioloDoBackfill(BackfillCase):
    def test_processa_o_dia_escreve_a_nota_e_marca(self):
        """O teste que faltava: entra no laço e vai até o vault.

        Enquanto `guilds` era indefinida, esta chamada levantava `NameError`
        antes de escrever qualquer coisa — e o comando estava assim para
        qualquer canal com mensagem, inclusive por `DLB_BACKFILL=1`.
        """
        self.assertEqual(self.rodar([msg("1", "olha https://e.com/a")]), 0)
        self.assertEqual(len(self.notas()), 1)
        self.assertTrue((self.root / "40_DIARIO" / "2026-08-15.md").exists())
        self.assertEqual(self.cliente.reacoes, [("1", "✅")])
        self.assertEqual(self.cursor, "1")

    def test_o_link_da_mensagem_leva_o_servidor(self):
        """A razão de `guilds` existir, não só de estar definida.

        Um "conserto" que criasse o dicionário e não o passasse a
        `extract_links` passaria no teste acima e continuaria produzindo link
        `/channels/@me/`, que não abre em canal de servidor.
        """
        self.rodar([msg("1", "https://e.com/a")])
        nota = next((self.root / "50_LINKS").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("channels/999/42/1", nota)

    def test_lote_sem_link_novo_avanca_o_cursor_sem_gastar_llm(self):
        """Trecho sem link também atravessa `extract_links` — mesmo caminho.

        É o ramo que anda o histórico de graça; se ele quebrar, o backfill fica
        parado no mesmo pedaço do canal para sempre.
        """
        self.assertEqual(self.rodar([msg("1", "sem link nenhum aqui")]), 0)
        self.assertEqual(self.notas(), [])
        self.assertEqual(self.classificou, [])
        self.assertEqual(self.cursor, "1")


class TestOsDoisCursoresSaoIndependentes(BackfillCase):
    def test_backfill_nao_encosta_no_cursor_do_sync(self):
        """Dois cursores no mesmo arquivo de estado: o sync anda do presente
        para frente, o backfill do começo para frente. Um escrever no campo do
        outro faria o histórico pular mensagem, e a deduplicação por URL não
        recria o que nunca foi lido."""
        store = Store(self.root / ".state")
        store.save("42", ChannelState("500", None, "estudos"))

        self.rodar([msg("1", "https://e.com/a")])

        lido = store.load("42")
        self.assertEqual(lido.last_message_id, "500")
        self.assertEqual(lido.backfill_after, "1")


if __name__ == "__main__":
    unittest.main()
