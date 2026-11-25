const form = document.querySelector("#admin-profile-form");
const statusMessage = document.querySelector("#profile-status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(form);
  const payload = {
    username: values.get("username"),
    display_name: values.get("display_name").trim() || null,
    email: values.get("email").trim() || null,
    current_password: values.get("current_password"),
    new_password: values.get("new_password") || null,
    confirm_new_password: values.get("confirm_new_password") || null,
  };
  statusMessage.textContent = "Saving profile…";
  try {
    const response = await fetch("/api/admin/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not update profile.");
    form.elements.username.value = data.username;
    form.elements.display_name.value = data.display_name || "";
    form.elements.email.value = data.email || "";
    form.elements.current_password.value = "";
    form.elements.new_password.value = "";
    form.elements.confirm_new_password.value = "";
    statusMessage.textContent = "Profile updated.";
  } catch (error) {
    statusMessage.textContent = error.message || "Could not update profile.";
  }
});
