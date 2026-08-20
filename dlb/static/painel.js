// Painel do dlb — o mínimo de JavaScript que faz diferença.
//
// A página é renderizada no servidor; nada aqui é necessário para ela funcionar.
// O que este arquivo faz é a única coisa que HTML sozinho não faz e que importa
// num painel deixado aberto numa aba: recarregar sem alguém apertar F5.
//
// Recarga é `location.reload()`, não fetch parcial. Um refresh de verdade não
// pode divergir do que o servidor renderiza — e é justamente o painel que
// precisa nunca mentir sobre o estado.

(function () {
  "use strict";

  var CHAVE = "dlb-painel-recarga";
  var SEGUNDOS = 30;

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
      // Aba escondida não recarrega: um painel em segundo plano ficaria batendo
      // no vault de graça, e o memo por mtime não gosta de trabalho inútil.
      if (document.hidden) return;
      contagem -= 1;
      if (contagem <= 0) {
        location.reload();
        return;
      }
      pintar();
    }, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", montarControle);
  } else {
    montarControle();
  }
})();
