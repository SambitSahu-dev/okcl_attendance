const form = document.querySelector("#month-lock-form");
const rows = document.querySelector("#month-lock-rows");
const statusMessage = document.querySelector("#status");

async function loadLocks() {
  const response = await fetch("/api/admin/month-locks");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load month locks.");
  rows.replaceChildren();
  if (!data.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "No months have been locked.";
    row.append(cell);
    rows.append(row);
  }
  data.forEach((lock) => {
    const row = document.createElement("tr");
    const month = document.createElement("td");
    month.textContent = `${lock.year}-${String(lock.month).padStart(2, "0")}`;
    const state = document.createElement("td");
    state.textContent = lock.locked ? "Locked" : "Unlocked";
    const lockedAt = document.createElement("td");
    lockedAt.textContent = lock.locked_at ? new Date(lock.locked_at).toLocaleString() : "—";
    const actor = document.createElement("td");
    actor.textContent = lock.updated_by_user_id ?? "—";
    row.append(month, state, lockedAt, actor);
    rows.append(row);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(form);
  try {
    const response = await fetch("/api/admin/month-locks", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        year: Number(values.get("year")),
        month: Number(values.get("month")),
        locked: values.get("locked") === "true",
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not update month lock.");
    statusMessage.textContent = `Month ${data.year}-${String(data.month).padStart(2, "0")} ${data.locked ? "locked" : "unlocked"}.`;
    await loadLocks();
  } catch (error) {
    statusMessage.textContent = error.message || "Could not update month lock.";
  }
});

loadLocks().catch((error) => {
  statusMessage.textContent = error.message || "Could not load month locks.";
});
