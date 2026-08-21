// Painel do dlb — o mínimo de JavaScript que faz diferença.
//
// A página é renderizada no servidor; nada aqui é necessário para ela funcionar.
// Sem JS, o dashboard aparece inteiro e correto — só não se atualiza sozinho.
//
// ## Por que o "tempo real" aqui é barato
//
// Duas classes de número, dois tratamentos:
//
// 1. **O que muda a cada segundo** — idade do heartbeat, contagem até o próximo
//    disparo, tempo de pé do painel. Isso é **aritmética de relógio**: o servidor
//    manda o instante base uma vez, e o navegador conta sozinho. Zero requisição.
//    Pedir ao servidor a cada segundo para ele responder "1 s mais que antes"
//    seria gastar rede para calcular subtração.
//
// 2. **O que muda quando o vault muda** — total, categorias, gráficos. Vem por
//    SSE a cada 5 s, e o que trafega é um punhado de números **mais a versão do
//    acervo**. Se a versão mudou, recarrega a página inteira: gráfico é SVG feito
//    no servidor, e mandar SVG a cada tick seria pagar kilobytes por uma imagem
//    idêntica. Captura acontece algumas vezes ao dia — recarregar nessa frequência
//    é mais barato que qualquer atualização parcial.
//
// Aba escondida não recebe nada: o SSE é fechado e reaberto no retorno.

(function () {
  "use strict";

  var CHAVE = "dlb-painel-recarga";
  var SEGUNDOS = 30;

  // ---------------------------------------------------------------- utilidades

  function humano(seg) {
    if (seg === null || seg === undefined || isNaN(seg)) return "nunca";
    if (seg < 90) return Math.round(seg) + " s";
    if (seg < 5400) return Math.floor(seg / 60) + " min";
    if (seg < 172800) return (seg / 3600).toFixed(1) + " h";
    return Math.floor(seg / 86400) + " d";
  }

  function porVivo(nome) {
    return document.querySelector('[data-vivo="' + nome + '"]');
  }

  // ------------------------------------------------------- relógio local (0 rede)

  function relogioLocal() {
    var base = {};
    ["hb-sync", "hb-resp"].forEach(function (nome) {
      var el = porVivo(nome);
      if (!el) return;
      // Guarda o valor que o servidor mandou e o instante em que chegou; daí em
      // diante é só somar o tempo decorrido.
      base[nome] = { el: el, seg: null, em: Date.now() };
    });

    var uptime = porVivo("uptime");
    var t0 = Date.now();
    var uptimeBase = uptime ? segundosDeTexto(uptime.textContent) : 0;

    var proximo = porVivo("proximo");
    var alvo = proximo && proximo.getAttribute("data-alvo");
    var alvoMs = alvo ? Date.parse(alvo) : NaN;

    setInterval(function () {
      if (document.hidden) return;
      Object.keys(base).forEach(function (nome) {
        var b = base[nome];
        if (b.seg === null) return;
        b.el.textContent = humano(b.seg + (Date.now() - b.em) / 1000);
      });
      if (uptime) {
        uptime.textContent = humano(uptimeBase + (Date.now() - t0) / 1000);
      }
      if (proximo && !isNaN(alvoMs)) {
        var faltam = (alvoMs - Date.now()) / 1000;
        var hora = alvo.substring(11, 16);
        proximo.textContent = faltam > 0 ? hora + " · em " + humano(faltam) : hora;
      }
    }, 1000);

    return base;
  }

  function segundosDeTexto(txt) {
    var m = /([\d.]+)\s*(s|min|h|d)/.exec(txt || "");
    if (!m) return 0;
    var n = parseFloat(m[1]);
    return { s: n, min: n * 60, h: n * 3600, d: n * 86400 }[m[2]] || 0;
  }

  // ------------------------------------------------------------------ SSE

  function aoVivo(base) {
    if (!window.EventSource) return;
    var indicador = document.querySelector(".vivo");
    var versaoAtual = null;
    var fonte = null;

    function abrir() {
      if (fonte) return;
      fonte = new EventSource("/api/ao-vivo");
      fonte.onmessage = function (evento) {
        var d;
        try { d = JSON.parse(evento.data); } catch (e) { return; }
        if (indicador) indicador.classList.remove("vivo-frio");

        if (versaoAtual === null) {
          versaoAtual = d.versao;
        } else if (d.versao !== versaoAtual) {
          // O acervo mudou: os gráficos vieram do servidor e estão velhos.
          location.reload();
          return;
        }
        Object.keys(base).forEach(function (nome) {
          var campo = nome === "hb-sync" ? "hb_sync" : "hb_responder";
          if (d[campo] !== undefined) {
            base[nome].seg = d[campo];
            base[nome].em = Date.now();
          }
        });
      };
      fonte.onerror = function () {
        if (indicador) indicador.classList.add("vivo-frio");
        fechar();
        // O servidor recusa acima do teto de clientes; reabrir em laço apertado
        // seria bater na porta. Vinte segundos é insistência aceitável.
        setTimeout(abrir, 20000);
      };
    }

    function fechar() {
      if (fonte) { fonte.close(); fonte = null; }
    }

    document.addEventListener("visibilitychange", function () {
      // Aba escondida não precisa de fluxo aberto — e cada conexão custa uma
      // thread no servidor, que é justamente o recurso escasso do `http.server`.
      if (document.hidden) fechar(); else abrir();
    });
    abrir();
  }

  // ------------------------------------------------- recarga manual do rodapé

  function montarControle() {
    var rodape = document.querySelector("footer");
    if (!rodape) return;

    var ligado = localStorage.getItem(CHAVE) === "1";
    var rotulo = document.createElement("label");
    rotulo.className = "recarga";

    var caixa = document.createElement("input");
    caixa.type = "checkbox";
    caixa.checked = ligado;

    var texto = document.createElement("span");
    var contagem = ligado ? SEGUNDOS : 0;

    function pintar() {
      texto.textContent = caixa.checked
        ? "recarregando em " + contagem + " s"
        : "recarregar sozinho a cada " + SEGUNDOS + " s";
    }

    caixa.addEventListener("change", function () {
      localStorage.setItem(CHAVE, caixa.checked ? "1" : "0");
      contagem = SEGUNDOS;
      pintar();
    });

    rotulo.appendChild(caixa);
    rotulo.appendChild(texto);
    rodape.appendChild(rotulo);
    pintar();

    setInterval(function () {
      if (!caixa.checked) return;
      if (document.hidden) return;
      contagem -= 1;
      if (contagem <= 0) { location.reload(); return; }
      pintar();
    }, 1000);
  }

  function iniciar() {
    montarControle();
    aoVivo(relogioLocal());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();
