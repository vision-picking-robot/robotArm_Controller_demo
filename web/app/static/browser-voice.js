export class BrowserVoice {
  constructor(update, scope = window) {
    this.scope = scope; this.update = update; this.generation = 0; this.active = false;
  }
  async api(action, body) {
    const response = await this.scope.fetch('/api/voice/' + action, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined, cache: 'no-store'
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '음성 서버 연결을 확인하세요.');
    return data;
  }
  async start() {
    const Recognition = this.scope.SpeechRecognition || this.scope.webkitSpeechRecognition;
    if (!Recognition) throw new Error('이 브라우저는 음성 인식을 지원하지 않습니다. Chrome 또는 Edge에서 실행하세요.');
    if (!this.scope.isSecureContext) throw new Error('마이크 사용에는 localhost 또는 HTTPS 접속이 필요합니다.');
    if (this.active || this.starting) return;
    this.starting = true;
    const generation = ++this.generation;
    let status;
    try { status = await this.api('wake'); } finally { this.starting = false; }
    if (generation !== this.generation) { await this.api('stop'); return; }
    this.session = status.session; this.turn = status.turn; this.active = true;
    this.update(status);
    const listen = () => {
      if (!this.active || generation !== this.generation) return;
      const recognition = this.recognition = new Recognition();
      recognition.lang = 'ko-KR'; recognition.interimResults = false;
      recognition.continuous = false; recognition.maxAlternatives = 1;
      let handled = false;
      recognition.onresult = async event => {
        if (handled || !this.active || generation !== this.generation) return;
        const result = event.results[event.resultIndex];
        if (!result?.isFinal) return;
        handled = true; recognition.stop();
        try {
          const next = await this.api('transcript', {session: this.session, turn: this.turn, text: result[0].transcript});
          if (!this.active || generation !== this.generation) return;
          this.turn = next.turn; this.update(next);
          if (!next.can_stop) { this.active = false; return; }
          const resume = () => { if (this.active && generation === this.generation) listen(); };
          if (next.reply && this.scope.speechSynthesis && this.scope.SpeechSynthesisUtterance) {
            const speech = new this.scope.SpeechSynthesisUtterance(next.reply);
            speech.lang = 'ko-KR';
            let resumed = false;
            speech.onend = speech.onerror = () => { if (!resumed) { resumed = true; resume(); } };
            this.scope.speechSynthesis.speak(speech);
          } else resume();
        } catch (error) { await this.fail(error, generation); }
      };
      recognition.onerror = event => {
        if (event.error === 'aborted' || generation !== this.generation) return;
        handled = true; void this.fail(new Error('마이크 입력 오류: ' + event.error), generation);
      };
      recognition.onend = () => {
        if (!handled && this.active && generation === this.generation) void this.stop().catch(error => this.update({state:'error',can_stop:false,error:error.message}));
      };
      try { recognition.start(); } catch (error) { void this.fail(error, generation); }
    };
    listen();
  }
  async fail(error, generation) {
    if (generation !== this.generation) return;
    await this.stop().catch(() => {});
    this.update({state: 'error', can_stop: false, error: error.message});
  }
  async stop() {
    this.active = false; ++this.generation;
    this.recognition?.abort(); this.scope.speechSynthesis?.cancel();
    const status = await this.api('stop'); this.update(status); return status;
  }
}
