import "./style.css";
import { PipecatClient } from "@pipecat-ai/client-js";
import { WebSocketTransport, ProtobufFrameSerializer } from "@pipecat-ai/websocket-transport";

const BOT_URL = import.meta.env.VITE_BOT_URL || "http://localhost:7860";

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const emptyEl = $("empty");
const statusEl = $("status");
const stage = $("stage");
const wave = $("wave");
const toggleBtn = $("toggle");

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- waveform ---------- */

const BAR_COUNT = 41;
const bars = Array.from({ length: BAR_COUNT }, () => {
  const bar = document.createElement("span");
  wave.appendChild(bar);
  return bar;
});

let speaker = null; // "bot" | "user" | null
let level = 0; // smoothed 0..1
const real = { user: 0, bot: 0, userAt: 0, botAt: 0 };

function setSpeaker(next) {
  speaker = next;
  stage.dataset.speaker = next || "idle";
}

function targetLevel(t) {
  if (!speaker) return 0;
  const lastReal = speaker === "user" ? real.userAt : real.botAt;
  if (performance.now() - lastReal < 250) {
    // real audio level reported by the transport
    const v = speaker === "user" ? real.user : real.bot;
    return Math.min(1, 0.2 + v * 1.6);
  }
  // no live levels available: gentle synthetic pulse while someone is speaking
  return 0.45 + 0.25 * Math.sin(t / 170) + 0.15 * Math.sin(t / 53);
}

function frame(t) {
  level += (targetLevel(t) - level) * 0.18;
  for (let i = 0; i < BAR_COUNT; i++) {
    const envelope = Math.sin((Math.PI * (i + 0.5)) / BAR_COUNT);
    const wobble = 0.55 + 0.45 * Math.sin(t / 120 + i * 0.7);
    if (reduceMotion) {
      bars[i].style.height = "24%";
      bars[i].style.opacity = String(0.25 + 0.75 * level);
    } else {
      const h = 6 + 94 * level * envelope * wobble;
      bars[i].style.height = `${Math.max(6, h)}%`;
    }
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* ---------- chat ---------- */

function setStatus(text, state) {
  statusEl.textContent = text;
  statusEl.dataset.state = state;
}

function scrollDown() {
  chat.scrollTo({ top: chat.scrollHeight, behavior: reduceMotion ? "auto" : "smooth" });
}

function addMessage(role, text = "") {
  emptyEl.hidden = true;
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  const p = document.createElement("p");
  p.textContent = text;
  el.appendChild(p);
  chat.appendChild(el);
  scrollDown();
  return el;
}

function setMessageText(el, text) {
  el.firstChild.textContent = text;
  scrollDown();
}

let userMsg = null;
let userCommitted = "";
let botMsg = null;

function resetTurns() {
  userMsg = null;
  userCommitted = "";
  botMsg = null;
}

/* ---------- connection ---------- */

let client = null;

function createClient() {
  return new PipecatClient({
    transport: new WebSocketTransport({ serializer: new ProtobufFrameSerializer() }),
    enableMic: true,
    callbacks: {
      onConnected: () => setStatus("Connected", "live"),
      onBotReady: () => setStatus("Interview starting", "live"),
      onDisconnected: () => handleEnded(),

      onUserStartedSpeaking: () => {
        botMsg = null; // next bot text belongs to a new turn
        setSpeaker("user");
        setStatus("Listening", "user");
      },
      onUserStoppedSpeaking: () => {
        if (speaker === "user") setSpeaker(null);
        setStatus("Thinking", "live");
      },
      onUserTranscript: (data) => {
        if (!data?.text) return;
        if (!userMsg) userMsg = addMessage("user");
        const shown = `${userCommitted} ${data.text}`.trim();
        userMsg.classList.toggle("interim", !data.final);
        setMessageText(userMsg, shown);
        if (data.final) userCommitted = shown;
      },

      onBotStartedSpeaking: () => {
        userMsg = null; // next user text belongs to a new turn
        userCommitted = "";
        setSpeaker("bot");
        setStatus("Speaking", "bot");
      },
      onBotStoppedSpeaking: () => {
        if (speaker === "bot") setSpeaker(null);
        setStatus("Your turn", "live");
      },
      onBotTranscript: (data) => {
        if (!data?.text) return;
        if (!botMsg) {
          botMsg = addMessage("bot", data.text);
        } else {
          setMessageText(botMsg, `${botMsg.firstChild.textContent} ${data.text}`);
        }
      },

      // real audio levels, if the transport provides them
      onLocalAudioLevel: (lvl) => {
        real.user = lvl;
        real.userAt = performance.now();
      },
      onRemoteAudioLevel: (lvl) => {
        real.bot = lvl;
        real.botAt = performance.now();
      },

      onError: (err) => {
        console.error(err);
        setStatus("Error", "error");
        addMessage("note", "Something went wrong. Open the browser console (F12) for details.");
      },
    },
  });
}

function handleEnded() {
  setSpeaker(null);
  client = null;
  toggleBtn.textContent = "Start interview";
  toggleBtn.dataset.mode = "start";
  toggleBtn.disabled = false;
  setStatus("Ended", "idle");
}

async function start() {
  toggleBtn.disabled = true;
  setStatus("Connecting", "connecting");
  chat.querySelectorAll(".msg").forEach((n) => n.remove());
  emptyEl.hidden = false;
  resetTurns();

  client = createClient();
   try {
    const res = await fetch(`${BOT_URL}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transport: "websocket" }),
    });
    if (!res.ok) throw new Error(`/start returned ${res.status}`);
    const data = await res.json();
    console.log("start response:", data);

    // take only the path from the server's answer; build host and scheme from BOT_URL
    let path = "/ws-client";
    if (data.wsUrl) {
      try {
        const u = new URL(data.wsUrl, BOT_URL);
        path = u.pathname + u.search;
      } catch {
        path = data.wsUrl;
      }
    }
    const wsUrl = BOT_URL.replace(/^http/, "ws") + path;
    console.log("connecting to:", wsUrl);

    await client.connect({ wsUrl });
    toggleBtn.textContent = "End interview";
    toggleBtn.dataset.mode = "end";
    toggleBtn.disabled = false;
  } catch (e) {
    console.error(e);
    client = null;
    setStatus("Couldn't connect", "error");
    addMessage("note", `Couldn't connect: ${e?.message || e}`);
    toggleBtn.disabled = false;
  }
}

async function stop() {
  toggleBtn.disabled = true;
  try {
    await client?.disconnect();
  } catch (e) {
    console.error(e);
  }
  handleEnded();
}

toggleBtn.addEventListener("click", () => (client ? stop() : start()));