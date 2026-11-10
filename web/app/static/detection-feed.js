// The server annotates each JPEG with the detections from that same input frame.
const stateNames = {
  connecting: "서버 연결 중", starting: "모델 준비 중", ready: "실시간 탐지 중",
  camera_unavailable: "카메라 연결 대기", stale: "영상 수신 지연", error: "실행 오류",
  stopped: "서버 종료", offline: "서버 연결 끊김", camera_off: "카메라 꺼짐",
  training: "학습·평가·변환 중", switching_model: "모델 전환 중", switching_camera: "카메라 전환 중"
};

export function watchDetections({ image, onStatus, onDetections, onPreview, matchedPreview = false,
  displayMode = "detections" }) {
  let socket;
  let reconnectTimer;
  let delay = 1000;
  let lastMessageAt = 0;
  let status;
  let frameId = -1;
  let closed = false;
  let nextFrame = null;
  let loadingFrame = null;
  let loadingBlob = null;
  let imageUrl = null;

  function releaseUrl() {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = null;
  }

  function clearImage() {
    nextFrame = null;
    loadingFrame = null;
    loadingBlob = null;
    image.hidden = true;
    image.removeAttribute("src");
    releaseUrl();
  }

  function acceptStatus(next) {
    const reset = !next.ready || next.session_id !== status?.session_id || next.model_version !== status?.model_version;
    status = next;
    if (reset) {
      frameId = -1;
      clearImage();
    }
    onStatus(next, stateNames[next.state] || next.state, reset);
    if (!matchedPreview && next.ready && !image.hasAttribute("src")) image.src = "/stream.mjpg?t=" + Date.now();
  }

  function imageLoaded() {
    if (!status?.ready || !image.hasAttribute("src")) return;
    if (matchedPreview) {
      if (!loadingFrame) return;
      frameId = loadingFrame.frame_id;
      onDetections(loadingFrame, loadingBlob);
      loadingFrame = null;
      loadingBlob = null;
      releaseUrl();
    }
    image.hidden = false;
    onPreview(true);
  }

  function imageFailed() {
    if (!status?.ready || !image.hasAttribute("src")) return;
    clearImage();
    onPreview(false);
    // The next server status retries the stream if detection is still ready.
  }

  function connect() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const query = matchedPreview ? "?" + new URLSearchParams({preview: "true", display_mode: displayMode}) : "";
    socket = new WebSocket(protocol + "//" + location.host + "/ws/detections" + query);
    lastMessageAt = Date.now();
    socket.onmessage = event => {
      if (closed || socket.readyState !== WebSocket.OPEN) return;
      if (matchedPreview && event.data instanceof Blob) {
        // Each JSON detection packet is followed by its JPEG. Skip whole pairs
        // while the browser is decoding so slow clients never queue old frames.
        if (nextFrame && !loadingFrame) {
          loadingFrame = nextFrame;
          loadingBlob = event.data;
          imageUrl = URL.createObjectURL(event.data);
          image.src = imageUrl;
        }
        nextFrame = null;
        return;
      }
      let packet;
      try {
        packet = JSON.parse(event.data);
      } catch (error) {
        console.error("Invalid detection feed message", error);
        return;
      }
      lastMessageAt = Date.now();
      if (packet.event === "status") {
        delay = 1000;
        acceptStatus(packet);
      } else if (packet.event === "detections" && status?.ready &&
          packet.session_id === status.session_id && packet.frame_id > frameId) {
        if (matchedPreview) {
          nextFrame = packet;
        } else {
          frameId = packet.frame_id;
          onDetections(packet);
        }
      }
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (closed) return;
      acceptStatus({ state: "offline", ready: false });
      reconnectTimer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10000);
    };
  }

  image.addEventListener("load", imageLoaded);
  image.addEventListener("error", imageFailed);
  acceptStatus({ state: "connecting", ready: false });
  connect();
  const watchdog = setInterval(() => {
    if (socket.readyState <= WebSocket.OPEN && Date.now() - lastMessageAt > 3000) {
      acceptStatus({ state: "offline", ready: false });
      socket.close();
    }
  }, 1000);

  return () => {
    closed = true;
    clearTimeout(reconnectTimer);
    clearInterval(watchdog);
    socket.close();
    image.removeEventListener("load", imageLoaded);
    image.removeEventListener("error", imageFailed);
    clearImage();
  };
}
