/* Dictation creates an editable draft; it never submits a message. */
window.InspectorVoice = function ({api, button, input, notify}) {
  let recorder, stream, timer, ticker, startedAt=0, busy=false, generation=0;
  const microphone='<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="8" y="3" width="8" height="12" rx="4"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3m-4 0h8"/></svg>';
  const status=document.createElement('span');status.className='voice-recording-status';status.hidden=true;status.setAttribute('role','status');button.before(status);
  const label = text => {
    const recording=text==='Stop recording',processing=text==='Transcribing…';
    button.dataset.state=recording?'recording':processing?'processing':'idle';
    button.innerHTML=recording?'<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>':microphone;
    button.setAttribute('aria-label',text==='Voice'?'Start voice input':text);button.title=button.getAttribute('aria-label');button.setAttribute('aria-pressed',String(recording));
    status.hidden=!(recording||processing);if(processing)status.textContent='Transcribing…';
  };
  label('Voice');
  const release = () => {clearTimeout(timer);clearInterval(ticker);stream?.getTracks().forEach(t=>t.stop());stream=null;};
  const cancel = () => {generation++;if(recorder?.state==='recording')recorder.stop();release();busy=false;button.disabled=false;label('Voice');};
  const wav = async blob => {
    const context=new AudioContext();
    try {
      const decoded=await context.decodeAudioData(await blob.arrayBuffer());
      if(decoded.duration>61)throw new Error('Record up to 60 seconds');
      const render=new OfflineAudioContext(1,Math.ceil(Math.min(60,decoded.duration)*24000),24000);
      const source=render.createBufferSource();source.buffer=decoded;source.connect(render.destination);source.start();
      const samples=(await render.startRendering()).getChannelData(0);
      const buffer=new ArrayBuffer(44+samples.length*2),view=new DataView(buffer);
      const str=(offset,value)=>{for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i));};
      str(0,'RIFF');view.setUint32(4,buffer.byteLength-8,true);str(8,'WAVE');str(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,24000,true);view.setUint32(28,48000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);str(36,'data');view.setUint32(40,samples.length*2,true);
      for(let i=0;i<samples.length;i++){const v=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,v<0?v*32768:v*32767,true);}
      return new Blob([buffer],{type:'audio/wav'});
    } finally {await context.close();}
  };
  button.onclick=async()=>{
    if(recorder?.state==='recording'){recorder.stop();release();return;}
    if(busy||input.disabled)return;
    const own=++generation;busy=true;const chunks=[];
    try {
      stream=await navigator.mediaDevices.getUserMedia({audio:true});
      if(own!==generation){release();return;}
      recorder=new MediaRecorder(stream);
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      recorder.onerror=()=>{cancel();notify('Microphone recording failed');};
      recorder.onstop=async()=>{
        release();if(own!==generation)return;
        label('Transcribing…');button.disabled=true;
        try {
          const body=await wav(new Blob(chunks,{type:recorder.mimeType}));
          const result=await api('/v1/voice/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body});
          if(own!==generation)return;
          if(!result.text){notify('No speech detected. Please try again.');return;}
          if(input.value.length+result.text.length+1>input.maxLength)throw new Error('The message is too long. Shorten it before adding dictation.');
          input.value+=(input.value&&!/\s$/.test(input.value)?' ':'')+result.text;
          input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();
        } catch(error){if(own===generation)notify(error.message||'Could not transcribe recording');}
        finally{if(own===generation){busy=false;button.disabled=false;label('Voice');}}
      };
      recorder.start();startedAt=Date.now();status.textContent='Recording · 0:00';label('Stop recording');ticker=setInterval(()=>{const seconds=Math.floor((Date.now()-startedAt)/1000);status.textContent='Recording · '+Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');},1000);timer=setTimeout(()=>{if(recorder.state==='recording')recorder.stop();release();},60000);
    } catch(error){release();busy=false;label('Voice');notify(error.name==='NotAllowedError'?'Allow microphone access to use voice input.':error.message||'Microphone unavailable');}
  };
  window.addEventListener('pagehide',cancel);
  return {cancel, async refresh(){try{const config=await api('/v1/voice/config');button.hidden=!(config.enabled&&navigator.mediaDevices?.getUserMedia&&window.MediaRecorder);}catch{button.hidden=true;}}};
};
