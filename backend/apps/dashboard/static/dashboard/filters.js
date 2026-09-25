/*
  Filter bars on management pages apply as you choose, like the dashboard's HTMX filters, without
  a separate Filter button. Plain GET forms: dropdowns and dates submit on change, the search box
  submits after a short pause, and focus returns to the search box after the reload. Without
  JavaScript the Filter button is still there and does the same thing.
*/
(function () {
  "use strict";

  function wire(form) {
    form.classList.add("js-autosubmit");
    var timer = null;
    var submit = function () { if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); } };
    form.addEventListener("change", function (event) {
      if (event.target.matches("select, input[type=date], input[type=checkbox]")) submit();
    });
    form.querySelectorAll("input[type=search]").forEach(function (input) {
      input.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { sessionStorage.setItem("filters:focus", input.id); submit(); }, 500);
      });
    });
    var focusId = null;
    try { focusId = sessionStorage.getItem("filters:focus"); sessionStorage.removeItem("filters:focus"); } catch (e) { /* storage blocked */ }
    var target = focusId && document.getElementById(focusId);
    if (target && form.contains(target)) {
      target.focus();
      var end = target.value.length;
      target.setSelectionRange(end, end);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form.toolbar[method=get]").forEach(wire);
  });
})();
