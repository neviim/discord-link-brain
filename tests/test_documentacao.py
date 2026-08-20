"""Rode com: python3 -m unittest discover -s tests -t .

Documentação desatualizada some do radar sem quebrar nada — o buraco
aconteceu com o README dizendo "113 testes, 55% de cobertura" muito depois
de a suíte ter crescido para centenas. Este teste trava o número de testes
contra a contagem real, no espírito do teste que já guarda o `.env.example`
em test_state_config.py.
"""

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _contar_testes(suite):
    if isinstance(suite, unittest.TestSuite):
        return sum(_contar_testes(t) for t in suite)
    return 1


class TestNumeroDeTestesNoReadmeNaoFicaParaTras(unittest.TestCase):
    def test_contagem_do_readme_bate_com_a_suite_real(self):
        readme = (RAIZ / "README.md").read_text(encoding="utf-8")
        m = re.search(r"(\d+) testes, \d+% de cobertura", readme)
        self.assertIsNotNone(m, "README não tem mais a linha '<N> testes, <N>% de cobertura'")
        anunciado = int(m.group(1))

        suite = unittest.TestLoader().discover(start_dir=str(RAIZ / "tests"), top_level_dir=str(RAIZ))
        real = _contar_testes(suite)

        self.assertEqual(
            anunciado, real,
            f"README diz {anunciado} testes, a suíte descoberta tem {real} — "
            "atualize a linha em README.md",
        )


if __name__ == "__main__":
    unittest.main()
