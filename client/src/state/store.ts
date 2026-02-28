/**
 * Client-side state store that mirrors server state.
 */

export interface SessionInfo {
  id: string;
  type: string;
  title: string;
  duration_minutes: number;
  speaker_name: string | null;
}

export interface AppState {
  // Connection
  serverConnected: boolean;
  webrtcConnected: boolean;

  // Conference
  conferenceState: string;
  isPaused: boolean;
  agendaLoaded: boolean;
  agendaTitle: string;
  sessions: SessionInfo[];

  // Current session
  currentSessionIndex: number;
  currentSessionTitle: string;
  currentSpeakerName: string;

  // Timer
  elapsedSeconds: number;
  remainingSeconds: number;
  totalSeconds: number;
  progressRatio: number;

  // Moderator
  moderatorStatus: "idle" | "speaking" | "listening";
  isMuted: boolean;

  // Transcript
  transcriptLines: string[];
}

export function createInitialState(): AppState {
  return {
    serverConnected: false,
    webrtcConnected: false,
    conferenceState: "idle",
    isPaused: false,
    agendaLoaded: false,
    agendaTitle: "",
    sessions: [],
    currentSessionIndex: 0,
    currentSessionTitle: "",
    currentSpeakerName: "",
    elapsedSeconds: 0,
    remainingSeconds: 0,
    totalSeconds: 0,
    progressRatio: 0,
    moderatorStatus: "idle",
    isMuted: false,
    transcriptLines: [],
  };
}

type Listener = (state: AppState) => void;

export class Store {
  private state: AppState;
  private listeners: Listener[] = [];

  constructor() {
    this.state = createInitialState();
  }

  getState(): AppState {
    return this.state;
  }

  update(partial: Partial<AppState>): void {
    this.state = { ...this.state, ...partial };
    this.notify();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  addTranscript(text: string): void {
    const lines = [...this.state.transcriptLines, text];
    // Keep last 50 lines
    if (lines.length > 50) lines.shift();
    this.update({ transcriptLines: lines });
  }

  private notify(): void {
    for (const listener of this.listeners) {
      try {
        listener(this.state);
      } catch (err) {
        console.error("Store listener error:", err);
      }
    }
  }
}
