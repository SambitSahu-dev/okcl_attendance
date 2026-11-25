const form = document.querySelector("#holiday-form");
const rows = document.querySelector("#holiday-rows");
const statusMessage = document.querySelector("#status");

async function loadHolidays() {
  const response = await fetch("/api/admin/holidays");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load holidays.");
  rows.replaceChildren();
  if (!data.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.textContent = "No date-wise holidays configured.";
    row.append(cell);
    rows.append(row);
  }
  data.forEach((holiday) => {
    const row = document.createElement("tr");
    const day = document.createElement("td");
    day.textContent = holiday.date;
    const name = document.createElement("td");
    name.textContent = holiday.name;
    const actionCell = document.createElement("td");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "secondary";
    remove.textContent = "Remove";
    remove.addEventListener("click", async () => {
      try {
        const result = await fetch(`/attendance/admin/holidays/${holiday.date}`, { method: "DELETE" });
        if (!result.ok) {
          const error = await result.json();
          throw new Error(error.detail || "Could not remove holiday.");
        }
        statusMessage.textContent = "Holiday removed.";
        await loadHolidays();
      } catch (error) {
        statusMessage.textContent = error.message || "Could not remove holiday.";
      }
    });
    actionCell.append(remove);
    row.append(day, name, actionCell);
    rows.append(row);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(form);
  try {
    const response = await fetch("/attendance/admin/holidays", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: values.get("date"), name: values.get("name") }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not add holiday.");
    form.reset();
    statusMessage.textContent = "Holiday added.";
    await loadHolidays();
  } catch (error) {
    statusMessage.textContent = error.message || "Could not add holiday.";
  }
});

loadHolidays().catch((error) => {
  statusMessage.textContent = error.message || "Could not load holidays.";
});
