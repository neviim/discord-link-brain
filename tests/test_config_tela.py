"""Rode com: python3 -m unittest discover -s tests -t .

A tela de configuração: o schema, a escrita no `.env` e a fronteira de mutação.

Os quatro critérios de pronto da fase, escritos no plano, estão aqui:

1. mudar `DLB_SYNC_EVERY_HOURS` → faixa "salvo, não aplicado" → aplicar;
2. valor inválido recusado com a mensagem do próprio validador, e **nada escrito**;
3. desfazer devolve o `.env` **byte-idêntico**;
4. o painel sobrevive a um `.env` que não parseia.

E o teste de contrato que trava o par schema ↔ `.env.example`, que é a classe de
defeito que já aconteceu neste repo (`DISCORD_POST_ENABLED` duplicado com valores
opostos) e que validação de tipo não pegaria: os dois valores eram válidos.
"""

import json
import os
import shutil
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dlb import acoes, compose, env_io, painel_schema
from dlb.painel_schema import CONFIG_SCHEMA, Invalido, POR_CHAVE

from tests.test_painel import PainelCase


class TestContratoDoSchema(unittest.TestCase):
    """O par schema ↔ `.env.example`, travado nas duas direções."""

    def test_toda_chave_configuravel_esta_no_schema_e_vice_versa(self):
        do_schema = {campo.chave for campo in CONFIG_SCHEMA}
        do_exemplo = painel_schema.chaves_configuraveis()
        self.assertEqual(sorted(do_exemplo - do_schema), [], "no exemplo e fora do schema")
        self.assertEqual(sorted(do_schema - do_exemplo), [], "no schema e fora do exemplo")

    def test_nenhuma_chave_aparece_duas_vezes_no_schema(self):
        """A tabela renderiza o formulário: chave repetida viraria dois controles
        para o mesmo valor, e o segundo ganharia em silêncio."""
        chaves = [campo.chave for campo in CONFIG_SCHEMA]
        self.assertEqual(len(chaves), len(set(chaves)))

    def test_toda_chave_lida_pelo_config_esta_no_schema(self):
        """Se o código lê e a tela não oferece, a variável fica invisível — que é
        como uma opção documentada deixa de existir na prática."""
        import re

        codigo = (Path(__file__).resolve().parent.parent / "dlb" / "config.py").read_text(
            encoding="utf-8"
        )
        lidas = set(re.findall(r'os\.environ\.get\(\s*"([A-Z][A-Z0-9_]*)"', codigo))
        internas = {"DLB_MODE", "DLB_HEARTBEAT", "DLB_HEARTBEAT_RESPONDER"}
        faltando = sorted(lidas - internas - {c.chave for c in CONFIG_SCHEMA})
        self.assertEqual(faltando, [])

    def test_todo_campo_tem_tipo_conhecido_e_rotulo(self):
        for campo in CONFIG_SCHEMA:
            with self.subTest(campo.chave):
                self.assertIn(campo.tipo, painel_schema._VALIDADORES)
                self.assertTrue(campo.rotulo)
                self.assertIn(campo.onde_vale, {"python", "agendador", "painel"})

    def test_escolha_sempre_tem_opcoes(self):
        for campo in CONFIG_SCHEMA:
            if campo.tipo == "escolha":
                with self.subTest(campo.chave):
                    self.assertTrue(campo.opcoes)

    def test_o_valor_do_exemplo_passa_na_propria_validacao(self):
        """O `.env.example` é o que todo mundo copia. Se um valor dele não passa
        no validador, a tela recusaria a configuração de fábrica.

        As obrigatórias vêm vazias de propósito no exemplo — é o que sinaliza
        "preencha antes de rodar" — então elas ficam de fora desta checagem.
        """
        valores = env_io.ler(painel_schema.CAMINHO_EXEMPLO)
        for chave, valor in valores.items():
            campo = POR_CHAVE[chave]
            if campo.obrigatorio and not valor:
                continue
            with self.subTest(chave):
                campo.validar(valor)

    def test_nenhum_segredo_vem_preenchido_no_exemplo(self):
        """Exemplo com token preenchido seria segredo versionado no repositório.

        Nem toda obrigatória é vazia: `OBSIDIAN_VAULT` vem com um caminho padrão
        de propósito, porque é palpite útil e não segredo.
        """
        valores = env_io.ler(painel_schema.CAMINHO_EXEMPLO)
        for campo in CONFIG_SCHEMA:
            if campo.segredo:
                with self.subTest(campo.chave):
                    self.assertEqual(valores.get(campo.chave, ""), "")


@unittest.skipUnless(shutil.which("git"), "precisa do git: a autoridade sobre .gitignore é ele")
class TestOsArquivosQueOPainelEscreveNaoVazam(unittest.TestCase):
    """O backup do `.env` tem o `DISCORD_TOKEN`. Ele não pode ser commitável.

    `.gitignore` tinha `.env` e só. `.env.bak` — que **este código** passou a criar
    — aparecia como arquivo novo no `git status`, esperando um `git add -A`
    distraído. O defeito nasceu com a feature, não estava no repo antes.

    Pulado onde não há `git` — a imagem `dev` do container não o tem, e
    `./dlb.sh dev test` é caminho documentado. Reimplementar a semântica do
    `.gitignore` para não depender dele seria trocar a autoridade por um palpite.
    """

    def ignorado(self, nome: str) -> bool:
        import subprocess

        raiz = Path(__file__).resolve().parent.parent
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", nome],
                cwd=str(raiz),
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )

    def test_o_env_e_os_arquivos_que_o_painel_deriva_dele_sao_ignorados(self):
        for nome in (".env", ".env.bak", ".env.tmp"):
            with self.subTest(nome):
                self.assertTrue(self.ignorado(nome), f"{nome} não está no .gitignore")

    def test_o_exemplo_continua_versionado(self):
        """Ignorar `.env*` inteiro levaria o `.env.example` junto, e ele é o
        contrato que o schema espelha."""
        self.assertFalse(self.ignorado(".env.example"))


class TestValidacao(unittest.TestCase):
    def validar(self, chave, valor):
        return POR_CHAVE[chave].validar(valor)

    def test_numero_fora_da_faixa_recusa_dizendo_a_faixa(self):
        with self.assertRaises(Invalido) as caixa:
            self.validar("DLB_SINTESE_DIA", "9")
        self.assertIn("máximo é 7", str(caixa.exception))

    def test_numero_que_nao_e_numero(self):
        with self.assertRaises(Invalido) as caixa:
            self.validar("DLB_SYNC_EVERY_HOURS", "seis")
        self.assertIn("não é número", str(caixa.exception))

    def test_booleano_recusa_true(self):
        """O shell compara com `= "1"` literal, então `true` não liga nada. Aceitar
        aqui produziria tela dizendo ligado e agendador desligado."""
        with self.assertRaises(Invalido) as caixa:
            self.validar("DLB_BACKFILL", "true")
        self.assertIn('"1" literal', str(caixa.exception))

    def test_escolha_fora_da_lista_mostra_a_lista(self):
        with self.assertRaises(Invalido) as caixa:
            self.validar("LLM_BACKEND", "gpt")
        self.assertIn("claude-cli", str(caixa.exception))

    def test_hora_invalida(self):
        for ruim in ("25:00", "3:5", "03h30", "abc"):
            with self.subTest(ruim):
                with self.assertRaises(Invalido):
                    self.validar("DLB_SYNC_AT", ruim)

    def test_hora_valida_e_normalizada_com_dois_digitos(self):
        self.assertEqual(self.validar("DLB_SYNC_AT", "3:30"), "03:30")

    def test_par_incompleto_e_recusado_antes_de_virar_canal_fantasma(self):
        """O `_pares` do `config.py` descarta par malformado em silêncio; a tela
        recusa antes, para o valor não sumir sem explicação."""
        with self.assertRaises(Invalido):
            self.validar("DISCORD_CHANNEL_NAMES", "111:estudos,lixo")

    def test_lista_com_virgula_sobrando(self):
        with self.assertRaises(Invalido):
            self.validar("DISCORD_CHANNEL_IDS", "111,,222")

    def test_vazio_passa_e_volta_ao_padrao(self):
        self.assertEqual(self.validar("DLB_SYNC_AT", ""), "")

    def test_vazio_nao_passa_no_que_e_obrigatorio(self):
        with self.assertRaises(Invalido) as caixa:
            self.validar("DISCORD_CHANNEL_IDS", "  ")
        self.assertIn("não pode ficar vazio", str(caixa.exception))

    def test_quebra_de_linha_e_recusada(self):
        """Uma linha nova no valor viraria uma chave nova no arquivo."""
        with self.assertRaises(Invalido):
            self.validar("RESPONDER_PREFIX", "?buscar\nDISCORD_TOKEN=roubado")

    def test_caminho_relativo_e_recusado(self):
        with self.assertRaises(Invalido):
            self.validar("OBSIDIAN_VAULT", "vault")
        self.assertEqual(self.validar("OBSIDIAN_VAULT", "~/x"), "~/x")

    def test_chave_desconhecida_nao_entra_pelo_formulario(self):
        _, erros = painel_schema.validar_tudo({"COISA_NOVA": "1"})
        self.assertIn("chave desconhecida", erros[0])

    def test_valida_tudo_antes_de_qualquer_escrita(self):
        limpos, erros = painel_schema.validar_tudo(
            {"DLB_BACKFILL": "1", "DLB_SINTESE_DIA": "99"}
        )
        self.assertEqual(len(erros), 1)
        self.assertEqual(limpos, {"DLB_BACKFILL": "1"})   # quem chama não escreve nada


class TestEscritaDoEnv(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.arquivo = Path(self._tmp.name) / ".env"
        self.arquivo.write_text(
            "# ---- Discord ----\n"
            "DISCORD_TOKEN=segredo\n"
            "DLB_SYNC_EVERY_HOURS=6\n"
            "\n"
            "# DLB_SYNC_AT=03:30        # HH:MM, uma vez por dia\n"
            "TEMAS_LIMIAR=8\n",
            encoding="utf-8",
        )
        self.antes = self.arquivo.read_bytes()

    def tearDown(self):
        self._tmp.cleanup()

    def test_troca_o_valor_no_lugar_preservando_comentario_e_ordem(self):
        env_io.escrever(self.arquivo, {"DLB_SYNC_EVERY_HOURS": "3"})
        texto = self.arquivo.read_text(encoding="utf-8")
        self.assertIn("# ---- Discord ----", texto)
        self.assertIn("DLB_SYNC_EVERY_HOURS=3", texto)
        self.assertNotIn("DLB_SYNC_EVERY_HOURS=6", texto)
        self.assertEqual(
            [l.split("=")[0] for l in texto.splitlines() if l and not l.startswith("#") and "=" in l],
            ["DISCORD_TOKEN", "DLB_SYNC_EVERY_HOURS", "TEMAS_LIMIAR"],
        )

    def test_chave_so_comentada_ganha_linha_ativa_e_a_sugestao_fica(self):
        """A linha comentada é documentação. Sobrescrevê-la perderia a explicação
        de formato justamente da chave que alguém acabou de descobrir."""
        env_io.escrever(self.arquivo, {"DLB_SYNC_AT": "04:15"})
        texto = self.arquivo.read_text(encoding="utf-8")
        self.assertIn("# DLB_SYNC_AT=03:30", texto)
        self.assertIn("\nDLB_SYNC_AT=04:15\n", texto)
        self.assertEqual(env_io.ler(self.arquivo)["DLB_SYNC_AT"], "04:15")

    def test_comentario_de_fim_de_linha_nao_vaza_para_o_valor(self):
        """O parser trata tudo depois do `=` como valor, então `03:30 # HH:MM`
        seria o valor. A escrita nunca cria comentário na mesma linha."""
        env_io.escrever(self.arquivo, {"DLB_SYNC_AT": "04:15"})
        self.assertNotIn("#", env_io.ler(self.arquivo)["DLB_SYNC_AT"])

    def test_chave_ausente_com_valor_vazio_nao_e_escrita(self):
        """O formulário posta as 55 chaves, a maioria em branco. Sem esta regra,
        salvar qualquer coisa acrescentaria cinquenta linhas `CHAVE=` de uma vez."""
        antes = self.arquivo.read_bytes()
        resultado = env_io.escrever(
            self.arquivo, {"DLB_BACKFILL": "", "DLB_SINTESE": "", "RESSURGIR_CANAL": ""}
        )
        self.assertEqual(resultado.mudadas, {})
        self.assertEqual(self.arquivo.read_bytes(), antes)

    def test_esvaziar_chave_que_existe_escreve_a_linha_vazia(self):
        """É como se libera o `DLB_SYNC_AT`: a grade em horas precisa chegar
        **vazia** ao container, e vazio é diferente de ausente para o compose."""
        env_io.escrever(self.arquivo, {"DLB_SYNC_EVERY_HOURS": ""})
        self.assertIn("DLB_SYNC_EVERY_HOURS=\n", self.arquivo.read_text(encoding="utf-8"))
        self.assertEqual(env_io.ler(self.arquivo)["DLB_SYNC_EVERY_HOURS"], "")

    def test_chave_nova_vai_para_o_fim(self):
        env_io.escrever(self.arquivo, {"PAINEL_CHAT_MAX_POR_HORA": "5"})
        self.assertTrue(
            self.arquivo.read_text(encoding="utf-8").rstrip().endswith("PAINEL_CHAT_MAX_POR_HORA=5")
        )

    def test_valor_igual_nao_reescreve_nem_faz_backup(self):
        resultado = env_io.escrever(self.arquivo, {"TEMAS_LIMIAR": "8"})
        self.assertEqual(resultado.mudadas, {})
        self.assertEqual(resultado.iguais, ("TEMAS_LIMIAR",))
        self.assertIsNone(env_io.tem_backup(self.arquivo))
        self.assertEqual(self.arquivo.read_bytes(), self.antes)

    def test_duplicata_e_removida_ao_salvar(self):
        self.arquivo.write_text("A=1\nB=2\nA=3\n", encoding="utf-8")
        env_io.escrever(self.arquivo, {"A": "9"})
        self.assertEqual(self.arquivo.read_text(encoding="utf-8"), "A=9\nB=2\n")

    def test_backup_e_env_ficam_com_modo_0600(self):
        """O arquivo tem o token do Discord. Backup legível pelo mundo seria o
        mesmo segredo, com outro nome."""
        resultado = env_io.escrever(self.arquivo, {"TEMAS_LIMIAR": "9"})
        self.assertEqual(oct(self.arquivo.stat().st_mode)[-3:], "600")
        self.assertEqual(oct(resultado.backup.stat().st_mode)[-3:], "600")

    def test_desfazer_devolve_o_arquivo_byte_identico(self):
        """Critério de pronto. Um rollback que reformatasse não seria rollback,
        seria uma terceira versão."""
        env_io.escrever(self.arquivo, {"DLB_SYNC_EVERY_HOURS": "3", "TEMAS_LIMIAR": "12"})
        self.assertNotEqual(self.arquivo.read_bytes(), self.antes)
        self.assertTrue(env_io.restaurar(self.arquivo))
        self.assertEqual(self.arquivo.read_bytes(), self.antes)

    def test_desfazer_sem_backup_devolve_falso_em_vez_de_explodir(self):
        self.assertFalse(env_io.restaurar(self.arquivo))

    def test_escrever_em_arquivo_que_nao_existe_cria(self):
        novo = Path(self._tmp.name) / "outro.env"
        env_io.escrever(novo, {"TEMAS_LIMIAR": "4"})
        self.assertEqual(env_io.ler(novo), {"TEMAS_LIMIAR": "4"})


class TestTelaDeConfig(PainelCase):
    """A tela e a fronteira, pelo HTTP."""

    def setUp(self):
        super().setUp()
        self.env = self.root / ".env"
        self.env.write_text(
            "DISCORD_TOKEN=segredo-de-verdade\nDLB_SYNC_EVERY_HOURS=6\nTEMAS_LIMIAR=8\n",
            encoding="utf-8",
        )
        self.aplicou = []
        self._aplicar = compose.aplicar
        compose.aplicar = self.aplicar_falso

    def tearDown(self):
        compose.aplicar = self._aplicar
        super().tearDown()

    def aplicar_falso(self, raiz, timeout=None):
        self.aplicou.append(Path(raiz))
        return {"ok": True, "codigo": 0, "saida": "Container dlb-prod Recreated", "comando": "./dlb.sh prod up"}

    def csrf(self):
        _, _, html = self.pegar("/config")
        marca = 'name=csrf value="'
        inicio = html.index(marca) + len(marca)
        return html[inicio : html.index('"', inicio)]

    def postar(self, rota, campos, cabecalhos=None, csrf=True, seguir=True):
        """POST no painel.

        `urlopen` **segue** o 303 por conta própria, então com `seguir=True` o
        corpo devolvido é o da página seguinte — que é justamente onde a faixa de
        resultado aparece, e onde ela é consumida. Um GET extra depois já não a
        veria. Com `seguir=False` dá para conferir o próprio 303.
        """
        dados = dict(campos)
        if csrf:
            dados["csrf"] = self.csrf()
        corpo = urllib.parse.urlencode(dados).encode("utf-8")
        pedido = urllib.request.Request(
            self.base + rota, data=corpo, method="POST", headers=cabecalhos or {}
        )
        abrir = urllib.request.urlopen if seguir else self._sem_seguir().open
        try:
            with abrir(pedido, timeout=10) as resposta:
                return resposta.status, resposta.read().decode("utf-8")
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8")

    @staticmethod
    def _sem_seguir():
        class Parado(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        return urllib.request.build_opener(Parado)

    # -------------------------------------------------------------- a tela

    def test_mostra_os_grupos_e_nao_devolve_o_segredo(self):
        codigo, _, html = self.pegar("/config")
        self.assertEqual(codigo, 200)
        for grupo in ("Discord", "Agendamento", "Histórico", "Painel"):
            self.assertIn(grupo, html)
        self.assertNotIn("segredo-de-verdade", html)
        self.assertIn('type="password"', html)
        self.assertIn("deixe em branco para manter", html)

    def test_o_ciclo_salvar_e_a_faixa_de_pendente(self):
        """Critério de pronto: mudar a grade → "salvo, não aplicado" → aplicar."""
        compose.inspecionar = lambda *a, **k: {
            "container": "dlb-prod", "rodando": True, "status": "running", "saude": "healthy",
            "desde": None, "criado": None, "imagem": "x",
            "env": {"DLB_SYNC_EVERY_HOURS": "6"},
        }
        codigo, html = self.postar("/config/salvar", {"DLB_SYNC_EVERY_HOURS": "3"})
        self.assertEqual(codigo, 200)          # 303 seguido pelo urlopen
        self.assertEqual(env_io.ler(self.env)["DLB_SYNC_EVERY_HOURS"], "3")
        self.assertIn("Salvo em", html)
        self.assertIn("Salvo, ainda não aplicado", html)
        self.assertIn("DLB_SYNC_EVERY_HOURS", html)

        _, html = self.postar("/config/aplicar", {})
        self.assertEqual(self.aplicou, [self.env.parent])
        self.assertIn("Container recriado", html)

    def test_o_post_responde_303_para_recarregar_nao_reenviar(self):
        codigo, _ = self.postar("/config/salvar", {"TEMAS_LIMIAR": "9"}, seguir=False)
        self.assertEqual(codigo, 303)

    def test_o_aviso_aparece_uma_vez_e_some(self):
        """A faixa é consumida no GET que a mostra: recarregar não a repete, e ela
        não fica grudada na tela para sempre."""
        _, html = self.postar("/config/salvar", {"TEMAS_LIMIAR": "9"})
        self.assertIn("Salvo em", html)
        self.assertNotIn("Salvo em", self.pegar("/config")[2])

    def test_chave_que_so_o_painel_le_nao_vira_pendente(self):
        """Apareceu configurando `PAINEL_TOKEN` de verdade: as chaves do painel
        constavam como "salvo, não aplicado", o que mandaria recriar o container
        por causa de uma configuração que ele não usa — e o painel só passaria a
        usá-la ao reiniciar, não com o `up`."""
        self.env.write_text(
            "DISCORD_TOKEN=x\nPAINEL_TOKEN=segredo\nPAINEL_HOSTS=dotcom\n"
            "PAINEL_CHAT_MAX_POR_HORA=5\n",
            encoding="utf-8",
        )
        compose.inspecionar = lambda *a, **k: {
            "container": "dlb-prod", "rodando": True, "status": "running", "saude": None,
            "desde": None, "criado": None, "imagem": "x", "env": {},
        }
        pendentes = [p["chave"] for p in json.loads(self.pegar("/api/config")[2])["pendentes"]]
        self.assertEqual([p for p in pendentes if p.startswith("PAINEL_")], [])

    def test_a_tela_diz_que_chave_do_painel_vale_ao_reiniciar_o_painel(self):
        _, _, html = self.pegar("/config")
        self.assertIn("Vale ao reiniciar o painel", html)

    def test_avisa_que_aplicar_dispara_um_ciclo_na_hora(self):
        """Descoberto aplicando de verdade: com `DLB_RUN_ON_START=1` o container
        recém-criado roda o ciclo imediatamente. Três aplicares seguidos mastigaram
        nove dias de histórico e gastaram modelo que ninguém agendou."""
        compose.inspecionar = lambda *a, **k: {
            "container": "dlb-prod", "rodando": True, "status": "running", "saude": "healthy",
            "desde": None, "criado": None, "imagem": "x",
            "env": {"DLB_RUN_ON_START": "1", "DLB_BACKFILL": "1", "DLB_RESSURGIR": "1"},
        }
        _, _, html = self.pegar("/config")
        self.assertIn("dispara um sync na hora", html)
        self.assertIn("backfill", html)
        self.assertIn("ressurgir", html)

    def test_sem_run_on_start_nao_avisa_nada(self):
        compose.inspecionar = lambda *a, **k: {
            "container": "dlb-prod", "rodando": True, "status": "running", "saude": None,
            "desde": None, "criado": None, "imagem": "x",
            "env": {"DLB_RUN_ON_START": "0"},
        }
        _, _, html = self.pegar("/config")
        self.assertNotIn("dispara um sync na hora", html)

    def test_valor_invalido_recusa_com_a_mensagem_e_nao_escreve_nada(self):
        """Critério de pronto. Meia validação escreveria metade do formulário."""
        antes = self.env.read_bytes()
        _, html = self.postar("/config/salvar", {"DLB_SINTESE_DIA": "99", "TEMAS_LIMIAR": "12"})
        self.assertIn("Nada foi salvo", html)
        self.assertIn("máximo é 7", html)
        self.assertEqual(self.env.read_bytes(), antes)

    def test_segredo_em_branco_mantem_o_que_estava(self):
        """Sem isto, abrir a tela e salvar apagaria o token do Discord."""
        self.postar("/config/salvar", {"DISCORD_TOKEN": "", "TEMAS_LIMIAR": "11"})
        self.assertEqual(env_io.ler(self.env)["DISCORD_TOKEN"], "segredo-de-verdade")
        self.assertEqual(env_io.ler(self.env)["TEMAS_LIMIAR"], "11")

    def test_salvar_o_formulario_inteiro_nao_incha_o_arquivo(self):
        """O caso real da tela: o navegador posta os 55 campos, quase todos vazios."""
        todos = {campo.chave: "" for campo in CONFIG_SCHEMA}
        todos["TEMAS_LIMIAR"] = "8"          # igual ao que já está
        antes_linhas = len(self.env.read_text(encoding="utf-8").splitlines())
        self.postar("/config/salvar", todos)
        self.assertEqual(
            len(self.env.read_text(encoding="utf-8").splitlines()), antes_linhas
        )
        self.assertEqual(env_io.ler(self.env)["DISCORD_TOKEN"], "segredo-de-verdade")

    def test_segredo_preenchido_e_gravado(self):
        self.postar("/config/salvar", {"DISCORD_TOKEN": "token-novo"})
        self.assertEqual(env_io.ler(self.env)["DISCORD_TOKEN"], "token-novo")

    def test_desfazer_pela_tela(self):
        antes = self.env.read_bytes()
        self.postar("/config/salvar", {"TEMAS_LIMIAR": "42"})
        _, html = self.postar("/config/desfazer", {})
        self.assertIn("voltou ao backup", html)
        self.assertEqual(self.env.read_bytes(), antes)

    def test_aplicar_entra_no_historico_de_execucoes(self):
        """Recriar o container é job — "por que o painel caiu às 3h" tem resposta."""
        self.postar("/config/aplicar", {})
        _, _, html = self.pegar("/execucoes")
        self.assertIn("aplicar", html)

    def test_aplicar_que_falha_mostra_a_saida_do_comando(self):
        compose.aplicar = lambda raiz, timeout=None: {
            "ok": False, "codigo": 1, "saida": "erro: porta 8765 já em uso",
            "comando": "./dlb.sh prod up",
        }
        _, html = self.postar("/config/aplicar", {})
        self.assertIn("O aplicar falhou", html)
        self.assertIn("porta 8765", html)

    # --------------------------------------------------------- a fronteira

    def test_post_sem_csrf_e_403_e_nao_escreve(self):
        antes = self.env.read_bytes()
        codigo, corpo = self.postar("/config/salvar", {"TEMAS_LIMIAR": "99"}, csrf=False)
        self.assertEqual(codigo, 403)
        self.assertIn("token de formulário", corpo)
        self.assertEqual(self.env.read_bytes(), antes)

    def test_csrf_de_outro_processo_nao_serve(self):
        codigo, _ = self.postar("/config/salvar", {"csrf": "x" * 43, "TEMAS_LIMIAR": "99"}, csrf=False)
        self.assertEqual(codigo, 403)

    def test_origem_cruzada_e_403_antes_do_csrf(self):
        """Navegador manda `Origin` em POST cross-site. É a checagem que pega o
        caso real; o token pega o cliente que não é navegador."""
        codigo, corpo = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "99"},
            cabecalhos={"Origin": "http://mau.exemplo"},
        )
        self.assertEqual(codigo, 403)
        self.assertIn("origem cruzada", corpo)

    def test_origem_de_nome_publico_atras_de_proxy_passa(self):
        """O defeito que apareceu no uso real, e o motivo de a checagem ter mudado.

        Com proxy na frente — `tailscale serve`, nginx, qualquer um — o navegador
        manda `Origin: https://maquina.SEU-TAILNET.ts.net` e o proxy reescreve o `Host`
        para `127.0.0.1:8765`. Comparar um com o outro recusava um login legítimo
        com "origem cruzada recusada". Agora a comparação é com a lista de nomes
        que o painel atende, declarada em `PAINEL_HOSTS`.
        """
        self.servidor.estado.cfg.painel_hosts = "maquina.SEU-TAILNET.ts.net"
        codigo, _ = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "17"},
            cabecalhos={"Origin": "https://maquina.SEU-TAILNET.ts.net"},
            seguir=False,
        )
        self.assertEqual(codigo, 303)
        self.assertEqual(env_io.ler(self.env)["TEMAS_LIMIAR"], "17")

    def test_porta_diferente_no_origin_nao_reprova(self):
        """Atrás de proxy a porta pública (443) não é a interna. Exigir porta igual
        reintroduziria o defeito de cima."""
        self.servidor.estado.cfg.painel_hosts = "painel.exemplo"
        codigo, _ = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "19"},
            cabecalhos={"Origin": "https://painel.exemplo"}, seguir=False,
        )
        self.assertEqual(codigo, 303)

    def test_referer_serve_quando_nao_ha_origin(self):
        codigo, _ = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "21"},
            cabecalhos={"Referer": f"http://127.0.0.1:{self.servidor.server_port}/config"},
            seguir=False,
        )
        self.assertEqual(codigo, 303)

    def test_referer_de_outro_site_e_recusado(self):
        codigo, corpo = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "23"},
            cabecalhos={"Referer": "http://mau.exemplo/pagina"},
        )
        self.assertEqual(codigo, 403)
        self.assertIn("origem cruzada", corpo)

    def test_origem_opaca_nao_bloqueia_o_formulario(self):
        """O defeito que o Dotcom levou na cara, e a causa era minha.

        Eu mandava `Referrer-Policy: no-referrer` em toda resposta. O navegador
        obedece: não manda `Referer` **e** serializa a origem como opaca, mandando
        `Origin: null` no POST do formulário. O painel então recusava o próprio
        formulário. Nenhum teste pegou porque teste manda os cabeçalhos que o teste
        quer, não os que o navegador decide mandar.

        `null` é ausência de informação, não hostilidade. Quem decide nesse caso é
        o token de CSRF, que uma página de outra origem não consegue ler.
        """
        codigo, _ = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "27"},
            cabecalhos={"Origin": "null"}, seguir=False,
        )
        self.assertEqual(codigo, 303)
        self.assertEqual(env_io.ler(self.env)["TEMAS_LIMIAR"], "27")

    def test_origem_opaca_ainda_precisa_do_csrf(self):
        """Aceitar `null` não pode virar porta aberta: sem o token, 403."""
        codigo, _ = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "29"},
            cabecalhos={"Origin": "null"}, csrf=False,
        )
        self.assertEqual(codigo, 403)

    def test_a_politica_de_referrer_nao_apaga_a_propria_origem(self):
        """`no-referrer` é o que provoca `Origin: null`. `same-origin` mantém a
        privacidade que interessa — nada vaza para terceiro — sem cegar o painel
        sobre as requisições dele mesmo."""
        _, cabecalhos, _ = self.pegar("/config")
        self.assertEqual(cabecalhos["Referrer-Policy"], "same-origin")

    def test_a_recusa_diz_o_que_viu_e_o_que_aceita(self):
        """"origem cruzada recusada" sozinho custou uma ida e volta de adivinhação:
        sem o valor do `Origin` ninguém sabia se era proxy, nome diferente ou
        processo velho. São cabeçalhos do próprio navegador de quem pediu."""
        _, corpo = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "23"},
            cabecalhos={"Origin": "http://mau.exemplo"},
        )
        dados = json.loads(corpo)
        self.assertEqual(dados["origin_recebido"], "http://mau.exemplo")
        self.assertIn("127.0.0.1", dados["nomes_aceitos"])
        self.assertIn("PAINEL_HOSTS", dados["como_resolver"])

    def test_origem_igual_ao_host_passa(self):
        codigo, _ = self.postar(
            "/config/salvar", {"TEMAS_LIMIAR": "13"},
            cabecalhos={"Origin": f"http://127.0.0.1:{self.servidor.server_port}"},
            seguir=False,
        )
        self.assertEqual(codigo, 303)
        self.assertEqual(env_io.ler(self.env)["TEMAS_LIMIAR"], "13")

    def test_corpo_gigante_e_413(self):
        corpo = urllib.parse.urlencode({"TEMAS_LIMIAR": "9" * 300000}).encode("utf-8")
        pedido = urllib.request.Request(self.base + "/config/salvar", data=corpo, method="POST")
        try:
            with urllib.request.urlopen(pedido, timeout=10) as r:
                self.fail(f"devia recusar, veio {r.status}")
        except urllib.error.HTTPError as erro:
            self.assertEqual(erro.code, 413)

    def test_rota_de_mutacao_desconhecida_e_405(self):
        codigo, _ = self.postar("/config/apagar-tudo", {}, csrf=False)
        self.assertEqual(codigo, 405)

    # ----------------------------------------------------- degradação

    def test_o_painel_sobrevive_a_env_que_nao_parseia(self):
        """Critério de pronto: com `.env` estragado, a tela de config abre e diz o
        que está errado, em vez de traceback na home."""
        self.env.write_text(
            "isto não é env\nDISCORD_TOKEN\n=sem chave\nTEMAS_LIMIAR=8\n", encoding="utf-8"
        )
        for rota in ("/", "/config", "/agenda", "/api/config"):
            with self.subTest(rota):
                self.assertEqual(self.pegar(rota)[0], 200)
        self.assertEqual(env_io.ler(self.env), {"TEMAS_LIMIAR": "8"})

    def test_api_config_nao_devolve_valor_de_segredo(self):
        """A API é a mesma fronteira da tela: segredo não sai por nenhuma das duas."""
        dados = json.loads(self.pegar("/api/config")[2])
        self.assertNotIn("segredo-de-verdade", json.dumps(dados))


class TestAcoesNaTela(PainelCase):
    """As ações pelo HTTP: mesma fronteira de mutação da configuração."""

    def setUp(self):
        super().setUp()
        (self.root / ".env").write_text("DISCORD_TOKEN=x\n", encoding="utf-8")
        from tests.test_vault import note

        self.nota = self.vault.write_link_note(
            "estudos", note("https://e.com/a", "Relays", ["Nostr"])
        ).path.stem
        self.rodou = []
        self._rodar = acoes.rodar
        acoes.rodar = lambda cfg, comando, timeout=None: (
            self.rodou.append(comando),
            acoes.Execucao(comando, True, 0, "pronto"),
        )[1]

    def tearDown(self):
        acoes.rodar = self._rodar
        super().tearDown()

    def csrf(self):
        _, _, html = self.pegar("/acervo")
        marca = 'name=csrf value="'
        inicio = html.index(marca) + len(marca)
        return html[inicio : html.index('"', inicio)]

    def postar(self, rota, campos, cabecalhos=None, csrf=True):
        dados = dict(campos)
        if csrf:
            dados["csrf"] = self.csrf()
        corpo = urllib.parse.urlencode(dados).encode("utf-8")
        pedido = urllib.request.Request(
            self.base + rota, data=corpo, method="POST", headers=cabecalhos or {}
        )
        try:
            with urllib.request.urlopen(pedido, timeout=10) as resposta:
                return resposta.status, resposta.read().decode("utf-8")
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8")

    def test_a_tabela_do_acervo_tem_o_controle_de_triagem(self):
        _, _, html = self.pegar("/acervo")
        self.assertIn('action="/acervo/triar"', html)
        for opcao in ("inbox", "lido", "aplicado", "descartado"):
            self.assertIn(f'<option value="{opcao}"', html)

    def test_triar_pela_tela_grava_e_avisa(self):
        codigo, html = self.postar("/acervo/triar", {"nota": self.nota, "para": "aplicado"})
        self.assertEqual(codigo, 200)          # 303 seguido
        self.assertIn("triagem", html)
        self.assertIn("aplicado", html)
        from dlb.corpus import ler_links

        self.assertEqual(ler_links(self.root)[0].triagem, "aplicado")

    def test_triar_volta_para_a_tela_de_onde_veio(self):
        """Triar do chat volta para o chat; do acervo, para o acervo — com o filtro
        que estava aplicado. Voltar sempre para a home perderia o contexto."""
        _, html = self.postar(
            "/acervo/triar", {"nota": self.nota, "para": "lido", "volta": "/chat"}
        )
        self.assertIn("Chat sobre o acervo", html)

    def test_triar_sem_csrf_e_403_e_nao_grava(self):
        codigo, _ = self.postar(
            "/acervo/triar", {"nota": self.nota, "para": "descartado"}, csrf=False
        )
        self.assertEqual(codigo, 403)
        from dlb.corpus import ler_links

        self.assertEqual(ler_links(self.root)[0].triagem, "inbox")

    def test_triar_de_outra_origem_e_403(self):
        codigo, _ = self.postar(
            "/acervo/triar", {"nota": self.nota, "para": "lido"},
            cabecalhos={"Origin": "http://mau.exemplo"},
        )
        self.assertEqual(codigo, 403)

    def test_nota_inexistente_vira_aviso_e_nao_500(self):
        """Recusa é resposta, não erro: o painel diz o motivo e segue."""
        codigo, html = self.postar("/acervo/triar", {"nota": "não existe", "para": "lido"})
        self.assertEqual(codigo, 200)
        self.assertIn("não achei a nota", html)

    def test_fuga_de_diretorio_pela_url_e_recusada(self):
        codigo, html = self.postar(
            "/acervo/triar", {"nota": "../../.ssh/id_rsa", "para": "lido"}
        )
        self.assertEqual(codigo, 200)
        self.assertIn("inválido", html)

    def test_execucoes_tem_os_quatro_botoes(self):
        _, _, html = self.pegar("/execucoes")
        self.assertIn('action="/executar"', html)
        for comando in ("sync", "backfill", "sintese", "temas"):
            self.assertIn(f'value="{comando}"', html)

    def test_disparar_comando_pela_tela(self):
        codigo, html = self.postar("/executar", {"comando": "sync"})
        self.assertEqual(codigo, 200)
        self.assertEqual(self.rodou, ["sync"])
        self.assertIn("sync terminou", html)

    def test_comando_recusado_pelo_lock_aparece_como_atencao(self):
        acoes.rodar = lambda cfg, comando, timeout=None: acoes.Execucao(
            comando, False, 3, "Recusado: sync já está rodando", recusado=True
        )
        _, html = self.postar("/executar", {"comando": "sync"})
        self.assertIn("faixa-atencao", html)
        self.assertIn("está com o lock", html)

    def test_comando_fora_da_lista_branca_nao_roda(self):
        acoes.rodar = self._rodar          # o de verdade, que tem a lista branca
        _, html = self.postar("/executar", {"comando": "ressurgir"})
        self.assertIn("não é permitido", html)

    def test_disparar_sem_csrf_e_403(self):
        codigo, _ = self.postar("/executar", {"comando": "sync"}, csrf=False)
        self.assertEqual(codigo, 403)
        self.assertEqual(self.rodou, [])


if __name__ == "__main__":
    unittest.main()
