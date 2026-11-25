const video = document.querySelector("#camera");
const canvas = document.querySelector("#capture");
const prompt = document.querySelector("#prompt");
const employeeGreeting = document.querySelector("#employee-greeting");
const result = document.querySelector("#result");
const gateClosed = document.querySelector("#gate-closed");
const gateWorkspace = document.querySelector("#gate-workspace");
const gateStatus = document.querySelector("#gate-status");
const gateState = document.querySelector("#gate-state");
const gateStateLabel = document.querySelector("#gate-state-label");
const openGateButton = document.querySelector("#open-gate");
const closeGateButton = document.querySelector("#close-gate");
const cancelButton = document.querySelector("#cancel");
const activityList = document.querySelector("#activity-list");
const activityEmpty = document.querySelector("#activity-empty");

let stream = null;
let scanId = null;
let activeScan = false;
let gateOpen = false;
let nextScanTimer = null;
let retryDelayMs = 1000;

function showEmployeeGreeting(employeeName) {
  const firstName = employeeName?.trim().split(/\s+/)[0];
  if (!firstName) {
    employeeGreeting.textContent = "";
    employeeGreeting.hidden = true;
    return;
  }
  employeeGreeting.textContent = `Hi ${firstName}!`;
  employeeGreeting.hidden = false;
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }
  video.srcObject = null;
}

function updateScanButtons() {
  cancelButton.hidden = !activeScan;
}

function scheduleScanRestart(delayMs) {
  window.clearTimeout(nextScanTimer);
  nextScanTimer = window.setTimeout(() => {
    nextScanTimer = null;
    if (gateOpen && stream && !activeScan) void beginScan();
  }, delayMs);
}

function captureFrame() {
  canvas.width = Math.min(video.videoWidth, 1280);
  canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
  const context = canvas.getContext("2d");
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.78);
}

function formatTime(value) {
  if (!value) return "Time unavailable";
  return `${new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(new Date(value))} UTC`;
}

function displayCompletion(data, operation) {
  result.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = data.employee_name || "Attendance recorded";
  const details = document.createElement("p");
  const attendance = data.attendance;
  details.textContent = attendance
    ? `Employee ID ${data.employee_id} | ${operation === "login" ? "Check-in" : "Check-out"} at ${formatTime(data.event_at)} | ${attendance.status}`
    : data.message || "Attendance recorded.";
  result.append(title, details);
  result.hidden = false;
}

function addActivity(data, operation) {
  if (!data.attendance) return;

  const item = document.createElement("li");
  item.className = "gate-activity-item";
  const kind = document.createElement("span");
  kind.className = `gate-activity-kind${operation === "logout" ? " checkout" : ""}`;
  kind.textContent = operation === "login" ? "CHECK-IN" : "CHECK-OUT";

  const detail = document.createElement("span");
  detail.className = "gate-activity-detail";
  const employee = document.createElement("strong");
  employee.textContent = data.employee_name || "Employee";
  const identity = document.createElement("small");
  identity.textContent = `Employee ID ${data.employee_id} | ${formatTime(data.event_at)}`;
  const status = document.createElement("small");
  status.textContent = data.attendance.status;
  detail.append(employee, identity, status);
  item.append(kind, detail);
  activityList.prepend(item);
  activityEmpty.hidden = true;

  while (activityList.children.length > 20) {
    activityList.lastElementChild.remove();
  }
}

async function cancelServerScan(currentScanId) {
  try {
    const response = await fetch("/attendance/scans/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: currentScanId }),
      keepalive: true,
    });
    if (!response.ok) throw new Error("The server could not cancel the scan.");
    return null;
  } catch (error) {
    return error instanceof Error ? error : new Error("Scan cancellation failed.");
  }
}

async function openGate() {
  if (gateOpen) return;
  gateOpen = true;
  gateClosed.hidden = true;
  gateWorkspace.hidden = false;
  openGateButton.disabled = true;
  openGateButton.setAttribute("aria-expanded", "true");
  gateStatus.textContent = "";
  gateState.dataset.state = "starting";
  gateStateLabel.textContent = "GATE OPENING";
  showEmployeeGreeting(null);
  prompt.textContent = "Requesting camera access…";
  updateScanButtons();

  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Camera access requires HTTPS or localhost.");
    }
    const openedStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
    });
    if (!gateOpen) {
      openedStream.getTracks().forEach((track) => track.stop());
      return;
    }
    stream = openedStream;
    video.srcObject = stream;
    await video.play();
    if (!gateOpen) {
      stopCamera();
      return;
    }
    gateState.dataset.state = "live";
    gateStateLabel.textContent = "GATE OPEN · CAMERA LIVE";
    prompt.textContent = "Camera ready. Starting automatic attendance scan…";
    updateScanButtons();
    void beginScan();
  } catch (error) {
    if (!gateOpen) return;
    stopCamera();
    showEmployeeGreeting(null);
    gateState.dataset.state = "error";
    gateStateLabel.textContent = "CAMERA UNAVAILABLE";
    prompt.textContent = `${error.message || "Camera could not start."} Close GATE and retry after checking camera permission.`;
    updateScanButtons();
  }
}

async function closeGate() {
  if (!gateOpen) return;
  window.clearTimeout(nextScanTimer);
  nextScanTimer = null;
  const currentScanId = scanId;
  scanId = null;
  activeScan = false;
  gateOpen = false;
  updateScanButtons();
  stopCamera();
  gateWorkspace.hidden = true;
  gateClosed.hidden = false;
  openGateButton.disabled = false;
  openGateButton.setAttribute("aria-expanded", "false");
  gateStatus.textContent = "GATE is closed. Camera is off.";

  if (currentScanId) {
    const error = await cancelServerScan(currentScanId);
    if (error) {
      gateStatus.textContent = `GATE is closed and the camera is off, but scan cancellation failed: ${error.message}`;
    }
  }
}

async function cancelScan() {
  const currentScanId = scanId;
  scanId = null;
  activeScan = false;
  updateScanButtons();
  const error = currentScanId ? await cancelServerScan(currentScanId) : null;
  prompt.textContent = error
    ? `Scan cancelled locally, but server cancellation failed: ${error.message}`
    : "Scan cancelled. GATE remains open.";
  if (gateOpen && stream) scheduleScanRestart(500);
}

async function beginScan() {
  if (!gateOpen || !stream || activeScan) return;
  result.hidden = true;
  showEmployeeGreeting(null);
  activeScan = true;
  updateScanButtons();
  prompt.textContent = "Starting attendance scan…";

  try {
    const startResponse = await fetch("/attendance/scans/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const startData = await startResponse.json();
    if (!startResponse.ok) throw new Error(startData.detail || "Could not start scan.");
    if (!gateOpen) {
      const error = await cancelServerScan(startData.scan_id);
      if (error) gateStatus.textContent = `GATE is closed; server scan cancellation failed: ${error.message}`;
      return;
    }
    scanId = startData.scan_id;
    prompt.textContent = startData.prompt;
    await frameLoop(scanId);
  } catch (error) {
    const currentScanId = scanId;
    scanId = null;
    activeScan = false;
    updateScanButtons();
    const cancelError = currentScanId ? await cancelServerScan(currentScanId) : null;
    if (gateOpen) {
      prompt.textContent = cancelError
        ? `${error.message || "Scan failed."} Server cancellation also failed: ${cancelError.message}`
        : `${error.message || "Scan failed."} GATE remains open.`;
      scheduleScanRestart(retryDelayMs);
      retryDelayMs = Math.min(retryDelayMs * 2, 10000);
    }
  }
}

async function frameLoop(currentScanId) {
  while (gateOpen && activeScan && scanId === currentScanId) {
    if (video.videoWidth === 0) {
      await new Promise((resolve) => window.setTimeout(resolve, 100));
      continue;
    }
    const response = await fetch("/attendance/scans/frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: currentScanId, capture: captureFrame() }),
    });
    const data = await response.json();
    if (!gateOpen || scanId !== currentScanId) return;
    if (!response.ok) throw new Error(data.detail || "Scan failed.");
    showEmployeeGreeting(data.employee_name);
    prompt.textContent = data.prompt;
    if (data.state === "complete") {
      if (data.operation !== "login" && data.operation !== "logout") {
        throw new Error("The attendance service did not identify a scan action.");
      }
      scanId = null;
      activeScan = false;
      updateScanButtons();
      retryDelayMs = 1000;
      displayCompletion(data, data.operation);
      addActivity(data, data.operation);
      prompt.textContent =
        "Attendance recorded. The next scan starts automatically when the camera is clear.";
      scheduleScanRestart(1500);
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 180));
  }
}

openGateButton.addEventListener("click", openGate);
closeGateButton.addEventListener("click", closeGate);
cancelButton.addEventListener("click", cancelScan);

window.addEventListener("pagehide", () => {
  window.clearTimeout(nextScanTimer);
  nextScanTimer = null;
  const currentScanId = scanId;
  gateOpen = false;
  scanId = null;
  activeScan = false;
  stopCamera();
  if (currentScanId) {
    navigator.sendBeacon(
      "/attendance/scans/cancel",
      new Blob([JSON.stringify({ scan_id: currentScanId })], { type: "application/json" }),
    );
  }
});
