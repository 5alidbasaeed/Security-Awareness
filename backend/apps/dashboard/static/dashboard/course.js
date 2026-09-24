// Slide navigation with the arrow keys. Progressive enhancement: the Back/Next links work without it.
document.addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const target = event.key === "ArrowRight" ? "slide-next" : event.key === "ArrowLeft" ? "slide-prev" : null;
  const link = target && document.getElementById(target);
  if (link) link.click();
});
