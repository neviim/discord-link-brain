"""Rode com: python3 -m unittest discover -s tests -t .

Nada que pareça segredo entra em arquivo rastreado.

Este arquivo existe porque o repositório vai ser **público**, e a assimetria é
brutal: um `git push` leva um segundo, e tirar um segredo do histórico do GitHub
depois exige reescrever a história, forçar push, e **rotacionar a credencial de
qualquer forma** — porque o que foi público uma vez está capturado.

Varredura manual não escala e não sobrevive à próxima semana. Teste sobrevive.

## O que é verificado

1. **Padrão de credencial** em qualquer arquivo rastreado — chave privada, token de
   provedor, `nsec` do Nostr, JWT.
2. **Atribuição de segredo com valor** — `TOKEN=`, `SECRET=`, `API_KEY=` seguidos de
   algo que não seja vazio nem placeholder.
3. **`.gitignore` cobrindo o que o próprio código escreve** — `.env`, e os
   derivados `.env.bak` e `.env.tmp` que a tela de configuração cria. `.env`
   sozinho **não** cobre `.env.bak`, e esse buraco já existiu aqui.
4. **Identificador de rede privada** — nome de tailnet e IP de CGNAT. Não é
   credencial: é o mapa de uma rede privada, e não tem por que estar num repo
   público quando um placeholder ensina a mesma coisa.
5. **Caminho absoluto de casa de alguém** — `/home/<nome>/...` em exemplo de
   documento. Vaza o usuário e só funciona na máquina de quem escreveu.

## O que este teste NÃO decide

Nome de usuário, apelido e id de canal do Discord **não** são credenciais — id de
canal não dá acesso a nada sem token e sem participação no servidor. Se devem ou não
aparecer num repo público é decisão de quem publica, não regra de segurança, e este
teste não a toma pelo dono.
"""

import re
import subprocess
import shutil
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Padrões de credencial de verdade. Cada um casa uma família específica: regex
# genérica demais ("[A-Za-z0-9]{32}") acusaria hash de commit e viraria ruído,
# e teste com ruído é teste que alguém desliga.
CREDENCIAIS = [
    ("chave privada PEM", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("nsec do Nostr", r"\bnsec1[02-9ac-hj-np-z]{20,}"),
    ("token da Anthropic", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("chave da OpenAI", r"\bsk-[A-Za-z0-9]{32,}"),
    ("token do GitHub", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    ("token do Slack", r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    ("chave da AWS", r"\bAKIA[0-9A-Z]{16}\b"),
    ("chave do Google", r"\bAIza[0-9A-Za-z_-]{30,}"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"),
    ("token de bot do Discord", r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}\b"),
]

# Valor que é claramente exemplo, e não segredo vazado. As três primeiras linhas
# saíram de casos reais que este teste acusou de si mesmo:
#
#   `<token do bot>`  -> o valor capturado para no espaço, virando `<token`;
#   `"DISCORD_TOKEN=x\n"` -> literal Python, o `\n` faz parte do texto do arquivo;
#   `abc`             -> token de três letras em dublê de teste.
PLACEHOLDERS = re.compile(
    r"^(<.*|x|xx+|\.\.\.|todo|placeholder|exemplo|example|seu[-_].*|"
    r"token-de-teste-\d+|segredo(-de-verdade|-do-painel)?|token-novo|"
    r"COLOQUE.*|CHANGEME|changeme|bot|1|0)$",
    re.I,
)

# Piso de tamanho, e ele **é** um enfraquecimento — declarado em vez de escondido:
# um segredo de menos de 12 caracteres passa por este teste. A troca é deliberada,
# porque credencial de verdade é longa (token de bot do Discord tem ~59, chave de
# provedor 32+) e sem o piso todo dublê de teste (`x`, `abc`, `segredo`) viraria
# alarme — e teste que grita à toa é teste que alguém desliga.
PISO_DE_TAMANHO = 12

# Três detalhes deste padrão saíram de erro dele mesmo, e cada um vale a linha:
#
# - `[ \t]*` e não `\s*` — `\s` casa `\n`, então em `CHAVE=` vazio ele pulava a
#   quebra e capturava a linha seguinte como se fosse o valor;
# - **sem `^` na frente** — ancorar no início da linha fazia só a *primeira*
#   atribuição de cada linha ser conferida. Numa linha de fonte Python como
#   `"A_TOKEN=x\nDISCORD_TOKEN=..."` a segunda chave passava sem exame, e é o tipo
#   de linha em que segredo de teste de verdade mora;
# - **`\\` fora do valor** — assim o valor para no `\n` escapado em vez de engolir a
#   atribuição seguinte, e a próxima vira um achado independente.
ATRIBUICAO = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|APIKEY|PASSWORD|SENHA|PASSWD))"
    r"[ \t]*=[ \t]*[\"']?([^\s\"'#\\]+)",
)

REDE_PRIVADA = [
    ("nome de tailnet", r"\btail[0-9a-f]{5,}\.ts\.net\b"),
    ("IP de tailnet (CGNAT)", r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
]

# `dlb` é o usuário que o container cria — `/home/dlb/` é caminho de imagem, não a
# casa de uma pessoa. Os outros nomes são placeholders documentados.
CASA_DE_ALGUEM = re.compile(
    r"/home/(?!SEU|seu|usuario|user\b|dlb\b)[a-z][a-z0-9_-]{2,}/"
)

# Este próprio arquivo contém os padrões, por definição. E o `.env.example` é o
# contrato de nomes de variável — ele tem `DISCORD_TOKEN=` vazio de propósito.
ISENTOS = {"tests/test_segredos.py"}


def rastreados() -> list[Path]:
    saida = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(RAIZ), capture_output=True, text=True, check=False
    ).stdout
    return [RAIZ / n for n in saida.split("\0") if n]


def textos() -> list[tuple[str, str]]:
    """(caminho relativo, conteúdo) de cada arquivo rastreado que é texto."""
    saida = []
    for caminho in rastreados():
        rel = str(caminho.relative_to(RAIZ))
        if rel in ISENTOS or not caminho.is_file():
            continue
        try:
            saida.append((rel, caminho.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue    # binário ou ilegível: nada de texto para varrer
    return saida


@unittest.skipUnless(shutil.which("git"), "precisa do git para listar os rastreados")
class TestNadaDeSegredoNoRepositorio(unittest.TestCase):
    def test_nenhum_padrao_de_credencial_em_arquivo_rastreado(self):
        achados = []
        for rel, texto in textos():
            for nome, padrao in CREDENCIAIS:
                for m in re.finditer(padrao, texto):
                    linha = texto[: m.start()].count("\n") + 1
                    achados.append(f"{rel}:{linha} parece {nome}")
        self.assertEqual(achados, [], "credencial em arquivo rastreado")

    def test_nenhuma_atribuicao_de_segredo_com_valor_real(self):
        """`DISCORD_TOKEN=` vazio no exemplo é o contrato; `DISCORD_TOKEN=abc123` é
        vazamento. A diferença é o valor, e é isso que se mede aqui."""
        achados = []
        for rel, texto in textos():
            for m in ATRIBUICAO.finditer(texto):
                chave, valor = m.group(1), m.group(2).strip("\"'")
                if PLACEHOLDERS.match(valor) or len(valor) < PISO_DE_TAMANHO:
                    continue
                linha = texto[: m.start()].count("\n") + 1
                achados.append(f"{rel}:{linha} {chave}={valor[:12]}…")
        self.assertEqual(achados, [], "atribuição de segredo com valor")

    def test_nenhum_identificador_de_rede_privada(self):
        """Nome de tailnet e IP de CGNAT não são credencial — são o mapa de uma rede
        privada. Placeholder ensina a mesma coisa sem publicar a rede de ninguém."""
        achados = []
        for rel, texto in textos():
            for nome, padrao in REDE_PRIVADA:
                for m in re.finditer(padrao, texto):
                    linha = texto[: m.start()].count("\n") + 1
                    achados.append(f"{rel}:{linha} {nome}: {m.group(0)}")
        self.assertEqual(achados, [], "identificador de rede privada")

    def test_nenhum_caminho_absoluto_de_casa(self):
        """`/home/alguem/...` num exemplo vaza o usuário e só funciona na máquina de
        quem escreveu. `$HOME` ou `%h` funciona em qualquer uma."""
        achados = []
        for rel, texto in textos():
            for m in CASA_DE_ALGUEM.finditer(texto):
                linha = texto[: m.start()].count("\n") + 1
                achados.append(f"{rel}:{linha} {m.group(0)}")
        self.assertEqual(achados, [], "caminho absoluto de casa de alguém")


@unittest.skipUnless(shutil.which("git"), "precisa do git")
class TestGitignoreCobreOQueOCodigoEscreve(unittest.TestCase):
    """O `.gitignore` tem de cobrir todo arquivo que o próprio projeto cria com
    segredo dentro. Já falhou uma vez: `.env` não cobria `.env.bak`."""

    def ignorado(self, nome: str) -> bool:
        return subprocess.run(
            ["git", "check-ignore", "-q", nome], cwd=str(RAIZ),
            capture_output=True, check=False,
        ).returncode == 0

    def test_o_env_e_seus_derivados_sao_ignorados(self):
        for nome in (".env", ".env.bak", ".env.tmp"):
            with self.subTest(nome):
                self.assertTrue(self.ignorado(nome), f"{nome} não está ignorado")

    def test_o_estado_local_e_ignorado(self):
        """`.state/` tem cursor de canal e o histórico de execuções — não é segredo,
        mas é estado da máquina de alguém e não pertence ao repositório."""
        for nome in (".state/1234.json", ".state/runs/x/summary.json", ".state/painel.json"):
            with self.subTest(nome):
                self.assertTrue(self.ignorado(nome))

    def test_o_exemplo_continua_versionado(self):
        """Ignorar `.env*` inteiro levaria junto o `.env.example`, que é o contrato
        que o schema do painel espelha."""
        self.assertFalse(self.ignorado(".env.example"))

    def test_nada_perigoso_esta_rastreado(self):
        proibidos = {".env", ".env.bak", ".env.tmp"}
        nomes = {str(p.relative_to(RAIZ)) for p in rastreados()}
        self.assertEqual(nomes & proibidos, set())
        self.assertEqual([n for n in nomes if n.startswith(".state/")], [])


if __name__ == "__main__":
    unittest.main()
