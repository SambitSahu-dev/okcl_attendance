const dayInput = document.querySelector("#attendance-day");
const rows = document.querySelector("#attendance-rows");
const statusMessage = document.querySelector("#status");

function addCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
}

function displayTime(value) {
  return value ? new Date(value).toLocaleTimeString([], { timeZone: "UTC", hour: "2-digit", minute: "2-digit" }) : "—";
}

async function loadAttendance() {
  statusMessage.textContent = "Loading attendance…";
  try {
    const response = await fetch(`/api/admin/attendance?day=${encodeURIComponent(dayInput.value)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load attendance.");
    rows.replaceChildren();
    if (!data.length) {
      const empty = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 8;
      cell.textContent = "No attendance records for this date.";
      empty.append(cell);
      rows.append(empty);
    }
    data.forEach((record) => {
      const row = document.createElement("tr");
      addCell(row, record.full_name);
      addCell(row, record.employee_code);
      addCell(row, record.date);
      addCell(row, displayTime(record.first_login_at));
      addCell(row, displayTime(record.final_logout_at));
      addCell(row, record.status);
      addCell(row, record.manually_edited ? "Yes" : "No");
      const actionCell = document.createElement("td");
      if (["standard", "super_admin", "hr"].includes(document.body.dataset.adminRole)) {
        const link = document.createElement("a");
        link.href = `/admin/attendance/${record.id}/edit`;
        link.textContent = "Edit";
        actionCell.append(link);
      }
      row.append(actionCell);
      rows.append(row);
    });
    statusMessage.textContent = "";
  } catch (error) {
    statusMessage.textContent = error.message || "Could not load attendance.";
  }
}

dayInput.addEventListener("change", loadAttendance);
loadAttendance();
