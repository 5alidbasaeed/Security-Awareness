// Print button (shown only when JavaScript runs; the page CSP forbids inline handlers).
document.querySelectorAll("[data-print]").forEach((button) => {
  button.hidden = false;
  button.addEventListener("click", () => window.print());
});
