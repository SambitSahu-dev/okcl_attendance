const insideCount = document.querySelector("#inside-count");
const outsideCount = document.querySelector("#outside-count");
const absentCount = document.querySelector("#absent-count");
const todayDay = document.querySelector("#today-day");
const todayWeekday = document.querySelector("#today-weekday");
const todayMonth = document.querySelector("#today-month");
const statusMessage = document.querySelector("#public-dashboard-status");

async function refreshPublicDashboard() {
  try {
    const response = await fetch("/api/public/dashboard");
    if (!response.ok) throw new Error("The attendance summary is unavailable.");
    const data = await response.json();
    const date = new Date(`${data.date}T00:00:00Z`);
    insideCount.textContent = data.inside_count;
    outsideCount.textContent = data.outside_count;
    absentCount.textContent = data.absent_count;
    todayDay.textContent = new Intl.DateTimeFormat("en", {
      day: "2-digit",
      timeZone: "UTC",
    }).format(date);
    todayWeekday.textContent = new Intl.DateTimeFormat("en", {
      weekday: "long",
      timeZone: "UTC",
    }).format(date);
    todayMonth.textContent = new Intl.DateTimeFormat("en", {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(date);
    statusMessage.textContent = "";
  } catch {
    statusMessage.textContent = "Live update unavailable; showing the last loaded counts.";
  }
}

refreshPublicDashboard();
window.setInterval(refreshPublicDashboard, 30000);
