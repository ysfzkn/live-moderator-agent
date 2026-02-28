/**
 * Main application orchestrator.
 * Connects ServerConnection, AudioStreamManager, Store, and UI.
 *
 * Gemini flow:
 *   1. Load agenda -> AGENDA_LOADED
 *   2. Click Connect -> sends CONNECT_AI -> AI_CONNECTED
 *   3. On AI_CONNECTED, get raw WS and start AudioStreamManager
 *   4. AUDIO_DATA messages -> playAudio on AudioStreamManager
 */

import { ServerConnection, type ServerMessage } from "./connection/server-ws";
import { AudioStreamManager } from "./audio/webrtc";
import { Store, type AppState, type SessionInfo } from "./state/store";

export class App {
  private server: ServerConnection;
  private audio: AudioStreamManager;
  private store: Store;

  constructor() {
    this.server = new ServerConnection();
    this.audio = new AudioStreamManager();
    this.store = new Store();
  }

  init(): void {
    this.setupServerHandlers();
    this.setupUIHandlers();
    this.setupStoreSubscription();
    this.server.connect();
  }

  private setupServerHandlers(): void {
    this.server.on("connection", (msg) => {
      const connected = msg.payload.status === "connected";
      this.store.update({ serverConnected: connected });
    });

    this.server.on("AGENDA_LOADED", (msg) => {
      const p = msg.payload;
      this.store.update({
        agendaLoaded: true,
        agendaTitle: p.title as string,
        sessions: p.sessions as SessionInfo[],
      });
    });

    this.server.on("AI_CONNECTED", async () => {
      try {
        const ws = this.server.getWebSocket();
        if (!ws) {
          console.error("No WebSocket available for audio streaming");
          return;
        }
        await this.audio.connect({ serverWs: ws });
        this.store.update({ webrtcConnected: true });
      } catch (err) {
        console.error("Audio connection failed:", err);
        this.store.update({ webrtcConnected: false });
      }
    });

    this.server.on("AUDIO_DATA", (msg) => {
      const data = msg.payload.data as string;
      if (data) {
        this.audio.playAudio(data);
      }
    });

    this.server.on("STATE_UPDATE", (msg) => {
      const p = msg.payload;
      this.store.update({
        conferenceState: p.state as string,
        currentSessionIndex: p.session_index as number,
        currentSessionTitle: (p.session_title as string) || "",
        currentSpeakerName: (p.speaker_name as string) || "",
        isPaused: (p.is_paused as boolean) || false,
      });
    });

    this.server.on("TIMER_TICK", (msg) => {
      const p = msg.payload;
      this.store.update({
        elapsedSeconds: p.elapsed_seconds as number,
        remainingSeconds: p.remaining_seconds as number,
        totalSeconds: p.total_seconds as number,
        progressRatio: p.progress_ratio as number,
      });
    });

    this.server.on("MODERATOR_STATUS", (msg) => {
      this.store.update({
        moderatorStatus: msg.payload.status as "idle" | "speaking" | "listening",
      });
    });

    this.server.on("TRANSCRIPT", (msg) => {
      const text = msg.payload.text as string;
      if (text) this.store.addTranscript(text);
    });

    this.server.on("ERROR", (msg) => {
      console.error("Server error:", msg.payload.message);
    });

    this.server.on("CONFERENCE_ENDED", () => {
      this.store.update({ conferenceState: "ended" });
    });
  }

  private setupUIHandlers(): void {
    // File drop / upload
    const fileDrop = document.getElementById("file-drop")!;
    const fileInput = document.getElementById("file-input") as HTMLInputElement;

    fileDrop.addEventListener("click", () => fileInput.click());
    fileDrop.addEventListener("dragover", (e) => {
      e.preventDefault();
      fileDrop.classList.add("dragover");
    });
    fileDrop.addEventListener("dragleave", () => {
      fileDrop.classList.remove("dragover");
    });
    fileDrop.addEventListener("drop", (e) => {
      e.preventDefault();
      fileDrop.classList.remove("dragover");
      const file = (e as DragEvent).dataTransfer?.files[0];
      if (file) this.loadAgendaFile(file);
    });
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (file) this.loadAgendaFile(file);
    });

    // Connect button - sends CONNECT_AI
    document.getElementById("btn-connect")!.addEventListener("click", () => {
      if (this.store.getState().agendaLoaded) {
        this.server.connectAI();
      }
    });

    // Controls
    document.getElementById("btn-start")!.addEventListener("click", () => {
      this.server.startConference();
    });

    document.getElementById("btn-pause")!.addEventListener("click", () => {
      this.server.pause();
    });

    document.getElementById("btn-resume")!.addEventListener("click", () => {
      this.server.resume();
    });

    document.getElementById("btn-next")!.addEventListener("click", () => {
      this.server.nextSession();
    });

    document.getElementById("btn-speaker-done")!.addEventListener("click", () => {
      this.server.speakerFinished();
    });

    document.getElementById("btn-interact")!.addEventListener("click", () => {
      this.server.toggleInteract();
    });

    document.getElementById("btn-mute")!.addEventListener("click", () => {
      if (this.audio.isMuted) {
        this.audio.unmuteMicrophone();
        this.store.update({ isMuted: false });
      } else {
        this.audio.muteMicrophone();
        this.store.update({ isMuted: true });
      }
    });

    document.getElementById("btn-override")!.addEventListener("click", () => {
      const input = document.getElementById("override-input") as HTMLInputElement;
      const msg = input.value.trim();
      if (msg) {
        this.server.overrideMessage(msg);
        input.value = "";
      }
    });

    // Override input enter key
    document.getElementById("override-input")!.addEventListener("keydown", (e) => {
      if ((e as KeyboardEvent).key === "Enter") {
        document.getElementById("btn-override")!.click();
      }
    });
  }

  private setupStoreSubscription(): void {
    this.store.subscribe((state) => this.render(state));
  }

  private async loadAgendaFile(file: File): Promise<void> {
    try {
      const text = await file.text();
      const agenda = JSON.parse(text);
      this.server.loadAgenda(agenda);
    } catch {
      console.error("Invalid agenda JSON file");
    }
  }

  private render(state: AppState): void {
    // Setup/Dashboard visibility
    const setup = document.getElementById("setup-screen")!;
    const dashboard = document.getElementById("dashboard")!;

    if (state.agendaLoaded) {
      setup.classList.add("hidden");
      dashboard.classList.remove("hidden");
    }

    // Server status
    const serverStatus = document.getElementById("server-status")!;
    serverStatus.className = `status-indicator ${state.serverConnected ? "status-connected" : "status-disconnected"}`;
    serverStatus.querySelector("span:last-child")!.textContent = state.serverConnected
      ? "Bagli"
      : "Baglanti yok";

    // AI connection status (replaces WebRTC status)
    const webrtcStatus = document.getElementById("webrtc-status")!;
    if (state.agendaLoaded) {
      webrtcStatus.classList.remove("hidden");
      webrtcStatus.className = `status-indicator ${state.webrtcConnected ? "status-connected" : "status-disconnected"}`;
    }

    // Moderator badge
    const badge = document.getElementById("moderator-badge")!;
    badge.className = `moderator-badge moderator-${state.moderatorStatus}`;
    const badgeTexts: Record<string, string> = {
      idle: "HAZIR",
      speaking: "KONUSUYOR",
      listening: "DINLIYOR",
    };
    badge.textContent = badgeTexts[state.moderatorStatus] || "HAZIR";

    // Speaker card
    this.renderSpeakerCard(state);

    // Timer
    this.renderTimer(state);

    // Controls
    this.renderControls(state);

    // Transcript
    this.renderTranscript(state);

    // Agenda sidebar
    this.renderAgendaList(state);

    // Conference progress
    this.renderConferenceProgress(state);

    // Paused overlay
    const paused = document.getElementById("paused-overlay")!;
    if (state.isPaused) {
      paused.classList.remove("hidden");
    } else {
      paused.classList.add("hidden");
    }

    // Mute button
    const muteBtn = document.getElementById("btn-mute")!;
    muteBtn.textContent = state.isMuted ? "Mikrofon Ac" : "Mikrofon Kapat";
  }

  private renderSpeakerCard(state: AppState): void {
    const avatar = document.getElementById("speaker-avatar")!;
    const name = document.getElementById("speaker-name")!;
    const titleOrg = document.getElementById("speaker-title-org")!;
    const org = document.getElementById("speaker-org")!;
    const talkTitle = document.getElementById("talk-title")!;

    const session = state.sessions[state.currentSessionIndex];

    if (state.conferenceState === "idle" || !session) {
      avatar.textContent = "?";
      name.textContent = "Konferans baslamadi";
      titleOrg.textContent = "";
      org.textContent = "";
      talkTitle.style.display = "none";
      return;
    }

    if (session.speaker_name) {
      const initials = session.speaker_name
        .split(" ")
        .map((n: string) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);
      avatar.textContent = initials;
      name.textContent = session.speaker_name;
      talkTitle.textContent = session.title;
      talkTitle.style.display = "inline-block";
    } else {
      avatar.textContent = session.type === "break" ? "C" : "M";
      name.textContent = session.title;
      titleOrg.textContent = "";
      org.textContent = "";
      talkTitle.style.display = "none";
    }
  }

  private renderTimer(state: AppState): void {
    const display = document.getElementById("timer-display")!;
    const label = document.getElementById("timer-label")!;
    const fill = document.getElementById("progress-fill")!;

    if (state.totalSeconds <= 0) {
      display.textContent = "--:--";
      display.className = "timer-display timer-green";
      label.textContent = "";
      fill.style.width = "0%";
      return;
    }

    const remaining = Math.max(0, state.remainingSeconds);
    const minutes = Math.floor(remaining / 60);
    const seconds = Math.floor(remaining % 60);
    display.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;

    // Color based on progress
    const ratio = state.progressRatio;
    let colorClass = "timer-green";
    let fillClass = "";
    if (ratio >= 0.95) {
      colorClass = "timer-red";
      fillClass = "danger";
    } else if (ratio >= 0.8) {
      colorClass = "timer-yellow";
      fillClass = "warning";
    }
    display.className = `timer-display ${colorClass}`;
    fill.className = `progress-fill ${fillClass}`;
    fill.style.width = `${Math.min(100, ratio * 100)}%`;

    const totalMin = Math.floor(state.totalSeconds / 60);
    label.textContent = `${totalMin} dakikalik oturum`;
  }

  private renderControls(state: AppState): void {
    const btnStart = document.getElementById("btn-start") as HTMLButtonElement;
    const btnPause = document.getElementById("btn-pause") as HTMLButtonElement;
    const btnResume = document.getElementById("btn-resume") as HTMLButtonElement;
    const btnNext = document.getElementById("btn-next") as HTMLButtonElement;
    const btnSpeakerDone = document.getElementById("btn-speaker-done") as HTMLButtonElement;
    const btnInteract = document.getElementById("btn-interact") as HTMLButtonElement;

    const isIdle = state.conferenceState === "idle";
    const isEnded = state.conferenceState === "ended";
    const isActive = !isIdle && !isEnded;

    // Speaker states where "Konusmaci Bitirdi" button should be visible
    const speakerStates = ["speaker_active", "interacting", "time_warning"];
    const isSpeakerState = speakerStates.includes(state.conferenceState);

    btnStart.classList.toggle("hidden", isActive || isEnded);
    btnPause.classList.toggle("hidden", !isActive || state.isPaused);
    btnResume.classList.toggle("hidden", !state.isPaused);
    btnNext.disabled = !isActive;

    // Show/hide and enable/disable speaker done button
    btnSpeakerDone.classList.toggle("hidden", !isSpeakerState);
    btnSpeakerDone.disabled = !isSpeakerState;

    btnInteract.disabled = !["speaker_active", "interacting"].includes(state.conferenceState);
    btnInteract.textContent =
      state.conferenceState === "interacting" ? "Dinleme Modu" : "Etkilesim";
  }

  private renderTranscript(state: AppState): void {
    const panel = document.getElementById("transcript-panel")!;
    if (state.transcriptLines.length === 0) {
      panel.innerHTML = `<div class="transcript-line" style="color: var(--text-muted);">Henuz konusma yok...</div>`;
      return;
    }

    panel.innerHTML = state.transcriptLines
      .map((line) => `<div class="transcript-line">${this.escapeHtml(line)}</div>`)
      .join("");

    // Auto-scroll to bottom
    panel.scrollTop = panel.scrollHeight;
  }

  private renderAgendaList(state: AppState): void {
    const list = document.getElementById("agenda-list")!;

    if (state.sessions.length === 0) {
      list.innerHTML = `<li class="agenda-item"><span style="color: var(--text-muted);">Agenda yuklenmedi</span></li>`;
      return;
    }

    list.innerHTML = state.sessions
      .map((session, i) => {
        let statusClass = "agenda-upcoming";
        let icon = "";
        if (i < state.currentSessionIndex) {
          statusClass = "agenda-completed";
          icon = "&#10003;";
        } else if (i === state.currentSessionIndex && state.conferenceState !== "idle") {
          statusClass = "agenda-current";
          icon = "&#9654;";
        } else {
          icon = String(i + 1);
        }

        const typeLabels: Record<string, string> = {
          opening: "Acilis",
          keynote: "Keynote",
          talk: "Sunum",
          panel: "Panel",
          break: "Mola",
          qa: "S&C",
          closing: "Kapanis",
        };

        return `
          <li class="agenda-item ${statusClass}">
            <span class="agenda-item-icon">${icon}</span>
            <div class="agenda-item-content">
              <div class="agenda-item-title">${this.escapeHtml(session.title)}</div>
              <div class="agenda-item-meta">
                ${typeLabels[session.type] || session.type}
                ${session.speaker_name ? ` - ${this.escapeHtml(session.speaker_name)}` : ""}
              </div>
            </div>
            <span class="agenda-item-duration">${session.duration_minutes}dk</span>
          </li>
        `;
      })
      .join("");
  }

  private renderConferenceProgress(state: AppState): void {
    const text = document.getElementById("progress-text")!;
    const fill = document.getElementById("conference-progress-fill")!;

    const total = state.sessions.length;
    const current = state.conferenceState === "idle" ? 0 : state.currentSessionIndex + 1;
    text.textContent = `${current} / ${total}`;
    fill.style.width = total > 0 ? `${(current / total) * 100}%` : "0%";
  }

  private escapeHtml(text: string): string {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
}
