/**
 * WebRTC connection to Azure OpenAI Realtime API.
 *
 * Handles:
 * - Microphone capture via getUserMedia
 * - SDP offer/answer exchange with Azure OpenAI using ephemeral token
 * - Audio playback of AI responses
 * - Data channel for realtime events
 */

export interface WebRTCConfig {
  token: string;
  endpointUrl: string;
  voice: string;
  onCallId?: (callId: string) => void;
  onConnectionChange?: (state: RTCPeerConnectionState) => void;
  onDataChannelMessage?: (event: MessageEvent) => void;
}

export class RealtimeWebRTC {
  private pc: RTCPeerConnection | null = null;
  private dataChannel: RTCDataChannel | null = null;
  private localStream: MediaStream | null = null;
  private audioElement: HTMLAudioElement | null = null;
  private config: WebRTCConfig | null = null;

  get connectionState(): string {
    return this.pc?.connectionState || "disconnected";
  }

  async connect(config: WebRTCConfig): Promise<void> {
    this.config = config;

    // Create peer connection
    this.pc = new RTCPeerConnection({
      iceServers: [], // Azure OpenAI handles ICE
    });

    // Handle incoming audio track (AI voice)
    this.pc.ontrack = (event) => {
      this.audioElement = document.createElement("audio");
      this.audioElement.autoplay = true;
      this.audioElement.srcObject = event.streams[0];
    };

    // Connection state changes
    this.pc.onconnectionstatechange = () => {
      const state = this.pc?.connectionState || "disconnected";
      console.log("WebRTC connection state:", state);
      config.onConnectionChange?.(state as RTCPeerConnectionState);
    };

    // Create data channel for realtime events
    this.dataChannel = this.pc.createDataChannel("oai-events");
    this.dataChannel.onmessage = (event) => {
      config.onDataChannelMessage?.(event);
    };

    // Get microphone
    try {
      this.localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      // Add microphone track to peer connection
      for (const track of this.localStream.getTracks()) {
        this.pc.addTrack(track, this.localStream);
      }
    } catch (err) {
      console.error("Microphone access denied:", err);
      throw new Error("Mikrofon erisimi reddedildi. Lutfen mikrofon izni verin.");
    }

    // Create SDP offer
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);

    // Send offer to Azure OpenAI and get answer
    const sdpResponse = await fetch(config.endpointUrl, {
      method: "POST",
      headers: {
        "api-key": config.token,
        "Content-Type": "application/sdp",
      },
      body: offer.sdp,
    });

    if (!sdpResponse.ok) {
      const errorText = await sdpResponse.text();
      throw new Error(`Azure OpenAI SDP exchange failed: ${sdpResponse.status} ${errorText}`);
    }

    // Extract call_id from Location header (if available)
    const location = sdpResponse.headers.get("Location");
    if (location) {
      // Location header format: /openai/realtime/calls/{call_id}
      const callId = location.split("/").pop();
      if (callId) {
        config.onCallId?.(callId);
      }
    }

    const answerSdp = await sdpResponse.text();
    await this.pc.setRemoteDescription({
      type: "answer",
      sdp: answerSdp,
    });

    console.log("WebRTC connected to Azure OpenAI Realtime API");
  }

  muteMicrophone(): void {
    if (this.localStream) {
      for (const track of this.localStream.getAudioTracks()) {
        track.enabled = false;
      }
    }
  }

  unmuteMicrophone(): void {
    if (this.localStream) {
      for (const track of this.localStream.getAudioTracks()) {
        track.enabled = true;
      }
    }
  }

  get isMuted(): boolean {
    if (!this.localStream) return true;
    const tracks = this.localStream.getAudioTracks();
    return tracks.length === 0 || !tracks[0].enabled;
  }

  setVolume(volume: number): void {
    if (this.audioElement) {
      this.audioElement.volume = Math.max(0, Math.min(1, volume));
    }
  }

  disconnect(): void {
    if (this.dataChannel) {
      this.dataChannel.close();
      this.dataChannel = null;
    }

    if (this.localStream) {
      for (const track of this.localStream.getTracks()) {
        track.stop();
      }
      this.localStream = null;
    }

    if (this.audioElement) {
      this.audioElement.pause();
      this.audioElement.srcObject = null;
      this.audioElement = null;
    }

    if (this.pc) {
      this.pc.close();
      this.pc = null;
    }

    console.log("WebRTC disconnected");
  }
}
