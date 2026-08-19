"""Classifica e resume os links de um dia usando um LLM.

Três backends:
  claude-cli  usa o binário `claude` já instalado (nenhuma API key necessária)
  anthropic   usa o SDK oficial e ANTHROPIC_API_KEY
  none        sem LLM: cai no título da página (útil para --dry-run e testes)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap

from .config import Config
from .extract import host_of
from .models import AREAS, CATEGORIAS, LinkHit, LinkNote, PageMeta

SYSTEM_PROMPT = textwrap.dedent(
    """\
    Você cataloga links para o segundo cérebro (vault Obsidian) do Dotcom.
    Para cada link, escreva uma nota curta em português do Brasil que permita,
    semanas depois, saber do que aquilo tratava sem reabrir a página.

    Regras:
    - `resumo`: 1 a 2 frases, concretas. Diga o que o conteúdo É e o que ele
      afirma ou entrega. Nada de "este artigo discute vários aspectos de".
    - Quando vier `texto_da_pagina`, **resuma o argumento central dele** — é o
      conteúdo real da página. A `descricao_da_pagina` costuma ser texto de
      divulgação; prefira o texto ao marketing quando os dois existirem.
    - Para vídeo do YouTube, use `titulo_da_pagina` e `canal`. Sem a descrição,
      diga do que o vídeo trata pelo título e por quem publicou, sem inventar
      conteúdo que você não viu.
    - Quando `pagina_legivel` for false, nada foi lido: escreva o que o link
      identificadamente é (domínio, caminho da URL, comentário de quem postou)
      e deixe claro que a página não pôde ser lida. Nunca invente o conteúdo.
    - `porque_importa`: 1 frase sobre por que valeria voltar nisso. Se não
      houver um motivo claro, devolva string vazia.
    - `conceitos`: 1 a 4 entidades nomeadas (tecnologia, produto, pessoa,
      método). São candidatas a virar página no wiki — nomes canônicos, sem
      colchetes, sem hashtag.
    - Não invente. Se os metadados forem pobres, diga menos.
    - Responda APENAS com um array JSON, sem cercas de código e sem comentários.
    """
)


class ClassifyError(RuntimeError):
    pass


def _link_payload(hits: list[LinkHit], metas: dict[str, PageMeta]) -> list[dict]:
    payload = []
    for index, hit in enumerate(hits):
        meta = metas.get(hit.url, PageMeta())
        item = {
            "id": index,
            "url": hit.url,
            "dominio": host_of(hit.url),
            "pagina_legivel": meta.legivel,
            "titulo_da_pagina": meta.title,
            "descricao_da_pagina": meta.description,
            "comentario_de_quem_postou": hit.context,
            "postado_por": hit.author,
            "hora": hit.hhmm,
        }
        # Só entram no prompt quando existem — campo vazio é ruído que o modelo
        # ainda assim lê e paga.
        if meta.kind == "youtube":
            item["tipo"] = "video do YouTube"
        if meta.author:
            item["canal" if meta.kind == "youtube" else "autor"] = meta.author
        if meta.text:
            item["texto_da_pagina"] = meta.text
        payload.append(item)
    return payload


def build_prompt(hits: list[LinkHit], metas: dict[str, PageMeta]) -> str:
    return textwrap.dedent(
        f"""\
        Classifique os {len(hits)} links abaixo.

        Categorias válidas (escolha exatamente uma por link):
        {", ".join(CATEGORIAS)}

        Áreas válidas (escolha exatamente uma por link):
        {", ".join(AREAS)}

        Devolva um array JSON com um objeto por link, na mesma ordem, no formato:
        {{"id": 0, "titulo": "...", "resumo": "...", "porque_importa": "...",
          "categoria": "artigo", "area": "area/ia", "conceitos": ["...", "..."]}}

        LINKS:
        {json.dumps(_link_payload(hits, metas), ensure_ascii=False, indent=2)}
        """
    )


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ClassifyError(f"Resposta do LLM não tem array JSON: {text[:300]}")
    return json.loads(text[start : end + 1])


def _run_claude_cli(cfg: Config, prompt: str) -> str:
    cmd = [
        cfg.claude_bin,
        "-p",
        "--output-format", "json",
        "--model", cfg.llm_model,
        "--append-system-prompt", SYSTEM_PROMPT,
    ]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=600
        )
    except FileNotFoundError as exc:
        raise ClassifyError(
            f"Binário '{cfg.claude_bin}' não encontrado. Ajuste CLAUDE_BIN ou "
            "use LLM_BACKEND=anthropic."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClassifyError("claude CLI estourou o tempo (600s).") from exc

    if proc.returncode != 0:
        raise ClassifyError(f"claude CLI falhou ({proc.returncode}): {proc.stderr[:500]}")

    try:
        return json.loads(proc.stdout)["result"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ClassifyError(
            f"Saída inesperada do claude CLI: {proc.stdout[:300]}"
        ) from exc


def _run_anthropic(cfg: Config, prompt: str) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise ClassifyError("pip install anthropic — ou use LLM_BACKEND=claude-cli.") from exc

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ClassifyError("ANTHROPIC_API_KEY não definido.")

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=cfg.llm_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise ClassifyError("O modelo recusou a requisição.")
    return "".join(b.text for b in response.content if b.type == "text")


def _fallback_notes(hits: list[LinkHit], metas: dict[str, PageMeta]) -> list[LinkNote]:
    notes = []
    for hit in hits:
        meta = metas.get(hit.url, PageMeta())
        notes.append(
            LinkNote(
                hit=hit,
                meta=meta,
                titulo=meta.title or host_of(hit.url),
                resumo=meta.description or hit.context or "(sem resumo — LLM desligado)",
                categoria="outro",
                area="area/ia",
            )
        )
    return notes


def classify(
    cfg: Config, hits: list[LinkHit], metas: dict[str, PageMeta]
) -> list[LinkNote]:
    """Um LLM por dia, não por link — barato e o contexto do dia ajuda."""
    if not hits:
        return []
    if cfg.llm_backend == "none":
        return _fallback_notes(hits, metas)

    prompt = build_prompt(hits, metas)
    if cfg.llm_backend == "claude-cli":
        raw = _run_claude_cli(cfg, prompt)
    elif cfg.llm_backend == "anthropic":
        raw = _run_anthropic(cfg, prompt)
    else:
        raise ClassifyError(f"LLM_BACKEND desconhecido: {cfg.llm_backend}")

    by_id = {int(item["id"]): item for item in _parse_json_array(raw) if "id" in item}

    notes: list[LinkNote] = []
    for index, hit in enumerate(hits):
        item = by_id.get(index)
        meta = metas.get(hit.url, PageMeta())
        if not item:
            # Link que o modelo pulou: entra mesmo assim, marcado para revisão.
            notes.append(_fallback_notes([hit], metas)[0])
            continue

        categoria = str(item.get("categoria", "outro")).strip()
        area = str(item.get("area", "area/ia")).strip()
        notes.append(
            LinkNote(
                hit=hit,
                meta=meta,
                titulo=str(item.get("titulo") or meta.title or host_of(hit.url)).strip(),
                resumo=str(item.get("resumo", "")).strip(),
                porque_importa=str(item.get("porque_importa", "")).strip(),
                categoria=categoria if categoria in CATEGORIAS else "outro",
                area=area if area in AREAS else "area/ia",
                conceitos=[
                    str(c).strip().strip("[]")
                    for c in (item.get("conceitos") or [])
                    if str(c).strip()
                ][:4],
            )
        )
    return notes
