/**
 * register.js — Face Registration Flow
 * Handles: name input → webcam start → frame capture loop → embedding generation
 */

let sessionId = null;
let webcamStream = null;
let captureInterval = null;
let isSending = false;

const video = document.getElementById('webcam');
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');

// ── Step Navigation ──────────────────────────────────────────

function showStep(stepId) {
    document.querySelectorAll('.register-step').forEach(s => s.classList.remove('active'));
    document.getElementById(stepId).classList.add('active');
}

// ── Start Registration ───────────────────────────────────────

async function startRegistration() {
    const nameInput = document.getElementById('person-name');
    const name = nameInput.value.trim();

    if (!name) {
        nameInput.style.borderColor = '#ef4444';
        nameInput.placeholder = 'Please enter a name!';
        return;
    }

    // Call backend to create session
    try {
        const res = await fetch('/api/register/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        const data = await res.json();

        if (data.error) {
            alert(data.error);
            return;
        }

        sessionId = data.session_id;
        document.getElementById('reg-shots').textContent = `0 / ${data.max_shots}`;

        // Switch to camera step
        showStep('step-camera');
        await startWebcam();
        startCaptureLoop();

    } catch (err) {
        alert('Failed to start registration: ' + err.message);
    }
}

// ── Webcam ───────────────────────────────────────────────────

async function startWebcam() {
    try {
        webcamStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
        });
        video.srcObject = webcamStream;
        await video.play();
    } catch (err) {
        alert('Camera access denied: ' + err.message);
    }
}

function stopWebcam() {
    if (webcamStream) {
        webcamStream.getTracks().forEach(t => t.stop());
        webcamStream = null;
    }
    video.srcObject = null;
}

// ── Frame Capture Loop ───────────────────────────────────────

function startCaptureLoop() {
    // Send a frame every 300ms
    captureInterval = setInterval(() => sendFrame(), 300);
}

function stopCaptureLoop() {
    if (captureInterval) {
        clearInterval(captureInterval);
        captureInterval = null;
    }
}

async function sendFrame() {
    if (isSending || !video.videoWidth) return;
    isSending = true;

    try {
        // Capture frame from video
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        ctx.drawImage(video, 0, 0);
        const imageData = canvas.toDataURL('image/jpeg', 0.8);

        const res = await fetch('/api/register/frame', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, image: imageData })
        });
        const data = await res.json();

        // Update UI
        updateRegUI(data);

        // Check if done
        if (data.status === 'COMPLETE') {
            stopCaptureLoop();
            stopWebcam();
            finishRegistration();
        }

    } catch (err) {
        console.error('Frame send error:', err);
    }

    isSending = false;
}

// ── UI Updates ───────────────────────────────────────────────

function updateRegUI(data) {
    const statusEl = document.getElementById('reg-status');
    const reasonEl = document.getElementById('reg-reason');
    const progressEl = document.getElementById('reg-progress');
    const shotsEl = document.getElementById('reg-shots');

    // Status badge
    statusEl.textContent = data.status || '—';
    statusEl.className = 'status-badge';
    if (data.status === 'CAPTURED' || data.status === 'COMPLETE' || data.status === 'BURST_CAPTURE') {
        statusEl.classList.add('success');
    } else if (data.status === 'INVALID' || data.status === 'NO_FACE') {
        statusEl.classList.add('warning');
    }

    // Reason
    if (data.reasons && data.reasons.length > 0) {
        reasonEl.textContent = data.reasons.join(' · ');
    } else if (data.status === 'STABILIZING') {
        reasonEl.textContent = 'Hold still...';
    } else if (data.status === 'BURST_CAPTURE') {
        reasonEl.textContent = 'Capturing faces — keep looking at camera!';
    } else if (data.status === 'CAPTURED') {
        reasonEl.textContent = 'Face captured!';
    } else {
        reasonEl.textContent = '—';
    }

    // Progress bar
    const pct = Math.round((data.progress || 0) * 100);
    progressEl.style.width = pct + '%';

    // Shot counter
    shotsEl.textContent = `${data.shots || 0} / ${data.max_shots || 10}`;
}

// ── Finish & Embedding ───────────────────────────────────────

async function finishRegistration() {
    showStep('step-processing');

    try {
        const res = await fetch('/api/register/finish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
        const data = await res.json();

        if (data.success) {
            const msg = document.getElementById('done-message');
            msg.textContent = `${data.name.replace(/_/g, ' ')} registered with ${data.embeddings_saved} embedding(s).`;
            showStep('step-done');
        } else {
            alert('Embedding generation failed: ' + (data.error || 'Unknown error'));
            showStep('step-name');
        }

    } catch (err) {
        alert('Error finishing registration: ' + err.message);
        showStep('step-name');
    }
}

// ── Cancel ───────────────────────────────────────────────────

function cancelRegistration() {
    stopCaptureLoop();
    stopWebcam();
    sessionId = null;
    showStep('step-name');
}
