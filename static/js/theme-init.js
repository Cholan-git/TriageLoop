// Runs synchronously in <head> so the saved theme and sidebar state apply
// before the first paint (no flash of the wrong theme).
(function () {
  var root = document.documentElement;
  var theme = "light", sidebar = "expanded";
  try {
    var t = localStorage.getItem("tl.theme");
    if (t === "light" || t === "dark" || t === "system") theme = t;
    var s = localStorage.getItem("tl.sidebar");
    if (s === "expanded" || s === "collapsed") sidebar = s;
  } catch (e) { /* storage unavailable: use defaults */ }
  var dark = theme === "dark" ||
    (theme === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.setAttribute("data-theme", dark ? "dark" : "light");
  root.setAttribute("data-theme-pref", theme);
  root.setAttribute("data-sidebar", sidebar);
})();
