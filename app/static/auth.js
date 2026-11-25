document.querySelectorAll("[data-logout]").forEach((link) => {
  link.addEventListener("click", async (event) => {
    event.preventDefault();
    try {
      await fetch("/auth/logout", { method: "POST" });
      window.location.assign("/login");
    } catch {
      window.location.assign("/login");
    }
  });
});
