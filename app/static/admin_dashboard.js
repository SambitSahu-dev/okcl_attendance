const insideCount = document.querySelector("#inside-count");
const outsideCount = document.querySelector("#outside-count");
const absentCount = document.querySelector("#absent-count");
const tableBody = document.querySelector("#same-day-rows");
const statusMessage = document.querySelector("#dashboard-status");

function cell(value) {
  const element = document.createElement("td");
  element.textContent = value;
  return element;
}

function time(value) {
  return value ? new Date(value).toLocaleTimeString([], { timeZone: "UTC", hour: "2-digit", minute: "2-digit" }) : "—";
}

async function refreshDashboard() {
  try {
    const response = await fetch("/api/admin/dashboard");
    if (!response.ok) {
      statusMessage.textContent = "Could not refresh dashboard data.";
      return;
    }
    const data = await response.json();
    statusMessage.textContent = "";
    insideCount.textContent = data.inside_count;
    outsideCount.textContent = data.outside_count;
    absentCount.textContent = data.absent_count;
    tableBody.replaceChildren();
    if (!data.table_rows.length) {
      const emptyRow = document.createElement("tr");
      const emptyCell = cell("No attendance scans have been recorded today.");
      emptyCell.colSpan = 7;
      emptyCell.className = "empty";
      emptyRow.append(emptyCell);
      tableBody.append(emptyRow);
      return;
    }
    data.table_rows.forEach((record) => {
      const row = document.createElement("tr");
      row.append(
        cell(record.full_name),
        cell(record.employee_code),
        cell(time(record.first_login_at)),
        cell(time(record.final_logout_at)),
        cell(record.status),
        cell(record.manually_edited ? "Yes" : "No"),
      );
      const actionCell = document.createElement("td");
      if (["standard", "super_admin", "hr"].includes(document.body.dataset.adminRole)) {
        const link = document.createElement("a");
        link.href = `/admin/attendance/${record.attendance_id}/edit`;
        link.textContent = "Edit";
        actionCell.append(link);
      }
      row.append(actionCell);
      tableBody.append(row);
    });
  } catch {
    statusMessage.textContent = "Dashboard refresh failed; showing the last loaded data.";
  }
}

window.setInterval(refreshDashboard, 30000);
