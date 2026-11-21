import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

async function api(path, data) {
  const options = data && {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)};
  const response = await fetch('/api/' + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error);
  return body;
}

function Review() {
  const [batch, setBatch] = useState(new URLSearchParams(window.location.search).get('batch') || '1');
  const [loadedBatch, setLoadedBatch] = useState(null);
  const [changes, setChanges] = useState([]);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(null);
  const [busy, setBusy] = useState(false);
  async function load(id) {
    setChanges(await api('batch/' + encodeURIComponent(id)));
    setLoadedBatch(Number(id));
  }
  useEffect(() => {load(batch).catch(e => setError(e.message));}, []);
  async function act(fn) {
    setBusy(true);
    setError('');
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const reviewed = changes.filter(c => c.decision).length;
  return <main>
    <header><div><p className="project">ONTOLOGY PROPOSER</p><h1>Review proposal</h1></div>
      <form onSubmit={e => {e.preventDefault(); act(async () => {await load(batch); setVersion(null);});}}>
        <label htmlFor="batch">Batch</label><input id="batch" type="number" min="1" value={batch} onChange={e => setBatch(e.target.value)} />
        <button disabled={busy}>Load</button>
      </form>
    </header>
    <section className="toolbar">
      <span>{reviewed} / {changes.length} reviewed</span>
      <button className="primary" disabled={busy || version !== null || reviewed !== changes.length || !changes.some(c => c.decision === 'approve')}
        onClick={() => act(async () => setVersion((await api('publish', {batch: loadedBatch})).version))}>Publish version</button>
    </section>
    {error && <p role="alert" className="blocked">{error}</p>}
    {version !== null && <p role="status">Published ontology version {version}.</p>}
    {changes.map(c => <article key={c.id}>
      <div className="row">
        <div><span className="kind">{c.before ? 'Update' : 'Add'} {c.after.kind}</span><h2>{c.after.name}</h2></div>
        <span className={c.validation.ok ? 'passed' : 'blocked'}>{c.validation.ok ? 'Validated' : 'Validation failed'}</span>
      </div>
      <p className="meta">Confidence {Math.round(c.after.confidence * 100)}% · {(Array.isArray(c.after.roles) ? c.after.roles.join(', ') : 'Invalid roles')}{c.decision ? ' · ' + c.decision + 'd' : ''}</p>
      {c.after.kind === 'metric' && c.validation.rows !== undefined &&
        <p>{c.validation.rows} joined rows · {c.validation.distinct_keys} distinct base keys · {c.validation.expected_rows} expected</p>}
      <details><summary>Definition and evidence</summary>
        {c.before && <pre>{JSON.stringify(c.before, null, 2)}</pre>}
        <pre>{JSON.stringify(c.after, null, 2)}</pre>
        <pre>{JSON.stringify(c.validation, null, 2)}</pre>
      </details>
      <div className="actions">{['approve','reject'].map(decision =>
        <button key={decision} disabled={busy || version !== null || (decision === 'approve' && !c.validation.ok)}
          aria-pressed={c.decision === decision}
          onClick={() => act(async () => {await api('decide', {id: c.id, decision}); await load(loadedBatch);})}>{decision}</button>)}</div>
    </article>)}
  </main>;
}

createRoot(document.getElementById('root')).render(<Review />);
