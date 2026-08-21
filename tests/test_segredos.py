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
6. **Id longo fora da lista sintética** — snowflake do Discord tem 17 a 19 dígitos.
   Id de canal não abre porta nenhuma sem token, mas revela que o servidor existe, e
   colar um id real num teste durante depuração é a coisa mais fácil do mundo.
7. **Padrão neutro de fuso e de caminho** — o sistema roda na máquina de quem
   instalar. Fuso de um lugar específico embutido no código faz o sistema mentir a
   hora para todo mundo que não mora lá, e caminho de vault com nome de pessoa não
   existe na máquina de mais ninguém.
8. **Perfil de pessoa em URL de código** — `github.com/<usuário>` num `User-Agent`
   sai pela rede em toda requisição. Esse vazamento não fica no repositório: ele
   viaja, e nem apagar o repo o desfaz.

## Por que lista-branca, e não lista-negra

Este arquivo é rastreado e vai ser **público**. Listar aqui o nome, o tailnet ou o id
que se quer proibir *publicaria exatamente aquilo que se está tirando* — a lista
negra viraria o vazamento. Daí todo teste daqui ser por **forma** (a coisa parece um
snowflake? parece um fuso regional?) ou por **lista-branca** (o padrão é um destes
valores neutros?). Nenhum valor pessoal aparece neste arquivo, de propósito.
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

# **Sem caixa, e isso não é zelo: é a brecha por onde um vazamento passou.** A
# primeira neutralização deste repo substituiu o nome de MagicDNS em minúsculas e
# deixou passar a **mesma string em MAIÚSCULAS** — que estava num teste de
# comparação de `Host` sem caixa, ou seja, escrita em maiúsculas de propósito. Ela
# sobreviveu em três versões antigas de `tests/test_painel.py` e só apareceu numa
# varredura do histórico feita à mão antes de publicar.
#
# Nome de domínio é insensível a caixa por definição, então um guarda sensível a
# caixa está errado sobre o próprio domínio que verifica.
REDE_PRIVADA = [
    ("nome de tailnet", re.compile(r"\btail[0-9a-f]{5,}\.ts\.net\b", re.I)),
    ("IP de tailnet (CGNAT)",
     re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")),
]

# `dlb` é o usuário que o container cria — `/home/dlb/` é caminho de imagem, não a
# casa de uma pessoa. Os outros nomes são placeholders documentados.
CASA_DE_ALGUEM = re.compile(
    r"/home/(?!SEU|seu|usuario|user\b|dlb\b)[a-z][a-z0-9_-]{2,}/"
)

# Snowflake do Discord tem 17 a 19 dígitos. Estes são **sintéticos**, e de propósito
# com zeros no fim: id assim se lê como fabricado à primeira vista. O timestamp
# embutido é real, o que mantém o teste de aritmética de `--since` com sentido.
IDS_SINTETICOS = {
    "1422734760345600000",   # canal de exemplo, criado em 2025-10-01
    "1217985616281600000",   # servidor de exemplo, criado em 2024-03-15
    "1900000000000000000",   # id de post numa URL de exemplo
}
ID_LONGO = re.compile(r"\b[0-9]{17,20}\b")

# Fuso de um lugar específico, na forma `Regiao/Cidade`, e **só em posição de
# valor**: depois de `=`, de `:-` (interpolação do compose) ou de `TIMEZONE=`.
#
# A primeira versão casava o arquivo inteiro e acusou o comentário do próprio
# `.env.example` que **ensina** a declarar o fuso ("`America/Sao_Paulo`,
# `Europe/Lisbon`"). Documentação citando um fuso é o oposto do defeito: é o que
# faz quem instala declarar o dele.
REGIAO = r"(?:America|Europe|Asia|Africa|Australia|Pacific|Atlantic|Indian|Antarctica)"
FUSO_COMO_VALOR = re.compile(rf"""(?:=\s*["']?|:-)({REGIAO}/\w+)""")

# Onde o **padrão** mora. Teste pode usar fuso regional como dado — é justamente
# assim que se testa a virada do dia. O que não pode é o padrão de fábrica.
ONDE_O_PADRAO_MORA = (
    ".env.example", "dlb.sh", "docker-compose.yml",
    "dlb/config.py", "dlb/agenda.py", "dlb/cli.py", "dlb/extract.py",
)

# Lista-branca do que "neutro" significa para o caminho do vault.
VAULT_NEUTRO = ("~/Documents/Obsidian/vault", "Documents/Obsidian/vault")

# `github.com/<um-segmento>` é perfil de pessoa; com dois é repositório.
PERFIL_EM_URL = re.compile(r"github\.com/[A-Za-z0-9_.-]+(?![A-Za-z0-9_./-])")

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
                for m in padrao.finditer(texto):
                    linha = texto[: m.start()].count("\n") + 1
                    achados.append(f"{rel}:{linha} {nome}: {m.group(0)}")
        self.assertEqual(achados, [], "identificador de rede privada")

    def test_o_guarda_de_rede_privada_nao_depende_de_caixa(self):
        """O teste que existe por causa de um vazamento que passou.

        Nome de MagicDNS em MAIÚSCULAS é a forma que sobreviveu à primeira
        neutralização, porque a substituição era sensível a caixa e o teste de
        `Host` do painel escreve o nome em maiúsculas de propósito. Um guarda que
        só vê minúsculas assina embaixo de um repo que ainda tem o nome da rede.
        """
        for forma in (
            "maquina.tailabc12.ts.net",
            "MAQUINA.TAILABC12.TS.NET",
            "Maquina.TailAbc12.Ts.Net",
        ):
            with self.subTest(forma):
                nome, padrao = REDE_PRIVADA[0]
                self.assertIsNotNone(
                    padrao.search(forma), f"{nome} não pegou {forma}"
                )

    def test_nenhum_caminho_absoluto_de_casa(self):
        """`/home/alguem/...` num exemplo vaza o usuário e só funciona na máquina de
        quem escreveu. `$HOME` ou `%h` funciona em qualquer uma."""
        achados = []
        for rel, texto in textos():
            for m in CASA_DE_ALGUEM.finditer(texto):
                linha = texto[: m.start()].count("\n") + 1
                achados.append(f"{rel}:{linha} {m.group(0)}")
        self.assertEqual(achados, [], "caminho absoluto de casa de alguém")


@unittest.skipUnless(shutil.which("git"), "precisa do git para listar os rastreados")
class TestNadaDeIdentidadeDeTerceiro(unittest.TestCase):
    """O que não é credencial e ainda assim não pertence a um repositório público."""

    def test_nenhum_id_longo_fora_da_lista_sintetica(self):
        """Id de canal do Discord não abre porta sem token — mas revela o servidor.

        Colar um id real num teste enquanto se depura é a coisa mais fácil do mundo,
        e ninguém revisa isso de novo antes do push. A lista de sintéticos é
        pequena de propósito: id novo tem de ser adicionado à mão, e é nesse
        momento que alguém pergunta se ele é real.
        """
        achados = []
        for rel, texto in textos():
            for m in ID_LONGO.finditer(texto):
                if m.group(0) in IDS_SINTETICOS:
                    continue
                linha = texto[: m.start()].count("\n") + 1
                achados.append(f"{rel}:{linha} id longo {m.group(0)}")
        self.assertEqual(
            achados, [],
            "id de 17-20 dígitos fora de IDS_SINTETICOS — se for sintético, "
            "acrescente lá com um comentário dizendo o que ele representa",
        )

    def test_o_fuso_padrao_nao_e_de_um_lugar_so(self):
        """`America/Sao_Paulo` embutido como padrão faz o sistema mentir a hora na
        máquina de quem não mora lá — e mentir plausivelmente, que é o pior tipo.

        Fuso regional **como dado de teste** é legítimo: é assim que se testa a
        virada do dia. Por isso este teste só olha onde o padrão de fábrica mora.
        """
        achados = []
        for rel, texto in textos():
            if rel not in ONDE_O_PADRAO_MORA:
                continue
            for m in FUSO_COMO_VALOR.finditer(texto):
                linha = texto[: m.start()].count("\n") + 1
                achados.append(f"{rel}:{linha} {m.group(0)}")
        self.assertEqual(achados, [], "fuso de um lugar específico como padrão — use UTC")

    def test_o_caminho_padrao_do_vault_e_neutro(self):
        """O padrão do `OBSIDIAN_VAULT` tem de existir na máquina de qualquer um.

        Conferido por **lista-branca**: a lista-negra teria de conter o caminho
        pessoal que se está tirando, e escrevê-lo aqui o publicaria de novo.
        """
        exemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
        m = re.search(r"^OBSIDIAN_VAULT=(.*)$", exemplo, re.M)
        self.assertIsNotNone(m, "`.env.example` perdeu a linha do OBSIDIAN_VAULT")
        self.assertIn(m.group(1).strip(), VAULT_NEUTRO)

        config = (RAIZ / "dlb" / "config.py").read_text(encoding="utf-8")
        m2 = re.search(r"vault_path: Path = Path\.home\(\) / \"([^\"]+)\"", config)
        self.assertIsNotNone(m2, "`config.py` mudou a forma do padrão de vault_path")
        self.assertIn(m2.group(1), VAULT_NEUTRO)

    def test_nenhum_perfil_de_pessoa_em_url_do_codigo(self):
        """Este é o único item da varredura que **não** fica no repositório.

        O `User-Agent` levava `github.com/<usuário>` para o Discord em toda
        requisição. Apagar o repo não desfaz isso: já viajou. Link para
        repositório (`github.com/dono/projeto`) segue permitido — é referência.
        """
        achados = []
        for rel, texto in textos():
            if not rel.startswith("dlb/"):
                continue
            for m in PERFIL_EM_URL.finditer(texto):
                linha = texto[: m.start()].count("\n") + 1
                achados.append(f"{rel}:{linha} {m.group(0)}")
        self.assertEqual(achados, [], "perfil de pessoa em URL dentro de `dlb/`")


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
