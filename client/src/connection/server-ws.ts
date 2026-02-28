/**
 * WebSocket connection to the Python FastAPI server.
 * Handles message routing, reconnection, and state synchronization.
 */

export type ServerMessageType =
  | "TOKEN_READY"
  | "STATE_UPDATE"
  | "TIMER_TICK"
  | "MODERATOR_STATUS"
  | "TRANSCRIPT"
  | "AGENDA_LOADED"
  | "ERROR"
  | "CONFERENCE_ENDED"
  | "CONNECTION";

export interface ServerMessage {
  type: ServerMessageType;
  payload: Record<string, unknown>;
}

export type MessageHandler = (msg: ServerMessage) => void;

export class ServerConnection {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, MessageHandler[]>();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private reconnectDelay = 1000;
  private url: string;
  private _connected = false;
  private _connecting = false;

  constructor(url?: string) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.url = url || `${protocol}//${window.location.host}/ws`;
  }

  get connected(): boolean {
    return this._connected;
  }

  connect(): void {
    if (this._connecting || this._connected) return;
    this._connecting = true;
    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this._connected = true;
        this._connecting = false;
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        this.emit("connection", { type: "CONNECTION", payload: { status: "connected" } });
      };

      this.ws.onmessage = (event) => {
        try {
          const msg: ServerMessage = JSON.parse(event.data);
          this.emit(msg.type, msg);
          this.emit("*", msg);
        } catch {
          console.error("Failed to parse server message");
        }
      };

      this.ws.onclose = () => {
        this._connected = false;
        this._connecting = false;
        this.emit("connection", { type: "CONNECTION", payload: { status: "disconnected" } });
        this.attemptReconnect();
      };

      this.ws.onerror = () => {
        this._connected = false;
        this._connecting = false;
      };
    } catch {
      this.attemptReconnect();
    }
  }

  on(event: string, handler: MessageHandler): void {
    const handlers = this.handlers.get(event) || [];
    handlers.push(handler);
    this.handlers.set(event, handlers);
  }

  off(event: string, handler: MessageHandler): void {
    const handlers = this.handlers.get(event) || [];
    this.handlers.set(
      event,
      handlers.filter((h) => h !== handler)
    );
  }

  send(type: string, payload?: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn("WebSocket not connected, cannot send:", type);
      return;
    }
    this.ws.send(JSON.stringify({ type, payload: payload || {} }));
  }

  // --- Convenience methods ---

  loadAgenda(agenda: unknown): void {
    this.send("LOAD_AGENDA", { agenda });
  }

  requestToken(): void {
    this.send("REQUEST_TOKEN");
  }

  startConference(): void {
    this.send("START_CONFERENCE");
  }

  pause(): void {
    this.send("PAUSE");
  }

  resume(): void {
    this.send("RESUME");
  }

  nextSession(): void {
    this.send("NEXT_SESSION");
  }

  toggleInteract(): void {
    this.send("TOGGLE_INTERACT");
  }

  overrideMessage(message: string): void {
    this.send("OVERRIDE_MESSAGE", { message });
  }

  sidebandConnect(callId: string): void {
    this.send("SIDEBAND_CONNECT", { call_id: callId });
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private emit(event: string, msg: ServerMessage): void {
    const handlers = this.handlers.get(event) || [];
    for (const handler of handlers) {
      try {
        handler(msg);
      } catch (err) {
        console.error(`Handler error for event ${event}:`, err);
      }
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error("Max reconnect attempts reached");
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1), 30000);

    setTimeout(() => {
      console.log(`Reconnecting (attempt ${this.reconnectAttempts})...`);
      this.connect();
    }, delay);
  }
}
