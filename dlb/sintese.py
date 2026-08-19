"""Síntese periódica: o que a semana foi, não o que ela teve.

A diferença entre isto e `dlb semana` é grande. O `semana` concatena o que já
está escrito e imprime; nunca gasta LLM e nunca toca o vault. A `sintese` faz uma
chamada de modelo e **escreve uma nota** — temas recorrentes, o que se conecta com
o que, e perguntas em aberto. É o que converte fluxo em conhecimento: ler sete
resumos no fim do mês dá uma leitura do seu próprio interesse que nota diária
nenhuma dá.

Backend padrão é o `codex`, e não o `claude-cli` que classifica: são duas
assinaturas diferentes, e o `docker/scheduler.sh` já vive a tensão entre a grade
de 6h e a janela de uso de 5h. Tirar a síntese dessa disputa é de graça.

Os runners aqui são separados dos de `classify.py` de propósito: lá o
`SYSTEM_PROMPT` está embutido na função, e parametrizá-lo quebraria os 27 testes
que trocam `_run_claude_cli` como global do módulo. Quando aparecer um terceiro
consumidor, é a hora de extrair um `llm.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .corpus import LinkEntry

SYSTEM_PROMPT = textwrap.dedent(
    """
    Você sintetiza uma semana de leitura de uma pessoa, a partir dos links que
    ela capturou e dos resumos já escritos sobre cada um.

    O que se espera de você:

    - `panorama`: 2 a 3 frases sobre o que a semana FOI. Não uma contagem, não
      uma lista — uma leitura. "A semana foi sobre X, com um desvio para Y."
    - `temas`: de 3 a 5. Cada um diz **o que se repetiu e por que aqueles links
      pertencem juntos**. Se você apenas reescrever os resumos um por um, você
      falhou: tema é o que emerge de vários links, não o rótulo de um.
    - `conexoes`: pares que se encontram. Cada uma nomeia **os dois lados** e diz
      o que os liga, em `texto`, e lista os `id` envolvidos em `links`. Se não
      houver conexão real, devolva lista vazia — inventar conexão é pior que não
      ter.
    - `perguntas`: exatamente 2 ou 3, em aberto, respondíveis em princípio.
      Perguntas que a semana levanta e não responde. Nada retórico.

    Regras que não se negociam:

    - Escreva em português do Brasil.
    - Só use o que está nos dados. Nada de conhecimento externo sobre os assuntos.
    - Cite link por `id`, o número inteiro, **sempre no campo `links`** e nunca
      dentro da prosa: quem lê a nota não vê número nenhum, vê o link montado
      por nós. Escrever "links: 6, 7" no texto é erro.
    - **Nunca escreva uma URL** — os endereços são montados a partir dos dados,
      não do seu texto.
    - `id` que não exista nos dados é descartado em silêncio; não chute.
    - Se a semana tiver pouco material, diga isso no panorama em vez de inflar.
    """
).strip()

SYSTEM_PROMPT_MES = textwrap.dedent(
    """
    Você faz o rollup mensal de sínteses semanais já escritas.

    A entrada são as sínteses das semanas do mês, não os links crus. Seu trabalho
    é o nível acima: o que atravessou o mês inteiro, o que apareceu e morreu numa
    semana só, e para onde o interesse da pessoa está andando.

    - `panorama`: 2 a 4 frases sobre o arco do mês.
    - `temas`: de 3 a 5, os que **atravessaram semanas**. Diga em quais.
    - `conexoes`: o que uma semana começou e outra retomou.
    - `perguntas`: 2 ou 3 que o mês deixou abertas.

    Em português do Brasil. Só o que está nos dados. Sem URL. Uma semana citada é
    citada pelo rótulo (`2026-W34`), e `links` fica vazio no rollup mensal.
    """
).strip()

# Schema entregue ao `codex --output-schema`. Com ele o modelo devolve JSON
# válido sem cerca e sem tagarelice, o que torna o parsing quase desnecessário —
# mas a validação abaixo continua, porque a fronteira do LLM é porteiro
# obrigatório neste projeto, não confiança.
SCHEMA = {
    "type": "object",
    "properties": {
        "panorama": {"type": "string"},
        "temas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "texto": {"type": "string"},
                    "links": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["nome", "texto", "links"],
                "additionalProperties": False,
            },
        },
        "conexoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string"},
                    "links": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["texto", "links"],
                "additionalProperties": False,
            },
        },
        "perguntas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["panorama", "temas", "conexoes", "perguntas"],
    "additionalProperties": False,
}


class SinteseError(RuntimeError):
    pass


@dataclass
class Tema:
    nome: str
    texto: str
    links: list[int] = field(default_factory=list)


@dataclass
class Conexao:
    texto: str
    links: list[int] = field(default_factory=list)


@dataclass
class Sintese:
    panorama: str = ""
    temas: list[Tema] = field(default_factory=list)
    conexoes: list[Conexao] = field(default_factory=list)
    perguntas: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- entrada


def _payload(entradas: list[LinkEntry]) -> list[dict]:
    """O que vai para o modelo. Sem URL de propósito: assim ele não tem como
    emitir um endereço, e todo link do texto final é montado por nós."""
    itens = []
    for i, e in enumerate(entradas):
        item = {"id": i, "titulo": e.titulo, "dia": e.dia, "categoria": e.categoria}
        if e.resumo:
            item["resumo"] = e.resumo
        if e.porque_importa:
            item["porque_importa"] = e.porque_importa
        if e.conceitos:
            item["conceitos"] = e.conceitos
        itens.append(item)
    return itens


def build_prompt(rotulo: str, entradas: list[LinkEntry]) -> str:
    return (
        f"Semana {rotulo}. {len(entradas)} link(s) capturados.\n\n"
        + json.dumps(_payload(entradas), ensure_ascii=False, indent=2)
    )


def build_prompt_mes(mes: str, blocos: dict[str, str]) -> str:
    partes = [f"Mês {mes}. {len(blocos)} semana(s) com síntese.\n"]
    for semana in sorted(blocos):
        partes.append(f"\n## {semana}\n\n{blocos[semana].strip()}\n")
    return "".join(partes)


def hash_fonte(entradas: list[LinkEntry]) -> str:
    """Impressão digital do material da semana.

    Serve para não gastar uma chamada de 30–60s regenerando semana que não mudou.
    Cobre a URL e o resumo: link novo muda o hash, e reclassificação também.
    """
    import hashlib

    material = "\n".join(sorted(f"{e.url}\t{e.resumo}" for e in entradas))
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ backends


def _run_codex(cfg: Config, prompt: str, system: str) -> str:
    """`codex exec` com schema de saída. Sem sandbox de escrita, sem sessão."""
    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema.json"
        saida = Path(tmp) / "saida.txt"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")

        cmd = [
            cfg.codex_bin,
            "exec",
            "--ephemeral",           # não deixa arquivo de sessão para trás
            "--skip-git-repo-check",  # o container roda fora de repositório
            "-s", "read-only",        # é geração de texto; não executa nada
            "--color", "never",
            "--output-schema", str(schema),
            "-o", str(saida),
        ]
        if cfg.sintese_model:
            cmd += ["-m", cfg.sintese_model]
        cmd.append("-")               # prompt pela entrada padrão

        try:
            proc = subprocess.run(
                cmd,
                input=f"{system}\n\n---\n\n{prompt}",
                capture_output=True,
                text=True,
                timeout=cfg.sintese_timeout,
            )
        except FileNotFoundError as exc:
            raise SinteseError(
                f"binário `{cfg.codex_bin}` não encontrado. Instale o Codex CLI ou "
                "use SINTESE_BACKEND=claude-cli."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SinteseError(
                f"codex não respondeu em {cfg.sintese_timeout}s"
            ) from exc

        if proc.returncode != 0:
            erro = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise SinteseError(f"codex falhou (código {proc.returncode}): {erro}")
        if not saida.is_file():
            raise SinteseError("codex não escreveu a resposta no arquivo de saída")
        return saida.read_text(encoding="utf-8")


def _run_claude_cli(cfg: Config, prompt: str, system: str) -> str:
    cmd = [
        cfg.claude_bin,
        "-p",
        "--output-format", "json",
        "--model", cfg.sintese_model or cfg.llm_model,
        "--append-system-prompt", system,
    ]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=cfg.sintese_timeout,
        )
    except FileNotFoundError as exc:
        raise SinteseError(f"binário `{cfg.claude_bin}` não encontrado.") from exc
    except subprocess.TimeoutExpired as exc:
        raise SinteseError(f"claude não respondeu em {cfg.sintese_timeout}s") from exc
    if proc.returncode != 0:
        raise SinteseError(
            f"claude falhou (código {proc.returncode}): "
            f"{(proc.stderr or '').strip()[-500:]}"
        )
    try:
        return json.loads(proc.stdout)["result"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SinteseError("resposta do claude não veio no formato esperado") from exc


def _run_anthropic(cfg: Config, prompt: str, system: str) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SinteseError("ANTHROPIC_API_KEY não definida.")
    try:
        import anthropic
    except ImportError as exc:
        raise SinteseError("pacote `anthropic` não instalado.") from exc

    client = anthropic.Anthropic()
    partes: list[str] = []
    with client.messages.stream(
        model=cfg.sintese_model or cfg.llm_model,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for texto in stream.text_stream:
            partes.append(texto)
    return "".join(partes)


# -------------------------------------------------------------------- saída


def _parse_objeto(texto: str) -> dict:
    """Acha o objeto JSON mesmo cercado em ```json ou com tagarelice em volta.

    Com `codex --output-schema` isto é quase sempre trivial, mas o backend do
    `claude-cli` não tem schema e devolve texto livre — e a fronteira do LLM é
    porteiro obrigatório neste projeto.
    """
    limpo = texto.strip()
    limpo = re.sub(r"^```(?:json)?\s*", "", limpo)
    limpo = re.sub(r"\s*```$", "", limpo)
    try:
        dado = json.loads(limpo)
    except json.JSONDecodeError:
        inicio, fim = limpo.find("{"), limpo.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise SinteseError("resposta do modelo não tem objeto JSON")
        try:
            dado = json.loads(limpo[inicio : fim + 1])
        except json.JSONDecodeError as exc:
            raise SinteseError("resposta do modelo não é JSON válido") from exc
    if not isinstance(dado, dict):
        raise SinteseError("resposta do modelo não é um objeto")
    return dado


# O modelo às vezes escreve "Links: 6, 7" no fim da frase, apesar da instrução.
# Id interno não diz nada a quem lê a nota — o link montado diz.
IDS_NA_PROSA = re.compile(r"[\s.;]*\(?\b[Ll]inks?:\s*[\d,\s]+\)?\s*$")


def _sem_ids(texto: str) -> str:
    return IDS_NA_PROSA.sub("", texto).rstrip(" .,;") + "." if texto else texto


def _validar(dado: dict, total: int) -> Sintese:
    """A fronteira. Nada do modelo entra na nota sem passar por aqui."""
    s = Sintese(panorama=str(dado.get("panorama") or "").strip())

    for bruto in dado.get("temas") or []:
        if not isinstance(bruto, dict):
            continue
        nome = str(bruto.get("nome") or "").strip()
        if not nome:
            continue
        # `id` fora da faixa é descartado em silêncio — é o que impede o modelo
        # de apontar para link que não existe.
        links = [
            int(i) for i in (bruto.get("links") or [])
            if isinstance(i, int) and 0 <= i < total
        ]
        s.temas.append(
            Tema(nome=nome, texto=str(bruto.get("texto") or "").strip(), links=links)
        )

    for bruto in dado.get("conexoes") or []:
        # Aceita string solta também: o backend sem schema (claude-cli) pode
        # devolver a forma antiga, e perder a conexão seria pior que renderizá-la
        # sem link.
        if isinstance(bruto, str):
            if bruto.strip():
                s.conexoes.append(Conexao(texto=_sem_ids(bruto.strip())))
            continue
        if not isinstance(bruto, dict):
            continue
        texto = str(bruto.get("texto") or "").strip()
        if not texto:
            continue
        links = [
            int(i) for i in (bruto.get("links") or [])
            if isinstance(i, int) and 0 <= i < total
        ]
        s.conexoes.append(Conexao(texto=_sem_ids(texto), links=links))

    s.perguntas = [str(p).strip() for p in (dado.get("perguntas") or []) if str(p).strip()]
    return s


def _despachar(cfg: Config, prompt: str, system: str) -> str:
    backend = cfg.sintese_backend
    if backend == "codex":
        return _run_codex(cfg, prompt, system)
    if backend == "claude-cli":
        return _run_claude_cli(cfg, prompt, system)
    if backend == "anthropic":
        return _run_anthropic(cfg, prompt, system)
    raise SinteseError(f"SINTESE_BACKEND desconhecido: {backend}")


def gerar(cfg: Config, rotulo: str, entradas: list[LinkEntry]) -> Sintese:
    """A síntese de uma semana. Levanta `SinteseError` se o modelo falhar."""
    if not entradas:
        raise SinteseError(f"{rotulo}: nenhum link no período")
    bruto = _despachar(cfg, build_prompt(rotulo, entradas), SYSTEM_PROMPT)
    return _validar(_parse_objeto(bruto), len(entradas))


def gerar_mes(cfg: Config, mes: str, blocos: dict[str, str]) -> Sintese:
    """O rollup do mês, a partir das sínteses semanais — não dos links crus."""
    if not blocos:
        raise SinteseError(f"{mes}: nenhuma síntese semanal para consolidar")
    bruto = _despachar(cfg, build_prompt_mes(mes, blocos), SYSTEM_PROMPT_MES)
    return _validar(_parse_objeto(bruto), 0)
