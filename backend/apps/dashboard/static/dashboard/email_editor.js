// The email composer: a contenteditable box with a small toolbar. On submit the box's HTML goes into the
// hidden field and the server sanitises it (only basic formatting survives; every link becomes the tracked link).
(() => {
  const form = document.getElementById("compose-form");
  const editor = document.getElementById("compose-editor");
  if (!form || !editor) return;
  const source = form.querySelector(".compose-source");
  const errorBox = document.getElementById("compose-error");
  const csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
  const TRACKED = "{{.URL}}";

  const showError = (message) => { errorBox.textContent = message; errorBox.hidden = !message; };
  const insertHtml = (html) => { editor.focus(); document.execCommand("insertHTML", false, html); };
  const escapeHtml = (text) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // Keep the selection inside the editor when a toolbar button is pressed.
  form.querySelector(".compose__toolbar").addEventListener("mousedown", (event) => {
    if (event.target.closest("button")) event.preventDefault();
  });

  form.querySelectorAll("[data-cmd]").forEach((button) => button.addEventListener("click", () => {
    editor.focus();
    const cmd = button.dataset.cmd;
    if (cmd === "link") {
      document.execCommand("createLink", false, TRACKED);
    } else if (cmd === "button") {
      const label = window.prompt("Button text", "Open document");
      if (label) insertHtml(`<a href="${TRACKED}" data-button="1">${escapeHtml(label)}</a>&nbsp;`);
    } else {
      document.execCommand(cmd, false, null);
    }
  }));

  const mergeSelect = form.querySelector('[data-insert="merge"]');
  mergeSelect.addEventListener("change", () => {
    if (mergeSelect.value) { editor.focus(); document.execCommand("insertText", false, mergeSelect.value); }
    mergeSelect.value = "";
  });

  const imageSelect = form.querySelector('[data-insert="image"]');
  const fileInput = document.getElementById("compose-upload");
  const insertImage = (id, url, name) => insertHtml(`<img src="${url}" data-image-id="${id}" alt="${escapeHtml(name || "")}">`);
  imageSelect.addEventListener("change", () => {
    const choice = imageSelect.selectedOptions[0];
    if (choice.value === "upload") fileInput.click();
    else if (choice.value) insertImage(choice.value, choice.dataset.url, choice.textContent);
    imageSelect.value = "";
  });
  fileInput.addEventListener("change", async () => {
    if (!fileInput.files.length) return;
    const data = new FormData();
    data.append("file", fileInput.files[0]);
    showError("");
    try {
      const response = await fetch(form.dataset.uploadUrl, { method: "POST", body: data, headers: { "X-CSRFToken": csrf } });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Upload failed.");
      const option = new Option(result.name, result.id);
      option.dataset.url = result.url;
      imageSelect.add(option, imageSelect.options[imageSelect.options.length - 1]);
      insertImage(result.id, result.url, result.name);
    } catch (error) {
      showError(error.message);
    }
    fileInput.value = "";
  });

  form.addEventListener("submit", () => { source.value = editor.innerHTML; });
})();
