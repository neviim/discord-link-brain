"""O chat do painel: `busca.buscar()` reusado, com fila de um e teto por hora.

Não há um segundo mecanismo de busca aqui. A pergunta vai para a **mesma função**
que `dlb buscar` chama, com o mesmo `cfg` e o mesmo acervo — então a resposta na
tela e a resposta no terminal são a mesma resposta, por construção e não por
disciplina. Duas implementações de busca na mesma ferramenta divergiriam no dia em
que alguém ajustasse o prompt de uma delas.

O que este módulo acrescenta é **contenção**, e ela existe por um motivo concreto:
o modelo roda por binário local (`codex`, `claude`) já logado na máquina, sem chave
de API. O painel é o **terceiro** consumidor desse mesmo login — classificação
diária, síntese/responder e agora o chat. Se o painel come a janela de uso, a
classificação da noite falha, e o painel reporta um erro que ele mesmo causou.

Três limites, nesta ordem:

1. **Fila de um.** Uma chamada de modelo por vez no painel inteiro, e no máximo um
   esperando. A segunda pergunta aguarda a primeira; a terceira é recusada na hora
   em vez de empilhar processos `codex`.
2. **Teto por hora**, contado em `.state/painel.json` — arquivo próprio, escritor
   próprio, como `state.py` exige. Sobrevive a reinício do painel de propósito: o
   teto existe para proteger a quota, e quota não reinicia junto com o processo.
3. **O timeout é o do runner** (`SINTESE_TIMEOUT`), não um novo. Um segundo
   timeout no painel não conseguiria matar o subprocess do modelo — ele só
   mentiria sobre ter matado. O que o painel cronometra é a **espera na fila**.

Sem streaming de token: `busca.buscar()` chama runners que usam
`subprocess.run(capture_output=True)`, então a saída só existe quando o processo
termina. A tela transmite progresso ("na fila", "perguntando ao modelo"), nunca
texto pela metade — prometer streaming aqui seria desenhar uma barra falsa.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import busca
from .config import Config
from .corpus import LinkEntry

ARQUIVO_USO = "painel.json"

# Quantos podem estar esperando além do que está rodando. Um: a segunda pergunta
# espera, a terceira ouve "espere a atual terminar". Empilhar dez pedidos numa
# fila que consome quota é como se gasta a janela de uso sem perceber.
FILA_MAXIMA = 1


@dataclass
class Registro:
    """Uma pergunta e o que aconteceu com ela. Vira linha na tela."""

    pergunta: str
    quando: str
    texto: str = ""
    confianca: str = "nenhuma"
    considerados: int = 0
    truncado: bool = False
    usados: list[dict] = field(default_factory=list)
    erro: str = ""
    duracao_s: float = 0.0
    esperou_s: float = 0.0
    chamou_modelo: bool = False

    def como_dict(self) -> dict:
        return dict(vars(self))


class Uso:
    """Contagem de chamadas de modelo do painel, em arquivo próprio.

    Espelha o que o `responder` já faz com `respostas[]` no cursor dele: guarda os
    instantes da última hora e descarta o resto. Não é métrica — é o dado que faz
    o teto valer e que a tela mostra para o Dotcom saber quanto da janela de uso o
    painel consumiu.
    """

    def __init__(self, state_dir: Path):
        self.caminho = Path(state_dir) / ARQUIVO_USO
        self._trava = threading.Lock()

    def _ler(self) -> list[float]:
        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        chamadas = dados.get("chamadas") if isinstance(dados, dict) else None
        return [c for c in (chamadas or []) if isinstance(c, (int, float))]

    def _gravar(self, chamadas: list[float]) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho.with_suffix(".json.tmp")
        temporario.write_text(
            json.dumps(
                {
                    "chamadas": chamadas,
                    "atualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporario, self.caminho)

    def na_ultima_hora(self, agora: float | None = None) -> list[float]:
        agora = time.time() if agora is None else agora
        return [c for c in self._ler() if agora - c < 3600]

    def registrar(self, agora: float | None = None) -> int:
        """Anota uma chamada e devolve quantas há na última hora, contando ela."""
        agora = time.time() if agora is None else agora
        with self._trava:
            recentes = self.na_ultima_hora(agora) + [agora]
            self._gravar(recentes)
            return len(recentes)


class Chat:
    """Estado do chat num processo de painel. Um por servidor."""

    def __init__(self, cfg: Config, ler_acervo, historico_max: int = 20):
        self.cfg = cfg
        self.ler_acervo = ler_acervo
        self.uso = Uso(cfg.state_dir)
        self.historico: list[Registro] = []
        self.historico_max = historico_max
        self._semaforo = threading.BoundedSemaphore(1)
        self._esperando = 0
        self._contagem = threading.Lock()

    # --------------------------------------------------------------- estado

    @property
    def ocupado(self) -> bool:
        with self._contagem:
            return self._esperando > 0

    def situacao(self) -> dict:
        recentes = self.uso.na_ultima_hora()
        return {
            "ocupado": self.ocupado,
            "chamadas_na_hora": len(recentes),
            "teto_por_hora": self.cfg.painel_chat_max_por_hora,
            "backend": self.cfg.busca_model_backend,
            "espera_maxima_s": self.cfg.painel_chat_espera_s,
        }

    # -------------------------------------------------------------- resposta

    def perguntar(self, pergunta: str) -> Registro:
        """Responde, ou explica por que não respondeu. Nunca levanta.

        A tela do painel não pode ter caminho que estoure: ela é aberta justamente
        quando algo já está estranho, e uma pergunta malfeita não é motivo para 500.
        """
        pergunta = (pergunta or "").strip()
        agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        registro = Registro(pergunta=pergunta, quando=agora)

        if not pergunta:
            registro.erro = "pergunta vazia"
            return registro
        if len(pergunta) > self.cfg.responder_max_pergunta:
            registro.erro = (
                f"pergunta com {len(pergunta)} caracteres — o teto é "
                f"{self.cfg.responder_max_pergunta}, o mesmo do bot no Discord."
            )
            return registro

        recentes = self.uso.na_ultima_hora()
        if len(recentes) >= self.cfg.painel_chat_max_por_hora:
            registro.erro = (
                f"teto de {self.cfg.painel_chat_max_por_hora} chamadas por hora atingido "
                f"({len(recentes)} na última hora). O painel divide a janela de uso do "
                "modelo com a classificação diária e com o responder."
            )
            return registro

        with self._contagem:
            if self._esperando > FILA_MAXIMA:
                registro.erro = (
                    "já tem uma pergunta rodando e outra esperando. Espere a atual "
                    "terminar — o painel faz uma chamada de modelo por vez."
                )
                return registro
            self._esperando += 1

        try:
            inicio_espera = time.monotonic()
            if not self._semaforo.acquire(timeout=self.cfg.painel_chat_espera_s):
                registro.esperou_s = round(time.monotonic() - inicio_espera, 2)
                registro.erro = (
                    f"esperei {int(registro.esperou_s)}s na fila e a pergunta anterior "
                    "não terminou. Tente de novo em instantes."
                )
                return registro
            registro.esperou_s = round(time.monotonic() - inicio_espera, 2)
            try:
                self._responder(registro)
            finally:
                self._semaforo.release()
        finally:
            with self._contagem:
                self._esperando -= 1
            self._guardar(registro)
        return registro

    def _responder(self, registro: Registro) -> None:
        t0 = time.monotonic()
        try:
            acervo: list[LinkEntry] = self.ler_acervo()
            # A chamada é contada **antes** de acontecer: se o processo do modelo
            # travar ou o painel morrer no meio, a quota foi gasta de qualquer
            # forma. Contar depois faria o teto proteger menos justamente nos
            # casos ruins.
            self.uso.registrar()
            registro.chamou_modelo = True
            resposta = busca.buscar(self.cfg, registro.pergunta, acervo)
        except busca.BuscaError as exc:
            registro.erro = str(exc)
        except Exception as exc:  # noqa: BLE001
            registro.erro = f"{type(exc).__name__}: {exc}"
        else:
            registro.texto = resposta.texto
            registro.confianca = resposta.confianca
            registro.considerados = resposta.considerados
            registro.truncado = resposta.truncado
            registro.usados = [
                {"nome": e.nome, "titulo": e.titulo, "url": e.url, "dia": e.dia}
                for e in resposta.usados
            ]
        finally:
            registro.duracao_s = round(time.monotonic() - t0, 2)

    def _guardar(self, registro: Registro) -> None:
        """Histórico **em memória**, e isso é uma escolha, não um esquecimento.

        Persistir transcrição pede decidir onde ela mora, quanto tempo fica e quem
        pode ler — e o painel ainda não tem autenticação. Reiniciar o painel perde
        o histórico; a resposta em si nunca foi o artefato, o acervo é.
        """
        self.historico.insert(0, registro)
        del self.historico[self.historico_max :]
