const form = document.querySelector("#attendance-edit-form");
const statusMessage = document.querySelector("#status-message");
const auditHistory = document.querySelector("#audit-history");

function dateTimeUtc(value) {
  if (!value) return null;
  return `${value}${value.length === 16 ? ":00" : ""}Z`;
}

async function loadAuditHistory() {
  const response = await fetch(`/api/admin/attendance/${form.dataset.recordId}/audit`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load audit history.");
  auditHistory.replaceChildren();
  if (!data.length) {
    auditHistory.textContent = "No manual edits recorded.";
    return;
  }
  data.forEach((entry) => {
    const item = document.createElement("article");
    item.className = "audit-entry";
    const heading = document.createElement("strong");
    heading.textContent = `${new Date(entry.edited_at).toLocaleString()} · User ${entry.actor_user_id}`;
    const reason = document.createElement("p");
    reason.textContent = `Reason: ${entry.reason}`;
    const values = document.createElement("pre");
    values.textContent = `Old: ${entry.old_values}\nNew: ${entry.new_values}`;
    item.append(heading, reason, values);
    auditHistory.append(item);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusMessage.textContent = "Saving correction…";
  const values = new FormData(form);
  try {
    const response = await fetch(`/api/admin/attendance/${form.dataset.recordId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        employee_id: Number(values.get("employee_id")),
        date: values.get("date"),
        first_login_at: dateTimeUtc(values.get("first_login_at")),
        final_logout_at: dateTimeUtc(values.get("final_logout_at")),
        status: values.get("status"),
        reason: values.get("reason"),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not save attendance correction.");
    statusMessage.textContent = "Correction saved and audit trail recorded.";
    form.elements.reason.value = "";
    await loadAuditHistory();
  } catch (error) {
    statusMessage.textContent = error.message || "Could not save correction.";
  }
});

loadAuditHistory().catch((error) => {
  auditHistory.textContent = error.message || "Could not load audit history.";
});
