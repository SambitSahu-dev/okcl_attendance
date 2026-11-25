const form = document.querySelector("#login-form");
const statusText = document.querySelector("#status");
const password = document.querySelector("#password");
const passwordToggle = document.querySelector("#toggle-password");
const submitButton = document.querySelector("#login-submit");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(form);
  statusText.textContent = "Signing in…";
  statusText.dataset.state = "loading";
  submitButton.disabled = true;
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: formData.get("username").trim(),
        password: formData.get("password"),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Sign in failed.");
    window.location.assign("/dashboard");
  } catch (error) {
    statusText.textContent = error.message || "Sign in failed.";
    statusText.dataset.state = "error";
    submitButton.disabled = false;
  }
});

passwordToggle.addEventListener("click", () => {
  const isVisible = password.type === "text";
  password.type = isVisible ? "password" : "text";
  passwordToggle.textContent = isVisible ? "Show" : "Hide";
  passwordToggle.setAttribute("aria-pressed", String(!isVisible));
});
