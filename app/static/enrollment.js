const video = document.querySelector("#camera");
const canvas = document.querySelector("#capture");
const startButton = document.querySelector("#start-camera");
const captureButton = document.querySelector("#capture-image");
const enrollButton = document.querySelector("#enroll");
const employeeSelect = document.querySelector("#employee");
const consentCheckbox = document.querySelector("#consent");
const countText = document.querySelector("#capture-count");
const previews = document.querySelector("#previews");
const statusText = document.querySelector("#status");

let stream = null;
const captures = [];

function refreshState() {
  countText.textContent = `${captures.length} of 10 images captured; at least 3 are required.`;
  captureButton.disabled = !stream || captures.length >= 10;
  enrollButton.disabled =
    captures.length < 3 || !consentCheckbox.checked || !employeeSelect.value;
}

function stopCamera() {
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  video.srcObject = null;
  captureButton.disabled = true;
}

startButton.addEventListener("click", async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
    });
    video.srcObject = stream;
    await video.play();
    refreshState();
    statusText.textContent = "Camera ready. Capture 3 to 10 images.";
  } catch (error) {
    statusText.textContent = error.message || "Camera access was not available.";
  }
});

captureButton.addEventListener("click", () => {
  if (!stream || video.videoWidth === 0 || captures.length >= 5) return;
  canvas.width = Math.min(video.videoWidth, 1280);
  canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  captures.push(canvas.toDataURL("image/jpeg", 0.82));
  const preview = document.createElement("img");
  preview.src = captures[captures.length - 1];
  preview.alt = `Capture ${captures.length}`;
  previews.append(preview);
  refreshState();
});

consentCheckbox.addEventListener("change", refreshState);
employeeSelect.addEventListener("change", refreshState);

enrollButton.addEventListener("click", async () => {
  enrollButton.disabled = true;
  statusText.textContent = "Processing captures…";
  try {
    const response = await fetch("/admin/enrollment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        employee_id: Number(employeeSelect.value),
        consent_granted: consentCheckbox.checked,
        captures,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Enrollment failed.");
    statusText.textContent = `Enrollment saved (${data.captures_processed} captures, consent recorded).`;
    captures.length = 0;
    previews.replaceChildren();
    consentCheckbox.checked = false;
    employeeSelect.selectedIndex = 0;
    stopCamera();
    refreshState();
  } catch (error) {
    statusText.textContent = error.message || "Enrollment failed.";
    refreshState();
  }
});

window.addEventListener("pagehide", stopCamera);
refreshState();
