/**
 * recognize.js — Live Face Recognition
 * Handles: webcam start → continuous frame sending → result display
 */

let webcamStream = null;
let recognizeLoop = null;
let isRunning = false;
let isSending = false;

const video = document.getElementById('webcam');
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');

// ── Toggle Recognition ───────────────────────────────────────

async function toggleRecognition() {
    const btn = document.getElementById('btn-start-recog');

    if (isRunning) {
        stopRecognition();
        btn.textContent = 'Start';
        btn.className = 'btn btn-primary';
    } else {
        await startRecognition();
        btn.textContent = 'Stop';
        btn.className = 'btn btn-danger';
    }
}

// ── Start ────────────────────────────────────────────────────

async function startRecognition() {
    try {
        webcamStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
        });
        video.srcObject = webcamStream;
        await video.play();

        isRunning = true;
        updateStatus('Scanning...', '');

        // Send frames every 400ms
        recognizeLoop = setInterval(() => sendRecognizeFrame(), 400);

    } catch (err) {
        updateStatus('Camera error', 'error');
        alert('Camera access denied: ' + err.message);
    }
}

// ── Stop ─────────────────────────────────────────────────────

function stopRecognition() {
    isRunning = false;

    if (recognizeLoop) {
        clearInterval(recognizeLoop);
        recognizeLoop = null;
    }

    if (webcamStream) {
        webcamStream.getTracks().forEach(t => t.stop());
        webcamStream = null;
    }

    video.srcObject = null;
    updateStatus('Stopped', '');
    hideOverlay();
}

// ── Send Frame ───────────────────────────────────────────────

async function sendRecognizeFrame() {
    if (isSending || !video.videoWidth) return;
    isSending = true;

    try {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        ctx.drawImage(video, 0, 0);
        const imageData = canvas.toDataURL('image/jpeg', 0.7);

        const res = await fetch('/api/recognize/frame', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: imageData })
        });
        const data = await res.json();

        handleRecognitionResult(data);

    } catch (err) {
        console.error('Recognition error:', err);
    }

    isSending = false;
}

// ── Handle Result ────────────────────────────────────────────

function handleRecognitionResult(data) {
    const overlay = document.getElementById('recog-overlay');
    const nameEl = document.getElementById('recog-name');
    const confEl = document.getElementById('recog-confidence');
    const consensusFill = document.getElementById('consensus-fill');
    const statusEl = document.getElementById('recog-status');

    if (data.status === 'no_face') {
        hideOverlay();
        updateStatus('Looking for face...', '');
        consensusFill.style.width = '0%';
        return;
    }

    if (data.status === 'low_quality') {
        hideOverlay();
        const reason = data.reasons ? data.reasons[0] : 'Low quality';
        updateStatus(reason, 'warning');
        consensusFill.style.width = '0%';
        return;
    }

    // Show overlay
    overlay.classList.remove('hidden', 'recognized', 'unknown');

    if (data.status === 'recognized') {
        const displayName = data.name.replace(/_/g, ' ');
        nameEl.textContent = displayName;
        confEl.textContent = (data.confidence * 100).toFixed(1) + '%';
        overlay.classList.add('recognized');
        updateStatus(`Recognized: ${displayName}`, 'success');
    } else {
        nameEl.textContent = 'Unknown';
        confEl.textContent = (data.confidence * 100).toFixed(1) + '%';
        overlay.classList.add('unknown');
        updateStatus('Unknown face', 'warning');
    }

    // Consensus progress
    const pct = Math.min((data.consecutive / data.required) * 100, 100);
    consensusFill.style.width = pct + '%';

    // Logged notification
    if (data.logged) {
        const loggedEl = document.getElementById('last-logged');
        const displayName = data.name.replace(/_/g, ' ');
        loggedEl.textContent = `✅ ${displayName} — just now`;
        loggedEl.style.color = '#10b981';

        // Flash effect
        overlay.style.boxShadow = '0 0 30px rgba(16, 185, 129, 0.5)';
        setTimeout(() => { overlay.style.boxShadow = 'none'; }, 1500);
    }
}

// ── UI Helpers ───────────────────────────────────────────────

function updateStatus(text, type) {
    const el = document.getElementById('recog-status');
    el.textContent = text;
    el.className = 'status-badge';
    if (type) el.classList.add(type);
}

function hideOverlay() {
    document.getElementById('recog-overlay').classList.add('hidden');
}
