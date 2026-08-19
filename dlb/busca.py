"""Responde uma pergunta a partir do acervo de `50_LINKS/`.

Sem embeddings, e a conta é simples: 70 notas somam ~42 mil caracteres, uns 12
mil tokens. O corpus **inteiro** cabe num prompt com folga de 10×, e mandar tudo
tem recall estritamente melhor que top-k por cosseno — não existe `k` para errar,
nem limiar de similaridade, nem chunk. Embeddings custariam instalar o Ollama,
baixar 275 MB, abrir a rede do container, mais um cache com chave de invalidação,
um reindexar e um GC — para *descartar* 62 de 70 documentos que já caberiam.

O ranqueamento existe mesmo assim, e não é enfeite: ele **ordena** para o corte
por orçamento de prompt. Hoje, com 70 notas, o corte nunca dispara e o ranking é
um no-op; com 300+ a degradação é gradual e o caminho de código é o mesmo.

Quando trocar por embeddings: `dlb doctor` imprime o tamanho do corpus contra
`BUSCA_MAX_CHARS`. Quando passar, ou quando a resposta começar a não achar coisa
que você sabe que está lá, escreva `_ranquear_embeddings` com a mesma assinatura
e acrescente um `elif`. O resto — prompt, validação, montagem dos links — já vale
para qualquer ranqueador.
"""

from __future__ import annotations

import json
import math
import re
import textwrap
import unicodedata
from dataclasses import dataclass, field

from .config import Config
from .corpus import LinkEntry
from .sintese import SinteseError, _parse_objeto, _run_anthropic, _run_claude_cli, _run_codex

# Palavras que aparecem em quase toda pergunta e não discriminam nada.
STOPWORDS = {
    "a", "à", "ao", "aos", "as", "às", "com", "como", "da", "das", "de", "do",
    "dos", "e", "em", "eu", "foi", "há", "isso", "já", "la", "lo", "mais", "me",
    "meu", "minha", "muito", "na", "nas", "no", "nos", "num", "numa", "o", "os",
    "ou", "para", "pela", "pelo", "por", "que", "qual", "quais", "quando",
    "se", "sem", "ser", "seu", "sobre", "sua", "tem", "ter", "tinha", "um",
    "uma", "vi", "voce", "você", "é",
}

# Peso por campo. Título e conceito valem mais porque são o que a pessoa lembra.
PESOS = (("titulo", 3), ("conceitos", 3), ("url", 2), ("resumo", 1), ("porque_importa", 1))

SYSTEM_PROMPT = textwrap.dedent(
    """
    Você responde perguntas sobre o acervo de links de uma pessoa. Os dados são
    o que ela salvou e o que já foi resumido sobre cada item.

    Isto é uma memória do que **ela** guardou, não uma enciclopédia. A pior falha
    possível é responder de cabeça sobre um assunto que não está no acervo: soa
    plausível e é mentira sobre o que ela tem.

    - Responda a pergunta, em português do Brasil, em no máximo 1200 caracteres.
      Texto corrido, não relatório.
    - Cite os itens que usou pelo `id`, no campo `usados`. **Nunca escreva uma
      URL** e nunca escreva o número na prosa — os links são montados por nós.
    - Se o acervo não tiver nada sobre aquilo, diga isso com franqueza e ponha
      `confianca: "nenhuma"`. Não improvise, não sugira o que ela "poderia"
      procurar.
    - Se tiver algo só tangencial, responda o que dá e diga que é tangencial,
      com `confianca: "media"`.
    """
).strip()

SCHEMA = {
    "type": "object",
    "properties": {
        "resposta": {"type": "string"},
        "usados": {"type": "array", "items": {"type": "integer"}},
        "confianca": {"type": "string", "enum": ["alta", "media", "nenhuma"]},
    },
    "required": ["resposta", "usados", "confianca"],
    "additionalProperties": False,
}


class BuscaError(RuntimeError):
    pass


@dataclass
class Resposta:
    texto: str = ""
    usados: list[LinkEntry] = field(default_factory=list)
    confianca: str = "nenhuma"
    considerados: int = 0
    truncado: bool = False


# ------------------------------------------------------------- ranqueamento


def normalizar(texto: str) -> str:
    """Sem acento e em caixa baixa: pergunta em português é digitada sem acento."""
    sem_marca = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return sem_marca.casefold()


def tokenizar(texto: str) -> list[str]:
    return [t for t in re.findall(r"\w+", normalizar(texto)) if t not in STOPWORDS]


def _campos(e: LinkEntry) -> dict[str, str]:
    return {
        "titulo": e.titulo,
        "conceitos": " ".join(e.conceitos),
        "url": e.url,
        "resumo": e.resumo,
        "porque_importa": e.porque_importa,
    }


def _ranquear_lexico(
    cfg: Config, pergunta: str, entradas: list[LinkEntry]
) -> list[tuple[float, LinkEntry]]:
    """(score, entrada) em ordem decrescente. Contrato de todo ranqueador."""
    termos = tokenizar(pergunta)
    if not termos:
        # Pergunta só com stopwords: devolve tudo, mais recente primeiro.
        return [(0.0, e) for e in sorted(entradas, key=lambda x: x.dia, reverse=True)]

    textos = [{k: normalizar(v) for k, v in _campos(e).items()} for e in entradas]
    total = len(entradas) or 1

    # IDF barato, para "IA" — que aparece em tudo — não dominar o ranking.
    idf = {}
    for termo in set(termos):
        df = sum(1 for campos in textos if any(termo in v for v in campos.values()))
        idf[termo] = math.log(1 + total / (1 + df))

    pontuados: list[tuple[float, LinkEntry]] = []
    for entrada, campos in zip(entradas, textos):
        score = 0.0
        for termo in termos:
            for nome, peso in PESOS:
                alvo = campos[nome]
                if not alvo:
                    continue
                # Prefixo para termo de 4+ letras: `agente` casa `agentes` sem
                # precisar de stemmer.
                if termo in alvo or (len(termo) >= 4 and re.search(rf"\b{re.escape(termo)}", alvo)):
                    score += peso * idf[termo]
        pontuados.append((score, entrada))

    # Empate desfeito pelo mais recente: acervo é cronológico.
    pontuados.sort(key=lambda par: (par[0], par[1].dia), reverse=True)
    return pontuados


def selecionar(
    cfg: Config, pergunta: str, entradas: list[LinkEntry]
) -> tuple[list[LinkEntry], bool]:
    """Ordena e corta pelo orçamento de prompt. Devolve (selecionadas, truncou)."""
    if cfg.busca_backend == "lexico-llm":
        ranqueadas = _ranquear_lexico(cfg, pergunta, entradas)
    else:
        raise BuscaError(f"BUSCA_BACKEND desconhecido: {cfg.busca_backend}")

    selecionadas: list[LinkEntry] = []
    gasto = 0
    for _score, entrada in ranqueadas:
        custo = len(entrada.titulo) + len(entrada.resumo) + len(entrada.porque_importa) + 80
        if gasto + custo > cfg.busca_max_chars and selecionadas:
            return selecionadas, True
        selecionadas.append(entrada)
        gasto += custo
    return selecionadas, False


# -------------------------------------------------------------------- prompt


def _payload(entradas: list[LinkEntry]) -> list[dict]:
    """Sem URL, como na síntese: o modelo não tem como emitir endereço."""
    itens = []
    for i, e in enumerate(entradas):
        item = {"id": i, "titulo": e.titulo, "dia": e.dia, "categoria": e.categoria}
        if e.resumo:
            item["resumo"] = e.resumo
        if e.porque_importa:
            item["porque_importa"] = e.porque_importa
        if e.conceitos:
            item["conceitos"] = e.conceitos
        if e.triagem:
            item["triagem"] = e.triagem
        itens.append(item)
    return itens


def build_prompt(pergunta: str, entradas: list[LinkEntry]) -> str:
    return (
        f"Pergunta: {pergunta}\n\n"
        f"Acervo ({len(entradas)} item(ns)):\n\n"
        + json.dumps(_payload(entradas), ensure_ascii=False, indent=2)
    )


CONFIANCAS = ("alta", "media", "nenhuma")


def buscar(cfg: Config, pergunta: str, entradas: list[LinkEntry]) -> Resposta:
    """A resposta a uma pergunta. `Resposta.usados` já são entradas, não ids."""
    pergunta = (pergunta or "").strip()
    if not pergunta:
        raise BuscaError("pergunta vazia")
    if not entradas:
        return Resposta(
            texto="Não há nada no acervo ainda. Rode `dlb migrar` para popular.",
            confianca="nenhuma",
        )

    selecionadas, truncado = selecionar(cfg, pergunta, entradas)
    prompt = build_prompt(pergunta, selecionadas)

    backend = cfg.busca_model_backend
    try:
        if backend == "codex":
            bruto = _run_codex(cfg, prompt, SYSTEM_PROMPT, schema=SCHEMA)
        elif backend == "claude-cli":
            bruto = _run_claude_cli(cfg, prompt, SYSTEM_PROMPT)
        elif backend == "anthropic":
            bruto = _run_anthropic(cfg, prompt, SYSTEM_PROMPT)
        else:
            raise BuscaError(f"BUSCA_MODEL_BACKEND desconhecido: {backend}")
    except SinteseError as exc:
        raise BuscaError(str(exc)) from exc

    dado = _parse_objeto(bruto)
    confianca = str(dado.get("confianca") or "nenhuma")
    usados = [
        selecionadas[i] for i in (dado.get("usados") or [])
        if isinstance(i, int) and 0 <= i < len(selecionadas)
    ]
    return Resposta(
        texto=str(dado.get("resposta") or "").strip(),
        usados=usados,
        confianca=confianca if confianca in CONFIANCAS else "nenhuma",
        considerados=len(selecionadas),
        truncado=truncado,
    )


def formatar_para_discord(r: Resposta) -> str:
    """A resposta como mensagem. Os links vêm do acervo, nunca do modelo."""
    partes = [r.texto or "Não consegui responder isso."]
    if r.usados:
        partes.append("")
        partes.append("📚 **no seu acervo:**")
        for e in r.usados:
            # `<url>` suprime a prévia do Discord: com 3 links a mensagem viraria
            # uma parede de cartões, e não precisa da permissão Embed Links.
            partes.append(f"· {e.titulo} — <{e.url}>")
    if r.truncado:
        partes.append("")
        partes.append(
            f"_(acervo maior que o orçamento; considerei os {r.considerados} mais "
            "relevantes)_"
        )
    return "\n".join(partes)
