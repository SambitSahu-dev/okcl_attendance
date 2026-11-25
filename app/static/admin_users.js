const form = document.querySelector("#admin-user-form");
const rows = document.querySelector("#admin-user-rows");
const statusMessage = document.querySelector("#status");
const roles = ["standard", "hr", "accounts", "team_lead", "audit"];

function addCell(row, content) {
  const cell = document.createElement("td");
  cell.append(content);
  row.append(cell);
}

async function loadAdmins() {
  const response = await fetch("/api/admin/users");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load admin accounts.");
  rows.replaceChildren();
  data.forEach((user) => {
    const row = document.createElement("tr");
    addCell(row, document.createTextNode(user.username));
    const role = document.createElement("select");
    roles.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value.replace("_", " ");
      option.selected = value === (user.admin_role || "standard");
      role.append(option);
    });
    addCell(row, role);
    const active = document.createElement("input");
    active.type = "checkbox";
    active.checked = user.is_active;
    active.setAttribute("aria-label", "Admin account active");
    addCell(row, active);
    const action = document.createElement("button");
    action.type = "button";
    action.textContent = "Save";
    action.addEventListener("click", async () => {
      action.disabled = true;
      try {
        const result = await fetch(`/api/admin/users/${user.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ admin_role: role.value, is_active: active.checked }),
        });
        const body = await result.json();
        if (!result.ok) throw new Error(body.detail || "Could not update admin account.");
        statusMessage.textContent = "Admin account updated.";
        await loadAdmins();
      } catch (error) {
        statusMessage.textContent = error.message || "Could not update admin account.";
        action.disabled = false;
      }
    });
    addCell(row, action);
    rows.append(row);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(form);
  statusMessage.textContent = "Creating admin login…";
  try {
    const response = await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: values.get("username"),
        password: values.get("password"),
        admin_role: values.get("admin_role"),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not create admin login.");
    form.reset();
    statusMessage.textContent = "Admin login created.";
    await loadAdmins();
  } catch (error) {
    statusMessage.textContent = error.message || "Could not create admin login.";
  }
});

loadAdmins().catch((error) => {
  statusMessage.textContent = error.message || "Could not load admin accounts.";
});
