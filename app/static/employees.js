const form = document.querySelector("#employee-form");
const profilePanel = document.querySelector("#employee-profile-panel");
const enrollmentPanel = document.querySelector("#face-enrollment-panel");
const rows = document.querySelector("#employee-rows");
const employeeFilter = document.querySelector("#employee-filter");
const statusMessage = document.querySelector("#status");
const video = document.querySelector("#employee-camera");
const canvas = document.querySelector("#employee-capture");
const startCameraButton = document.querySelector("#start-employee-camera");
const captureButton = document.querySelector("#capture-employee-picture");
const trainButton = document.querySelector("#train-system");
const finishLaterButton = document.querySelector("#finish-enrollment-later");
const addAnotherButton = document.querySelector("#add-another-employee");
const consentCheckbox = document.querySelector("#employee-consent");
const captureCount = document.querySelector("#employee-capture-count");
const previews = document.querySelector("#employee-previews");
const enrollmentStatus = document.querySelector("#enrollment-status");
const employeeSummary = document.querySelector("#employee-summary");
const anglePrompt = document.querySelector("#employee-angle-prompt");

const faceAngles = [
  "Look straight ahead",
  "Turn your face slightly to the left",
  "Turn a little further to the left",
  "Return to the center",
  "Turn your face slightly to the right",
  "Turn a little further to the right",
  "Return to the center",
  "Raise your chin slightly",
  "Lower your chin slightly",
  "Look straight ahead",
];
let stream = null;
let employeeId = null;
let captures = [];
let training = false;
let capturing = false;
let employeeRecords = [];

function addCell(row, content) {
  const cell = document.createElement("td");
  cell.append(content);
  row.append(cell);
}

function textInput(value, label) {
  const input = document.createElement("input");
  input.value = value || "";
  input.readOnly = true;
  input.setAttribute("aria-label", label);
  return input;
}

function refreshCaptureState() {
  captureCount.textContent = `${captures.length} of 10 pictures captured.`;
  captureButton.textContent =
    captures.length === faceAngles.length ? "Retake 10 pictures" : "Capture 10 pictures";
  captureButton.disabled =
    !stream || !consentCheckbox.checked || capturing || training;
  startCameraButton.disabled = capturing || training;
  trainButton.disabled =
    !employeeId ||
    captures.length !== faceAngles.length ||
    !consentCheckbox.checked ||
    capturing ||
    training;
  consentCheckbox.disabled = capturing || training;
}

function stopCamera() {
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  video.srcObject = null;
  captureButton.disabled = true;
  startCameraButton.textContent = "Start camera";
}

function clearCaptureState() {
  captures = [];
  previews.replaceChildren();
  consentCheckbox.checked = false;
  training = false;
  refreshCaptureState();
}

function renderEmployees() {
  const filter = employeeFilter.value;
  const filteredEmployees = employeeRecords.filter((employee) => {
    if (filter === "active") return employee.is_active && !employee.is_deleted;
    if (filter === "inactive") return !employee.is_active && !employee.is_deleted;
    if (filter === "open-enrolment") {
      return employee.is_active && !employee.face_enrolled && !employee.is_deleted;
    }
    if (filter === "deleted") return employee.is_deleted;
    return true;
  });
  rows.replaceChildren();
  if (filteredEmployees.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "empty";
    cell.textContent = "No employees match this filter.";
    row.append(cell);
    rows.append(row);
    return;
  }
  filteredEmployees.forEach((employee) => {
    const row = document.createElement("tr");
    const code = textInput(employee.employee_code, "Employee code");
    const name = textInput(employee.full_name, "Full name");
    const email = textInput(employee.email, "Email");
    const active = document.createElement("input");
    active.type = "checkbox";
    active.checked = employee.is_active;
    active.disabled = true;
    active.setAttribute("aria-label", "Employee active");
    addCell(row, code);
    addCell(row, name);
    addCell(row, email);
    addCell(row, document.createTextNode(employee.username || "No login"));

    const faceSetup = document.createElement("td");
    if (employee.is_deleted) {
      faceSetup.textContent = "Deleted";
    } else if (employee.face_enrolled) {
      faceSetup.textContent = "Trained";
    } else if (employee.is_active) {
      const link = document.createElement("a");
      link.href = "/admin/enrollment";
      link.className = "table-action";
      link.textContent = "Open enrollment";
      faceSetup.append(link);
    } else {
      faceSetup.textContent = "Inactive";
    }
    row.append(faceSetup);
    addCell(row, employee.is_deleted ? document.createTextNode("Deleted") : active);

    if (employee.is_deleted) {
      const actionCell = document.createElement("td");
      if (filter === "deleted") {
        const permanentDelete = document.createElement("button");
        permanentDelete.type = "button";
        permanentDelete.className = "secondary employee-delete";
        permanentDelete.textContent = "Permanently Delete";
        permanentDelete.addEventListener("click", async () => {
          const confirmed = window.confirm(
            `Permanently delete ${employee.full_name} (${employee.employee_code})? ` +
            "This erases attendance, logout, consent, and audit history, login access, " +
            "face data, stored images, reports, and backups. This cannot be undone.",
          );
          if (!confirmed) return;
          permanentDelete.disabled = true;
          try {
            const result = await fetch(
              `/api/admin/employees/${employee.id}/permanent`,
              { method: "DELETE" },
            );
            if (!result.ok) {
              const body = await result.json();
              throw new Error(body.detail || "Could not permanently delete employee.");
            }
            statusMessage.textContent =
              "Employee and all associated data were permanently deleted.";
          } catch (error) {
            statusMessage.textContent =
              error.message || "Could not permanently delete employee.";
            permanentDelete.disabled = false;
            return;
          }
          try {
            await loadEmployees();
          } catch (error) {
            statusMessage.textContent =
              `Employee was deleted, but the directory could not refresh: ${error.message || "unknown error"}.`;
          }
        });
        actionCell.append(permanentDelete);
      } else {
        actionCell.textContent = "Use Deleted Employee filter for permanent deletion.";
      }
      row.append(actionCell);
      rows.append(row);
      return;
    }

    const actionCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "employee-row-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save";
    save.hidden = true;
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "Cancel";
    cancel.hidden = true;
    const fields = [code, name, email];
    edit.addEventListener("click", () => {
      fields.forEach((field) => { field.readOnly = false; });
      active.disabled = false;
      edit.hidden = true;
      save.hidden = false;
      cancel.hidden = false;
    });
    cancel.addEventListener("click", () => {
      code.value = employee.employee_code;
      name.value = employee.full_name;
      email.value = employee.email || "";
      active.checked = employee.is_active;
      fields.forEach((field) => { field.readOnly = true; });
      active.disabled = true;
      edit.hidden = false;
      save.hidden = true;
      cancel.hidden = true;
    });
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const result = await fetch(`/api/admin/employees/${employee.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            employee_code: code.value,
            full_name: name.value,
            email: email.value.trim() || null,
            is_active: active.checked,
          }),
        });
        const body = await result.json();
        if (!result.ok) throw new Error(body.detail || "Could not update employee.");
        statusMessage.textContent = "Employee details saved.";
        await loadEmployees();
      } catch (error) {
        statusMessage.textContent = error.message || "Could not update employee.";
        save.disabled = false;
      }
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "secondary employee-delete";
    remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
      const confirmed = window.confirm(
        "Deactivate this employee and erase face embeddings and stored login images? " +
        "Profile, consent, attendance, and audit history will be retained.",
      );
      if (!confirmed) return;
      remove.disabled = true;
      try {
        const result = await fetch(`/api/admin/employees/${employee.id}`, {
          method: "DELETE",
        });
        if (!result.ok) {
          const body = await result.json();
          throw new Error(body.detail || "Could not delete employee.");
        }
        statusMessage.textContent =
          "Employee deactivated; face data and login images removed. Attendance history was retained.";
        await loadEmployees();
      } catch (error) {
        statusMessage.textContent = error.message || "Could not delete employee.";
        remove.disabled = false;
        try {
          await loadEmployees();
        } catch (loadError) {
          statusMessage.textContent +=
            ` Directory could not refresh: ${loadError.message || "unknown error"}.`;
        }
      }
    });
    actions.append(edit, save, cancel, remove);
    actionCell.append(actions);
    row.append(actionCell);
    rows.append(row);
  });
}

async function loadEmployees() {
  const response = await fetch("/api/admin/employees");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load employees.");
  employeeRecords = data;
  renderEmployees();
}

employeeFilter.addEventListener("change", renderEmployees);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = form.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  statusMessage.textContent = "Creating employee profile…";
  const values = new FormData(form);
  try {
    const response = await fetch("/api/admin/employees", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        employee_code: values.get("employee_code"),
        full_name: values.get("full_name"),
        email: values.get("email").trim() || null,
        username: values.get("username"),
        password: values.get("password"),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not create employee.");

    employeeId = data.id;
    form.reset();
    profilePanel.hidden = true;
    enrollmentPanel.hidden = false;
    employeeSummary.textContent = `${data.full_name} · ${data.employee_code}`;
    statusMessage.textContent = "";
    enrollmentStatus.textContent = "Profile created. Start the camera to capture face pictures.";
    clearCaptureState();
    try {
      await loadEmployees();
    } catch (error) {
      enrollmentStatus.textContent +=
        ` Employee list could not refresh: ${error.message || "unknown error"}.`;
    }
    enrollmentPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    statusMessage.textContent = error.message || "Could not create employee.";
  } finally {
    submitButton.disabled = false;
  }
});

startCameraButton.addEventListener("click", async () => {
  startCameraButton.disabled = true;
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("Camera access requires a supported browser and secure connection.");
    }
    stopCamera();
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
    });
    video.srcObject = stream;
    await video.play();
    startCameraButton.textContent = "Restart camera";
    enrollmentStatus.textContent =
      "Camera ready. Confirm consent, then follow each angle prompt.";
    refreshCaptureState();
  } catch (error) {
    stopCamera();
    enrollmentStatus.textContent = error.message || "Camera access was not available.";
  } finally {
    startCameraButton.disabled = false;
  }
});

captureButton.addEventListener("click", async () => {
  if (!stream || !consentCheckbox.checked || capturing || training) return;
  captures = [];
  previews.replaceChildren();
  capturing = true;
  refreshCaptureState();
  try {
    for (let index = 0; index < faceAngles.length; index += 1) {
      anglePrompt.textContent =
        `Picture ${index + 1} of ${faceAngles.length}: ${faceAngles[index]}.`;
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (!stream || video.videoWidth === 0 || video.videoHeight === 0) {
        throw new Error("The camera frame is unavailable. Restart the camera and try again.");
      }
      canvas.width = Math.min(video.videoWidth, 1280);
      canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      const capture = canvas.toDataURL("image/jpeg", 0.82);
      captures.push(capture);
      const preview = document.createElement("img");
      preview.src = capture;
      preview.alt = `Employee picture ${captures.length}`;
      previews.append(preview);
      captureCount.textContent = `${captures.length} of ${faceAngles.length} pictures captured.`;
    }
    anglePrompt.textContent = "All 10 pictures captured. You can train the system now.";
  } catch (error) {
    anglePrompt.textContent = error.message || "Automatic picture capture failed.";
  } finally {
    capturing = false;
    refreshCaptureState();
  }
});

consentCheckbox.addEventListener("change", refreshCaptureState);

trainButton.addEventListener("click", async () => {
  if (
    !employeeId ||
    captures.length !== faceAngles.length ||
    !consentCheckbox.checked ||
    capturing
  ) return;
  training = true;
  trainButton.disabled = true;
  finishLaterButton.disabled = true;
  enrollmentStatus.textContent = "Training face recognition…";
  refreshCaptureState();
  try {
    const response = await fetch("/admin/enrollment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        employee_id: employeeId,
        consent_granted: consentCheckbox.checked,
        captures,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Face training failed.");
    enrollmentStatus.textContent =
      `Training complete. ${data.captures_processed} pictures processed and consent recorded.`;
    stopCamera();
    clearCaptureState();
    trainButton.hidden = true;
    finishLaterButton.hidden = true;
    addAnotherButton.hidden = false;
    startCameraButton.hidden = true;
    captureButton.hidden = true;
    consentCheckbox.disabled = true;
  } catch (error) {
    enrollmentStatus.textContent = error.message || "Face training failed.";
    training = false;
    finishLaterButton.disabled = false;
    refreshCaptureState();
    return;
  }
  try {
    await loadEmployees();
  } catch (error) {
    enrollmentStatus.textContent += ` Employee list could not refresh: ${error.message || "unknown error"}.`;
  }
});

function returnToEmployeeForm(message) {
  stopCamera();
  clearCaptureState();
  employeeId = null;
  enrollmentPanel.hidden = true;
  profilePanel.hidden = false;
  trainButton.hidden = false;
  finishLaterButton.hidden = false;
  finishLaterButton.disabled = false;
  addAnotherButton.hidden = true;
  startCameraButton.hidden = false;
  captureButton.hidden = false;
  consentCheckbox.disabled = false;
  enrollmentStatus.textContent = "";
  statusMessage.textContent = message;
  profilePanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

finishLaterButton.addEventListener("click", () => {
  returnToEmployeeForm(
    "Employee profile saved. Face training can be completed later from Enrollment.",
  );
});

addAnotherButton.addEventListener("click", () => {
  returnToEmployeeForm("");
});

window.addEventListener("pagehide", stopCamera);
loadEmployees().catch((error) => {
  statusMessage.textContent = error.message || "Could not load employees.";
});
refreshCaptureState();
