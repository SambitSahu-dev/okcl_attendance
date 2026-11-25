const form = document.querySelector("#report-form");
const statusMessage = document.querySelector("#status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(form);
  const year = Number(values.get("year"));
  const month = Number(values.get("month"));
  statusMessage.textContent = "Generating Excel report…";
  try {
    const response = await fetch(
      `/api/admin/reports/monthly.xlsx?year=${year}&month=${month}`,
    );
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Could not generate the report.");
    }
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `attendance-${year}-${String(month).padStart(2, "0")}.xlsx`;
    document.body.append(link);
    link.click();
    window.setTimeout(() => {
      URL.revokeObjectURL(link.href);
      link.remove();
    }, 1000);
    statusMessage.textContent = "Report downloaded.";
  } catch (error) {
    statusMessage.textContent = error.message || "Could not generate the report.";
  }
});
