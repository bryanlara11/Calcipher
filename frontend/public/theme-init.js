// Runs before the stylesheet/app to avoid flashing the wrong theme.
(() => {
  let preference = "system";
  try { preference = localStorage.getItem("calcipher.theme") || "system"; } catch { /* storage can be unavailable */ }
  if (!["light", "dark", "system"].includes(preference)) preference = "system";
  const dark = preference === "dark" || (preference === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#161513" : "#FAF8F4");
})();
