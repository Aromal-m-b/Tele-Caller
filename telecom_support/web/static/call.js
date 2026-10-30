/* Phone-style browser UI. Speech reasoning and service actions remain server-side. */
(() => {
  const $ = id => document.getElementById(id);
  let active = null, phase = 'dialer';
  const captureOptions = {echoCancellation:true,noiseSuppression:true,autoGainControl:true};
  function setPhase(next, title = '', message = '') {
    phase = next; $('device').dataset.phase = next;
    $('dial-screen').hidden = next !== 'dialer';
    $('call-screen').hidden = !['connecting','ringing','connected'].includes(next);
    $('ended-screen').hidden = next !== 'ended';
    if (title) $('connection').textContent = title;
    if (message) $('message').textContent = message;
    $('permission').hidden = next !== 'connecting';
    $('call-controls').hidden = !['ringing','connected'].includes(next);
    $('timer').hidden = next !== 'connected';
    $('end-label').textContent = next === 'connected' ? 'End call' : 'Cancel';
    $('end').setAttribute('aria-label',next === 'connected' ? 'End call' : 'Cancel call');
  }
  async function request(path, access, options = {}) {
    const response = await fetch(path,{...options,headers:{'X-Call-Access':access,'ngrok-skip-browser-warning':'1'},cache:'no-store'});
    let body;
    try { body = await response.json(); } catch (_) { throw new Error('The server did not return a session response. Check that the WebRTC server is running.'); }
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The call request failed.');
    return body;
  }
  const elapsed = call => call.started ? Math.floor((performance.now() - call.started)/1000) : 0;
  function formatTime(seconds) { return String(Math.floor(seconds/60)).padStart(2,'0') + ':' + String(seconds%60).padStart(2,'0'); }
  async function dispose(call, notify = true) {
    call.cancelled = true;
    clearInterval(call.poll); clearInterval(call.clock);
    if (call.permission) call.permission.onchange = null;
    if (call.mic) call.mic.stop();
    if (call.room) { try { await call.room.disconnect(); } catch (_) {} }
    if (notify && call.session) {
      try { await request('/api/sessions/' + encodeURIComponent(call.session.session_id),call.access,{method:'DELETE'}); } catch (_) {}
    }
  }
  function finish(call, message = 'Your voice session is closed.', notify = true) {
    if (active !== call) return;
    const seconds = elapsed(call);
    active = null;
    $('remote-audio').replaceChildren(); $('sound').hidden = true;
    $('ended-title').textContent = call.started ? 'Call ended' : 'Call cancelled';
    $('ended-message').textContent = message;
    $('ended-duration').textContent = formatTime(seconds);
    $('ended-duration').hidden = !call.started;
    setPhase('ended');
    return dispose(call,notify);
  }
  function fail(call, error) {
    if (active !== call) return;
    active = null;
    $('remote-audio').replaceChildren(); $('sound').hidden = true;
    setPhase('dialer'); $('dial-error').textContent = error.message || 'The connection failed. Please try again.';
    return dispose(call);
  }
  function markConnected(call) {
    if (active !== call || call.started) return;
    call.started = performance.now();
    setPhase('connected','Connected','You’re speaking with Maya.');
    $('timer').textContent = '00:00';
    call.clock = setInterval(() => { if (active === call) $('timer').textContent = formatTime(elapsed(call)); },1000);
  }
  async function poll(call) {
    if (active !== call || !call.session || call.polling) return;
    call.polling = true;
    try {
      const state = await request('/api/sessions/' + encodeURIComponent(call.session.session_id),call.access);
      if (active !== call) return;
      if (state.status === 'connected') markConnected(call);
      else if (state.status === 'error') {
        $('ended-title').textContent = 'Connection ended';
        await finish(call,'Maya could not continue. Check the server connection and try again.',false);
        $('ended-title').textContent = 'Connection ended';
      } else if (state.status === 'ended') await finish(call,'The voice session has ended.',false);
    } catch (_) { /* RTC audio can keep working through a transient status-request failure. */ }
    finally { call.polling = false; }
  }
  async function watchPermission(call) {
    if (!navigator.permissions || !navigator.permissions.query) return;
    try {
      const permission = await navigator.permissions.query({name:'microphone'});
      if (active !== call) return;
      call.permission = permission;
      permission.onchange = () => {
        if (active === call && phase === 'connecting' && permission.state === 'granted' && !call.capturing) acquireMicrophone(call);
      };
    } catch (_) { /* Not every browser exposes microphone permission queries. */ }
  }
  async function connectAgent(call) {
    setPhase('ringing','Ringing…','Connecting to agent');
    $('mute').setAttribute('aria-pressed','false'); $('mute-label').textContent = 'Mute';
    $('speaker').setAttribute('aria-pressed','false'); $('speaker-label').textContent = 'Speaker';
    try {
      const created = await request('/api/sessions',call.access,{method:'POST'});
      call.session = created;
      if (active !== call) { await dispose(call); return; }
      await call.room.connect(created.url,created.token);
      if (active !== call) { await dispose(call); return; }
      await call.room.localParticipant.publishTrack(call.mic,{source:LivekitClient.Track.Source.Microphone});
      if (active !== call) { await dispose(call); return; }
      call.poll = setInterval(() => poll(call),1000);
      await poll(call);
    } catch (error) { await fail(call,error); }
  }
  async function acquireMicrophone(call) {
    if (active !== call || call.capturing || call.session) return;
    call.capturing = true; $('retry-mic').disabled = true;
    $('permission-message').textContent = 'Choose Allow in your browser’s microphone permission dialog.';
    try {
      const mic = await LivekitClient.createLocalAudioTrack(captureOptions);
      if (active !== call) { mic.stop(); return; }
      call.mic = mic;
    } catch (error) {
      if (active !== call) return;
      let message = 'Microphone access is required. Allow it in this site’s browser settings, then try again.';
      if (error.name === 'NotFoundError') message = 'No microphone was found. Connect one, then try again.';
      if (error.name === 'NotReadableError') message = 'The microphone is unavailable or in use. Check the device, then try again.';
      $('permission-message').textContent = message;
      $('message').textContent = 'Waiting for microphone access';
      watchPermission(call);
      return;
    } finally {
      call.capturing = false;
      if (active === call) $('retry-mic').disabled = false;
    }
    await connectAgent(call);
  }
  async function startCall() {
    if (active) return;
    const access = $('code').value.trim();
    $('dial-error').textContent = '';
    if (!access) { $('dial-error').textContent = 'Enter your private dial code first.'; return; }
    if (!window.isSecureContext || !navigator.mediaDevices) { $('dial-error').textContent = 'Use localhost or the HTTPS ngrok page to enable your microphone.'; return; }
    if (!window.LivekitClient) { $('dial-error').textContent = 'The voice client did not load. Refresh the page and check the WebRTC server.'; return; }
    const call = {access,started:0,cancelled:false,muted:false,speakerMuted:false};
    active = call;
    setPhase('connecting','Connecting…','Allow microphone access to continue.');
    try {
      const {Room,RoomEvent,Track} = LivekitClient;
      call.room = new Room({adaptiveStream:true,dynacast:true});
      call.room.on(RoomEvent.TrackSubscribed,track => {
        if (active !== call || track.kind !== Track.Kind.Audio) return;
        const element = track.attach(); element.autoplay = true; element.muted = call.speakerMuted;
        $('remote-audio').append(element);
      });
      call.room.on(RoomEvent.AudioPlaybackStatusChanged,() => { if (active === call) $('sound').hidden = call.room.canPlaybackAudio; });
      call.room.on(RoomEvent.Disconnected,() => { if (active === call && !call.cancelled) finish(call,'The voice connection has ended.'); });
      // Unlock output while the Call click is still a user gesture. Capture asks for permission.
      call.room.startAudio().catch(() => { if (active === call) $('sound').hidden = false; });
      await acquireMicrophone(call);
    } catch (error) { await fail(call,error); }
  }
  document.querySelectorAll('[data-key]').forEach(button => button.addEventListener('click',() => {
    if (phase !== 'dialer' || $('code').value.length >= 64) return;
    $('code').value += button.dataset.key; $('dial-error').textContent = '';
  }));
  $('erase').addEventListener('click',() => { $('code').value = $('code').value.slice(0,-1); });
  $('code').addEventListener('keydown',event => { if (event.key === 'Enter') { event.preventDefault(); startCall(); } });
  $('start').addEventListener('click',startCall);
  $('redial').addEventListener('click',startCall);
  $('back').addEventListener('click',() => setPhase('dialer'));
  $('retry-mic').addEventListener('click',() => { if (active) return acquireMicrophone(active); });
  $('end').addEventListener('click',() => { if (active) return finish(active,active.started ? 'Your voice session is closed.' : 'You cancelled before the conversation started.'); });
  $('mute').addEventListener('click',async () => {
    const call = active;
    if (!call || !call.mic) return;
    try {
      if (call.muted) await call.mic.unmute(); else await call.mic.mute();
      if (active !== call) return;
      call.muted = !call.muted;
      $('mute').setAttribute('aria-pressed',String(call.muted)); $('mute-label').textContent = call.muted ? 'Unmute' : 'Mute';
    } catch (_) { if (active === call) $('message').textContent = 'Check your microphone permission and device.'; }
  });
  $('speaker').addEventListener('click',() => {
    if (!active) return;
    active.speakerMuted = !active.speakerMuted;
    $('remote-audio').querySelectorAll('audio').forEach(element => { element.muted = active.speakerMuted; });
    $('speaker').setAttribute('aria-pressed',String(active.speakerMuted));
    $('speaker-label').textContent = active.speakerMuted ? 'Sound off' : 'Speaker';
  });
  $('sound').addEventListener('click',() => { if (active) active.room.startAudio().catch(() => {}); });
  window.addEventListener('pagehide',() => {
    if (!active) return;
    if (active.mic) active.mic.stop();
    if (active.session) fetch('/api/sessions/' + encodeURIComponent(active.session.session_id),{method:'DELETE',headers:{'X-Call-Access':active.access,'ngrok-skip-browser-warning':'1'},keepalive:true}).catch(() => {});
  });
})();
