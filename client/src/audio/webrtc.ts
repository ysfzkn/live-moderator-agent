/**
 * Audio stream manager for Gemini Live API via WebSocket.
 *
 * Handles:
 * - Microphone capture via getUserMedia at 16kHz mono
 * - ScriptProcessorNode to convert Float32 -> Int16 PCM
 * - Sends binary PCM frames via the server WebSocket
 * - Receives base64-encoded PCM audio from server and plays via AudioBufferSourceNode at 24kHz
 * - Audio playback queue for smooth output
 */

export interface AudioStreamConfig {
  serverWs: WebSocket;
}

export class AudioStreamManager {
  private audioContext: AudioContext | null = null;
  private playbackContext: AudioContext | null = null;
  private localStream: MediaStream | null = null;
  private scriptProcessor: ScriptProcessorNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private serverWs: WebSocket | null = null;
  private _isMuted = false;

  // Playback queue
  private playbackQueue: AudioBuffer[] = [];
  private isPlaying = false;
  private nextPlayTime = 0;

  get isMuted(): boolean {
    return this._isMuted;
  }

  async connect(config: AudioStreamConfig): Promise<void> {
    this.serverWs = config.serverWs;

    // Create audio context for capture at 16kHz
    this.audioContext = new AudioContext({ sampleRate: 16000 });

    // Create playback context at 24kHz (Gemini output rate)
    this.playbackContext = new AudioContext({ sampleRate: 24000 });

    // Get microphone
    try {
      this.localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
        },
      });
    } catch (err) {
      console.error("Microphone access denied:", err);
      throw new Error("Mikrofon erisimi reddedildi. Lutfen mikrofon izni verin.");
    }

    // Create source from microphone
    this.sourceNode = this.audioContext.createMediaStreamSource(this.localStream);

    // ScriptProcessorNode for Float32 -> Int16 PCM conversion
    // Buffer size 4096, 1 input channel, 1 output channel
    this.scriptProcessor = this.audioContext.createScriptProcessor(4096, 1, 1);

    this.scriptProcessor.onaudioprocess = (event: AudioProcessingEvent) => {
      if (this._isMuted) return;
      if (!this.serverWs || this.serverWs.readyState !== WebSocket.OPEN) return;

      const inputData = event.inputBuffer.getChannelData(0);

      // Convert Float32 [-1, 1] to Int16 [-32768, 32767]
      const int16Data = new Int16Array(inputData.length);
      for (let i = 0; i < inputData.length; i++) {
        const s = Math.max(-1, Math.min(1, inputData[i]));
        int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }

      // Send binary PCM frame via WebSocket
      this.serverWs.send(int16Data.buffer);
    };

    // Connect: mic -> scriptProcessor -> destination (required for processing to work)
    this.sourceNode.connect(this.scriptProcessor);
    this.scriptProcessor.connect(this.audioContext.destination);

    console.log("AudioStreamManager connected: mic capture at 16kHz, playback at 24kHz");
  }

  /**
   * Decode base64-encoded PCM Int16 audio and play it via AudioBufferSourceNode.
   * Gemini sends audio as base64 PCM at 24kHz.
   */
  playAudio(base64Data: string): void {
    if (!this.playbackContext) return;

    // Decode base64 to binary
    const binaryString = atob(base64Data);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }

    // Interpret as Int16 PCM
    const int16Data = new Int16Array(bytes.buffer);

    // Convert Int16 -> Float32 for Web Audio API
    const float32Data = new Float32Array(int16Data.length);
    for (let i = 0; i < int16Data.length; i++) {
      float32Data[i] = int16Data[i] / 32768.0;
    }

    // Create AudioBuffer at 24kHz mono
    const audioBuffer = this.playbackContext.createBuffer(1, float32Data.length, 24000);
    audioBuffer.getChannelData(0).set(float32Data);

    // Add to queue and play
    this.playbackQueue.push(audioBuffer);
    this._processQueue();
  }

  private _processQueue(): void {
    if (this.isPlaying || this.playbackQueue.length === 0 || !this.playbackContext) return;

    this.isPlaying = true;
    const buffer = this.playbackQueue.shift()!;

    const source = this.playbackContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this.playbackContext.destination);

    // Schedule playback at the right time to avoid gaps
    const now = this.playbackContext.currentTime;
    const startTime = Math.max(now, this.nextPlayTime);
    source.start(startTime);
    this.nextPlayTime = startTime + buffer.duration;

    source.onended = () => {
      this.isPlaying = false;
      this._processQueue();
    };
  }

  muteMicrophone(): void {
    this._isMuted = true;
    if (this.localStream) {
      for (const track of this.localStream.getAudioTracks()) {
        track.enabled = false;
      }
    }
  }

  unmuteMicrophone(): void {
    this._isMuted = false;
    if (this.localStream) {
      for (const track of this.localStream.getAudioTracks()) {
        track.enabled = true;
      }
    }
  }

  disconnect(): void {
    if (this.scriptProcessor) {
      this.scriptProcessor.disconnect();
      this.scriptProcessor = null;
    }

    if (this.sourceNode) {
      this.sourceNode.disconnect();
      this.sourceNode = null;
    }

    if (this.localStream) {
      for (const track of this.localStream.getTracks()) {
        track.stop();
      }
      this.localStream = null;
    }

    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }

    if (this.playbackContext) {
      this.playbackContext.close();
      this.playbackContext = null;
    }

    this.playbackQueue = [];
    this.isPlaying = false;
    this.nextPlayTime = 0;
    this.serverWs = null;

    console.log("AudioStreamManager disconnected");
  }
}
