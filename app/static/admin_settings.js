const form = document.querySelector("#late-cutoff-form");
const cutoff = document.querySelector("#late-cutoff");
const status = document.querySelector("#settings-status");

async function loadSettings() {
  const response = await fetch("/attendance/admin/settings");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load attendance settings.");
  cutoff.value = data.late_cutoff_time || "";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  status.textContent = "Saving…";
  try {
    const response = await fetch("/attendance/admin/settings/late-cutoff", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cutoff_time: cutoff.value || null }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not save the cutoff.");
    cutoff.value = data.late_cutoff_time || "";
    status.textContent = cutoff.value
      ? `Late cutoff saved: ${cutoff.value} UTC.`
      : "Late marking is disabled.";
  } catch (error) {
    status.textContent = error.message || "Could not save the cutoff.";
  }
});

loadSettings().catch((error) => {
  status.textContent = error.message || "Could not load attendance settings.";
});
